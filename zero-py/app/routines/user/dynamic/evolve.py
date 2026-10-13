"""evolve ---- 自改闭环: 备份 -> 预检 -> 落盘 -> 等热重载 -> 跑断言 -> 留或回滚.

为什么需要它: 我能给自己写 routine, 但"改完是变好还是变坏"必须有**客观判据**,
否则自迭代就是用流利的叙述掩盖随机游走. 本 routine 是那个判据的执行器:

  1. 预检 (写盘前): compile() 查语法; 把候选写成一个 ``_`` 前缀探针文件
     (watcher 跳过 ``_`` 前缀, 不触发注册) 真实 import 一次, 查类/schema ----
     语法错/ImportError 在这里就被挡住, 不会污染现网文件.
  2. 备份原文件到 runtime/evolve/.
  3. 落盘新代码.
  4. 等热重载**真的生效**: 比对候选源码里的 description 与 kernel 路由返回的
     ``routine/doc``. 不一致说明 reload 没成功 (老版本仍在服务, upsert 中间态),
     继续等; 超时则判失败 ----
     这一步防的是最阴的假阳性: 代码没加载, 测试却在老版本上通过.
  5. 跑断言: 每个 test {kwargs, expect} 真调一次目标 routine, 逐 key 比对.
  6. 全过 -> keep (写台账); 任一失败 -> 从备份还原 (写台账).
"""
from __future__ import annotations

import asyncio
import compileall
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine

_ZERO_PY = Path(__file__).resolve().parents[4]
_RUNTIME = _ZERO_PY / 'runtime' / 'evolve'
_JOURNAL = _RUNTIME / 'journal.jsonl'
_BACKUPS = _RUNTIME / 'backups'


class TestCase(BaseModel):
    kwargs: Dict[str, Any] = Field(default_factory=dict, description='调用目标 routine 的入参')
    expect: Dict[str, Any] = Field(default_factory=dict,
                                   description='期望结果: 逐 key 与返回值比对 (== 语义)')
    label: str = Field(default='', description='给这个用例起个名, 便于看台账')


class EvolveInput(BaseModel):
    target: str = Field(description='目标 routine 名 (如 sysinfo); 用于调 doc/跑测试')
    file: str = Field(description='要改写的源码文件路径 (绝对路径, 或相对项目根)')
    new_code: str = Field(default='', description='新源码全文; 留空则只跑测试不写盘')
    tests: List[TestCase] = Field(default_factory=list, description='验收断言, 全过才保留改动')
    reload_timeout: float = Field(default=12.0, ge=1.0, le=60.0, description='等热重载生效的最长秒数')


class Evolve(Routine):
    name = 'evolve'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '自改闭环: 备份+预检+等热重载真生效+跑断言, 失败自动回滚并记台账',
        'input_schema': EvolveInput.model_json_schema(),
    }

    # ---- 基础设施 ----

    def _resolve(self, file: str) -> Path:
        p = Path(file)
        if not p.is_absolute():
            p = _ZERO_PY / file
        return p.resolve()

    def _dotted(self, path: Path) -> Optional[str]:
        try:
            rel = path.relative_to(_ZERO_PY)
        except ValueError:
            return None
        if not rel.name.endswith('.py'):
            return None
        parts = list(rel.with_suffix('').parts)
        if parts and parts[-1] == '__init__':
            parts = parts[:-1]
        if not parts or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', parts[0]):
            return None
        return '.'.join(parts)

    def _probe(self, path: Path, code: str, target: str) -> Dict[str, Any]:
        """写盘前预检: 语法 + 真 import 一次 (用 watcher 会跳过的 _ 前缀探针文件)."""
        try:
            compile(code, str(path), 'exec')
        except SyntaxError as exc:
            return {'ok': False, 'stage': 'syntax', 'error': f'{exc.__class__.__name__}: {exc}'}

        dotted = self._dotted(path)
        probe = path.with_name('_evolve_probe.py')
        modname = (dotted.rsplit('.', 1)[0] + '._evolve_probe') if dotted else None
        try:
            probe.write_text(code, encoding='utf-8')
            spec = importlib.util.spec_from_file_location(modname or '_evolve_probe', probe)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as exc:
            return {'ok': False, 'stage': 'import', 'error': f'{exc.__class__.__name__}: {exc}'}
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

        found = [getattr(v, 'name', None) for v in vars(mod).values()
                 if isinstance(v, type) and issubclass(v, Routine) and v is not Routine]
        if target and target not in found:
            return {'ok': False, 'stage': 'schema',
                    'error': f'探针里没有 name={target!r} 的 Routine 子类, 实际找到 {found}'}
        return {'ok': True, 'classes': found, 'sha256': hashlib.sha256(code.encode('utf-8')).hexdigest()[:12]}

    async def _invoke(self, name: str, kwargs: Dict[str, Any], timeout: float = 30.0) -> Any:
        handle = await self.create(name, dict(kwargs))
        await handle.start()
        return await asyncio.wait_for(handle, timeout=timeout)

    async def _doc_desc(self, target: str) -> Optional[str]:
        try:
            doc = await self._invoke('routine/doc', {'name': target}, timeout=10.0)
        except Exception:
            return None
        text = doc.get('doc') if isinstance(doc, dict) else None
        return text

    @staticmethod
    def _declared_desc(code: str) -> Optional[str]:
        m = re.search(r"['\"]description['\"]\s*:\s*(['\"])(.*?)\1", code, re.S)
        return m.group(2) if m else None

    async def _await_reload(self, target: str, code: str, timeout: float) -> Dict[str, Any]:
        """等热重载真生效: kernel 路由返回的 doc 里应出现新声明 description."""
        want = self._declared_desc(code)
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = await self._doc_desc(target)
            if want and last and want in last:
                return {'reloaded': True, 'want': want, 'got': last}
            if not want and last:
                return {'reloaded': None, 'note': '候选未改变 description, 无法用 doc 判定, 改用断言把关'}
            await asyncio.sleep(0.8)
        return {'reloaded': False, 'want': want, 'got': last}

    def _log(self, row: Dict[str, Any]) -> None:
        _RUNTIME.mkdir(parents=True, exist_ok=True)
        with _JOURNAL.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    # ---- 主流程 ----

    async def run(self, kwargs: Dict[str, Any]):
        inp = EvolveInput.model_validate(kwargs)
        path = self._resolve(inp.file)
        if not path.exists():
            raise FileNotFoundError(f'目标文件不存在: {path}')

        original = path.read_text(encoding='utf-8')
        result: Dict[str, Any] = {'target': inp.target, 'file': str(path), 'steps': []}

        # 0. 预检 (只在要写盘时)
        if inp.new_code:
            probe = self._probe(path, inp.new_code, inp.target)
            result['steps'].append({'preflight': probe})
            if not probe.get('ok'):
                result.update(status='rejected_preflight', verdict='未落盘')
                self._log({**self._journal_row(inp, path, result)})
                return result

        # 1. 备份 + 落盘
        backup = None
        if inp.new_code:
            _BACKUPS.mkdir(parents=True, exist_ok=True)
            backup = _BACKUPS / f'{path.stem}.{time.strftime("%Y%m%d_%H%M%S")}.py.bak'
            shutil.copy2(path, backup)
            path.write_text(inp.new_code, encoding='utf-8')
            result['backup'] = str(backup)
            result['steps'].append({'write': f'已落盘 {len(inp.new_code)} bytes'})

            gate = await self._await_reload(inp.target, inp.new_code, inp.reload_timeout)
            result['steps'].append({'reload_gate': gate})
            if gate.get('reloaded') is False:
                shutil.copy2(backup, path)
                result.update(status='rolled_back', reason='热重载未生效, 已回滚',
                              verdict='回滚')
                self._log(self._journal_row(inp, path, result))
                return result
        else:
            result['steps'].append({'write': 'skip (new_code 为空, 只跑测试)'})

        # 2. 断言
        fails = []
        for tc in inp.tests:
            row = {'label': tc.label or 'test', 'kwargs': tc.kwargs}
            try:
                got = await self._invoke(inp.target, tc.kwargs, timeout=60.0)
                row['got'] = got
                bad = {k: {'want': v, 'got': (got or {}).get(k)} for k, v in tc.expect.items()
                       if (got or {}).get(k) != v}
                row['ok'] = not bad
                if bad:
                    row['mismatch'] = bad
                    fails.append(row)
            except Exception as exc:
                row.update(ok=False, error=f'{exc.__class__.__name__}: {exc}')
                fails.append(row)
            result['steps'].append({'test': row})

        # 3. 判决
        if fails:
            if backup is not None:
                shutil.copy2(backup, path)
                result.update(status='rolled_back', reason=f'{len(fails)}/{len(inp.tests)} 断言失败, 已回滚',
                              verdict='回滚')
            else:
                result.update(status='failed', reason=f'{len(fails)}/{len(inp.tests)} 断言失败(未改盘)',
                              verdict='失败')
        else:
            result.update(status='kept', reason=f'{len(inp.tests)}/{len(inp.tests)} 断言通过',
                          verdict='保留')
        self._log(self._journal_row(inp, path, result))
        result['journal'] = str(_JOURNAL)
        return result

    def _journal_row(self, inp: EvolveInput, path: Path, result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'target': inp.target,
            'file': str(path),
            'status': result.get('status'),
            'reason': result.get('reason'),
            'sha': hashlib.sha256(inp.new_code.encode('utf-8')).hexdigest()[:12] if inp.new_code else None,
            'bytes': len(inp.new_code) if inp.new_code else None,
            'tests': [{'label': t.label, 'kwargs': t.kwargs, 'expect': t.expect} for t in inp.tests],
            'backup': result.get('backup'),
        }
