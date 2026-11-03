"""bb_line —— 画一条直线; 它是"进对象表"的两种笔迹之一(另一种是 write).

进了对象表, 就能被 ``ring`` / ``underline`` / ``erase`` 引用 —— 想让箭头
以后还能被指, 就用它代替 ``bb_arrow``.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, ConfigDict, Field

from .base import BbCommand, anchor, flag_of, parse, obj_id

__all__ = ['BbLine']


class BbLineInput(BaseModel):
    """直线的两个端点 + 要不要虚线."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description='对象 id(之后 ring / underline / erase 靠它引用)')
    from_: str = Field(alias='from', description='起点: "0.12,0.28" 或 "right-of:a1"')
    to: str = Field(description='终点: 同上')
    dash: str = Field(default='', description='虚线: 写 "true"')


class BbLine(BbCommand):
    """画直线: 两端点给定, 线宽与抖动由黑板侧决定."""

    name = 'bb_line'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': '画一条直线: from / to 两个锚点; 会进对象表, 可被引用',
        'example': (
            'from="below:a1" to="0.62,0.55" —— 挂靠写法比写死坐标稳, '
            '换字号也不会画歪.'
        ),
        'input_schema': BbLineInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbLineInput, kwargs)
        ident = obj_id(inp.id)
        act = {'do': 'line',
               'from': anchor(inp.from_, 'from'),
               'to': anchor(inp.to, 'to'),
               'dash': flag_of(inp.dash, 'dash')}
        r = await self._draw(act, ident)
        rid = r.get('id') or ident
        return {'content': f'id={rid}', 'details': {'id': rid}}
