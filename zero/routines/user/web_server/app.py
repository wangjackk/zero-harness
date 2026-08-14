"""FastAPI app 构造:所有 HTTP 路由 + WS 端点.

由 ``server.WebServer._start_shared`` 调用.设计成独立模块,让 server.py
只关心 routine 生命周期 + 框架事件订阅,@app 路由定义集中在此.

路由内通过 ``WebServer._active`` 类级引用拿实例
(passive routine 重启时类级单例复用 uvicorn server,instance 可能换).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import agents as _agents
from ._json import _Json

logger = logging.getLogger(__name__)


def build_app(server_cls) -> FastAPI:
    """构造 FastAPI app.``server_cls`` 是 WebServer 类(用其类级 _active/_handles)."""

    app = FastAPI(title='zero', docs_url='/docs', redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=['*'],
        allow_methods=['*'],
        allow_headers=['*'],
    )

    # ------------------------------------------------------------------
    # HTTP 路由
    # ------------------------------------------------------------------

    @app.get('/health')
    async def _health():
        return _Json({'ok': True})

    @app.get('/models')
    async def _models():
        """暴露 models.yaml 配置, 供前端 create agent 时选模型.

        返回结构: {default: 'seed/glm-5.2', models: [
            {key, provider, name, model, max_context, reasoning_effort, has_tools}
        ]}
        """
        try:
            from zero.routines.user.agents._core.llm import _MODELS, _DEFAULT_MODEL
            models = []
            for key, cfg in _MODELS.items():
                provider = key.split('/', 1)[0] if '/' in key else key
                extra = cfg.get('extra') or {}
                reasoning_cfg = extra.get('reasoning') if isinstance(extra, dict) else None
                effort = (
                    reasoning_cfg.get('effort')
                    if isinstance(reasoning_cfg, dict) else None
                )
                models.append({
                    'key': key,
                    'provider': provider,
                    'name': cfg.get('model', key),
                    'max_context': int(cfg.get('max_context', 0)),
                    'reasoning_effort': effort,
                    'has_tools': bool(cfg.get('tools')),
                })
            return _Json({'default': _DEFAULT_MODEL, 'models': models})
        except Exception as exc:
            logger.warning('[http] /models error: %s', exc)
            return _Json({'default': '', 'models': [], 'error': str(exc)})

    @app.get('/routines')
    async def _routines():
        """跨 hub 全量 routine 列表(走 kernel catalog,跟 WS get_routines 一致)."""
        inst = server_cls._active
        if inst is None:
            return _Json({'routines': []})
        try:
            routines = await inst.get_routines()
        except NotImplementedError:
            return _Json({'routines': []})
        except Exception as exc:
            logger.warning('[http] /routines error: %s', exc)
            return _Json({'routines': []})
        out = []
        for r in routines:
            meta = r.get('meta') or {}
            out.append({
                'name': r.get('name', ''),
                'is_passive': bool((r.get('is_passive') or {}).get('flag', False)),
                'hub_id': r.get('hub_id', ''),
                'meta': meta,
            })
        return _Json({'routines': out})

    @app.get('/builtin_skills')
    async def _builtin_skills():
        try:
            from zero.routines.user.skills.registry import list_builtin_skills, list_prime_skills
            skills = list_builtin_skills()
            prime_skills = list_prime_skills()
            return _Json({'ok': True, 'skills': skills, 'prime_skills': prime_skills})
        except Exception as exc:
            return _Json({'ok': False, 'error': str(exc), 'skills': [], 'prime_skills': []})

    @app.post('/run/{name}')
    async def _run(name: str,
                   body: Dict[str, Any] = Body(default_factory=dict)):
        """以 user agent 身份执行 routine (curl 测试入口).

        内部固定走 user agent: 查 user rid → req(rid, 'run', {target, kwargs}).
        agent 内部 self.call 注入 from_agent_id='user'.
        """
        import time
        t0 = time.perf_counter()
        inst = server_cls._active
        if inst is None:
            return _Json({'ok': False, 'error': 'no active WebServer instance'})
        try:
            listing = await inst.ctx.call('list_running_agents')
        except Exception as exc:
            return _Json({'ok': False, 'error': f'list_running_agents failed: {exc}'})
        t1 = time.perf_counter()
        agents = (listing or {}).get('agents') or []
        agent_rid = None
        for a in agents:
            if a.get('agent_id') == 'user':
                agent_rid = a.get('rid')
                break
        if not agent_rid:
            return _Json({'ok': False, 'error': "agent 'user' not live or not found"})
        try:
            result = await inst.ctx.req(
                agent_rid, 'run',
                {'target': name, 'kwargs': body}, timeout=600.0,
            )
            t2 = time.perf_counter()
            logger.info('[latency] /run/%s: list=%.1fms req=%.1fms total=%.1fms',
                        name, (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000)
            return _Json(result)
        except Exception as exc:
            return _Json({'ok': False, 'error': str(exc)})

    @app.post('/agents/{agent_id}/run/{name}')
    async def _agent_run(agent_id: str,
                         name: str,
                         body: Dict[str, Any] = Body(default_factory=dict)):
        """以指定 agent 身份执行 routine. HTTP 只转发, agent 内部执行.

        查 agent rid → req(rid, 'run', {target, kwargs}) → agent 自己 call.
        """
        import time
        t0 = time.perf_counter()
        inst = server_cls._active
        if inst is None:
            return _Json({'ok': False, 'error': 'no active WebServer instance'})
        # 查 agent rid
        try:
            listing = await inst.ctx.call('list_running_agents')
        except Exception as exc:
            return _Json({'ok': False, 'error': f'list_running_agents failed: {exc}'})
        t1 = time.perf_counter()
        agents = (listing or {}).get('agents') or []
        agent_rid = None
        for a in agents:
            if a.get('agent_id') == agent_id:
                agent_rid = a.get('rid')
                break
        if not agent_rid:
            return _Json({'ok': False, 'error': f'agent {agent_id!r} not live or not found'})
        # 转发给 agent, agent 内部 self.call 注入 from_agent_id
        try:
            result = await inst.ctx.req(
                agent_rid, 'run',
                {'target': name, 'kwargs': body}, timeout=600.0,
            )
            t2 = time.perf_counter()
            logger.info('[latency] /agents/%s/run/%s: list=%.1fms req=%.1fms total=%.1fms',
                        agent_id, name, (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000)
            return _Json(result)
        except Exception as exc:
            return _Json({'ok': False, 'error': str(exc)})

    # ---- agents: HTTP 等价物,复用 _on_* 内部方法 ----
    # WS handler 签名是 (msg, reply),reply 是 async (dict) -> None.
    # HTTP 端用 box dict 收集 reply 的内容,最后 _Json(box) 返回.
    # 这样 WS / HTTP 走同一份业务 logic,不重复实现.

    async def _run_ws_handler(handler, msg: dict) -> dict:
        inst = server_cls._active
        if inst is None:
            return {'ok': False, 'error': 'no active WebServer instance'}
        box: dict = {}

        async def reply(d: dict) -> None:
            box.update(d)

        await handler(inst, msg, reply)
        return box

    @app.get('/agents')
    async def _http_agents():
        """列出所有 agent(prime + user/world)."""
        return _Json(await _run_ws_handler(_agents.on_list_agents, {}))

    @app.post('/agents/create')
    async def _http_agent_create(body: Dict[str, Any] = Body(default_factory=dict)):
        """创建 agent.body:kind/project_dir/agent_id/model/..."""
        return _Json(await _run_ws_handler(_agents.on_create_agent, body))

    @app.post('/agents/stop')
    async def _http_agent_stop(body: Dict[str, Any] = Body(default_factory=dict)):
        """停止 agent.body:{agent_id}."""
        return _Json(await _run_ws_handler(_agents.on_stop_agent, body))

    @app.post('/agents/resume')
    async def _http_agent_resume(body: Dict[str, Any] = Body(default_factory=dict)):
        """恢复 agent.body:{kind, agent_id, model, ...}."""
        return _Json(await _run_ws_handler(_agents.on_resume_agent, body))

    @app.post('/agents/delete')
    async def _http_agent_delete(body: Dict[str, Any] = Body(default_factory=dict)):
        """删除 agent (DB 行 + messages). body:{agent_id}. live 拒绝."""
        return _Json(await _run_ws_handler(_agents.on_delete_agent, body))

    # ------------------------------------------------------------------
    # WS 端点
    # ------------------------------------------------------------------

    @app.websocket('/ws')
    async def _ws_endpoint(ws: WebSocket):
        """WS 主通道:前端 <-> 框架事件桥."""
        await ws.accept()
        inst = server_cls._active
        if inst is None:
            await ws.close(code=1011, reason='no active WebServer instance')
            return
        inst._ws_clients.add(ws)
        logger.info('[ws] client connected, total=%d', len(inst._ws_clients))

        async def _reply(data: dict) -> None:
            await ws.send_text(json.dumps(data, ensure_ascii=False))

        try:
            await inst._on_client_connected(_reply)
        except Exception as exc:
            logger.warning('[ws] connect_handler error: %s', exc)

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await ws.send_text(json.dumps({'type': 'error', 'message': 'invalid json'}))
                    continue
                await inst._on_client_message(msg, _reply)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning('[ws] connection error: %s', exc)
        finally:
            inst._ws_clients.discard(ws)
            logger.info('[ws] client disconnected, total=%d', len(inst._ws_clients))

    return app
