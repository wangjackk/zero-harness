"""ListRunningAgents -- 列出所有 live agent (prime/xml + user/world).

向各 resident manager 发 live_agents req, 合并 live 的, 返回 agent_id +
routine_id + agent_type. manager id 通过 get_running_routines 按 name 找
(不依赖 bridge). 另外检查 user_agent / world_agent passive routine 是否在
运行, 在则加入列表.

用法:
    run_routine({name: 'list_running_agents'})
    # 返回: {'agents': [{'agent_id': 'prime_1', 'routine_id': '...', 'agent_type': 'prime'}, ...]}
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

_log = setup_logger('list_running_agents')


class ListRunningAgentsInput(BaseModel):
    pass


class ListRunningAgentsOutput(BaseModel):
    agents: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='live agent 列表. 每个 item: {agent_id, routine_id, agent_type}',
    )


class ListRunningAgents(Routine):
    """列出所有运行中的 agent (prime + user/world)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '列出所有运行中的 agent (prime + user/world). 返回 agent_id + routine_id + agent_type.',
        'input_schema': ListRunningAgentsInput.model_json_schema(),
        'output_schema': ListRunningAgentsOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        from zero.routines.user.agents._core.rid import resolve_all_agents
        agents = await resolve_all_agents(self)
        _log.info('list_running_agents: %d live agents', len(agents))
        return {'agents': agents}
