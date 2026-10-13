"""FlowManager ---- flow agent 生命周期 manager (passive, 对齐 prime manager 形式).

职责边界: FlowHub 是纯前门(HTTP/WS 协议适配), agent 生命周期(spawn/stop/
list/delete + 直击 agent 的转发)集中在本 manager:

- spawn: ``create('flow_agent', kwargs)`` 直起, 不经 preset 与 sqlite Store
- 存储: 集中索引 ``~/.zero/flow-agents.json`` (发现不扫盘) + workspace 内
  agent.json (hub 元数据); EventLog JSONL 仍是唯一真相
- 变更通知: ``publish('agents_changed' / 'conversation_open')`` ---- 前门
  订阅后广播给自己的 WS 客户端
- rid 解析热路径: ``live_agents`` (fetch_agent_state -> resolve_agent_rid)
"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from routine import Routine, request
from routine.logger import setup_logger

from .eventlog import derive_history, derive_surface_from, load_events
from .llm import DEFAULT_MODEL
from .stats import derive_stats
from .wire import MODE_PRIORITY, workspace_for

_log = setup_logger('flow.manager')

_AGENT_ROUTINE = 'flow_agent'
_REQ_TIMEOUT = 10.0


class FlowManager(Routine):
    """resident passive manager: create/list/stop/delete flow agent children."""

    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': 'Resident manager for flow agents. Spawns/lists/stops '
                       'flow agent children; front door (FlowHub) forwards '
                       'lifecycle ops here.',
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop = asyncio.Event()
        # agent_id -> {routine_id, handle, session_id, project_dir, model, ...}
        self._agents: Dict[str, dict] = {}
        self._index: Optional[Dict[str, dict]] = None

    async def run(self, kwargs: Dict[str, Any]) -> None:
        _log.info('flow manager started')
        await self._stop.wait()

    async def stop(self) -> None:
        for info in list(self._agents.values()):
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                try:
                    await handle.stop()
                except Exception as exc:
                    _log.warning('stop child on manager stop: %r', exc)
        self._agents.clear()
        self._stop.set()

    # ── agent 注册 (agent 启动后 req 过来, 补充 manager 重启场景) ──

    @request('register_agent')
    async def on_register_agent(self, source, data: dict) -> dict:
        agent_id = str((data or {}).get('agent_id') or '')
        routine_id = str((data or {}).get('routine_id') or '')
        if agent_id and routine_id:
            info = self._agents.setdefault(agent_id, {})
            info['routine_id'] = routine_id
            _log.info('registered agent %s (rid=%s)', agent_id, routine_id)
            await self.publish('conversation_open', {
                'agent_id': agent_id,
                'name': str((data or {}).get('name') or agent_id),
            })
        return {'ok': True}

    @request('live_agents')
    async def on_live_agents(self, source, data: dict) -> dict:
        """纯内存 live agent 快照 [{agent_id, routine_id}] ---- rid 解析热路径
        (fetch_agent_state -> resolve_agent_rid) 专用, 与 prime manager 同契约."""
        return {'agents': [
            {'agent_id': aid, 'routine_id': info['routine_id']}
            for aid, info in self._agents.items() if info.get('routine_id')
        ]}

    @request('list_agents')
    async def on_list_agents(self, source, data: dict) -> dict:
        return {'agents': self._list_items()}

    # ── agent 生命周期 ──

    @request('create_agent')
    async def on_create_agent(self, source, data: dict) -> dict:
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if agent_id and self._find_meta(agent_id) is not None:
            return {'ok': False,
                    'error': f'agent {agent_id} already exists; resume instead'}
        if not agent_id:
            agent_id = f'flow_{uuid4().hex[:6]}'
        return await self._spawn(
            agent_id=agent_id, session_id=uuid4().hex, data=data,
            is_resume=False)

    @request('resume_agent')
    async def on_resume_agent(self, source, data: dict) -> dict:
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent {agent_id} already live'}
        r = await self._ensure_live(agent_id, data)
        return r or {'ok': False, 'error': f'agent {agent_id} already live'}

    async def _ensure_live(self, agent_id: str, data: dict) -> Optional[dict]:
        """not live 但索引有历史: 自动拉起 (发消息即激活).

        前端无 stop/resume 概念 ---- 用户对 stopped 会话发消息, manager 静默
        resume 后投递. 已 live 返回 None; 无历史记录返回 error dict.
        """
        if agent_id in self._agents:
            return None
        meta = self._find_meta(agent_id)
        if meta is None:
            return {'ok': False, 'error': f'agent {agent_id} not found'}
        merged = {**meta, **{k: v for k, v in data.items()
                             if k not in ('type', 'id', 'message', 'mode')
                             and v is not None}}
        return await self._spawn(
            agent_id=agent_id,
            session_id=str(meta.get('session_id') or uuid4().hex),
            data=merged, is_resume=True)

    async def _spawn(self, *, agent_id: str, session_id: str, data: dict,
                     is_resume: bool) -> dict:
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent {agent_id} already live'}
        # workspace 归属: 目录规范化 (dsh workspace = canonical path)
        project_dir = data.get('project_dir') or data.get('project_dir_root_path')
        if project_dir:
            project_dir = str(Path(str(project_dir)).resolve())
        child_kwargs: Dict[str, Any] = {
            'agent_id': agent_id,
            'agent_name': 'Flow',
            'session_id': session_id,
            'project_dir_root_path': project_dir,
            'model': data.get('model'),
            'plan_mode': bool(data.get('plan_mode', False)),
            'extra_instructions': data.get('extra_instructions'),
            'max_turns': data.get('max_turns'),
            # 与 prime 对齐: 显式列表 (ipython 控制环境 + skill 目录加载)
            'enabled_tools': ['ipython'],
            'condense_config': data.get('condense_config'),
        }
        try:
            handle = await self.create(_AGENT_ROUTINE, child_kwargs)
            await handle.start()
        except Exception as exc:
            _log.error('spawn flow agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}

        meta = {
            'agent_id': agent_id,
            'session_id': session_id,
            'project_dir': project_dir,
            'model': data.get('model') or DEFAULT_MODEL,
            'plan_mode': bool(data.get('plan_mode', False)),
            'title': str(data.get('title') or ''),
            'created_at': time.time(),
        }
        self._agents[agent_id] = {**meta, 'routine_id': handle.id, 'handle': handle}
        self._write_meta(agent_id)
        action = 'resumed' if is_resume else 'created'
        _log.info('%s flow agent %s (rid=%s session=%s)',
                  action, agent_id, handle.id, session_id)
        await self.publish('agents_changed')
        return {'ok': True, 'agent_id': agent_id,
                'routine_id': handle.id, 'session_id': session_id}

    @request('stop_agent')
    async def on_stop_agent(self, source, data: dict) -> dict:
        agent_id = str((data or {}).get('agent_id') or '')
        info = self._agents.get(agent_id)
        if info is None:
            return {'ok': False, 'error': f'agent {agent_id} not live'}
        try:
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                await handle.stop()
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        self._agents.pop(agent_id, None)
        await self.publish('agents_changed')
        return {'ok': True, 'agent_id': agent_id}

    @request('delete_agent')
    async def on_delete_agent(self, source, data: dict) -> dict:
        """删除 agent: 清 workspace (EventLog + agent.json). 不可恢复."""
        agent_id = str((data or {}).get('agent_id') or '')
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent {agent_id} is live; stop it first'}
        meta = self._find_meta(agent_id)
        if meta is None:
            return {'ok': False, 'error': f'agent {agent_id} not found'}
        ws = workspace_for(agent_id, meta.get('project_dir'))
        try:
            if ws.exists():
                shutil.rmtree(ws)
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        self._index_remove(agent_id)
        await self.publish('agents_changed')
        return {'ok': True, 'agent_id': agent_id}

    # ── 直击 agent 的请求 (前门转发面) ──

    @request('chat_message')
    async def on_chat_message(self, source, data: dict) -> dict:
        """三档路由 (dsh inbox 语义): followup / steer / inject.

        not live 但有历史: 自动 resume 后投递 (发消息即激活, 调用方无感)."""
        data = data or {}
        agent_id = str(data.get('agent_id') or '')
        mode = str(data.get('mode') or 'followup')
        priority = MODE_PRIORITY.get(mode)
        if priority is None:
            return {'ok': False, 'error': f'unknown mode: {mode!r}'}
        r = await self._ensure_live(agent_id, data)
        if r is not None and not r.get('ok'):
            return r
        text = str(data.get('message') or '')
        if mode == 'followup' and text.strip():
            await self._set_title_if_empty(agent_id, text)
        from .._core.messaging import build_content
        return await self._req_agent(agent_id, 'chat_message',
                                     {'content': build_content(text),
                                      'priority': priority,
                                      'id': data.get('id')})

    @request('interrupt')
    async def on_interrupt(self, source, data: dict) -> dict:
        return await self._req_agent(
            str((data or {}).get('agent_id') or ''), 'interrupt', {})

    @request('compact')
    async def on_compact(self, source, data: dict) -> dict:
        return await self._req_agent(
            str((data or {}).get('agent_id') or ''), 'compact', {})

    @request('set_effort')
    async def on_set_effort(self, source, data: dict) -> dict:
        return await self._req_agent(
            str((data or {}).get('agent_id') or ''), 'set_effort',
            {'effort': (data or {}).get('effort')})

    @request('get_history')
    async def on_get_history(self, source, data: dict) -> dict:
        """live: 转发 agent; stopped: 事件流只读重放 (dsh projcache 派生路)."""
        agent_id = str((data or {}).get('agent_id') or '')
        if agent_id not in self._agents:
            meta = self._load_index().get(agent_id)
            if meta is not None:
                return self._history_from_disk(meta)
        return await self._req_agent(agent_id, 'get_history', {})

    @request('connect_snapshot')
    async def on_connect_snapshot(self, source, data: dict) -> dict:
        """WS client 连接快照: agents 全量 + 每个 live agent 的当前历史."""
        sessions = []
        for agent_id, info in self._agents.items():
            if not info.get('routine_id'):
                continue
            result = await self._req_agent(agent_id, 'get_history', {})
            if result.get('ok'):
                sessions.append({
                    'agent_id': agent_id,
                    'session_id': result.get('session_id'),
                    'is_new': not bool(result.get('messages')),
                    'messages': result.get('messages', []),
                    'stats': result.get('stats'),
                })
        return {'agents': self._list_items(), 'sessions': sessions}

    async def _req_agent(self, agent_id: str, op: str, data: dict) -> dict:
        rid = (self._agents.get(agent_id) or {}).get('routine_id')
        if not rid:
            return {'ok': False, 'error': f'agent {agent_id} not live'}
        try:
            return dict(await self.ctx.req(rid, op, data,
                                           timeout=_REQ_TIMEOUT) or {})
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    async def _set_title_if_empty(self, agent_id: str, text: str) -> None:
        """首条 followup 消息作为会话标题 (dsh session title 语义)."""
        info = self._agents.get(agent_id)
        if info is None or info.get('title'):
            return
        info['title'] = text[:48].replace('\n', ' ')
        self._write_meta(agent_id)
        await self.publish('agents_changed')

    def _list_items(self) -> list[dict]:
        items: dict[str, dict] = {}
        for agent_id, meta in self._load_index().items():
            items[agent_id] = {**meta, 'status': 'stopped', 'live': False}
        for agent_id, info in self._agents.items():
            handle = info.get('handle')
            items[agent_id] = {
                **{k: v for k, v in info.items() if k != 'handle'},
                'routine_id': handle.id if handle else None,
                'status': 'live', 'live': True,
                'done': handle.is_done() if handle else True,
            }
        return list(items.values())

    # ── 集中索引 (dsh storages/workspace.json 角色: 发现不扫盘) ──

    def _find_meta(self, agent_id: str) -> Optional[dict]:
        """live 表或集中索引里找 agent 元数据."""
        info = self._agents.get(agent_id)
        if info is not None:
            return {k: v for k, v in info.items() if k != 'handle'}
        return self._load_index().get(agent_id)

    @property
    def _index_path(self) -> Path:
        return Path.home() / '.zero' / 'flow-agents.json'

    def _load_index(self) -> Dict[str, dict]:
        """agent_id -> {workspace, agent_id, session_id, project_dir, ...}."""
        if self._index is None:
            try:
                data = json.loads(
                    self._index_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                data = None
            self._index = data if isinstance(data, dict) else {}
        return self._index

    def _save_index(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(self._load_index(), ensure_ascii=False, indent=2),
            encoding='utf-8')

    def _index_record(self, agent_id: str, meta: dict) -> None:
        ws = workspace_for(agent_id, meta.get('project_dir'))
        self._load_index()[agent_id] = {
            'agent_id': agent_id,
            'session_id': meta.get('session_id'),
            'project_dir': meta.get('project_dir'),
            'workspace': str(ws),
            'title': meta.get('title') or '',
            'model': meta.get('model'),
            'created_at': meta.get('created_at'),
        }
        self._save_index()

    def _index_remove(self, agent_id: str) -> None:
        if self._load_index().pop(agent_id, None) is not None:
            self._save_index()

    @staticmethod
    def _history_from_disk(meta: dict) -> dict:
        """已停止 agent 的历史: 事件流只读重放 (dsh projcache 派生路)."""
        ws = Path(str(meta.get('workspace') or ''))
        sid = str(meta.get('session_id') or '')
        events = load_events(ws / 'sessions' / f'{sid}.jsonl') if ws and sid else []
        return {
            'ok': True, 'agent_id': meta.get('agent_id'), 'session_id': sid,
            'messages': derive_history(derive_surface_from(events)),
            'stats': derive_stats(events),
        }

    def _write_meta(self, agent_id: str) -> None:
        """agent.json (workspace 内) + 集中索引: hub 元数据 (EventLog 仍是唯一真相)."""
        info = self._agents.get(agent_id)
        if info is None:
            return
        meta = {k: v for k, v in info.items() if k not in ('handle', 'routine_id')}
        ws = workspace_for(agent_id, info.get('project_dir'))
        try:
            ws.mkdir(parents=True, exist_ok=True)
            (ws / 'agent.json').write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as exc:
            _log.warning('write agent.json (%s): %r', ws, exc)
        self._index_record(agent_id, meta)
