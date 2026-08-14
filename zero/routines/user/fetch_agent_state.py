"""FetchAgentState -- 按 agent_id 拿 agent 运行时状态.

封装 ``get_agent_rid`` (查 rid) + ``req(rid, 'agent_state')`` (拿 state) 两步,
返回 agent 的 skill_dir / skill_index_cache_dir / project_root / session_id 等.

tool routine 被 agent push 时框架注入 ``agent_id`` (持久身份), 需要这些信息时
调本 routine 一步拿到, 不用各自 import helper + 两步 call.

用法:
    run_routine({name: 'fetch_agent_state', agent_id: 'prime_1'})
    # {'ok': True, 'agent_id': 'prime_1', 'skill_dir': '...', 'session_id': '...', ...}
    # agent 不 live: {'ok': False, 'error': '...'}
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

_log = setup_logger('fetch_agent_state')


class FetchAgentStateInput(BaseModel):
    agent_id: str = Field(description='agent 的唯一身份id')


class FetchAgentStateOutput(BaseModel):
    ok: bool
    agent_id: str | None = None
    skill_dir: str | None = None
    skill_index_cache_dir: str | None = None
    project_root: str | None = None
    session_id: str | None = None
    error: str | None = None


class FetchAgentState(Routine):
    """按 agent_id 拿 agent 运行时状态 (复用 get_agent_rid + req agent_state)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '按 agent_id 拿 agent 运行时状态 (skill_dir / project_root / session_id 等). 内部 call get_agent_rid 查 rid 再 req agent_state.',
        'input_schema': FetchAgentStateInput.model_json_schema(),
        'output_schema': FetchAgentStateOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = FetchAgentStateInput.model_validate(kwargs)

        # 1. 查 rid
        try:
            rid_resp = await self.call('get_agent_rid', {'agent_id': inp.agent_id})
        except Exception as exc:
            _log.warning('get_agent_rid failed: %r', exc)
            return {'ok': False, 'error': f'get_agent_rid failed: {exc}'}
        if not (rid_resp or {}).get('ok'):
            return {'ok': False, 'error': (rid_resp or {}).get('error', 'agent not found')}

        # 2. req agent_state (重试一次, 应对 agent 事件循环偶发调度延迟)
        state = None
        last_exc: Exception | None = None
        for attempt in (0, 1):
            try:
                state = await self.req(rid_resp['rid'], 'agent_state', {}, timeout=5.0)
                break
            except Exception as exc:
                last_exc = exc
                _log.warning('req agent_state attempt %d failed: %r', attempt + 1, exc)
        if state is None:
            return {'ok': False, 'error': f'req agent_state failed: {last_exc}'}

        if not isinstance(state, dict):
            return {'ok': False, 'error': f'agent_state returned non-dict: {state!r}'}

        _log.info('fetch_agent_state: %s -> skill_dir=%s session=%s',
                  inp.agent_id, state.get('skill_dir') or '-', state.get('session_id') or '-')
        return {
            'ok': True,
            'agent_id': state.get('agent_id') or inp.agent_id,
            'skill_dir': state.get('skill_dir'),
            'skill_index_cache_dir': state.get('skill_index_cache_dir'),
            'project_root': state.get('project_root'),
            'session_id': state.get('session_id'),
        }
