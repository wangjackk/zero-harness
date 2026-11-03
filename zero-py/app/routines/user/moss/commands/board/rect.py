"""bb_rect —— 画一个矩形: 中心 + 宽高, 一条笔迹绕一圈.

``at`` 是矩形**中心**(不是左上角): 黑板侧按"贴边"语义解析锚点, 再折回左上角,
所以 ``at="below:a1"`` 得到的是"整个矩形落在 a1 下面", 不会压字.

``w`` / ``h`` 以**画布短边**为单位, 两者相等就是正方形 —— 这不是风格问题:
按轴归一化的话 ``w=0.3 h=0.3`` 在宽屏上就是个长方形.

它是**一整条连续笔迹**, 角上自带小圆角(``round``, 画布像素, 默认 7) ——
所以不会像四条 line 那样在角上断开。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, anchor, len_of, parse, obj_id, px_of

__all__ = ['BbRect']


class BbRectInput(BaseModel):
    """中心 + 宽高 + 圆角."""

    id: str = Field(description='对象 id(进对象表, 之后 ring / underline / erase 靠它引用)')
    at: str = Field(description='矩形**中心**: "0.35,0.33"(归一化) 或 "below:a1"')
    w: str = Field(description='宽, 以画布短边为单位(如 0.3)')
    h: str = Field(description='高, 同上; 与 w 相等就是正方形')
    round: str = Field(default='', description='角上圆角(画布像素, 默认 7)')


class BbRect(BbCommand):
    """画矩形: 一条笔迹绕一圈, 角上走圆角."""

    name = 'bb_rect'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': (
            '画一个矩形: at = 中心, w / h = 宽高(以画布短边为单位), '
            'round = 角上圆角(画布像素, 默认 7); w=h 就是正方形'),
        'example': (
            'at="0.35,0.33" w="0.3" h="0.3" —— 一个正方形, 边长是短边的 30%.\n'
            'at="below:a1" w="0.4" h="0.18" —— 整个框落在 a1 下面, 不压字.\n'
            '一整条笔迹绕一圈(不是四条 line 拼的), 角上自带圆角, 所以不会断.'),
        'input_schema': BbRectInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbRectInput, kwargs)
        ident = obj_id(inp.id)
        act = {'do': 'rect', 'at': anchor(inp.at, 'at'),
               'w': len_of(inp.w, 'w'), 'h': len_of(inp.h, 'h'),
               'round': px_of(inp.round, 'round', 7, hi=60)}
        r = await self._draw(act, ident)
        rid = r.get('id') or ident
        return {'content': f'id={rid}', 'details': {'id': rid}}
