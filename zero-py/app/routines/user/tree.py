"""tree ---- 模块树管理 routine (增量事务模式).

事实源 tree.json; kernel 内存树 (冲突锥/占用) 只看 id, name 仅为展示
标签无运行时语义. 编辑操作以事务即时生效 (先 kernel 后文件, kernel
拒绝则文件不动):

  - add {parent, id, name?}: kernel load_module + 文件挂载
  - remove {id}: 子树自底 kernel unload (被占拒绝, 已拆补偿回挂) + 文件删除
  - reorder {parent, id, dir}: 同级换位 (kernel children 顺序无语义, 只动文件)
  - rename {id, name}: 只改文件 (kernel 不动, 展示层读文件)

本模块分两层 (同构 models.py):
  - TreeHub (name 'tree', passive 常驻): req handlers get / apply
  - bridge routines (tree/get | tree/apply): 一次性 routine, run 内解析
    hub rid 后 req 转发 ---- 所有消费方统一按 name call, web_server 的
    GET /tree、POST /tree/apply 端点即经 call 消费
"""
from __future__ import annotations

import asyncio
import json
from asyncio import Event
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Tuple

from routine import Routine, request
from routine.logger import setup_logger

__all__ = ['TreeHub', 'TreeGet', 'TreeApply']

_log = setup_logger('tree')

# tree.json: app/tree.json (main.py 启动读入注入 kernel.create).
# tree.py parents: user → routines → app.
_TREE_FILE = Path(__file__).resolve().parents[2] / 'tree.json'


# ──────────────────────────────────────────────────────────────────────────────
# 文件读写 (原子落盘)

def _read_modules() -> Dict[str, Any]:
    raw = json.loads(_TREE_FILE.read_text(encoding='utf-8'))
    modules = raw.get('modules')
    if not isinstance(modules, dict) or not modules:
        raise ValueError(f'invalid tree.json: {raw!r}')
    return modules


def _write_modules(modules: Dict[str, Any]) -> None:
    tmp = _TREE_FILE.with_suffix('.json.tmp')
    tmp.write_text(
        json.dumps({'modules': modules}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    tmp.replace(_TREE_FILE)


def _clean_rec(rec: Dict[str, Any]) -> Dict[str, Any]:
    """删空 children/name 的键, 保持文件与 kernel serialize 同规."""
    out: Dict[str, Any] = {}
    if rec.get('name'):
        out['name'] = rec['name']
    if rec.get('children'):
        out['children'] = list(rec['children'])
    return out


class TreeHub(Routine):
    """模块树管理 hub:编辑事务的 req 出口 (kernel 增量 + tree.json 同步)."""

    name = 'tree'
    is_passive = True

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '模块树管理 hub (passive 常驻). req events: get (读 tree.json '
            '静态定义) / apply {op, ...} (编辑事务, 即时生效): add '
            '{parent, id, name?} / remove {id} (被占模块拒绝) / move '
            '{id, parent} (跨父移动子树) / reorder {parent, id, dir: -1|1} '
            '/ rename {id, name}.'
        ),
    }

    def __init__(self):
        super().__init__()
        self._stop_event: Event | None = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._stop_event = Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    # ── req handlers ──

    @request('get')
    async def on_get(self, source, data) -> Dict[str, Any]:
        """读 tree.json 静态定义. 文件缺失/损坏抛异常 (调用方兜底)."""
        return {'ok': True, 'modules': _read_modules()}

    @request('apply')
    async def on_apply(self, source, data) -> Dict[str, Any]:
        """编辑事务: 先 kernel 后文件, kernel 拒绝则文件不动."""
        try:
            return await self._dispatch((data or {}).get('op'), data or {})
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    # ── op 分派 (kernel 侧失败即整体失败, 英文错误消息) ──

    async def _dispatch(self, op: Any, data: Dict[str, Any]) -> Dict[str, Any]:
        if op == 'add':
            return await self._op_add(data)
        if op == 'remove':
            return await self._op_remove(data)
        if op == 'move':
            return await self._op_move(data)
        if op == 'reorder':
            return await self._op_reorder(data)
        if op == 'rename':
            return self._op_rename(data)
        return {'ok': False, 'error': f'unknown op: {op!r}'}

    async def _op_add(self, data: Dict[str, Any]) -> Dict[str, Any]:
        mid = str(data.get('id') or '').strip()
        parent = str(data.get('parent') or '').strip()
        name = str(data.get('name') or '').strip()
        if not mid:
            return {'ok': False, 'error': 'id: non-empty string required'}
        if not parent:
            return {'ok': False, 'error': 'parent: non-empty string required'}
        modules = _read_modules()
        if mid in modules:
            return {'ok': False, 'error': f'module {mid!r} already exists'}
        if parent not in modules:
            return {'ok': False, 'error': f'parent module {parent!r} not found'}
        await self.load_module(parent, mid, name)
        modules[mid] = {'name': name} if name and name != mid else {}
        modules[parent].setdefault('children', []).append(mid)
        modules[parent] = _clean_rec(modules[parent])
        try:
            _write_modules(modules)
        except Exception as exc:
            _log.warning('add %s: kernel ok but persist failed: %s', mid, exc)
            return {'ok': False, 'error': f'kernel updated but persist failed: {exc}'}
        _log.info('tree.json: +%s (under %s)', mid, parent)
        return {'ok': True}

    async def _op_remove(self, data: Dict[str, Any]) -> Dict[str, Any]:
        mid = str(data.get('id') or '').strip()
        if mid == 'root':
            return {'ok': False, 'error': "'root' cannot be removed"}
        modules = _read_modules()
        if mid not in modules:
            return {'ok': False, 'error': f'module {mid!r} not found'}
        # 子树收集: (id, parent, name), 叶子在前 (自底 unload)
        order: List[Tuple[str, str, str]] = []
        parent_of = {c: m for m, rec in modules.items()
                     for c in (rec.get('children') or [])}

        def collect(m: str) -> None:
            for c in modules[m].get('children') or []:
                collect(c)
            order.append((m, parent_of.get(m, ''), modules[m].get('name') or ''))

        collect(mid)
        # kernel 自底 unload; 失败补偿已拆的逆序重挂
        done: List[Tuple[str, str, str]] = []
        try:
            for m, p, nm in order:
                await self.unload_module(m)
                done.append((m, p, nm))
        except Exception as exc:
            for m, p, nm in reversed(done):
                try:
                    await self.load_module(p, m, nm)
                except Exception:
                    _log.error('remove %s: rollback load %s failed', mid, m)
            return {'ok': False, 'error': str(exc)}
        # 文件删条目 + 父 children 移除
        for m, _, _ in order:
            del modules[m]
        p = parent_of.get(mid)
        if p and p in modules:
            modules[p]['children'] = [c for c in (modules[p].get('children') or [])
                                      if c != mid]
            modules[p] = _clean_rec(modules[p])
        try:
            _write_modules(modules)
        except Exception as exc:
            _log.warning('remove %s: kernel ok but persist failed: %s', mid, exc)
            return {'ok': False, 'error': f'kernel updated but persist failed: {exc}'}
        _log.info('tree.json: -%s (%d modules)', mid, len(order))
        return {'ok': True}

    async def _op_move(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """跨父移动子树: kernel 自底 unload 整棵子树, mid 挂新父、子孙挂原父
        (前序); 任一步失败全量补偿回原位, 文件不动."""
        mid = str(data.get('id') or '').strip()
        new_parent = str(data.get('parent') or '').strip()
        if mid == 'root':
            return {'ok': False, 'error': "'root' cannot be moved"}
        if not new_parent:
            return {'ok': False, 'error': 'parent: non-empty string required'}
        modules = _read_modules()
        if mid not in modules:
            return {'ok': False, 'error': f'module {mid!r} not found'}
        if new_parent not in modules:
            return {'ok': False, 'error': f'parent module {new_parent!r} not found'}
        parent_of = {c: m for m, rec in modules.items()
                     for c in (rec.get('children') or [])}
        old_parent = parent_of.get(mid, '')
        if new_parent == old_parent:
            return {'ok': True}   # 同父 no-op
        # 环检测: 新父不得在 mid 子树内 (含自身)
        subtree: set = set()
        stack = [mid]
        while stack:
            m = stack.pop()
            subtree.add(m)
            stack.extend(modules[m].get('children') or [])
        if new_parent in subtree:
            return {'ok': False,
                    'error': f'cannot move {mid!r} into its own subtree'}
        # 子树收集 (后序: 叶子先)
        order: List[Tuple[str, str, str]] = []

        def collect(m: str) -> None:
            for c in modules[m].get('children') or []:
                collect(c)
            order.append((m, parent_of.get(m, ''), modules[m].get('name') or ''))

        collect(mid)
        # kernel: 自底 unload → 前序挂回 (mid → 新父, 其余 → 原父)
        unloaded: List[Tuple[str, str, str]] = []
        loaded_new: List[Tuple[str, str, str]] = []
        try:
            for m, p, nm in order:
                await self.unload_module(m)
                unloaded.append((m, p, nm))
            for m, p, nm in reversed(unloaded):
                await self.load_module(new_parent if m == mid else p, m, nm)
                loaded_new.append((m, p, nm))
        except Exception as exc:
            # 全量补偿: 先拆已挂新位的 (子先), 再全部挂回原位 (父先)
            try:
                for m, _, _ in reversed(loaded_new):
                    await self.unload_module(m)
                for m, p, nm in reversed(unloaded):
                    await self.load_module(p, m, nm)
            except Exception:
                _log.error('move %s: rollback failed', mid)
            return {'ok': False, 'error': str(exc)}
        # 文件: 旧父 children 移除, 新父 append (子树条目内部不变)
        if old_parent and old_parent in modules:
            modules[old_parent]['children'] = [
                c for c in (modules[old_parent].get('children') or []) if c != mid]
            modules[old_parent] = _clean_rec(modules[old_parent])
        modules[new_parent].setdefault('children', []).append(mid)
        modules[new_parent] = _clean_rec(modules[new_parent])
        try:
            _write_modules(modules)
        except Exception as exc:
            _log.warning('move %s: kernel ok but persist failed: %s', mid, exc)
            return {'ok': False, 'error': f'kernel updated but persist failed: {exc}'}
        _log.info('tree.json: %s moved %s -> %s', mid, old_parent, new_parent)
        return {'ok': True}

    async def _op_reorder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        parent = str(data.get('parent') or '').strip()
        mid = str(data.get('id') or '').strip()
        dir_ = data.get('dir')
        if dir_ not in (-1, 1):
            return {'ok': False, 'error': 'dir: -1 or 1 required'}
        modules = _read_modules()
        rec = modules.get(parent)
        if rec is None:
            return {'ok': False, 'error': f'parent module {parent!r} not found'}
        children = rec.get('children') or []
        try:
            i = children.index(mid)
        except ValueError:
            return {'ok': False, 'error': f'module {mid!r} is not a child of {parent!r}'}
        j = i + dir_
        if j < 0 or j >= len(children):
            return {'ok': True}   # 边界 no-op
        children[i], children[j] = children[j], children[i]
        rec['children'] = children
        modules[parent] = _clean_rec(rec)
        _write_modules(modules)
        return {'ok': True}

    def _op_rename(self, data: Dict[str, Any]) -> Dict[str, Any]:
        mid = str(data.get('id') or '').strip()
        name = str(data.get('name') or '').strip()
        modules = _read_modules()
        if mid not in modules:
            return {'ok': False, 'error': f'module {mid!r} not found'}
        if name and name != mid:
            modules[mid]['name'] = name
        else:
            modules[mid].pop('name', None)
        modules[mid] = _clean_rec(modules[mid])
        _write_modules(modules)
        return {'ok': True}


# ──────────────────────────────────────────────────────────────────────────────
# bridge routines (一次性, run 内 req 常驻 hub)

async def _find_hub_rid(routine: Routine) -> str:
    """按 name 'tree' 查常驻 hub 的 rid (启动竞态兜底重试 ~3s)."""
    for _ in range(30):
        for r in await routine.get_running_routines():
            if str(r.get('name') or '') == 'tree':
                rid = str(r.get('id') or '').strip()
                if rid:
                    return rid
        await asyncio.sleep(0.1)
    raise RuntimeError('tree hub routine not running (name: tree)')


class TreeGet(Routine):
    name = 'tree/get'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '读模块树静态定义 (tree.json). 返回 {ok, modules}.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'get', {})


class TreeApply(Routine):
    name = 'tree/apply'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '模块树编辑事务 (即时生效). kwargs: {op, ...}, op ∈ '
                       'add {parent, id, name?} / remove {id} / move '
                       '{id, parent} / reorder {parent, id, dir} / rename '
                       '{id, name}.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'apply', kwargs or {})
