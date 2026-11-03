"""回执契约的装配守卫: 装进这套身体的每个 ``run``, 交回的键只能是那三个.

``frames.receipt`` 只在**那个能力被叫到**时才炸 —— 一整车的能力可以带着
错的回执上车, 等模型第一次伸手才炸(``car/status`` 那次就是这样: 车装上来
第一次问状态, 回执被自己的字段卡住). 这个测试在改完就炸, 不等到用.

只判 AST 能判死的三种形态, 判不了的交运行时::

    return None                放过(琐碎成功, content 缺省 ok)
    return {..}                键必须 ⊆ content/details/observe
    return x.model_dump()      判错 —— 一整个 pydantic 结果平铺出去,
                               键全是业务字段(正是 car/status/car/map 那批)
    其余(委托 return await self._execute(...) / 变量)  放过

跑法::

    PYTHONPATH=routine-py python -m app.routines.user.mossv2.tests.test_receipts
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterator, List

import yaml

from ..frames import RECEIPT_KEYS

__all__: List[str] = []

# app/(清单路径基准, 与 loader 的条目基准一致)
_APP = Path(__file__).resolve().parents[4]
_YAML = _APP / 'moss.yaml'
# 测试自己也有 run 的替身, 不算装配
_SKIP_PARTS = {'tests', '__pycache__'}


def _modules() -> List[Path]:
    """清单里的条目 → 源文件(目录递归收, 不收 __init__ 与 tests)."""
    spec: Any = yaml.safe_load(_YAML.read_text(encoding='utf-8'))
    out: List[Path] = []
    for ent in spec.get('routines') or []:
        rel = ent.get('path') if isinstance(ent, dict) else ent
        p = _APP / str(rel)
        if p.is_dir():
            out += [f for f in sorted(p.rglob('*.py'))
                    if f.name != '__init__.py'
                    and not (_SKIP_PARTS & set(f.parts))]
        elif p.is_file():
            out.append(p)
    return out


def _returns(fn: ast.AST) -> Iterator[ast.Return]:
    """fn 体内的 return, **不钻进嵌套函数**(里面的是另一件事)."""
    for child in ast.iter_child_nodes(fn):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(child, ast.Return):
            yield child
        yield from _returns(child)


def _why_bad(ret: ast.Return) -> str:
    """这条 return 为什么过不了契约; 空串 = 放过."""
    v = ret.value
    if v is None or (isinstance(v, ast.Constant) and v.value is None):
        return ''
    if isinstance(v, ast.Dict):
        keys = [k.value for k in v.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if len(keys) != len(v.keys):
            return ''        # {**x} / 变量键: 编译期判不了, 交给运行时
        extra = sorted(set(keys) - RECEIPT_KEYS)
        return f'dict 里多了 {extra}' if extra else ''
    if (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
            and v.func.attr == 'model_dump'):
        return '把 pydantic 结果平铺交出去了(该塞进 details)'
    return ''


async def t_runs_conform() -> None:
    """清单里每个 run 的 return 都过契约."""
    mods = _modules()
    checked = 0
    bad: List[str] = []
    for path in mods:
        # utf-8-sig: 车身那批文件带 BOM(import 能吃, ast.parse 不吃)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), str(path))
        rel = path.relative_to(_APP)
        for node in ast.walk(tree):
            if (not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    or node.name != 'run'):
                continue
            checked += 1
            for ret in _returns(node):
                why = _why_bad(ret)
                if why:
                    bad.append(f'{rel}:{ret.lineno} {why}')
    # 扫到 0 个 = 清单没解析出来, 那这条断言是假绿
    assert checked >= 20, f'只扫到 {checked} 个 run, 清单解析坏了?'
    assert not bad, ('这些 run 交回的东西过不了回执契约:\n  '
                     + '\n  '.join(bad))
    print(f't_runs_conform ok: {len(mods)} 个模块 / {checked} 个 run')


async def t_guard_bites() -> None:
    """守卫自己要能咬人 —— 两种真出过事的写法必须被抓到."""
    src = ('class X:\n'
           '    async def run(self, kwargs):\n'
           '        return Out(a=1).model_dump()\n'
           '    async def other(self, kwargs):\n'
           '        return {"content": "ok", "result": 1}\n'
           '    async def run2(self):\n'
           '        return {"content": "ok", "details": {}}\n')
    rets = [_r for n in ast.walk(ast.parse(src))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            for _r in _returns(n)]
    whys = [w for w in map(_why_bad, rets) if w]
    assert len(whys) == 2, whys
    print('t_guard_bites ok: 平铺 model_dump / dict 多余键 两种都咬')


async def main() -> None:
    await t_guard_bites()
    await t_runs_conform()
    print('ALL PASS')


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
