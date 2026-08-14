"""hub_routine: IPython 里一键启动 routine hub,拿到 passive routine 实例.

用法::

    import asyncio
    from hub_routine import connect_hub, disconnect_hub

    # 一键连接:起 hub + passive routine,拿到实例
    r = await connect_hub()

    # r 是 passive routine 实例,直接用其原生方法:
    res = await r.call("echo", {"message": "hi"})
    await r.subscribe("assistant_output", handler, namespace="prime_6")
    await r.publish("my_topic", {"key": "val"}, namespace="prime_6")

    # 结束
    await disconnect_hub()

也可注册自定义 routine::

    from routine import Routine

    class MyR(Routine):
        name = "my_routine"
        async def run(self, kwargs):
            return "hello"

    r = await connect_hub(routines=[MyR])
"""
from __future__ import annotations

import asyncio
import os
from typing import List, Optional, Type

from routine import (
    Routine,
    Routines,
    RoutineHub,
    GrpcClientTransport,
)

# 模块级单例
_hub: Optional[RoutineHub] = None
_transport: Optional[GrpcClientTransport] = None
_instance: Optional[Routine] = None


class _Passive(Routine):
    """内置 passive routine:启动后常驻,提供 call/subscribe/publish 能力."""
    name = "hub_routine_passive"
    is_passive = True

    async def run(self, kwargs: dict):
        self._stop_evt = asyncio.Event()
        yield "hub_routine ready"
        await self._stop_evt.wait()

    async def stop(self):
        self._stop_evt.set()


def _resolve_addr(kernel_addr: Optional[str]) -> str:
    return kernel_addr or os.environ.get("ZERO_KERNEL_ADDR", "127.0.0.1:8888")


async def start_hub(
    *,
    routines: Optional[List[Type[Routine]]] = None,
    hub_id: str = "hub_routine",
    kernel_addr: Optional[str] = None,
    wait: float = 2.0,
) -> Routine:
    """启动 hub + passive routine,返回 passive 实例.

    Args:
        routines: 额外注册的 routine 类列表.
        hub_id: hub 标识符.
        kernel_addr: kernel gRPC 地址,默认读 ZERO_KERNEL_ADDR 或 127.0.0.1:8888.
        wait: 等 kernel auto-start passive 的秒数.

    Returns:
        passive routine 实例,可直接 call/subscribe/publish.
    """
    global _hub, _transport, _instance
    if _hub is not None:
        raise RuntimeError("hub already started, call stop_hub() first")

    addr = _resolve_addr(kernel_addr)

    rs = Routines()
    rs.register(_Passive)
    if routines:
        for r in routines:
            rs.register(r)

    _transport = GrpcClientTransport(addr)
    _hub = RoutineHub(rs, transport=_transport, hub_id=hub_id)
    _transport.attach(_hub)
    await _transport.start()

    # 等 kernel auto-start passive routine
    for _ in range(int(wait * 10)):
        await asyncio.sleep(0.1)
        if _hub.runtime.running_instances:
            break

    if not _hub.runtime.running_instances:
        raise RuntimeError(
            f"passive routine not started after {wait}s "
            "(kernel not connected or auto-start failed)"
        )

    _instance = list(_hub.runtime.running_instances.values())[0]
    return _instance


async def stop_hub() -> None:
    """停止 hub."""
    global _hub, _transport, _instance
    if _instance and _instance._started:
        try:
            await _instance.stop()
        except Exception:
            pass
    if _transport:
        await _transport.stop()
    _hub = None
    _transport = None
    _instance = None


__all__ = ["start_hub", "stop_hub"]
