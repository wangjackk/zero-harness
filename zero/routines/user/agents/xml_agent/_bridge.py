"""共享: 向 WS bridge 注册自己 (agent_id/namespace/name), 轮询直到成功.

prime/reactor 共用实现, 唯一差异是 name 前缀
和注册成功后的回调 (emit_session_history vs emit_session_changed).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from routine.errors import ReqError, ReqTimeout


async def register_with_bridge(
    *,
    agent_id: str,
    routine_id: str,
    name_prefix: str,
    bridge_name: str,
    ctx: Any,
    stop_event: asyncio.Event,
    logger: Any,
    on_success: Callable[[], Awaitable[None]] | None = None,
    retry_interval: float = 0.2,
) -> None:
    """向 WS bridge req 注册自己, 轮询直到成功或 stop_event 被 set.

    参数:
        agent_id: agent 持久 id (同时作为 namespace)
        routine_id: 当前 routine 实例 id (self.id)
        name_prefix: 显示名前缀 (如 'Reactor-'/'Prime-'/'Xml-')
        bridge_name: bridge routine 名 (_BRIDGE_NAME)
        ctx: routine ctx (提供 get_running_routines / req)
        stop_event: agent 停止事件, set 后立即退出避免孤儿 task
        logger: agent logger
        on_success: 注册成功后的异步回调 (emit session 事件等), 可空
        retry_interval: 查不到 bridge 或 req 失败时的重试间隔
    """
    payload = {
        'agent_id': agent_id,
        'namespace': agent_id,
        'name': f'{name_prefix}{agent_id[:8]}',
        'routine_id': routine_id,
    }
    try:
        while not stop_event.is_set():
            try:
                routines = await ctx.get_running_routines()
            except Exception as exc:
                logger.warning(f'get_running failed ({exc!r}), retry')
                routines = []
            bridge_id = None
            for r in routines:
                if str(r.get('name') or '') == bridge_name:
                    bridge_id = str(r.get('id') or '') or None
                    break
            if bridge_id is not None:
                try:
                    await ctx.req(bridge_id, 'register_agent', payload, timeout=2.0)
                    logger.info(f'registered with bridge (bridge_id={bridge_id})')
                    if on_success is not None:
                        await on_success()
                    return
                except (ReqTimeout, ReqError) as exc:
                    logger.warning(f'register req failed ({exc!r}), retry')
            await asyncio.sleep(retry_interval)
    except asyncio.CancelledError:
        return
