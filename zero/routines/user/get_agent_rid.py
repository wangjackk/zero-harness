"""GetAgentRid -- 按 agent_id 反查运行时 rid.

用法:
    run_routine({name: 'get_agent_rid', agent_id: 'prime_x'})
    # 返回: {'ok': True, 'agent_id': 'prime_x', 'rid': '...', 'agent_type': 'prime'}
    # 未找到: {'ok': False, 'error': 'agent prime_x not live or not found'}
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

_log = setup_logger('get_agent_rid')


class GetAgentRidInput(BaseModel):
    agent_id: str = Field(description='要查的 agent_id')


class GetAgentRidOutput(BaseModel):
    ok: bool
    agent_id: str
    rid: str | None = None
    agent_type: str | None = None
    error: str | None = None


class GetAgentRid(Routine):
    """按 agent_id 反查 live agent 的 rid (复用 list_running_agents)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '按 agent_id 反查运行时 rid. 复用 list_running_agents.',
        'input_schema': GetAgentRidInput.model_json_schema(),
        'output_schema': GetAgentRidOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = GetAgentRidInput.model_validate(kwargs)

        try:
            listing = await self.call('list_running_agents')
        except Exception as exc:
            _log.warning('list_running_agents failed: %r', exc)
            return {'ok': False, 'agent_id': inp.agent_id,
                    'error': f'list_running_agents failed: {exc}'}

        for a in (listing or {}).get('agents') or []:
            if a.get('agent_id') == inp.agent_id:
                rid = a.get('rid')
                if not rid:
                    return {'ok': False, 'agent_id': inp.agent_id,
                            'error': f'agent {inp.agent_id} live but rid missing'}
                _log.info('get_agent_rid: %s -> %s (%s)',
                          inp.agent_id, rid, a.get('agent_type'))
                return {'ok': True, 'agent_id': inp.agent_id,
                        'rid': rid, 'agent_type': a.get('agent_type')}

        return {'ok': False, 'agent_id': inp.agent_id,
                'error': f'agent {inp.agent_id} not live or not found'}
