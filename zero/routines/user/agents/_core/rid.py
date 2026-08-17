"""agent rid 解析 helper -- 直接两跳快查询, 不骑 routine 生命周期.

背景: send_message / fetch_agent_state 等需要按 agent_id 查目标 agent 的 rid.
原来走 get_agent_rid -> list_running_agents 两次 call, 每次都是完整 routine
生命周期 (submit/created/submitted/start/started/run/stopped, ~16 wire 往返),
lookup 实测 ~32ms, 而实际需要的只有两跳快查询:

  1. get_running_routines() -- transport/kernel 直接查询, 不起 routine
  2. req(manager_rid, 'list_agents') -- p2p 一跳

用法 (Routine 子类内):

    from zero.routines.user.agents._core.rid import resolve_agent_rid

    rid, agent_type = await resolve_agent_rid(self, 'prime_1')
    if rid is None:
        ...  # not live / not found
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from routine import Routine

# (manager routine name, agent_type) -- 与 list_running_agents.py 保持一致.
_MANAGERS = [
    ('prime_agent_manager', 'prime'),
    ('xml_agents', 'xml'),
    ('flow_manager', 'flow'),
]

# passive 常驻 agent: routine name 即 agent_id 即 agent_type.
_PASSIVE_AGENTS = [
    ('user_agent', 'user', 'user'),
    ('world_agent', 'world', 'world'),
]


async def resolve_agent_rid(routine: "Routine", agent_id: str,
                            ) -> Tuple[Optional[str], Optional[str]]:
    """按 agent_id 解析 (rid, agent_type). 未找到返回 (None, None).

    两跳: get_running_routines + req(manager, list_agents).
    不确定是否 live 时逐个 manager 查 (与原语义一致, 只跳过生命周期开销).
    """
    routines = await routine.get_running_routines()
    name_to_rid = {
        str(r.get('name') or ''): str(r.get('id') or '')
        for r in routines
    }

    # passive 常驻 agent: 直接命中, 不需要问 manager.
    for routine_name, passive_id, agent_type in _PASSIVE_AGENTS:
        if agent_id == passive_id:
            rid = name_to_rid.get(routine_name)
            return rid or None, agent_type if rid else None

    for manager_name, agent_type in _MANAGERS:
        rid = name_to_rid.get(manager_name)
        if not rid:
            continue
        try:
            # live_agents: manager 纯内存快照(不碰 SQLite), 热路径快.
            resp = await routine.req(rid, 'live_agents', {}, timeout=5.0)
        except Exception:
            continue
        for item in (resp or {}).get('agents') or []:
            if item.get('agent_id') == agent_id and item.get('routine_id'):
                return (str(item['routine_id']), agent_type)
    return (None, None)


async def resolve_all_agents(routine: "Routine") -> list:
    """列出全部 live agent [{agent_id, routine_id, agent_type}] (list_running_agents 内核)."""
    routines = await routine.get_running_routines()
    name_to_rid = {
        str(r.get('name') or ''): str(r.get('id') or '')
        for r in routines
    }
    agents: list = []
    for manager_name, agent_type in _MANAGERS:
        rid = name_to_rid.get(manager_name)
        if not rid:
            continue
        try:
            resp = await routine.req(rid, 'live_agents', {}, timeout=5.0)
        except Exception:
            continue
        agents.extend(
            {'agent_id': it.get('agent_id'), 'routine_id': it.get('routine_id'),
             'agent_type': agent_type}
            for it in (resp or {}).get('agents') or []
            if it.get('routine_id')
        )
    for routine_name, passive_id, agent_type in _PASSIVE_AGENTS:
        rid = name_to_rid.get(routine_name)
        if rid:
            agents.append({'agent_id': passive_id, 'routine_id': rid,
                           'agent_type': agent_type})
    return agents
