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

from .._core.llm import _DEFAULT_MODEL
from .._core.store import Store, get_store

__all__ = ['PrimeAgentManager', 'CreatePrimeAgent', 'StopPrimeAgent', 'ResumePrimeAgent', 'ListPrimeAgents', 'DeletePrimeAgent']

_log = setup_logger('prime.manager')

_AGENT_ROUTINE_NAME = 'prime_agent'
_MANAGER_NAME = 'prime_agent_manager'
_TYPES_HUB_NAME = 'agent_hub'
# 归属 tag: 共享 agents 库的行归属声明 (pi/TS 侧写 'pi'), list 只认自己.
_OWNER_TAG = 'prime'

# 组装 defaults (原随附 preset 'prime' 的文件化快照内联至此; 旧 agent_preset
# 体系已淘汰, 预制体统一走 ~/.zero/presets 的 kind+params 预填).
_DEFAULT_TOOLS = ['ipython']
_BASE_PRELOAD_SKILLS = ['routine_bridge']
_BASE_LEVEL1_SKILLS = ['hub_routine']

# agent 类型 manifest: manager 启动时主动 register 到 agent_hub
# (hub 纯被动, 不扫描 catalog), 前端经 agent_hub/types 查类型清单动态渲染创建表单.
_MANIFEST: Dict[str, Any] = {
    'kind': 'prime',
    'label': 'Prime',
    'description': 'IPython 持久控制环境的编码 agent, 只有 ipython 一个 tool',
    'can_resume': True,
    'uses_model': True,
    'uses_skills': True,
    'routines': {
        'create': 'create_prime_agent',
        'list': 'list_prime_agents',
        'stop': 'stop_prime_agent',
        'resume': 'resume_prime_agent',
        'delete': 'delete_prime_agent',
    },
    'create_fields': [
        {'name': 'agent_id', 'label': 'Agent ID', 'type': 'text',
         'required': False, 'placeholder': 'auto-generated'},
        {'name': 'extra_instructions', 'label': '附加系统指令',
         'type': 'textarea', 'required': False,
         'placeholder': '可选 persona'},
    ],
}


async def _find_rid_by_name(routine: 'Routine', name: str) -> Optional[str]:
    """按 name 查 running routine 的 rid (单次, 竞态重试在调用方)."""
    for r in await routine.get_running_routines():
        if str(r.get('name') or '') == name:
            rid = str(r.get('id') or '').strip()
            if rid:
                return rid
    return None

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

    async def run(self, kwargs: Dict[str, Any]) -> None:
        _log.info('prime agents manager started')
        asyncio.create_task(self._register_manifest())
        await self._stop.wait()

    async def _register_manifest(self) -> None:
        """向 agent_hub 注册 manifest (hub 纯被动, manager 主动注册).

        启动竞态 (hub 尚未就绪) 兜底重试 ~5s; 成功即止.
        """
        for _ in range(50):
            try:
                rid = await _find_rid_by_name(self, _TYPES_HUB_NAME)
                if rid:
                    await self.req(rid, 'register', _MANIFEST)
                    _log.info('agent manifest registered (kind: %s)',
                              _MANIFEST['kind'])
                    return
            except Exception as exc:
                _log.warning('agent_types register retry: %r', exc)
            await asyncio.sleep(0.1)
        _log.error('agent_types register failed: agent_hub unreachable')

    async def stop(self) -> None:
        # 注销 (尽力而为; hub 已停则静默放弃)
        try:
            rid = await _find_rid_by_name(self, _TYPES_HUB_NAME)
            if rid:
                await self.req(rid, 'unregister', {'kind': _MANIFEST['kind']})
        except Exception as exc:
            _log.warning('agent_types unregister on stop: %r', exc)
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
        store = self._store_for()
        agent_id = str(data.get('agent_id') or '').strip()
        if agent_id:
            if store.get_agent(agent_id) is not None:
                return {'ok': False, 'error': f'agent_id {agent_id} already exists; use resume instead'}
        else:
            agent_id = self._next_agent_id(store)
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        session_id = uuid4().hex
        # 解析成最终生效 model (含全局默认兜底) 后持久化:
        # resume 只认这份记录, 不再受后续默认值变化影响.
        model = data.get('model') or _DEFAULT_MODEL
        try:
            store.register_agent(agent_id, session_id=session_id, model=model, preset=_OWNER_TAG)
        except Exception as exc:
            _log.warning('persist agent %s failed: %r', agent_id, exc)
        return await self._spawn_child(
            agent_id, data, session_id=session_id, is_resume=False, model=model)

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
        session_id = str(agent_rec.get('session_id') or '') or uuid4().hex
        # model 优先级: 显式传参 > agent 记录持久化值 > 全局默认.
        # resume 不换模型 ---- 持久化值即会话创建时实际生效的 model.
        model = data.get('model') or agent_rec.get('model') or _DEFAULT_MODEL
        try:
            store.register_agent(agent_id, session_id=session_id, model=model, preset=_OWNER_TAG)
        except Exception as exc:
            _log.warning('persist agent %s failed: %r', agent_id, exc)
        return await self._spawn_child(
            agent_id, data, session_id=session_id, is_resume=True, model=model)

    async def _spawn_child(
        self, agent_id: str, data: dict, *, session_id: str,
        is_resume: bool, model: str,
    ) -> dict:
        # 组装: 代码 defaults, 调用方显式传参覆盖/叠加.
        # model 由调用方解析完毕传入 (create/resume 各有自己的优先级链).
        project_dir = data.get('project_dir') or data.get('project_dir_root_path')
        plan_mode = bool(data.get('plan_mode', False))
        extra_instructions = data.get('extra_instructions')
        max_turns = data.get('max_turns')
        enabled_tools = list(data.get('enabled_tools') or _DEFAULT_TOOLS)
        disabled_tools = data.get('disabled_tools')
        # skills 参数 [{name, level}] (前端 SkillPicker): full -> L2 全文预载,
        # summary -> L1 轻量发现; 与显式 preload_skills/level1_skills 一并叠加.
        preload_names: list[str] = []
        level1_names: list[str] = []
        for s in (data.get('skills') or []):
            if not isinstance(s, dict):
                continue
            name = str(s.get('name') or '').strip()
            if not name:
                continue
            if str(s.get('level') or 'summary') == 'full':
                preload_names.append(name)
            else:
                level1_names.append(name)
        preload_skills = list(dict.fromkeys(
            _BASE_PRELOAD_SKILLS + preload_names
            + list(data.get('preload_skills') or [])))
        level1_skills = list(dict.fromkeys(
            _BASE_LEVEL1_SKILLS + level1_names
            + list(data.get('level1_skills') or [])))
        condense_config = data.get('condense_config')
        agent_routine = _AGENT_ROUTINE_NAME

        child_kwargs: Dict[str, Any] = {
            'agent_id': agent_id,
            'agent_name': data.get('agent_name'),
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
            handle = await self.create(agent_routine, child_kwargs)
            await handle.start()
        except Exception as exc:
            _log.error('spawn agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}

        self._agents[agent_id] = {
            'handle': handle,
            'project_dir': project_dir,
            'session_id': session_id,
        }
        action = 'resumed' if is_resume else 'created'
        _log.info(
            '%s prime agent: agent_id=%s routine_id=%s session_id=%s project_dir=%s',
            action, agent_id, handle.id, session_id, project_dir,
        )
        return {
            'ok': True,
            'agent_id': agent_id,
            'routine_id': handle.id,
            'session_id': session_id,
        }

    @request('live_agents')
    async def on_live_agents(self, source, data: dict) -> dict:
        """纯内存 live agent 快照 [{agent_id, routine_id}] ---- 热路径
        (rid 解析) 专用, 不碰 SQLite. 全量元数据走 list_agents."""
        return {'agents': [
            {'agent_id': aid, 'routine_id': info['handle'].id}
            for aid, info in self._agents.items() if info.get('handle')
        ]}

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
            # 归属声明: agents 库被多类型共享 (pi/TS 侧也写), preset 列是
            # 各类型的归属 tag; 本 manager 只认自己的行, 不再猜前缀.
            if preset != _OWNER_TAG:
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
                'routine_id': handle.id if handle else None,
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
        if store.get_agent(agent_id) is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not found'}
        try:
            store.delete_agent(agent_id)
        except Exception as exc:
            _log.warning('delete prime agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}
        _log.info('deleted prime agent: agent_id=%s', agent_id)
        return {'ok': True, 'agent_id': agent_id}

    def _next_agent_id(self, store: 'Store') -> str:
        """生成下一个 prime_<N> agent_id."""
        existing = {r.get('agent_id', '') for r in store.list_agents()}
        n = 1
        while f'prime_{n}' in existing or f'prime_{n}' in self._agents:
            n += 1
        return f'prime_{n}'


# ──────────────────────────────────────────────────────────────────────────────
# entry routine (bridge -> manager)
# ──────────────────────────────────────────────────────────────────────────────

class CreatePrimeAgentInput(BaseModel):
    agent_id: str | None = Field(None, description='Agent id. Auto-generated when omitted.')
    project_dir: str | None = Field(None, description='Project root directory path.')
    model: str | None = Field(None, description='LLM model name.')
    plan_mode: bool = Field(False, description='Readonly tools only.')
    extra_instructions: str | None = Field(None, description='Extra system instructions.')
    max_turns: int | None = Field(None, description='Max conversation turns.')
    skills: list[dict] | None = Field(
        None, description='Initial skills [{name, level}]: level full -> '
        'preload (L2 全文), summary -> level1 (轻量发现).')
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


class ResumePrimeAgentInput(BaseModel):
    agent_id: str = Field(description='Agent id to resume (e.g. prime_1).')
    model: str | None = Field(None, description='LLM model override.')
    project_dir: str | None = Field(None, description='Project root override.')
    plan_mode: bool | None = Field(None, description='Readonly tools only.')
    extra_instructions: str | None = Field(None, description='Extra system instructions.')
    max_turns: int | None = Field(None, description='Max conversation turns.')
    skills: list[dict] | None = Field(
        None, description='Initial skills [{name, level}] (full -> preload, '
        'summary -> level1).')
    preload_skills: list[str] | None = Field(None, description='Skills to preload.')
    level1_skills: list[str] | None = Field(None, description='Level-1 skill discovery.')
    enabled_tools: list[str] | None = Field(None, description='Tool whitelist.')
    disabled_tools: list[str] | None = Field(None, description='Tool blacklist.')


class ResumePrimeAgent(Routine):
    """entry routine: resume a stopped PrimeAgent via the resident manager."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': ResumePrimeAgentInput.model_json_schema(),
        'description': (
            'Entry routine that resumes a stopped PrimeAgent via the resident '
            'PrimeAgentManager. Rebuilds state from persisted history.'
        ),
    }

    _REQ_TIMEOUT = 10.0

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        ResumePrimeAgentInput.model_validate(kwargs or {})
        manager_id = await self._find_manager()
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot resume agent'
            )
        # 全量 kwargs 转发 (agent_id 必填, model/skills 等可选覆盖), 对齐 TS 语义
        return await self.ctx.req(
            manager_id, 'resume_agent', kwargs or {},
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


class ListPrimeAgentsInput(BaseModel):
    project_dir: str | None = Field(None, description='Optional project dir filter.')


class ListPrimeAgentsOutput(BaseModel):
    agents: list | None = Field(None, description='Agent records with live status.')


class ListPrimeAgents(Routine):
    """entry routine: list all PrimeAgents (live + stopped) via the manager."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': ListPrimeAgentsInput.model_json_schema(),
        'output_schema': ListPrimeAgentsOutput.model_json_schema(),
        'description': (
            'Entry routine that lists all PrimeAgents (live + stopped) via the '
            'resident PrimeAgentManager.'
        ),
    }

    _REQ_TIMEOUT = 10.0

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        manager_id = await self._find_manager()
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot list agents'
            )
        return await self.ctx.req(
            manager_id, 'list_agents', kwargs or {},
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


class DeletePrimeAgentInput(BaseModel):
    agent_id: str = Field(description='Agent id to delete (must be stopped).')


class DeletePrimeAgentOutput(BaseModel):
    ok: bool = Field(description='Whether the agent was deleted.')
    agent_id: str | None = Field(None, description='The deleted agent id.')
    error: str | None = Field(None, description='Error message when ok is false.')


class DeletePrimeAgent(Routine):
    """entry routine: delete a stopped PrimeAgent record via the manager."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': DeletePrimeAgentInput.model_json_schema(),
        'output_schema': DeletePrimeAgentOutput.model_json_schema(),
        'description': (
            'Entry routine that deletes a stopped PrimeAgent record (store row '
            '+ messages) via the resident PrimeAgentManager. Irreversible; '
            'live agents are rejected (stop first).'
        ),
    }

    _REQ_TIMEOUT = 10.0

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        inp = DeletePrimeAgentInput.model_validate(kwargs or {})
        manager_id = await self._find_manager()
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot delete agent'
            )
        return await self.ctx.req(
            manager_id, 'delete_agent', {'agent_id': inp.agent_id},
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
