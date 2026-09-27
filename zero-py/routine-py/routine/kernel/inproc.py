"""InProcessTransport ---- 进程内内存直连(hub ↔ kernel 同事件循环).

零序列化零跨进程:消息 dict 原样流转.双向各一条 FIFO 队列 + 单 worker,
与 grpc stream 的"逐条顺序投递、处理方可 spawn"语义对齐:

  - hub → kernel: ``send_event`` 入队,kernel worker 逐条 ``await kernel.dispatch(msg)``
    (dispatch 内对阻塞操作 spawn 独立 task,不堵队列).
  - kernel → hub: ``kernel.send_out(msg)`` 入队,hub worker 逐条
    ``await hub.dispatch_inbound(peer_id, msg)``(hub 侧自身 spawn handler).
  - 查询(``req``)不走队列:直接 ``await kernel.handle_req(msg)`` 同步返回,
    查询零排队、最低延迟.

peer 语义:in-proc 下 hub 的 peer 恒为 ``KERNEL_PEER``,kernel 的 conn 恒为
``HUB_CONN``(单 hub;多 hub 是将来 grpc 版的事).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from ..protocol import (
    REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING,
)
from ..transport import Transport

if TYPE_CHECKING:
    from .manager import Kernel

# hub 视角的 peer(kernel 的稳定身份);kernel 视角的 conn(hub 的稳定身份)
KERNEL_PEER = 'kernel'
HUB_CONN = 'hub'

_log = logging.getLogger('kernel.link')


class InProcessLink:
    """hub 与 kernel 的进程内直连链路(双向 FIFO)."""

    def __init__(self) -> None:
        self._to_kernel: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._to_hub: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._kernel: Optional['Kernel'] = None
        self._hub_inbound = None  # hub.dispatch_inbound
        self._tasks: list[asyncio.Task] = []

    def bind(self, kernel: 'Kernel', hub_inbound) -> None:
        self._kernel = kernel
        self._hub_inbound = hub_inbound

    async def start(self) -> None:
        if self._kernel is None or self._hub_inbound is None:
            raise RuntimeError('link not bound (call bind before start)')
        self._tasks.append(asyncio.create_task(self._kernel_loop()))
        self._tasks.append(asyncio.create_task(self._hub_loop()))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    # --- 出站入队(非阻塞,FIFO 保序)---

    def hub_to_kernel(self, msg: Dict[str, Any]) -> None:
        self._to_kernel.put_nowait(msg)

    def kernel_to_hub(self, msg: Dict[str, Any]) -> None:
        self._to_hub.put_nowait(msg)

    # --- 查询直达(同步 await,不走队列)---

    async def hub_req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        if self._kernel is None:
            raise RuntimeError('link not bound')
        return await self._kernel.handle_req(msg)

    # --- workers ---

    async def _kernel_loop(self) -> None:
        kernel = self._kernel
        while True:
            msg = await self._to_kernel.get()
            try:
                await kernel.dispatch(msg)
            except Exception:
                _log.exception('kernel dispatch failed: %r', msg)

    async def _hub_loop(self) -> None:
        inbound = self._hub_inbound
        while True:
            msg = await self._to_hub.get()
            try:
                await inbound(KERNEL_PEER, msg)
            except Exception:
                _log.exception('hub inbound failed: %r', msg)

    async def _depth_sampler(self) -> None:
        """周期采样双向队列深度,超阈值 warn(定位慢消费者,而非静默增长)."""
        while True:
            await asyncio.sleep(_DEPTH_SAMPLE_S)
            to_kernel = self._to_kernel.qsize()
            to_hub = self._to_hub.qsize()
            if to_kernel > _DEPTH_WARN or to_hub > _DEPTH_WARN:
                _log.warning(
                    'link queue depth high: to_kernel=%d to_hub=%d',
                    to_kernel, to_hub)


class InProcessTransport(Transport):
    """hub 侧持有的 transport 实现:发事件入 kernel 队列,查询直达."""

    def __init__(self, link: InProcessLink):
        super().__init__()
        self._link = link

    async def start(self) -> None:
        pass  # worker 由 link.start 统一起

    async def stop(self) -> None:
        pass

    async def send_event(self, payload: Dict[str, Any],
                         peer_id: Optional[str] = None) -> None:
        self._link.hub_to_kernel(payload)

    async def req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        return await self._link.hub_req(msg)

    async def get_running_routines(self) -> list:
        reply = await self.req({'event': REQ_GET_RUNNING})
        return reply.get('routines', [])

    async def get_module_tree(self):
        from .module_tree import ModuleTree
        reply = await self.req({'event': REQ_GET_MODULE_TREE})
        return ModuleTree.from_dict(reply['tree'])

    async def get_routines(self) -> list:
        reply = await self.req({'event': REQ_GET_ROUTINES})
        return reply.get('routines', [])
