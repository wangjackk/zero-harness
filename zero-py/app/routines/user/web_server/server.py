"""WebServer ---- HTTP + WS 前门 routine (FastAPI + uvicorn).

职责:
- HTTP 端点:``/health`` ``/routines`` ``/library_skills`` ``/run_routine``
  (POST/GET, 名字走 body/查询参数, 格式自由可含 ``/``; X-Agent-Id 头指定调用方身份) ``/agents*``
  ``/docs``(路由定义见 ``app.build_app``)
- WS 端点:``/ws``----前端主通道,处理 user_input/run/agent/ui_request 等
  消息,广播 assistant_output/feedback/sys_prompt/session_changed 等框架事件

一个 uvicorn server 同时跑 HTTP + WS,监听地址默认读 ``app/config.yaml``
(``network.web_server``,``WebServerInput`` pydantic default_factory 取值;
类型归一+范围检查,run 内不再手写校验).
``/routines`` 走 ``self.get_routines()``(kernel catalog 跨 hub 全量).

passive 自动起,uvicorn server 由 instance 持有: run 起 / stop 优雅关
(should_exit → serve 退出, 5s 硬取消兜底). reload 与其他 routine 同语义:
kernel 停老实例(server 随之优雅关)→ auto-start 新实例重绑端口, 无共享
单例无复用分支; 重复 start 撞端口直接报错.
拆分参考:_json / app / agents 各管一摊,本模块只管 routine 生命周期 +
框架事件订阅 + WS 消息分发.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from asyncio import Event
from typing import Any, Dict, Optional
from uuid import uuid4

import uvicorn
from app.config import network
from fastapi import WebSocket
from pydantic import BaseModel, Field

from routine import Routine, request, subscribe
from app.routines.user.agents._core.rid import resolve_agent_rid, resolve_all_agents
from . import agents as _agents
from .app import build_app

__all__ = ['WebServer']

logger = logging.getLogger(__name__)


class WebServerInput(BaseModel):
    """run() 入参声明:校验 + 类型归一(wire 数字经 proto number 往返回来是
    float,int 字段自动收 7781.0 → 7781)+ 范围检查,一处声明全链不管.
    缺省值取 app/config.yaml (network.web_server) ---- 网络配置唯一事实源."""

    host: str = Field(default_factory=lambda: network().web_server.host)
    port: int = Field(
        default_factory=lambda: network().web_server.port, ge=1, le=65535)


class WebServer(Routine):
    """HTTP + WS 前门:curl 触发任意 routine + WS 桥接前端."""

    meta = {'description': 'HTTP + WS 前门:curl 触发 routine / WS 桥接前端'}
    is_passive = True

    def __init__(self):
        super().__init__()
        self._stop_event = None
        # uvicorn server + serve() 任务: instance 持有, run 起 / stop 优雅关
        self._server: Optional['uvicorn.Server'] = None
        self._serve_task: Optional[asyncio.Task] = None
        # agent_id -> 显示名称
        self._agent_names: dict[str, str] = {}
        # ui_request req_id -> Future:routine 经 ctx.req('ui_request') 发起前端
        # 弹窗,广播 ui_request 后 await future,前端 ui_response 到达时 _resolve_ui 设值.
        self._pending_ui: dict[str, asyncio.Future] = {}
        # WS 客户端连接集合(broadcast 用)
        self._ws_clients: set[WebSocket] = set()
        # agent_id -> 最新 sys_prompt 全量快照(WS 只发增量通知, HTTP 按需拉全量)
        self._sys_prompts: dict[str, dict] = {}
        # routine 树本地状态(id -> {id, parent_id, name, state}), lifecycle
        # 观察流帧驱动; 新 WS 客户端连上补发快照(增量帧只广播一次不重放).
        self._routine_tree: dict[str, dict] = {}
        # lifecycle 事件历史(服务端缓存, 刷新后前端时间轨恢复用).
        # 上限与前端一致(600); t 为服务端收帧时刻(ms).
        self._lifecycle_history: list[dict] = []
        # lifecycle 观察流消费任务(on_started 起, stop 取消)
        self._lifecycle_task: Optional[asyncio.Task] = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        self._stop_event = Event()

    async def on_started(self) -> None:
        # lifecycle 观察流: web_server 自己是 routine, 直接 watch 拿帧
        # (快照+增量), 更新本地树后广播. kernel 侧 conn 级去重, 增量帧
        # 每个 conn 只发一次, 这里是唯一消费点.
        self._lifecycle_task = asyncio.create_task(self._lifecycle_pump())
        self._logger.info('[http/ws] bridge ready, ws clients: %d', len(self._ws_clients))

    async def _lifecycle_pump(self) -> None:
        q = await self.ctx.watch_lifecycle()
        while True:
            f = await q.get()
            t, rid = f.get('type'), str(f.get('id'))
            pid, name = str(f.get('parent_id')), str(f.get('name'))
            if t == 'created':
                self._routine_tree[rid] = {'id': rid, 'parent_id': pid,
                                           'name': name, 'state': 'created'}
            elif t == 'started':
                self._routine_tree[rid] = {'id': rid, 'parent_id': pid,
                                           'name': name, 'state': 'running'}
            else:  # stopped / destroyed
                self._routine_tree.pop(rid, None)
            self._lifecycle_history.append({
                't': time.time() * 1000, 'type': t,
                'id': rid, 'parent_id': pid, 'name': name, 'seq': f.get('seq'),
            })
            if len(self._lifecycle_history) > 600:
                del self._lifecycle_history[:len(self._lifecycle_history) - 600]
            await self._broadcast({
                'type': 'lifecycle', 'event_type': t,
                'id': rid, 'parent_id': pid, 'name': name,
                'seq': f.get('seq'),
            })

    async def stop(self) -> None:
        if self._lifecycle_task:
            self._lifecycle_task.cancel()
            self._lifecycle_task = None
        # 优雅关 server: should_exit → serve() 关监听 socket + 逐个关 WS 连接,
        # ASGI 任务经正常 disconnect 出口结束; 5s 超时硬取消兜底.
        server, task = self._server, self._serve_task
        self._server = self._serve_task = None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except Exception:
                task.cancel()
        self._stop_event.set()

    async def run(self, kwargs: Dict[str, Any]):
        self._start_server(WebServerInput.model_validate(kwargs or {}))
        await self._stop_event.wait()

    def _start_server(self, cfg: WebServerInput) -> None:
        host, port = cfg.host, cfg.port
        # bridge 回拨地址: bind 地址里的通配 (0.0.0.0 / ::) 不是可连接地址,
        # 归一成 loopback, 免得 routine_bridge 连 http://0.0.0.0:port 报
        # ConnectError. 对外访问照旧走真实网卡 IP.
        connect_host = '127.0.0.1' if host in ('0.0.0.0', '', '::') else host
        # bridge 地址事实源: 回拨地址写进进程 env, IPython tool 透传给
        # kernel 子进程, routine_bridge 读 ZERO_HTTP_ADDR 回拨本 server.
        # 端口默认来自 app/config.yaml (network.web_server).
        os.environ['ZERO_HTTP_ADDR'] = f'{connect_host}:{port}'
        config = uvicorn.Config(
            build_app(self), host=host, port=port,
            log_level='warning', access_log=False,
            lifespan='off',  # 无 lifespan 事件, 免退出噪音
            # 图片以 base64 data URL 内联进 content, 大图会超
            # uvicorn 默认 ws 消息上限
            ws_max_size=32 * 1024 * 1024,
        )
        server = uvicorn.Server(config)
        # uvicorn>=0.30: install_signal_handlers 已移除, 信号捕获在 serve() 内的
        # capture_signals(); guest server 不抢进程 SIGINT (Ctrl+C 归 app 入口),
        # 否则信号被吞进 uvicorn 优雅关闭, 主循环收不到退不出.
        server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
        self._server = server
        self._logger.info('🔌 http/ws server on http://%s:%s (bridge %s)',
                          host, port, connect_host)

        async def _serve():
            try:
                await server.serve()
            except SystemExit as exc:
                self._logger.error('http/ws server startup failed (exit %s) -- '
                                   'port busy? addr=%s:%s', exc.code, host, port)
            except Exception as exc:
                self._logger.error('http/ws server error: %s', exc)
            finally:
                # server 挂了(端口占用等): 放行 run(), 让 instance 走正常停止,
                # 不留僵尸常驻.
                self._stop_event.set()

        self._serve_task = asyncio.create_task(_serve())

    # ------------------------------------------------------------------
    # 框架事件 -> WS 广播
    # ------------------------------------------------------------------

    @request('register_agent')
    async def on_register_agent(self, source, data: dict) -> dict:
        """agent 启动后主动 req 注册自己(替代 conversation_open pubsub)."""
        agent_id = str(data.get('agent_id') or '')
        name = str(data.get('name') or agent_id)
        if agent_id:
            self._agent_names[agent_id] = name
            self._logger.info('[bridge] register_agent: agent_id=%s', agent_id)
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

    @subscribe('voice')
    async def on_voice(self, source, data: dict) -> None:
        """voice_listener 转发: 语音总线事件 (wake/result/aborted/state) → 前端."""
        await self._broadcast({'type': 'voice', **data})

    @subscribe('incoming_message')
    async def on_incoming_message(self, source, data: dict) -> None:
        await self._broadcast({'type': 'incoming_message', **data})

    @subscribe('show_image')
    async def on_show_image(self, source, data: dict) -> None:
        """show_image routine 的图片推流: base64 全量直接广播给前端渲染."""
        await self._broadcast({'type': 'show_image', **data})

    @subscribe('feedback')
    async def on_feedback(self, source, data: dict) -> None:
        self._logger.info('[bridge] got feedback agent_id=%s', data.get('agent_id'))
        await self._broadcast({'type': 'feedback', **data})

    @subscribe('agent_status')
    async def on_agent_status(self, source, data: dict) -> None:
        """agent 瞬态状态 (如 compaction 进行中) → 前端状态条."""
        await self._broadcast({'type': 'agent_status', **data})

    @subscribe('sys_prompt')
    async def on_sys_prompt(self, source, data: dict) -> None:
        """全量快照只留本地缓存; WS 只广播轻量通知(不带 messages),
        前端收到通知后经 HTTP GET /agents/{id}/sys_prompt 拉全量."""
        agent_id = str(data.get('agent_id') or '')
        if agent_id:
            self._sys_prompts[agent_id] = {
                'agent_id': agent_id,
                'epoch': data.get('epoch'),
                'message_id': data.get('message_id'),
                'messages': data.get('messages', []),
            }
        messages = data.get('messages') or []
        await self._broadcast({
            'type': 'sys_prompt',
            'epoch': data.get('epoch'),
            'message_id': data.get('message_id'),
            'agent_id': data.get('agent_id'),
            'count': len(messages),
        })

    @request('sys_prompt')
    async def on_sys_prompt_req(self, source, data: dict) -> dict:
        """sys_prompt 全量快照 (缓存读口, sys_prompt routine 经 req 调)."""
        agent_id = str(data.get('agent_id') or '')
        snap = self._sys_prompts.get(agent_id)
        if snap is None:
            return {'agent_id': agent_id, 'messages': []}
        return snap

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
        # routine 树当前状态 + 事件历史(增量帧不重放, 刷新/重连靠快照恢复)
        if self._routine_tree or self._lifecycle_history:
            await reply({'type': 'lifecycle_snapshot',
                         'routines': list(self._routine_tree.values()),
                         'events': list(self._lifecycle_history)})
        # 动态解析 live agents(不依赖易失的内存注册表, web_server 重载后依然可用)
        agents = await resolve_all_agents(self)
        for item in agents:
            agent_id = str(item.get('agent_id') or '')
            rid = item.get('routine_id')
            if not agent_id or not rid:
                continue
            name = self._agent_names.get(agent_id, agent_id)
            await reply({'type': 'conversation_open', 'agent_id': agent_id, 'name': name})
            try:
                result = await self.req(
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
                    # usage 回填: 前端刷新后圆环立即恢复 (无轮次时 None)
                    'usage': result.get('usage'),
                })

    async def _on_client_message(self, msg: dict, reply) -> None:
        msg_type = msg.get('type')
        if msg_type == 'run_routine':
            self._logger.info(f'start run routine: {msg}')
            # asyncio.create_task(self.call('sound_effect', kwargs={'effect': 'start'}))
            # 发消息也走这里: run_routine send_message (content 数组,
            # 前端图片以 data: URL 内联, 前后端可跨设备).
            asyncio.create_task(self._on_run_routine(msg, reply))
        elif msg_type == 'interrupt_run':
            # RoutineRunner 停止: 打断调用方 agent 当前 run 的目标
            # (fire-and-forget, run_routine_result 会因打断自行到达).
            agent_id = str(msg.get('agent_id') or 'user').strip() or 'user'
            try:
                rid, _ = await resolve_agent_rid(self, agent_id)
                if rid:
                    await self.req(rid, 'interrupt', {}, timeout=10.0)
            except Exception as exc:
                logger.warning('[bridge] interrupt_run (%s) failed: %s',
                               agent_id, exc)
        elif msg_type == 'interrupt':
            agent_id = str(msg.get('agent_id') or 'main')
            logger.info('[ws] interrupt: agent_id=%s', agent_id)
            result = await self.call('interrupt_agent', kwargs={'to': agent_id})
            if not result.get('ok'):
                logger.warning('[bridge] interrupt (%s) failed: %s',
                               agent_id, result.get('error'))
        elif msg_type == 'set_effort':
            agent_id = str(msg.get('agent_id') or 'main')
            rid, _ = await resolve_agent_rid(self, agent_id)
            req_id = msg.get('id')
            if not rid:
                await reply({'type': 'set_effort_result', 'id': req_id,
                             'ok': False, 'error': 'agent not found'})
                return
            try:
                result = await self.req(rid, 'set_effort',
                                        {'effort': msg.get('effort')},
                                        timeout=10.0)
                await reply({'type': 'set_effort_result', 'id': req_id, **result})
            except Exception as exc:
                await reply({'type': 'set_effort_result', 'id': req_id,
                             'ok': False, 'error': str(exc)})
        elif msg_type == 'get_routines':
            await self._on_get_routines(reply)
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

    async def _on_run_routine(self, msg: dict, reply) -> None:
        """以调用方 agent 身份执行 routine (与 HTTP /run_routine 同语义).

        msg: {name, kwargs, agent_id?} -- agent_id 缺省 user.
        回包 {type: 'run_routine_result', id, name, result|error}.
        """
        req_id = msg.get('id')
        name = str(msg.get('name') or '').strip()

        try:
            kwargs = msg.get('kwargs') or {}
            if not name:
                raise ValueError('name is empty')
            if not isinstance(kwargs, dict):
                raise ValueError('kwargs must be an object')
            from app.routines.user.agents._core.rid import resolve_agent_rid
            agent_id = str(msg.get('agent_id') or 'user').strip() or 'user'
            agent_rid, _ = await resolve_agent_rid(self, agent_id)
            if not agent_rid:
                raise ValueError(f"agent '{agent_id}' not live or not found")
            result = await self.req(
                agent_rid, 'run',
                {'target': name, 'kwargs': kwargs}, timeout=600.0)
            self._logger.info(f'run routine stopped: {result}')
            await reply({'type': 'run_routine_result', 'id': req_id,
                        'name': name, 'result': result})
        except Exception as exc:
            await reply({'type': 'run_routine_result', 'id': req_id,
                        'name': name, 'error': str(exc)})

        # asyncio.create_task(self.call('sound_effect', kwargs={'effect': 'stop'}))

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
