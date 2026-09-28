"""agent_hub ---- 被动的 agent 类型注册中心 (hub + 查询 bridge).

与 models hub 同构, 纯被动: 不扫描 catalog 不猜谁是 agent ---- 各类型
manager 启动时主动 req register 提交自己的 manifest, 停止时 unregister
注销; 前端经 bridge 查 list 动态渲染创建表单.

register 顺带记录注册方 rid ---- hub 因此也是 live agent 聚合的唯一来源:
agent_hub/running_agents / send_message 等 rid 解析方只需问 hub 一跳
(req 'live_agents'), 不再各自维护 manager 名单 (新 manager 注册即被发现,
跨语言 manager 经 kernel 路由同样成立).

  - AgentHub (name 'agent_hub', passive 常驻): 内存 kind → manifest
      注册表, req events: register {manifest} (upsert) / unregister {kind}
      / list / live_agents (聚合各 manager + passive 常驻 agent) /
      agent_rid {agent_id} (反查单个 agent 的 rid) / agent_state {agent_id}
      (转发目标 agent 的 agent_state, 运行时状态)
  - AgentHubTypes (name 'agent_hub/types', 一次性 bridge): run 内解析
      hub rid 后 req 转发 list ---- 前端统一按 name call.
  - AgentHubRunningAgents (name 'agent_hub/running_agents', 一次性 bridge):
      同上转发 live_agents ---- 列出全部运行中的 agent.
  - AgentHubRid (name 'agent/rid', 一次性 bridge): 同上转发 agent_rid ----
      按 agent_id 反查 routine_id.
  - AgentHubState (name 'agent/state', 一次性 bridge): 同上转发
      agent_state ---- 按 agent_id 拿运行时状态.
"""
from __future__ import annotations

import asyncio
from asyncio import Event
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine, request

__all__ = ['AgentHub', 'AgentHubTypes', 'AgentHubRunningAgents',
           'AgentHubRid', 'AgentHubState']

# passive 常驻 agent: routine name 即 agent_id 即 agent_type (无 manager 归属,
# hub 聚合时直接按 name 查 running).
_PASSIVE_AGENTS = [
    ('user_agent', 'user'),
    ('world_agent', 'world'),
]


async def _find_rid_by_name(routine: Routine, name: str) -> Optional[str]:
    """按 name 查 running routine 的 rid (单次, 不重试 ---- 竞态重试在调用方)."""
    for r in await routine.get_running_routines():
        if str(r.get('name') or '') == name:
            rid = str(r.get('id') or '').strip()
            if rid:
                return rid
    return None


class AgentHub(Routine):
    """agent 类型注册中心: 纯被动注册表, manager 主动注册/注销."""

    name = 'agent_hub'
    is_passive = True

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': (
            'agent 类型注册中心 (passive 常驻, 纯被动): 各类型 manager 启动时 '
            '主动 register 提交 manifest, 停止时 unregister 注销. req events: '
            'register {manifest} (upsert, 键 = manifest.kind) / unregister '
            '{kind} / list (全部已注册 manifest) / live_agents (聚合各 manager '
            '的 live agent + passive 常驻 agent).'
        ),
    }

    def __init__(self):
        super().__init__()
        self._stop_event: Optional[Event] = None
        # kind -> manifest (内存注册表; hub 重启即清空, manager 重启会重新注册)
        self._types: Dict[str, Dict[str, Any]] = {}
        # kind -> 注册 manager rid (register 时由 source 记录; manager 重启
        # 重新注册即自愈, 死 rid 在 live_agents 聚合时被 req 失败淘汰)
        self._manager_rids: Dict[str, str] = {}

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._stop_event = Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    # ── req handlers ──

    @request('ping')
    async def on_ping(self, source, data) -> Dict[str, Any]:
        """存活探测 (manager 周期对账用, 极轻)."""
        return {'ok': True}

    @request('register')
    async def on_register(self, source, data) -> Dict[str, Any]:
        kind = str((data or {}).get('kind') or '').strip()
        if not kind:
            raise ValueError('manifest.kind: non-empty string required')
        rid = str((source and source.id) or '').strip()
        if rid:
            # 拒绝已死 source 的注册: manager 实例被替换后, 旧实例遗留的
            # loop task 仍可能拿死 rid 来注册, 覆盖掉新实例的活 rid ----
            # live_agents 聚合从此寻址死 rid, 全部 agent 失联.
            running_ids = {
                str(r.get('id') or '')
                for r in await self.get_running_routines()
            }
            if rid not in running_ids:
                raise ValueError(
                    f'register rejected: source routine {rid} not running '
                    f'(stale manager loop?)'
                )
        self._types[kind] = data
        if rid:
            self._manager_rids[kind] = rid
        else:
            self._manager_rids.pop(kind, None)
        return {'ok': True, 'kind': kind}

    @request('unregister')
    async def on_unregister(self, source, data) -> Dict[str, Any]:
        kind = str((data or {}).get('kind') or '')
        self._types.pop(kind, None)
        self._manager_rids.pop(kind, None)
        return {'ok': True, 'kind': kind}

    @request('list')
    async def on_list(self, source, data) -> Dict[str, Any]:
        types = sorted(self._types.values(),
                       key=lambda m: str(m.get('kind', '')))
        return {'types': types}

    @request('live_agents')
    async def on_live_agents(self, source, data) -> Dict[str, Any]:
        """聚合全部 live agent: 逐个已注册 manager req live_agents (失败即跳过,
        死 manager 不挡别人) + passive 常驻 agent (按 name 查 running).

        item 形状与 agent_hub/running_agents 出口一致:
        {agent_id, routine_id, agent_type (kind)}.
        """
        agents: list = []
        for kind, rid in list(self._manager_rids.items()):
            try:
                resp = await self.req(rid, 'live_agents', {}, timeout=5.0)
            except Exception:
                continue
            agents.extend(
                {'agent_id': it.get('agent_id'), 'routine_id': it.get('routine_id'),
                 'agent_type': kind}
                for it in (resp or {}).get('agents') or []
                if it.get('agent_id') and it.get('routine_id')
            )
        name_to_rid = {
            str(r.get('name') or ''): str(r.get('id') or '')
            for r in await self.get_running_routines()
        }
        for routine_name, passive_id in _PASSIVE_AGENTS:
            rid = name_to_rid.get(routine_name)
            if rid:
                agents.append({'agent_id': passive_id, 'routine_id': rid,
                               'agent_type': passive_id})
        return {'agents': agents}


    @request('agent_rid')
    async def on_agent_rid(self, source, data) -> Dict[str, Any]:
        """按 agent_id 反查 (routine_id, agent_type); 未找到 routine_id=None."""
        agent_id = str((data or {}).get('agent_id') or '').strip()
        if not agent_id:
            raise ValueError('agent_id: non-empty string required')
        agents = (await self.on_live_agents(source, {})).get('agents') or []
        for it in agents:
            if it.get('agent_id') == agent_id:
                return {'ok': True, 'agent_id': agent_id,
                        'routine_id': it.get('routine_id'),
                        'agent_type': it.get('agent_type')}
        return {'ok': False, 'agent_id': agent_id,
                'error': f'agent {agent_id} not live or not found'}

    @request('agent_state')
    async def on_agent_state(self, source, data) -> Dict[str, Any]:
        """按 agent_id 拿 agent 运行时状态 (转发目标 agent 的 agent_state).

        agent_rid 解析 rid 后 req 目标 agent, 重试一次应对事件循环偶发
        调度延迟. 返回 {ok, agent_id, skill_dir, project_root, ...}.
        """
        agent_id = str((data or {}).get('agent_id') or '').strip()
        if not agent_id:
            raise ValueError('agent_id: non-empty string required')
        rid = (await self.on_agent_rid(source, data)).get('routine_id')
        if not rid:
            return {'ok': False,
                    'error': f'agent {agent_id} not live or not found'}
        state = None
        last_exc: Optional[Exception] = None
        for attempt in (0, 1):
            try:
                state = await self.req(rid, 'agent_state', {}, timeout=5.0)
                break
            except Exception as exc:
                last_exc = exc
        if state is None:
            return {'ok': False,
                    'error': f'req agent_state failed: {last_exc}'}
        if not isinstance(state, dict):
            return {'ok': False,
                    'error': f'agent_state returned non-dict: {state!r}'}
        return {
            'ok': True,
            'agent_id': state.get('agent_id') or agent_id,
            'skill_dir': state.get('skill_dir'),
            'skill_index_cache_dir': state.get('skill_index_cache_dir'),
            'project_root': state.get('project_root'),
            'session_id': state.get('session_id'),
            'tts_instruction': state.get('tts_instruction'),
        }


class AgentHubTypes(Routine):
    """查 agent 类型清单 (agent_hub 查询 bridge): 各类型 manager 自声明 manifest."""

    name = 'agent_hub/types'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '列出全部已注册 agent 类型 manifest. 返回 {types: '
            '[{kind, label, routines, create_fields, uses_model, ...}]}, '
            '前端据此动态渲染创建表单.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        for _ in range(30):
            rid = await _find_rid_by_name(self, AgentHub.name)
            if rid:
                return await self.req(rid, 'list', {})
            await asyncio.sleep(0.1)
        raise RuntimeError(
            'agent_hub routine not running (name: agent_hub)')


class AgentHubRunningAgentsInput(BaseModel):
    pass


class AgentHubRunningAgentsOutput(BaseModel):
    agents: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='live agent 列表. 每个 item: {agent_id, routine_id, agent_type}',
    )


class AgentHubRunningAgents(Routine):
    """列出所有运行中的 agent (各类型 + user/world) ---- agent_hub 查询 bridge."""

    name = 'agent_hub/running_agents'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '列出所有运行中的 agent (各类型 + user/world). 返回 agent_id + routine_id + agent_type.',
        'input_schema': AgentHubRunningAgentsInput.model_json_schema(),
        'output_schema': AgentHubRunningAgentsOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        for _ in range(30):
            rid = await _find_rid_by_name(self, AgentHub.name)
            if rid:
                return await self.req(rid, 'live_agents', {})
            await asyncio.sleep(0.1)
        raise RuntimeError(
            'agent_hub routine not running (name: agent_hub)')


class AgentHubRidInput(BaseModel):
    agent_id: str = Field(description='要查的 agent_id')


class AgentHubRidOutput(BaseModel):
    ok: bool
    agent_id: str
    routine_id: str | None = None
    agent_type: str | None = None
    error: str | None = None


class AgentHubRid(Routine):
    """按 agent_id 反查 live agent 的 routine_id ---- agent_hub 查询 bridge."""

    name = 'agent/rid'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '按 agent_id 反查运行中的 agent 的 routine_id (agent_hub 查询 bridge).',
        'input_schema': AgentHubRidInput.model_json_schema(),
        'output_schema': AgentHubRidOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = AgentHubRidInput.model_validate(kwargs)
        for _ in range(30):
            rid = await _find_rid_by_name(self, AgentHub.name)
            if rid:
                return await self.req(rid, 'agent_rid',
                                      {'agent_id': inp.agent_id})
            await asyncio.sleep(0.1)
        raise RuntimeError(
            'agent_hub routine not running (name: agent_hub)')


class AgentHubStateInput(BaseModel):
    agent_id: str = Field(description='agent 的唯一身份id')


class AgentHubStateOutput(BaseModel):
    ok: bool
    agent_id: str | None = None
    skill_dir: str | None = None
    skill_index_cache_dir: str | None = None
    project_root: str | None = None
    session_id: str | None = None
    tts_instruction: str | None = None
    error: str | None = None


class AgentHubState(Routine):
    """按 agent_id 拿 agent 运行时状态 ---- agent_hub 查询 bridge."""

    name = 'agent/state'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '按 agent_id 拿 agent 运行时状态 (skill_dir / project_root / '
            'session_id 等). 内部经 agent/rid 查 rid 再 req 目标 agent.'
        ),
        'input_schema': AgentHubStateInput.model_json_schema(),
        'output_schema': AgentHubStateOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = AgentHubStateInput.model_validate(kwargs)
        for _ in range(30):
            rid = await _find_rid_by_name(self, AgentHub.name)
            if rid:
                return await self.req(rid, 'agent_state',
                                      {'agent_id': inp.agent_id})
            await asyncio.sleep(0.1)
        raise RuntimeError(
            'agent_hub routine not running (name: agent_hub)')
