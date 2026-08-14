"""WebServer ---- HTTP + WS 前门 routine (FastAPI + uvicorn).

职责:
- HTTP 端点:``/health`` ``/routines`` ``/builtin_skills`` ``/run/{name}``
  ``/agents/{id}/run/{name}`` ``/agents*``
  ``/docs``(路由定义见 ``app.build_app``)
- WS 端点:``/ws``----前端主通道,处理 user_input/run/agent/ui_request 等
  消息,广播 assistant_output/feedback/sys_prompt/session_changed 等框架事件

一个 uvicorn server 同时跑 HTTP + WS,监听地址由 ``WebserverInput``(pydantic
BaseModel)校验 ``run(kwargs)`` 收到的 yaml 条目 kwargs(host/port, 随注册注入
``is_passive`` 流动进来;类型归一+必填检查,run 内不再手写校验).
``/routines`` 走 ``self.get_routines()``(kernel catalog 跨 hub 全量).

passive 自动起,类级单例复用 uvicorn server(kernel reconnect 不重绑端口).
拆分参考:_json / app / agents 各管一摊,本模块只管 routine 生命周期 +
框架事件订阅 + WS 消息分发.
"""
from __future__ import annotations

import asyncio
import json
import logging
from asyncio import Event
from typing import Any, Dict, Optional
from uuid import uuid4

import uvicorn
from fastapi import WebSocket
from pydantic import BaseModel, Field

from routine import Routine, request, subscribe

from . import agents as _agents
from .app import build_app

logger = logging.getLogger(__name__)


class WebServerInput(BaseModel):
    """run() 入参声明:校验 + 类型归一(wire 数字经 proto number 往返回来是
    float,int 字段自动收 7781.0 → 7781)+ 必填/范围检查,一处声明全链不管."""

    host: str
    port: int = Field(ge=1, le=65535)


class WebServer(Routine):
    """HTTP + WS 前门:curl 触发任意 routine + WS 桥接前端."""

    meta = {'description': 'HTTP + WS 前门:curl 触发 routine / WS 桥接前端'}
    is_passive = True

    # 类级单例:kernel 每次 reconnect 都重新 auto-start passive(给新 rid →
    # 新 instance),但监听同一个端口只该起一次.首 instance 建共享 uvicorn
    # server,后续 instance 复用(不重绑端口,避免 winerror 10048).server 是
    # passive 常驻服务,生命周期跟进程一样长----instance 的 stop 只放行本 instance
    # 的 run(),不动共享 server(它活到进程结束).app 路由不绑死某个 instance----
    # 用类级 ``_active`` 投递(ctx 本质是 server 级 io 引用,instance 即使 stop 仍可投递).
    _shared_server: Optional['uvicorn.Server'] = None
    _active: Optional['WebServer'] = None        # 最新 start 的 instance,路由用它投递

    def __init__(self):
        super().__init__()
        self._stop_event = None
        # agent_id -> namespace 路由表,由 register_agent 填充
        self._agent_ns: dict[str, str] = {}
        # agent_id -> 显示名称
        self._agent_names: dict[str, str] = {}
        # agent_id -> agent routine id (for req'ing message history on reconnect).
        self._agent_rids: dict[str, str] = {}
        # ui_request req_id -> Future:routine 经 ctx.req('ui_request') 发起前端
        # 弹窗,广播 ui_request 后 await future,前端 ui_response 到达时 _resolve_ui 设值.
        self._pending_ui: dict[str, asyncio.Future] = {}
        # WS 客户端连接集合(broadcast 用)
        self._ws_clients: set[WebSocket] = set()

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        self._stop_event = Event()

    async def on_started(self) -> None:
        # 不需要挂.保留 hook 打日志.
        self._logger.info('[http/ws] bridge ready, ws clients: %d', len(self._ws_clients))

    async def run(self, kwargs: Dict[str, Any]):
        WebServer._active = self            # 路由经类级 instance 投递
        if WebServer._shared_server is None:
            self._start_shared(WebServerInput.model_validate(kwargs or {}))
        else:
            self._logger.info('🔌 http/ws server already running, reuse')
        await self._stop_event.wait()

    def _start_shared(self, cfg: WebServerInput) -> None:
        host, port = cfg.host, cfg.port
        addr = f'{host}:{port}'
        config = uvicorn.Config(
            build_app(WebServer), host=host, port=port,
            log_level='warning', access_log=False,
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        WebServer._shared_server = server
        self._logger.info('🔌 http/ws server on http://%s', addr)
        async def _serve():
            try:
                await server.serve()
            except SystemExit as exc:
                self._logger.error('http/ws server startup failed (exit %s) -- '
                                   'port busy? addr=%s', exc.code, addr)
                WebServer._shared_server = None
            except Exception as exc:
                self._logger.error('http/ws server error: %s', exc)
                WebServer._shared_server = None
        asyncio.create_task(_serve())

    async def stop(self) -> None:
        self._stop_event.set()

    # ------------------------------------------------------------------
    # 框架事件 -> WS 广播
    # ------------------------------------------------------------------

    @request('register_agent')
    async def on_register_agent(self, source, data: dict) -> dict:
        """agent 启动后主动 req 注册自己(替代 conversation_open pubsub)."""
        agent_id = str(data.get('agent_id') or '')
        namespace = str(data.get('namespace') or agent_id)
        name = str(data.get('name') or agent_id)
        routine_id = str(data.get('routine_id') or '') or None
        if agent_id:
            self._agent_ns[agent_id] = namespace
            self._agent_names[agent_id] = name
            if routine_id:
                self._agent_rids[agent_id] = routine_id
            self._logger.info('[bridge] register_agent: agent_id=%s namespace=%s',
                        agent_id, namespace)
            # user/world 是系统内部 agent, 注册但不广播 conversation_open (前端不显示)
            if agent_id not in ('user', 'world'):
                await self._broadcast({
                    'type': 'conversation_open',
                    'agent_id': agent_id,
                    'name': name,
                })
        return {'ok': True}

    @request('ui_request')
    async def on_ui_request(self, source, data: dict) -> dict:
        """routine -> bridge -> 前端 UI 请求,阻塞等用户响应后返回回执."""
        component = str(data.get('component') or '')
        props = data.get('props') or {}
        timeout = float(data.get('timeout') or 300)

        req_id = uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_ui[req_id] = fut

        await self._broadcast({
            'type': 'ui_request',
            'id': req_id,
            'component': component,
            'props': props,
        })
        self._logger.info('[bridge] ui_request id=%s component=%s', req_id, component)

        try:
            value = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            return {'ok': True, 'value': value}
        except asyncio.TimeoutError:
            await self._broadcast({'type': 'ui_cancel', 'id': req_id})
            return {
                'ok': False,
                'error': f'ui_request {req_id} timeout after {timeout}s',
                'timed_out': True,
            }
        finally:
            self._pending_ui.pop(req_id, None)

    def _resolve_ui(self, req_id: str, value: Any = None, error: str | None = None) -> None:
        """前端 ui_response 到达时 resolve 对应的 pending future."""
        fut = self._pending_ui.pop(req_id, None)
        if fut is None or fut.done():
            self._logger.warning('[bridge] no pending ui request for id=%s', req_id)
            return
        if error:
            fut.set_exception(RuntimeError(error))
        else:
            fut.set_result(value)
        self._logger.info('[bridge] ui resolved id=%s error=%s', req_id, error)

    @subscribe('assistant_output')
    async def on_assistant_output(self, source, data: dict) -> None:
        await self._broadcast({'type': 'assistant_output', **data})

    @subscribe('incoming_message')
    async def on_incoming_message(self, source, data: dict) -> None:
        await self._broadcast({'type': 'incoming_message', **data})

    @subscribe('feedback')
    async def on_feedback(self, source, data: dict) -> None:
        self._logger.info('[bridge] got feedback agent_id=%s', data.get('agent_id'))
        await self._broadcast({'type': 'feedback', **data})

    @subscribe('sys_prompt')
    async def on_sys_prompt(self, source, data: dict) -> None:
        self._logger.info('[bridge] got sys_prompt agent_id=%s', data.get('agent_id'))
        await self._broadcast({
            'type': 'sys_prompt',
            'epoch': data.get('epoch'),
            'message_id': data.get('message_id'),
            'agent_id': data.get('agent_id'),
            'messages': data.get('messages', []),
        })

    @subscribe('session_changed')
    async def on_session_changed(self, source, data: dict) -> None:
        self._logger.info('[bridge] got session_changed agent_id=%s', data.get('agent_id'))
        await self._broadcast({'type': 'session_changed', **data})

    @subscribe('usage')
    async def on_usage(self, source, data: dict) -> None:
        await self._broadcast({'type': 'usage', **data})

    # ------------------------------------------------------------------
    # WS 广播
    # ------------------------------------------------------------------

    async def _broadcast(self, data: dict) -> None:
        if not self._ws_clients:
            return
        payload = json.dumps(data, ensure_ascii=False)
        dead: set[WebSocket] = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    async def _broadcast_bytes(self, data: bytes) -> None:
        """广播二进制帧(用于 PCM 音频流)."""
        if not self._ws_clients:
            return
        dead: set[WebSocket] = set()
        for ws in list(self._ws_clients):
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.add(ws)
        self._ws_clients -= dead

    # ------------------------------------------------------------------
    # WS 客户端消息 -> 框架事件
    # ------------------------------------------------------------------

    async def _on_client_connected(self, reply) -> None:
        """新客户端连上时补发 conversation_open + 向 agent 拉会话历史."""
        for agent_id in self._agent_ns:
            name = self._agent_names.get(agent_id, agent_id)
            await reply({'type': 'conversation_open', 'agent_id': agent_id, 'name': name})
            rid = self._agent_rids.get(agent_id)
            if not rid:
                continue
            try:
                result = await self.ctx.req(
                    rid, 'get_history', {}, timeout=3.0,
                )
            except Exception as exc:
                logger.warning('[bridge] get_history (%s) failed: %r', agent_id, exc)
                continue
            if result.get('ok'):
                await reply({
                    'type': 'session_changed',
                    'agent_id': agent_id,
                    'session_id': result.get('session_id'),
                    'is_new': not bool(result.get('messages')),
                    'messages': result.get('messages', []),
                })

    async def _on_client_message(self, msg: dict, reply) -> None:
        msg_type = msg.get('type')
        if msg_type == 'user_input':
            text = str(msg.get('text') or '').strip()
            if text:
                agent_id = str(msg.get('agent_id') or 'main')
                namespace = self._agent_ns.get(agent_id, agent_id)
                await self.publish('user_input', {'text': text}, namespace=namespace)
        elif msg_type == 'interrupt':
            agent_id = str(msg.get('agent_id') or 'main')
            namespace = self._agent_ns.get(agent_id, agent_id)
            await self.publish('interrupt', {}, namespace=namespace)
        elif msg_type == 'set_effort':
            agent_id = str(msg.get('agent_id') or 'main')
            rid = self._agent_rids.get(agent_id)
            req_id = msg.get('id')
            if not rid:
                await reply({'type': 'set_effort_result', 'id': req_id,
                             'ok': False, 'error': 'agent not found'})
                return
            try:
                result = await self.call(rid, 'set_effort', {'effort': msg.get('effort')})
                await reply({'type': 'set_effort_result', 'id': req_id, **result})
            except Exception as exc:
                await reply({'type': 'set_effort_result', 'id': req_id,
                             'ok': False, 'error': str(exc)})
        elif msg_type == 'get_routines':
            await self._on_get_routines(reply)
        elif msg_type == 'run':
            asyncio.create_task(self._on_run(msg, reply))
        elif msg_type == 'create_agent':
            asyncio.create_task(_agents.on_create_agent(self, msg, reply))
        elif msg_type == 'resume_agent':
            asyncio.create_task(_agents.on_resume_agent(self, msg, reply))
        elif msg_type == 'list_agents':
            asyncio.create_task(_agents.on_list_agents(self, msg, reply))
        elif msg_type == 'stop_agent':
            asyncio.create_task(_agents.on_stop_agent(self, msg, reply))
        elif msg_type == 'delete_agent':
            asyncio.create_task(_agents.on_delete_agent(self, msg, reply))
        elif msg_type == 'ui_response':
            self._resolve_ui(msg.get('id', ''), msg.get('value'), msg.get('error'))
        elif msg_type == 'audio_playback_done':
            pass
        else:
            logger.warning('unknown ws message type: %s', msg_type)

    async def _on_run(self, msg: dict, reply) -> None:
        """统一执行入口,根据 format 分发."""
        fmt = str(msg.get('format') or '').strip()
        req_id = msg.get('id')
        try:
            if fmt == 'form':
                name = str(msg.get('name') or '').strip()
                kwargs = msg.get('kwargs') or {}
                if not name:
                    await reply({'type': 'run_result', 'id': req_id,
                                 'error': 'name is empty'})
                    return
                result = await self.call(name, kwargs)
                await reply({'type': 'run_result', 'id': req_id,
                             'name': name, 'result': result})
            else:
                await reply({'type': 'run_result', 'id': req_id,
                             'error': f'unknown format: {fmt!r}'})
        except Exception as exc:
            await reply({'type': 'run_result', 'id': req_id, 'error': str(exc)})

    async def _on_get_routines(self, reply) -> None:
        """返回跨 hub 全量 routine 列表(走 kernel catalog)."""
        try:
            routines = await self.get_routines()
        except NotImplementedError:
            await reply({'type': 'routines', 'routines': []})
            return
        except Exception as exc:
            logger.warning('[bridge] get_routines error: %s', exc)
            await reply({'type': 'routines', 'routines': []})
            return
        out = []
        for r in routines:
            meta = r.get('meta') or {}
            out.append({
                'name': r.get('name', ''),
                'is_passive': bool((r.get('is_passive') or {}).get('flag', False)),
                'hub_id': r.get('hub_id', ''),
                'meta': meta,
            })
        await reply({'type': 'routines', 'routines': out})
