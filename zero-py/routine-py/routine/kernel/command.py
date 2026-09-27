"""Command ---- kernel 的执行单元状态机(移植 Go kernel/command,asyncio 化).

状态流转: created → starting(Start)→ running(MarkRunning)→ stopped.
Stop 取消 body task 并等其退出(模块由 body 退出时释放).
"""
from __future__ import annotations

import asyncio
import itertools
from enum import Enum
from typing import Any, Coroutine, Dict, List, Optional

_ids = itertools.count(1)


class State(Enum):
    CREATED = 'created'
    STARTING = 'starting'
    RUNNING = 'running'
    STOPPING = 'stopping'
    STOPPED = 'stopped'


class Command:
    """routine 命令体.body 是 async fn(驱动远端 lifecycle 的 run_remote)."""

    def __init__(self, name: str,
                 body: Optional[Coroutine[Any, Any, None]] = None):
        self.name = name
        self.id = next(_ids)
        self.parent_id: int = 0
        self.kwargs: Dict[str, Any] = {}
        self.modules: List[str] = []
        self._state = State.CREATED
        self._body = body
        self._task: Optional[asyncio.Task] = None

    def __repr__(self) -> str:
        return f'{self.name}-{self.id}'

    @property
    def state(self) -> State:
        return self._state

    def start(self) -> None:
        """启动 body:进入 starting.Created 态外调用无效."""
        if self._state != State.CREATED:
            return
        if self._body is None:
            raise RuntimeError(f'command {self!r} has no body attached')
        self._state = State.STARTING

        async def _run() -> None:
            try:
                await self._body
            finally:
                self._state = State.STOPPED

        self._task = asyncio.create_task(_run(), name=f'cmd:{self}')

    def mark_running(self) -> None:
        """lifecycle.started 回报后调:starting → running."""
        if self._state == State.STARTING:
            self._state = State.RUNNING

    def attach_body(self, body: Coroutine[Any, Any, None]) -> None:
        """start 前注入 body(kernel 在 _start 处闭包 node 后调用)."""
        if self._task is not None:
            raise RuntimeError(f'command {self!r} already started')
        self._body = body

    def set_modules(self, modules: List[str]) -> None:
        self.modules = list(modules)

    def add_modules(self, modules: List[str]) -> None:
        for m in modules:
            if m not in self.modules:
                self.modules.append(m)

    def remove_modules(self, modules: List[str]) -> None:
        self.modules = [m for m in self.modules if m not in set(modules)]

    async def stop(self) -> None:
        """打断 body:cancel + 等 task 退出.idempotent."""
        if self._state not in (State.STARTING, State.RUNNING, State.STOPPING):
            return
        self._state = State.STOPPING
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._state = State.STOPPED

    async def wait(self) -> None:
        """等 body 退出(自然完成或被打断)."""
        if self._task is not None and not self._task.done():
            try:
                await self._task
            except asyncio.CancelledError:
                pass
