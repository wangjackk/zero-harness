"""bb_erase —— 擦掉一个对象, 或者清空整块板.

按对象擦是**整笔一起擦**(写字是逐字多条笔迹, 一次全没), 板子从笔迹日志
重建, 不会误伤旁边的对象 —— 所以擦完那支笔的 id 就没了, 也就不能再被引用.
擦一个不存在的 id 什么也不发生(黑板幂等, 不报错).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, parse, obj_id

__all__ = ['BbErase']


class BbEraseInput(BaseModel):
    """擦谁: 对象 id 或 "all"."""

    around: str = Field(description='对象 id, 或 "all" 清空整块板')
    id: str = Field(default='', description='可选的名字(擦除动作本身不进对象表)')


class BbErase(BbCommand):
    """擦: 一个对象 / 整块板."""

    name = 'bb_erase'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': '擦掉一个对象(around = 对象 id), 或者 around="all" 清空整块板',
        'example': (
            'around="a1" 只擦 a1 的笔迹, 旁边的不动; around="all" 是重来.\n'
            '擦完对象表里就没有它了, 再用这个 id 会落到画布中心.'
        ),
        'input_schema': BbEraseInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbEraseInput, kwargs)
        raw = str(inp.around or '').strip()
        if not raw:
            raise RuntimeError('around 必填: 对象 id, 或 "all" 清空整块板')
        target = 'all' if raw.lower() == 'all' else obj_id(raw, 'around')
        if target != 'all' and await self._known(target) is False:
            raise RuntimeError(
                f'黑板上没有对象 {target!r}: 擦不存在的 id 什么也不会发生'
                '(黑板不报错) —— 用 <bb_objects/> 看它现在有哪些')
        ident = obj_id(inp.id) if str(inp.id or '').strip() else ''
        r = await self._draw({'do': 'erase', 'around': target}, ident)
        return {'content': '擦了全部' if target == 'all' else f'擦了 {target}',
                'details': {'erased': target, 'id': r.get('id')}}
