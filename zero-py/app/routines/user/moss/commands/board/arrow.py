"""bb_arrow —— 画一个箭头: 杆 + 两撇, 三笔同时起.

它**不进对象表**, 所以之后引用不到它(要能被指就用 bb_line). 指"那边的
东西"时, 箭头画在对象旁边, 圈/划线的对象仍是文字本身.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, ConfigDict, Field

from .base import BbCommand, anchor, parse, obj_id

__all__ = ['BbArrow']


class BbArrowInput(BaseModel):
    """箭头的两个端点."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias='from', description='起点: "0.12,0.28" 或 "below:a1"')
    to: str = Field(description='终点(箭头所指处): 同上')
    id: str = Field(default='', description='可选的名字(箭头不进对象表, 一般不填)')


class BbArrow(BbCommand):
    """画箭头: 从 from 指向 to."""

    name = 'bb_arrow'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': '画一个箭头: from → to; 不进对象表(引用不到它)',
        'example': (
            'from="below:a1" to="0.62,0.55" —— 从这句下方指向别处.\n'
            '箭头是三笔(杆 + 两撇)同时起, 总时长按杆算.'
        ),
        'input_schema': BbArrowInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbArrowInput, kwargs)
        ident = obj_id(inp.id) if str(inp.id or '').strip() else ''
        act = {'do': 'arrow',
               'from': anchor(inp.from_, 'from'),
               'to': anchor(inp.to, 'to')}
        r = await self._draw(act, ident)
        rid = r.get('id') or ident
        # 箭头不进对象表(黑板自己编号): 没有 id 时 content 留空 → 'ok'
        return {'content': f'id={rid}' if rid else '', 'details': {'id': rid}}
