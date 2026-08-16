"""ListRunningAgents -- 列出所有 live agent (prime/xml + user/world).

向各 resident manager 发 list_agents req, 合并 live 的, 返回 agent_id + rid +
agent_type. manager rid 通过 get_running_routines 按 name 找 (不依赖 bridge).
另外检查 user_agent / world_agent passive routine 是否在运行, 在则加入列表.

用法:
    run_routine({name: 'list_running_agents'})
    # 返回: {'agents': [{'agent_id': 'prime_1', 'rid': '...', 'agent_type': 'prime'}, ...]}
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

_log = setup_logger('list_running_agents')

# (manager routine name, agent_type) -- 所有 resident manager.
_MANAGERS = [
    ('prime_agent_manager', 'prime'),
    ('xml_agents', 'xml'),
]

# passive 常驻 agent (不是 manager 管理的, 直接查 routine 是否在运行).
_PASSIVE_AGENTS = [
    ('user_agent', 'user', 'user'),
    ('world_agent', 'world', 'world'),
]


class ListRunningAgentsInput(BaseModel):
    pass


class ListRunningAgentsOutput(BaseModel):
    agents: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='live agent 列表. 每个 item: {agent_id, rid, agent_type}',
    )


class ListRunningAgents(Routine):
    """列出所有运行中的 agent (prime + user/world)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '列出所有运行中的 agent (prime + user/world). 返回 agent_id + rid + agent_type.',
        'input_schema': ListRunningAgentsInput.model_json_schema(),
        'output_schema': ListRunningAgentsOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        routines = await self.get_running_routines()
        name_to_rid = {
            str(r.get('name') or ''): str(r.get('id') or '')
            for r in routines
        }

        async def _query(manager_name: str, agent_type: str) -> list[dict]:
            rid = name_to_rid.get(manager_name)
            if not rid:
                return []
            try:
                resp = await self.req(rid, 'list_agents', {}, timeout=5.0)
            except Exception as exc:
                _log.warning('req %s list_agents failed: %r', manager_name, exc)
                return []
            items = (resp or {}).get('agents') or []
            return [
                {
                    'agent_id': item.get('agent_id'),
                    'rid': item.get('handle_id'),
                    'agent_type': agent_type,
                }
                for item in items
                if item.get('live') and item.get('handle_id')
            ]

        results = await asyncio.gather(
            *[_query(name, atype) for name, atype in _MANAGERS]
        )
        agents: list[dict] = []
        for r in results:
            agents.extend(r)
        # passive 常驻 agent (如 user_agent): 直接从 name_to_rid 查
        for routine_name, agent_id, agent_type in _PASSIVE_AGENTS:
            rid = name_to_rid.get(routine_name)
            if rid:
                agents.append({'agent_id': agent_id, 'rid': rid, 'agent_type': agent_type})
        _log.info('list_running_agents: %d live agents', len(agents))
        return {'agents': agents}
