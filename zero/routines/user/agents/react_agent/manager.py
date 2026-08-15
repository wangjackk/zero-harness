"""ReactAgents -- resident passive routine that spawns react_agent children.

is_passive=True -> kernel auto-starts on connect (single resident instance).
Driven via req (bridge -> entry routine -> manager):

  - create_agent  : submit+start a ReactAgent child, return agent_id
  - list_agents   : enumerate agents (live + historical, from the sqlite Memory)
  - stop_agent    : cascade-stop a child

Mirrors prime/manager.py. Agent records persist in the sqlite Memory
(react_agent/memory.py agents table): create writes a row, list reads from
the db. agent_id 是身份, session_id 是 UUID 会话身份 (agents 表持久化). 无线性链. status/handle_id
是运行时态, 由 manager 内存管 (self._agents dict), 不持久化. live 状态跨重启
全丢失, list_agents 通过内存判断 live/stopped.

Each child is an independent ReactAgent instance, isolated by agent_id (own
pubsub namespace). 一个 agent 一个 session (session_id 是 UUID), resume = 重启
某个 agent_id (历史消息按 session_id 自动加载).
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from routine import Routine, request
from routine.logger import setup_logger

from .agent import ReactAgent
from .memory import get_memory
from .llm import LLMClient, _DEFAULT_MODEL

_log = setup_logger('react_agent.manager')

# child routine name (ReactAgent.name snake) -- what manager submits.
_AGENT_ROUTINE_NAME = 'react_agent'
# resident manager routine name (ReactAgents.name snake) -- what
# CreateReactAgent locates by name to req into.
_MANAGER_NAME = 'react_agents'

# OpenViking 不默认启用: 前端未传 ov_config 时为 None, agent 只用本地
# sqlite memory (provider 对 None 有完整降级路径). 要长期记忆显式传配置.


class ReactAgentsInput(BaseModel):
    pass


class ReactAgentsOutput(BaseModel):
    pass


class ReactAgents(Routine):
    """resident passive manager: dynamically create/list/stop react agents.

    agent records persist in the sqlite Memory so the roster survives restart;
    live handles held in-memory only for stop + status annotation.
    """

    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': ReactAgentsInput.model_json_schema(),
        'output_schema': ReactAgentsOutput.model_json_schema(),
        'description': (
            'Resident manager for react agents. Spawns/lists/stops ReactAgent '
            'child instances on request. Agent records persist to sqlite; each '
            'child runs concurrently, isolated by agent_id.'
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop = asyncio.Event()
        # agent_id -> {'handle': RoutineHandle, 'session_id': str, 'model': str|None}
        # only live agents; historical ones live in the db.
        self._agents: Dict[str, dict] = {}
        # agent_ids with an in-flight stop (handle.stop() not yet acked).
        # guards against concurrent stop reqs (tab close + Stop button, or a
        # double-click): the second sees the agent already stopping and returns
        # ok idempotently instead of hitting handle's "another start/stop in
        # flight" RuntimeError.
        self._stopping: set[str] = set()
        self._mem = get_memory()

    async def on_started(self) -> None:
        # status 不再持久化 (agent = session, manager 内存管 live 状态).
        # restart 时 self._agents 为空, list_agents 读 DB 历史元数据,
        # live 状态由内存判断.
        _log.info('react agents manager started')

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """resident: wait for stop. children outlive individual req handlers."""
        await self._stop.wait()

    async def stop(self) -> None:
        # cascade-stop all live children. status 不持久化, 内存清空即可.
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
    # req handlers (bridge -> entry routine -> manager)
    # ------------------------------------------------------------------

    @request('create_agent')
    async def on_create(self, source, data: dict) -> dict:
        """创建新 ReactAgent. agent_id 不传则自动生成 (react_<N> 自增);
        传了且 DB 已存在则拒绝 (那是 resume 的语义).
        """
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if agent_id:
            if self._mem.get_agent(agent_id) is not None:
                return {'ok': False, 'error': f'agent_id {agent_id} already exists; use resume instead'}
        else:
            agent_id = self._mem.next_agent_id()
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        session_id = uuid4().hex
        model = data.get('model') or _DEFAULT_MODEL
        try:
            self._mem.register_agent(agent_id, session_id=session_id, model=model)
        except Exception as exc:
            _log.warning('persist agent %s failed: %r', agent_id, exc)
        return await self._spawn_child(agent_id, data, is_resume=False, session_id=session_id)

    @request('resume_agent')
    async def on_resume(self, source, data: dict) -> dict:
        """恢复已停止的 react agent. agent_id 必传, 必须已存在 DB, 当前不能 live.
        child 从 messages 表按 session_id (agents 表持久化的 UUID) replay 历史.
        """
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        agent_rec = self._mem.get_agent(agent_id)
        if agent_rec is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not found'}
        session_id = str(agent_rec.get('session_id') or '') or uuid4().hex
        return await self._spawn_child(agent_id, data, is_resume=True, session_id=session_id)

    async def _spawn_child(
        self, agent_id: str, data: dict, *, is_resume: bool, session_id: str,
    ) -> dict:
        """公共 spawn 逻辑: 解析参数 → submit+start child → 记录 live.

        create / resume 共用, 区别仅在前置校验 (agent_id 来源 + DB 检查) 和
        register_agent (由调用方各自处理).

        ov_config 未传为 None -> agent 只用本地 sqlite memory;
        其他可选配置 (condense_config / preload_skills / level1_skills)
        data 里没传的字段不写入 child_kwargs, ReactAgent.on_started 用 .get()
        取时得 None -> 走默认 (禁用).
        """
        model = data.get('model') or _DEFAULT_MODEL
        child_kwargs: Dict[str, Any] = {
            'agent_id': agent_id,
            'session_id': session_id,
            'model': model,
            'ov_config': data.get('ov_config'),
        }
        for opt_key in ('condense_config',
                        'preload_skills', 'level1_skills'):
            if data.get(opt_key) is not None:
                child_kwargs[opt_key] = data[opt_key]
        try:
            handle = await self.submit(_AGENT_ROUTINE_NAME, child_kwargs)
            await handle.start()
        except Exception as exc:
            action = 'resume' if is_resume else 'create'
            _log.error('%s react agent %s failed: %r', action, agent_id, exc)
            return {'ok': False, 'error': str(exc)}

        self._agents[agent_id] = {
            'handle': handle,
            'model': model,
            'reasoning_effort': LLMClient(model).reasoning_effort,
        }
        action = 'resumed' if is_resume else 'created'
        _log.info(
            '%s react agent: agent_id=%s handle_id=%s model=%s',
            action, agent_id, handle.id, model,
        )
        return {
            'ok': True,
            'agent_id': agent_id,
            'handle_id': handle.id,
            'session_id': session_id,
        }

    @request('list_agents')
    async def on_list(self, source, data: dict) -> dict:
        """enumerate all agents (live + historical), annotated with running state."""
        rows = self._mem.list_agents()
        items = []
        for row in rows:
            agent_id = row.get('agent_id')
            info = self._agents.get(agent_id) if agent_id else None
            handle = info.get('handle') if info else None
            live = info is not None and handle is not None and not handle.is_done()
            items.append({
                'agent_id': agent_id,
                'session_id': agent_id,  # agent = session
                'model': row.get('model'),
                'reasoning_effort': (info or {}).get('reasoning_effort'),
                'status': 'live' if live else 'stopped',
                'handle_id': handle.id if live else None,
                'title': row.get('title'),
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                'live': live,
                'started': bool(handle and handle.is_started()) if live else False,
                'done': bool(handle and handle.is_done()) if info else True,
            })
        return {'agents': items}

    @request('stop_agent')
    async def on_stop_agent(self, source, data: dict) -> dict:
        """cascade-stop a live child. status 由 manager 内存管 (不持久化)."""
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        info = self._agents.get(agent_id)
        if info is not None:
            if agent_id in self._stopping:
                # already stopping (concurrent req): idempotent ok, don't
                # re-issue handle.stop() -> would hit "another start/stop in
                # flight". the in-flight stop will pop.
                _log.info('stop %s already in flight, idempotent ok', agent_id)
                return {'ok': True, 'agent_id': agent_id}
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                self._stopping.add(agent_id)
                try:
                    await handle.stop()
                except Exception as exc:
                    _log.warning('stop child %s failed: %r', agent_id, exc)
                    return {'ok': False, 'error': str(exc)}
                finally:
                    self._stopping.discard(agent_id)
            self._agents.pop(agent_id, None)
        else:
            # not live here: idempotent ok if it's a known historical agent,
            # else signal not-found so bridge can try the other manager.
            row = self._mem.get_agent(agent_id)
            if row is None:
                return {'ok': False, 'error': 'agent not found'}
        _log.info('stopped react agent: agent_id=%s', agent_id)
        return {'ok': True, 'agent_id': agent_id}

    @request('delete_agent')
    async def on_delete_agent(self, source, data: dict) -> dict:
        """删除 agent: 拒绝 live, 调 memory.delete_agent 删 agents + messages.

        删除前如果是 live 先拒 (前端应先 stop 再 delete). 删完不可恢复.
        """
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} is live; stop it first'}
        if self._mem.get_agent(agent_id) is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not found'}
        try:
            self._mem.delete_agent(agent_id)
        except Exception as exc:
            _log.warning('delete agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}
        _log.info('deleted react agent: agent_id=%s', agent_id)
        return {'ok': True, 'agent_id': agent_id}


# ======================================================================
# entry routine -- bridge calls this to spawn an agent
# ======================================================================

class CreateReactAgentInput(BaseModel):
    agent_id: Optional[str] = Field(
        None, description='Instance id; auto-generated when omitted. Reuses a stopped agent if it exists.',
    )
    model: Optional[str] = Field(
        None, description='LLM model key (provider/model_name, e.g. seed/glm-5.2). Defaults to models.yaml default when omitted.',
    )


class CreateReactAgentOutput(BaseModel):
    pass


class CreateReactAgent(Routine):
    """entry routine: spawn a react agent via the resident manager.

    bridge submits this routine (ctx.call) -> run locates the resident
    ReactAgents manager by name and reqs it to submit+start a ReactAgent
    child. returns the new agent_id.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': CreateReactAgentInput.model_json_schema(),
        'output_schema': CreateReactAgentOutput.model_json_schema(),
        'description': (
            'Entry routine that spawns a react agent via the resident '
            'ReactAgents manager. Returns the new agent_id. The spawned agent '
            'runs concurrently, isolated by agent_id.'
        ),
    }

    # req timeout: manager submit+start; normally < 1s. 10s generous ceiling.
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
        """locate the resident manager routine id by name."""
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
