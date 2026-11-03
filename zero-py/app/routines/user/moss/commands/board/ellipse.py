"""bb_ellipse —— 画一个标准椭圆: 圆心 + 两轴半径.

``rx`` / ``ry`` 都以**画布短边**为单位; 省略 ``ry`` 就是正圆(与 ``bb_circle``
等价, 但那条更直白 —— 画圆用 circle).

和 ``bb_ring`` 的区别同 ``bb_circle``: 这个画的是几何图形, ring 是批注强调。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, anchor, len_of, parse, obj_id

__all__ = ['BbEllipse']


class BbEllipseInput(BaseModel):
    """圆心 + 两轴半径."""

    id: str = Field(description='对象 id(进对象表, 之后 ring / underline / erase 靠它引用)')
    at: str = Field(description='圆心: "0.5,0.4"(归一化) 或 "above:a1"(贴边)')
    rx: str = Field(description='横向半径, 以画布短边为单位')
    ry: str = Field(default='', description='纵向半径, 同上; 不填 = 与 rx 相同')


class BbEllipse(BbCommand):
    """画椭圆: 圆心 + 两轴半径, 闭合、等宽."""

    name = 'bb_ellipse'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': (
            '画一个标准椭圆: at = 圆心, rx / ry = 两轴半径(以画布短边为单位); '
            '不填 ry 就是正圆'),
        'example': (
            'at="0.5,0.4" rx="0.18" ry="0.11" —— 横着的椭圆.\n'
            'at="below:a1" rx="0.12" —— 挂在 a1 下面一个正圆(ry 不填).\n'
            '画圆优先用 bb_circle, 这里只在两轴不等时用.'),
        'input_schema': BbEllipseInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbEllipseInput, kwargs)
        ident = obj_id(inp.id)
        rx = len_of(inp.rx, 'rx')
        act = {'do': 'ellipse', 'at': anchor(inp.at, 'at'),
               'rx': rx, 'ry': len_of(inp.ry, 'ry', default=rx)}
        r = await self._draw(act, ident)
        rid = r.get('id') or ident
        return {'content': f'id={rid}', 'details': {'id': rid}}
