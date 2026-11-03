"""chan_status —— 看轨: 每条轨现在堵不堵(只读, 不动队列).

叫 ``status`` 而不是 ``list``: 它交付的不是"有哪些轨"(那点信息每条记录
里的 ``chan`` 字段就带出来了), 而是**运行时状态** —— 排着几个、正在跑
什么、有没有抢跑的在跑、有没有容器还在收 body. 「list」暗示资源枚举(给 id
供后续引用), 而这里的消费者要的是"我还敢往 b 投吗".

名字也就此三层对齐: 动作 ``chan_status`` → 控制面 ``op='status'`` →
channel 的 ``@request('status')``, 一路同名, 没有翻译层.

不给 ``name`` = 全部轨; 给 = 只看那一条(同一张表的两种粒度, 不是两个动作).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import ChanCommand

__all__ = ['ChanStatus']


def _lane_line(st: Dict[str, Any]) -> str:
    """一条轨 → 模型读的那一小段: ``main: 排队 2, 在跑 say``.

    只写"敢不敢往这条轨投"要看的东西; 五个原始计数留在 details 里.
    全空 = 空轨 —— 这一句就是查询的全部答案.
    """
    name = st.get('chan')
    if st.get('error'):
        return f'{name}: 问不到({st["error"]})'
    bits = []
    run = st.get('running')
    if run:
        bits.append(f'在跑 {run.get("verb")}')
    if st.get('queued'):
        bits.append(f'排队 {st["queued"]}')
    if st.get('soon'):
        bits.append(f'抢占 {st["soon"]}')
    if st.get('deferred'):
        bits.append(f'收 body {st["deferred"]}')
    return f'{name}: ' + (', '.join(bits) if bits else '空')


class ChanStatusInput(BaseModel):
    """看轨参数(可空)."""

    name: str = Field(default='', description='只看这一条轨; 省略 = 全部轨')


class ChanStatus(ChanCommand):
    """看轨: req shell chan(status) 拿每轨状态."""

    name = 'chan_status'
    op = 'status'

    meta: ClassVar[Dict[str, Any]] = {
        **ChanCommand.meta,
        'description': ('看轨: 每条轨排着几个 routine(queued)、正在跑什么'
                        '(running)、有没有抢跑的在跑(soon)、有没有容器还在'
                        '收 body(deferred); 带 name 只看一条'),
        # 备注(跟着能力走): 只在 chan_status 进词表时出现.
        'example': (
            '不带 name = 全部轨, 带 name = 只看那一条.\n'
            '回执一句话一条轨: `main: 空` / `b1: 排队 2, 在跑 say` ——\n'
            '排队>0 = 投上去要排队; 写着"空" = 立刻就能投.'
        ),
        'input_schema': ChanStatusInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = ChanStatusInput.model_validate(kwargs)
        r = await self._chan({'name': inp.name})
        return {'content': '; '.join(_lane_line(st)
                                     for st in r.get('lanes') or []),
                'details': r}
