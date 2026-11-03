"""bb_underline —— 在某个对象下面划一条(比直线多一点儿弧度).

和圈一样只认对象 id: 黑板侧拿 bbox 的底边算位置, 所以字换行/换字号它
自己跟得上, 不用模型算坐标.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, parse, obj_id, px_of

__all__ = ['BbUnderline']


class BbUnderlineInput(BaseModel):
    """划谁 + 离多近."""

    around: str = Field(description='对象 id(要划的那一笔)')
    id: str = Field(default='', description='可选的名字(不进对象表)')
    pad: str = Field(default='', description='离对象底边多远(画布像素, 默认 8)')


class BbUnderline(BbCommand):
    """划横线: 跟着对象的底边."""

    name = 'bb_underline'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': '在某个对象下面划一条: around = 对象 id',
        'example': 'around="a1" —— 比圈更轻的强调; 和圈可以叠在同一句上.',
        'input_schema': BbUnderlineInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbUnderlineInput, kwargs)
        around = obj_id(inp.around, 'around')
        ident = obj_id(inp.id) if str(inp.id or '').strip() else ''
        if await self._known(around) is False:
            raise RuntimeError(
                f'黑板上没有对象 {around!r}: 引用不存在的 id, 线会落在'
                '画布中心附近(黑板不报错)')
        r = await self._draw({'do': 'underline', 'around': around,
                              'pad': px_of(inp.pad, 'pad', 8)}, ident)
        rid = r.get('id') or ''
        return {'content': f'划线 {around}' + (f' (id={rid})' if rid else ''),
                'details': {'id': rid, 'around': around}}
