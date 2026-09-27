"""循环运行时原语: Abort 协作式中断 + Running 相位.

轮次/步骤事件日志由 EventLog 承担 (持久 + 派生).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


class Aborted(Exception):
    """协作式中断: abort 触发后, 循环检查点抛出."""

    def __init__(self, reason: str = ''):
        self.reason = reason or 'aborted'
        super().__init__(self.reason)


class Abort:
    """单次失效的协作取消信号 (对齐 dsh AbortController).

    abort() 只置位不抢占; 循环在检查点 throw_if_aborted() 主动退出.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self.reason: str | None = None

    def abort(self, reason: str = 'cancel') -> None:
        if not self._event.is_set():
            self.reason = reason
            self._event.set()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def throw_if_aborted(self) -> None:
        if self._event.is_set():
            raise Aborted(self.reason or 'aborted')


@dataclass
class Running:
    """驱动器活跃相位: 持取消信号 + turn/step 游标. None = idle."""

    abort: Abort = field(default_factory=Abort)
    turn: int = 0
    step: int = 0
    # 当前轮 message_id(loop._turn 开轮时挂上): _drive 被 task.cancel 直杀时
    # 补发 final 收尾帧要用同 id, 前端才能关对流的 streaming 状态
    message_id: str = ''
