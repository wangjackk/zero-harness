"""agent rid 解析 helper -- 问 agent_hub 一跳, 不骑 routine 生命周期.

背景: send_message / fetch_agent_state 等需要按 agent_id 查目标 agent 的 rid.
各类型 manager 启动时向 agent_hub 注册 manifest (register 顺带记录 manager rid),
hub 的 live_agents req 聚合全部已注册 manager + passive 常驻 agent ---- 这里
只做 "找 hub rid + req + 按 agent_id 匹配", 不维护任何 manager 名单
(新 manager 注册即被发现, 跨语言 manager 经 kernel 路由同样成立).

用法 (Routine 子类内):

    from app.routines.user.agents._core.rid import resolve_agent_rid

    rid, agent_type = await resolve_agent_rid(self, 'prime_1')
    if rid is None:
        ...  # not live / not found
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from routine import Routine

_HUB_NAME = 'agent_hub'


async def _find_hub_rid(routine: "Routine") -> Optional[str]:
    """按 name 查 agent_hub 的 rid (单次, 不重试 ---- 竞态重试在调用方)."""
    for r in await routine.get_running_routines():
        if str(r.get('name') or '') == _HUB_NAME:
            rid = str(r.get('id') or '').strip()
            if rid:
                return rid
    return None


async def resolve_agent_rid(routine: "Routine", agent_id: str,
                            ) -> Tuple[Optional[str], Optional[str]]:
    """按 agent_id 解析 (rid, agent_type). 未找到返回 (None, None).

    一跳: req(agent_hub, 'live_agents') 后本地按 agent_id 匹配.
    """
    for item in await resolve_all_agents(routine):
        if item.get('agent_id') == agent_id and item.get('routine_id'):
            return (str(item['routine_id']), item.get('agent_type'))
    return (None, None)


async def resolve_all_agents(routine: "Routine") -> list:
    """列出全部 live agent [{agent_id, routine_id, agent_type}]
    (agent_hub/running_agents 内核; 聚合实现见 agent_hub)."""
    hub_rid = await _find_hub_rid(routine)
    if not hub_rid:
        return []
    try:
        resp = await routine.req(hub_rid, 'live_agents', {}, timeout=10.0)
    except Exception:
        return []
    return list((resp or {}).get('agents') or [])
