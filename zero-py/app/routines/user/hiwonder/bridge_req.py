"""bridge_req ---- ros_bridge 桥寻址 req 小工具(rid 解析 + 缓存 + 失效重试).

桥实例按 rid 寻址(不按 name); 桥热重载后 rid 换新, ReqTimeout 视为
缓存失效 ---- 重解析再试一次(仍超时 = 桥真没在跑, 抛给 caller).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from routine import Routine
from routine.errors import ReqTimeout

_rid: Optional[str] = None


async def _resolve(rt: Routine) -> str:
    """查 ros_bridge 桥实例 rid."""
    for r in await rt.get_running_routines():
        if str((r or {}).get('name') or '') == 'ros_bridge':
            rid = str(r.get('id') or '').strip()
            if rid:
                return rid
    raise RuntimeError('ros_bridge not running')


async def bridge_req(rt: Routine, event: str, data: Dict[str, Any],
                     timeout: float = 5.0) -> Any:
    """req 桥: rid 走模块缓存; ReqTimeout = 桥热重载 rid 失效, 重解析再试."""
    global _rid
    if _rid is None:
        _rid = await _resolve(rt)
    try:
        return await rt.req(_rid, event, data, timeout=timeout)
    except ReqTimeout:
        _rid = await _resolve(rt)
        return await rt.req(_rid, event, data, timeout=timeout)
