"""Inbox 双队列: next-turn FIFO (followup) + next-step (steer / inject).

对齐 dsh Inbox 语义:
- followup → next-turn, 唤醒: 追加一轮新对话, 当前轮完成后处理
- steer    → next-step, 唤醒: 中途引导, 下一个步骤边界生效
- inject   → next-step, 不唤醒: 注入上下文, 躺队列等下次唤醒

领取 (claim) 是删除式 splice: 轮次边界领 next-step 全部 + next-turn
一条; 步骤之间只领 next-step. 领取后被拒绝 (pre_step reject) 的批次
保持已删除 ---- 空轮次留痕.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

InboxTarget = Literal['next-turn', 'next-step']


@dataclass
class InboxMessage:
    text: str
    source: str = 'user'          # 'user' 或 from_agent id
    id: str = field(default_factory=lambda: uuid4().hex)


class Inbox:
    """pending 输入双队列. 单线程事件循环内使用, 无锁."""

    def __init__(self) -> None:
        self._next_turn: list[InboxMessage] = []
        self._next_step: list[InboxMessage] = []

    async def send(self, msg: InboxMessage, target: InboxTarget) -> None:
        q = self._next_turn if target == 'next-turn' else self._next_step
        q.append(msg)

    async def claim(self, target: InboxTarget) -> list[InboxMessage]:
        """删除式领取.

        next-turn: 一条 next-turn (若有) + next-step 全部 ---- 轮次边界.
        next-step: 仅 next-step 全部 ---- 步骤之间.
        """
        if target == 'next-turn':
            claimed = self._next_step
            self._next_step = []
            if self._next_turn:
                claimed = [self._next_turn.pop(0), *claimed]
            return claimed
        claimed = self._next_step
        self._next_step = []
        return claimed

    async def clear(self) -> None:
        self._next_turn.clear()
        self._next_step.clear()

    @property
    def has_pending(self) -> bool:
        return bool(self._next_turn or self._next_step)

    @property
    def next_step_pending(self) -> bool:
        return bool(self._next_step)
