"""bb_circle —— 画一个标准圆(几何图形): 圆心 + 半径.

它和 ``bb_ring`` 是**两件事**, 别混:

    bb_circle  几何图形   闭合的真圆, 抖动 < 1px   圆心 at + 半径 r
    bb_ring    批注强调   手画的圈, 留 6~10% 缺口  圈住某个对象 + pad

要在黑板上画个圆(讲"这就是圆形"), 用这个; 要把某句话圈起来强调, 用 ring.

``r`` 以**画布短边**为单位: ``0.1`` = 短边的十分之一。不按轴归一化 —— 按轴
折的话宽屏上画出来是横着压扁的椭圆。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, anchor, len_of, parse, obj_id

__all__ = ['BbCircle']


class BbCircleInput(BaseModel):
    """圆心 + 半径."""

    id: str = Field(description='对象 id(进对象表, 之后 ring / underline / erase 靠它引用)')
    at: str = Field(description='圆心: "0.5,0.4"(归一化) 或 "below:a1"(贴边)')
    r: str = Field(description='半径, 以画布短边为单位(如 0.08)')


class BbCircle(BbCommand):
    """画标准圆: 闭合、等宽, 像素上宽高相等."""

    name = 'bb_circle'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': (
            '画一个标准圆(几何图形): at = 圆心, r = 半径(以画布短边为单位). '
            '要圈住某句话做强调请用 bb_ring'),
        'example': (
            'at="0.5,0.4" r="0.12" —— 画布正中偏上的一个圆.\n'
            'at="below:a1" r="0.09" —— 挂在 a1 这句下面: 圆的外边缘离字底一个 '
            'GAP, 水平居中, 不压字.\n'
            'r 是**短边**比例: 0.1 就是短边的十分之一, 宽屏上也不会压成椭圆.'),
        'input_schema': BbCircleInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbCircleInput, kwargs)
        ident = obj_id(inp.id)
        r = await self._draw({'do': 'circle',
                              'at': anchor(inp.at, 'at'),
                              'r': len_of(inp.r, 'r')}, ident)
        rid = r.get('id') or ident
        return {'content': f'id={rid}', 'details': {'id': rid}}
