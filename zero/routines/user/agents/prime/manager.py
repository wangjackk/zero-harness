"""PrimeAgentManager -- resident passive routine that spawns PrimeAgent children.

spawn 的是 PrimeAgent (只有 ipython tool).
复用同一 sqlite Store (共享 ~/.zero/sessions.db), agent_id 前缀 prime_.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from routine import Routine, request
from routine.logger import setup_logger

from .._core.presets import load_preset
from .._core.store import Store, get_store

_log = setup_logger('prime.manager')

_AGENT_ROUTINE_NAME = 'prime_agent'
_MANAGER_NAME = 'prime_agent_manager'
_DEFAULT_PRESET = 'prime'

class PrimeAgentsInput(BaseModel):
    pass


class PrimeAgentsOutput(BaseModel):
    pass


class PrimeAgentManager(Routine):
    """resident passive manager: dynamically create/list/stop PrimeAgent children."""

    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': PrimeAgentsInput.model_json_schema(),
        'output_schema': PrimeAgentsOutput.model_json_schema(),
        'description': (
            'Resident manager for PrimeAgent coding agents. Spawns/lists/stops '
            'PrimeAgent child instances on request. Agent records persist to '
            'sqlite; each child runs concurrently, isolated by agent_id.'
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop = asyncio.Event()
        self._agents: Dict[str, dict] = {}
        self._stopping: set[str] = set()

    def _store_for(self) -> 'Store':
        return get_store()

    async def on_started(self) -> None:
        _log.info('prime agents manager started')

    async def run(self, kwargs: Dict[str, Any]) -> None:
        await self._stop.wait()

    async def stop(self) -> None:
        for agent_id in list(self._agents):
            info = self._agents.get(agent_id)
            if info is None:
                continue
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                try:
                    await handle.stop()
                except Exception as exc:
                    _log.warning('stop child %s on manager stop: %r', agent_id, exc)
        self._agents.clear()
        self._stop.set()

    # ------------------------------------------------------------------
    # req handlers
    # ------------------------------------------------------------------

    @request('create_agent')
    async def on_create(self, source, data: dict) -> dict:
        data = data or {}
        preset_id = str(data.get('preset') or _DEFAULT_PRESET)
        try:
            preset = load_preset(preset_id)
        except (FileNotFoundError, ValueError) as exc:
            return {'ok': False, 'error': f'preset error: {exc}'}
        store = self._store_for()
        agent_id = str(data.get('agent_id') or '').strip()
        if agent_id:
            if store.get_agent(agent_id) is not None:
                return {'ok': False, 'error': f'agent_id {agent_id} already exists; use resume instead'}
        else:
            agent_id = self._next_agent_id(store, preset_id)
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        session_id = uuid4().hex
        try:
            store.register_agent(
                agent_id, session_id=session_id,
                model=data.get('model') or preset.get('model'), preset=preset_id,
            )
        except Exception as exc:
            _log.warning('persist agent %s failed: %r', agent_id, exc)
        return await self._spawn_child(
            agent_id, data, preset, session_id=session_id, is_resume=False)

    @request('resume_agent')
    async def on_resume(self, source, data: dict) -> dict:
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        store = self._store_for()
        agent_rec = store.get_agent(agent_id)
        if agent_rec is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not found'}
        preset_id = str(data.get('preset') or agent_rec.get('preset') or _DEFAULT_PRESET)
        try:
            preset = load_preset(preset_id)
        except (FileNotFoundError, ValueError) as exc:
            return {'ok': False, 'error': f'preset error: {exc}'}
        session_id = str(agent_rec.get('session_id') or '') or uuid4().hex
        return await self._spawn_child(
            agent_id, data, preset, session_id=session_id, is_resume=True)

    async def _spawn_child(
        self, agent_id: str, data: dict, preset: dict, *, session_id: str,
        is_resume: bool,
    ) -> dict:
        # preset 声明是 defaults, 调用方显式传参覆盖.
        project_dir = data.get('project_dir') or data.get('project_dir_root_path')
        model = data.get('model') or preset.get('model')
        plan_mode = bool(data.get('plan_mode', False))
        extra_instructions = data.get('extra_instructions') or preset.get('extra_instructions')
        max_turns = data.get('max_turns')
        enabled_tools = list(data.get('enabled_tools') or preset.get('enabled_tools') or [])
        disabled_tools = data.get('disabled_tools')
        user_skills = list(data.get('preload_skills') or [])
        preload_skills = list(dict.fromkeys(
            list(preset.get('preload_skills') or []) + user_skills))
        user_l1 = list(data.get('level1_skills') or [])
        level1_skills = list(dict.fromkeys(
            list(preset.get('level1_skills') or []) + user_l1))
        condense_config = data.get('condense_config')
        agent_routine = str(preset.get('agent_routine') or _AGENT_ROUTINE_NAME)

        child_kwargs: Dict[str, Any] = {
            'agent_id': agent_id,
            'agent_name': preset.get('name'),
            'project_dir_root_path': project_dir,
            'model': model,
            'plan_mode': plan_mode,
            'extra_instructions': extra_instructions,
            'max_turns': max_turns,
            'session_id': session_id,
            'preload_skills': preload_skills,
            'level1_skills': level1_skills,
            'enabled_tools': enabled_tools,
            'disabled_tools': disabled_tools,
            'condense_config': condense_config,
        }
        try:
            handle = await self.submit(agent_routine, child_kwargs)
            await handle.start()
        except Exception as exc:
            _log.error('spawn agent %s (preset %s) failed: %r',
                       agent_id, preset.get('id'), exc)
            return {'ok': False, 'error': str(exc)}

        self._agents[agent_id] = {
            'handle': handle,
            'project_dir': project_dir,
            'session_id': session_id,
        }
        action = 'resumed' if is_resume else 'created'
        _log.info(
            '%s prime agent: agent_id=%s handle_id=%s session_id=%s project_dir=%s',
            action, agent_id, handle.id, session_id, project_dir,
        )
        return {
            'ok': True,
            'agent_id': agent_id,
            'handle_id': handle.id,
            'session_id': session_id,
        }

    @request('list_agents')
    async def on_list(self, source, data: dict) -> dict:
        data = data or {}
        store = self._store_for()
        rows = store.list_agents()

        filter_pd = data.get('project_dir')
        if filter_pd:
            filter_pd_resolved = str(Path(filter_pd).resolve())
            rows = [
                r for r in rows
                if (info := self._agents.get(r.get('agent_id'))) is not None
                and info.get('project_dir')
                and str(Path(info['project_dir']).resolve()) == filter_pd_resolved
            ]

        items = []
        for row in rows:
            agent_id = row.get('agent_id', '')
            preset = row.get('preset')
            # 本 manager 管的记录: 带 preset 列, 或旧数据 prime_ 前缀 (migration 前).
            # preset 已删的历史 agent 仍列出 (resume 时 load_preset 失败会报错).
            if not (preset or agent_id.startswith('prime_')):
                continue
            info = self._agents.get(agent_id)
            live = info is not None
            handle = (info or {}).get('handle')
            items.append({
                'agent_id': agent_id,
                'preset': preset,
                'model': row.get('model'),
                'reasoning_effort': store.get_last_reasoning_effort(agent_id),
                'title': row.get('title'),
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                'session_id': (info or {}).get('session_id'),
                'project_dir': (info or {}).get('project_dir'),
                'handle_id': handle.id if handle else None,
                'status': 'live' if live else 'stopped',
                'live': live,
                'started': live,
                'done': handle.is_done() if handle else True,
            })
        return {'agents': items}

    @request('stop_agent')
    async def on_stop(self, source, data: dict) -> dict:
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        info = self._agents.get(agent_id)
        if info is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not live'}
        if agent_id in self._stopping:
            return {'ok': True, 'agent_id': agent_id}
        self._stopping.add(agent_id)
        try:
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                await handle.stop()
            self._agents.pop(agent_id, None)
            _log.info('stopped prime agent: agent_id=%s', agent_id)
            return {'ok': True, 'agent_id': agent_id}
        except Exception as exc:
            _log.warning('stop prime agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}
        finally:
            self._stopping.discard(agent_id)

    @request('delete_agent')
    async def on_delete(self, source, data: dict) -> dict:
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} is live; stop it first'}
        store = self._store_for()
        try:
            store.delete_agent(agent_id)
        except Exception as exc:
            _log.warning('delete prime agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}
        _log.info('deleted prime agent: agent_id=%s', agent_id)
        return {'ok': True, 'agent_id': agent_id}

    def _next_agent_id(self, store: 'Store', preset_id: str) -> str:
        """生成下一个 <preset>_<N> agent_id (prime preset 即旧版 prime_<N>)."""
        existing = {r.get('agent_id', '') for r in store.list_agents()}
        n = 1
        while f'{preset_id}_{n}' in existing or f'{preset_id}_{n}' in self._agents:
            n += 1
        return f'{preset_id}_{n}'


# ──────────────────────────────────────────────────────────────────────────────
# entry routine (bridge -> manager)
# ──────────────────────────────────────────────────────────────────────────────

class CreatePrimeAgentInput(BaseModel):
    agent_id: str | None = Field(None, description='Agent id. Auto-generated when omitted.')
    preset: str | None = Field(None, description='Agent preset id. Defaults to "prime".')
    project_dir: str | None = Field(None, description='Project root directory path.')
    model: str | None = Field(None, description='LLM model name.')
    plan_mode: bool = Field(False, description='Readonly tools only.')
    extra_instructions: str | None = Field(None, description='Extra system instructions.')
    max_turns: int | None = Field(None, description='Max conversation turns.')
    preload_skills: list[str] | None = Field(None, description='Skills to preload.')
    level1_skills: list[str] | None = Field(None, description='Level-1 skill discovery.')
    enabled_tools: list[str] | None = Field(None, description='Tool whitelist.')
    disabled_tools: list[str] | None = Field(None, description='Tool blacklist.')


class CreatePrimeAgentOutput(BaseModel):
    pass


class CreatePrimeAgent(Routine):
    """entry routine: spawn a PrimeAgent via the resident manager."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': CreatePrimeAgentInput.model_json_schema(),
        'output_schema': CreatePrimeAgentOutput.model_json_schema(),
        'description': (
            'Entry routine that spawns a PrimeAgent coding agent via the '
            'resident PrimeAgentManager. Returns the new agent_id.'
        ),
    }

    _REQ_TIMEOUT = 10.0

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        manager_id = await self._find_manager()
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot spawn agent'
            )
        return await self.ctx.req(
            manager_id, 'create_agent', kwargs or {},
            timeout=self._REQ_TIMEOUT,
        )

    async def _find_manager(self) -> Optional[str]:
        for _ in range(50):
            try:
                routines = await self.ctx.get_running_routines()
            except Exception as exc:
                _log.warning('get_running failed (%r), retry', exc)
                routines = []
            for r in routines:
                if str(r.get('name') or '') == _MANAGER_NAME:
                    rid = str(r.get('id') or '').strip()
                    if rid:
                        return rid
            await asyncio.sleep(0.1)
        return None


class StopPrimeAgentInput(BaseModel):
    agent_id: str = Field(description='Agent id to stop (e.g. prime_1).')


class StopPrimeAgentOutput(BaseModel):
    ok: bool = Field(description='Whether the agent was stopped.')
    agent_id: str | None = Field(None, description='The stopped agent id.')
    error: str | None = Field(None, description='Error message when ok is false.')


class StopPrimeAgent(Routine):
    """entry routine: stop a live PrimeAgent via the resident manager."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': StopPrimeAgentInput.model_json_schema(),
        'output_schema': StopPrimeAgentOutput.model_json_schema(),
        'description': (
            'Entry routine that stops a live PrimeAgent via the resident '
            'PrimeAgentManager. The agent record persists; resume to restart.'
        ),
    }

    _REQ_TIMEOUT = 10.0

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        inp = StopPrimeAgentInput.model_validate(kwargs or {})
        manager_id = await self._find_manager()
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot stop agent'
            )
        return await self.ctx.req(
            manager_id, 'stop_agent', {'agent_id': inp.agent_id},
            timeout=self._REQ_TIMEOUT,
        )

    async def _find_manager(self) -> Optional[str]:
        for _ in range(50):
            try:
                routines = await self.ctx.get_running_routines()
            except Exception as exc:
                _log.warning('get_running failed (%r), retry', exc)
                routines = []
            for r in routines:
                if str(r.get('name') or '') == _MANAGER_NAME:
                    rid = str(r.get('id') or '').strip()
                    if rid:
                        return rid
            await asyncio.sleep(0.1)
        return None
