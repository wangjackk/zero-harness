"""bb_ring —— 把某句话圈起来强调(手绘的圈, 不是几何圆).

``around`` 只认**对象 id**(write / line / rect / poly / circle / ellipse 的
id), 不接受坐标: 黑板侧拿它量出来的 bbox 来算圈的形状, 所以字换了字号、换了
长度, 它自己跟得上.

它**故意不圆**: 留 6~10% 的缺口、带手抖, 起点略偏 —— 人强调一句话时是本子上
的一圈, 不是圆规画的。要画圆请用 ``bb_circle``。

引用不存在的 id 时黑板会静默圈在画布正中心, 所以这里先查一遍快照, 查不到就
报错, 别让模型对着一张"圈在中间"的图猜。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import BbCommand, parse, obj_id, px_of

__all__ = ['BbRing']


class BbRingInput(BaseModel):
    """圈谁 + 留多宽."""

    around: str = Field(description='对象 id(不是坐标): 要圈住的那一笔')
    id: str = Field(default='', description='可选的名字(圈不进对象表, 一般不填)')
    pad: str = Field(default='', description='离对象外缘多远(画布像素, 默认 14)')


class BbRing(BbCommand):
    """圈住一个对象: 手画的圈, 留缺口, 贴字迹外扩."""

    name = 'bb_ring'

    meta: ClassVar[Dict[str, Any]] = {
        **BbCommand.meta,
        'description': (
            '把黑板上某个对象圈起来强调: around = 对象 id, pad = 离它多远'
            '(画布像素). 画的是手绘的圈, 不是圆 —— 要画圆用 bb_circle'),
        'example': (
            'around="a1" pad="18" —— 把 a1 那句圈起来, 比默认松一点.\n'
            '想知道 a1 现在在哪、有多宽, 先 <bb_objects/> 看快照.\n'
            '引用不到 id 时黑板会静默圈在画布中心, 所以 id 得确认过.'),
        'input_schema': BbRingInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = parse(BbRingInput, kwargs)
        around = obj_id(inp.around, 'around')
        ident = obj_id(inp.id) if str(inp.id or '').strip() else ''
        if await self._known(around) is False:
            raise RuntimeError(
                f'黑板上没有对象 {around!r}: 引用不存在的 id, 圈会落在'
                '画布正中心(黑板不报错) —— 先写再圈')
        r = await self._draw({'do': 'ring', 'around': around,
                              'pad': px_of(inp.pad, 'pad', 14)}, ident)
        rid = r.get('id') or ''
        return {'content': f'圈住 {around}' + (f' (id={rid})' if rid else ''),
                'details': {'id': rid, 'around': around}}
