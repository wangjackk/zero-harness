"""bb_point —— 点一下: 一个 5.5px 的小圈, 用来"指这儿".

说话时指位置用: 比写一行字轻, 也比箭头短(箭头有方向, 点没有).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, anchor, parse, obj_id

__all__ = ['BbPoint']


class BbPointInput(BaseModel):
    """点哪里."""

    at: str = Field(description='坐标 "0.12,0.28" 或 "below:a1"')
    id: str = Field(default='', description='可选的名字(不进对象表)')


class BbPoint(BbCommand):
    """点一个位置: 一个小圈."""

    name = 'bb_point'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': '在某个位置点一下(at = 锚点): 一个小圈, 用来指地方',
        'example': 'at="0.42,0.61" —— 指屏幕中间偏右那块.',
        'input_schema': BbPointInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbPointInput, kwargs)
        ident = obj_id(inp.id) if str(inp.id or '').strip() else ''
        r = await self._draw({'do': 'point', 'at': anchor(inp.at, 'at')}, ident)
        rid = r.get('id') or ident
        return {'content': f'id={rid}' if rid else '', 'details': {'id': rid}}
