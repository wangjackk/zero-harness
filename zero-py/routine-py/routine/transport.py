"""Transport ---- 传输层抽象:入站投递 + 出站发送 + 同步查询,传输无关.

routine 包提供 InProcessTransport(进程内)(进程内内存直连);接口契约与老版 routine-py
对齐,将来加 grpc / ws 实现时上层(RoutineHub / RunContext / Kernel)零改动:

  - ``send_event(payload, peer_id)``:出站事件(fire-and-forget,FIFO 保序).
  - ``req(msg)``:同步查询(dict 进 dict 出).
  - ``get_running_routines / get_module_tree / get_routines``:查询的语义化封装.

入站:transport 收到消息时调 inbound 回调 ``(peer_id, msg)``;peer 断开调
peer_down(in-proc 下永不触发,保留接口).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

# 入站回调: (peer_id, msg) -> awaitable
InboundHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
# peer 断开回调: peer_id -> awaitable
PeerDownHandler = Callable[[str], Awaitable[None]]


class Transport:
    """传输层接口.子类实现 _send / req;inbound/peer_down 由 hub 经 attach 注入."""

    def __init__(self) -> None:
        self._inbound: Optional[InboundHandler] = None
        self._peer_down: Optional[PeerDownHandler] = None

    # --- 注入回调(hub.attach 调)---

    def set_inbound(self, handler: InboundHandler) -> None:
        self._inbound = handler

    def set_peer_down(self, handler: PeerDownHandler) -> None:
        self._peer_down = handler

    # --- 生命周期 ---

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    # --- 出站 ---

    async def send_event(self, payload: Dict[str, Any],
                         peer_id: Optional[str] = None) -> None:
        """发事件 dict 给 peer(peer_id in-proc 下忽略,单 kernel).fire-and-forget."""
        raise NotImplementedError

    # --- 同步查询 ---

    async def req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """routine→kernel 同步查询(dict 进 dict 出)."""
        raise NotImplementedError

    async def get_running_routines(self) -> list:
        raise NotImplementedError

    async def get_module_tree(self) -> Optional[Any]:
        raise NotImplementedError

    async def get_routines(self) -> list:
        raise NotImplementedError
