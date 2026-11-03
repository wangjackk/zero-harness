"""bb_poly —— 折线 / 多边形: 一串点, 一整条笔迹.

**画形状不要用 line 拼**: 每条 line 是独立的一笔, 各自两端收细、各自带手抖,
两笔在角上对不齐, 看着就是断的。poly 是一整条连续笔迹, 角上自带走圆角
(``round``, 画布像素, 默认 7), 笔迹本身没有接缝。

``points`` 是**空白分隔的一串锚点**(属性装不下数组, 这是唯一为属性扩的语法):

    points="0.5,0.16 0.32,0.52 0.68,0.52" close="true"

里面每一段仍是同一个锚点写法, 所以也可以混用挂靠:
``points="0.1,0.2 below:a1 right-of:a1"``.

``close="true"`` 会绕回起点并略微过冲一小截(人画框就是这么收笔的), 所以角上
是实的, 不会留缺口。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, anchors_of, flag_of, parse, obj_id, px_of

__all__ = ['BbPoly']


class BbPolyInput(BaseModel):
    """一串点 + 要不要闭合."""

    id: str = Field(description='对象 id(进对象表, 之后 ring / underline / erase 靠它引用)')
    points: str = Field(
        description='至少两个锚点, 空白分隔: "0.5,0.16 0.32,0.52 0.68,0.52"')
    close: str = Field(default='', description='首尾相连(多边形): 写 "true"')
    round: str = Field(default='', description='角上圆角(画布像素, 默认 7)')


class BbPoly(BbCommand):
    """画折线/多边形: 一条笔迹走完所有点."""

    name = 'bb_poly'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': (
            '画折线或多边形: points = 空白分隔的锚点(至少两个), '
            'close="true" 首尾相连. 一整条笔迹, 角上自带圆角 —— '
            '拼形状别用多条 bb_line'),
        'example': (
            '三角形: points="0.5,0.16 0.32,0.52 0.68,0.52" close="true"\n'
            '折线: points="0.1,0.6 0.3,0.4 0.5,0.62 0.7,0.38"(不写 close)\n'
            '点也可以是挂靠: points="0.1,0.2 below:a1 right-of:a1"\n'
            '用 line 拼形状, 角上一定断; 要形状就用这个.'),
        'input_schema': BbPolyInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbPolyInput, kwargs)
        ident = obj_id(inp.id)
        act = {'do': 'poly',
               'points': anchors_of(inp.points, 'points'),
               'close': flag_of(inp.close, 'close'),
               'round': px_of(inp.round, 'round', 7, hi=60)}
        r = await self._draw(act, ident)
        rid = r.get('id') or ident
        return {'content': f'id={rid}', 'details': {'id': rid}}
