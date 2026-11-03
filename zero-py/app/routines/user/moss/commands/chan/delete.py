"""chan_delete —— 回收一条空轨(开轨的逆操作).

为什么需要它: 轨表**只增不减**就是漏 —— 每条轨是一个常驻 channel 实例,
开十条丢九条(各带一条队列与一个消费者)没有任何回收路径, 只能等 moss 停.

只删**空轨**: 队列里还有活时删轨会卡在 ``channel.stop()`` 的 ``join`` 上
等它们跑完, 那不是回收而是挂住调用方. 所以"还有活"当场拒绝, 并给出
下一步该做什么(``<chan_clear name="b"/>`` 清掉再说). ``main`` 不许删(装配
预建的默认轨, 删了模型就没有可说话的地方了).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import ChanCommand

__all__ = ['ChanDelete']


class ChanDeleteInput(BaseModel):
    """删轨参数."""

    name: str = Field(description='要回收的轨道名(必须是空轨, 且不能是 main)')


class ChanDelete(ChanCommand):
    """回收空轨: req shell chan(delete)."""

    name = 'chan_delete'
    op = 'delete'

    meta: ClassVar[Dict[str, Any]] = {
        **ChanCommand.meta,
        'description': ('回收一条空轨(开轨的逆操作): 该轨没有排队或在跑的 '
                        'routine 才允许删; 还有活就先 chan_clear 再删. '
                        'main 不许删'),
        # 备注(跟着能力走): 只在 chan_delete 进词表时出现.
        'example': (
            '轨上还有活时回执是错误(带 queued/running 数字) —— 先 chan_clear\n'
            '再删. 删掉后往 b 投递会报 unknown lane(要么重开, 要么改用 main).'
        ),
        'input_schema': ChanDeleteInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = ChanDeleteInput.model_validate(kwargs)
        r = await self._chan({'name': inp.name})
        lanes = ', '.join(r.get('lanes') or [])
        return {'content': f'删了 {inp.name}; 还剩 {lanes or "无"}',
                'details': r}
