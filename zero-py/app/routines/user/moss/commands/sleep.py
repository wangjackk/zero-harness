"""Sleep —— 睡一会儿: 原版标准原语 ``sleep(duration, chan="")``.

**它不是控制流, 是占道动作**(``docs/orchestration.md``): 同一条 lane 上
后面的指令在队列里自然等它 —— 原版 ``chan`` 参数想表达的"在哪条轨上
阻塞", 在扁平轨模型里由标签第一槽直接给出(``<arm:sleep duration="1"/>``
锚 arm 轨), 所以这里只有 ``duration``.

回执只报睡了几秒, 不带注意债: 睡完不需要被"看", 下一拍自然到来
(原版 sleep 完成也不 observe).
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine.routine import Routine

__all__ = ['Sleep']


class SleepInput(BaseModel):
    """睡眠参数."""

    duration: float = Field(default=0.0, description='睡多少秒')


class Sleep(Routine):
    """睡 duration 秒, 阻塞本轨后续指令(跨轨不受影响)."""

    name = 'sleep'

    meta: ClassVar[Dict[str, Any]] = {
        'description': ('睡一会儿: 阻塞本轨后续的 routine duration 秒'
                        '(跨轨并行不受影响)'),
        'input_schema': SleepInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = SleepInput.model_validate(kwargs or {})
        self._seconds: float = max(0.0, float(inp.duration))

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if self._seconds > 0:
            await asyncio.sleep(self._seconds)
        return {'content': f'等了 {self._seconds:.2f}s',
                'details': {'slept': f'{self._seconds:.2f}s'}}

    async def stop(self) -> None:
        # 被打断: asyncio.sleep 随任务取消而结束, 无需额外汇合点
        pass
