"""FastAPI app 构造:所有 HTTP 路由 + WS 端点.

由 ``server.WebServer._start_server`` 调用.设计成独立模块,让 server.py
只关心 routine 生命周期 + 框架事件订阅,@app 路由定义集中在此.

路由直接闭包捕获 WebServer instance(app 与 uvicorn server 同生命周期,
instance stop 即 server 优雅关闭, 路由不会 outlive instance).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import Body, FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import agents as _agents
from ._json import _Json

logger = logging.getLogger(__name__)


def build_app(inst) -> FastAPI:
    """构造 FastAPI app.``inst`` 是 WebServer instance(路由闭包直接引用)."""

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

    @app.post('/upload')
    async def _upload(request: Request):
        """图片上传: 原始 body 直读 (multipart 解析留给前端 FormData 不做,
        直接收二进制 + content-type 定扩展), 落盘 runtime/uploads/.
        返回 {'ok', path, url}: path 是 '/files/<name>', 消息里引用它."""
        from app.routines.user.agents._core.uploads import (
            ext_from_mime, save_image,
        )
        body = await request.body()
        if not body:
            return _Json({'ok': False, 'error': 'empty body'})
        ext = ext_from_mime(request.headers.get('content-type', ''))
        if not ext:
            name = request.query_params.get('name', '')
            if '.' in name:
                ext = name.rsplit('.', 1)[1].lower()
        try:
            path = save_image(body, ext or 'png')
        except ValueError as exc:
            return _Json({'ok': False, 'error': str(exc)})
        return _Json({'ok': True, 'path': path, 'url': path})

    @app.get('/files/{name}')
    async def _files(name: str):
        """访问上传的图片资源 (消息里的 images 路径渲染用)."""
        from app.routines.user.agents._core.uploads import UPLOADS_DIR, _MIME_BY_EXT
        if '/' in name or '\\' in name or '..' in name:
            return _Json({'ok': False, 'error': 'invalid file name'})
        file = UPLOADS_DIR / name
        if not file.is_file():
            return _Json({'ok': False, 'error': 'not found'})
        ext = file.suffix.lstrip('.').lower()
        return FileResponse(file, media_type=_MIME_BY_EXT.get(ext, 'application/octet-stream'))

    @app.get('/models')
    async def _models():
        """暴露 models.yaml 配置, 供前端 create agent 时选模型.

        返回结构: {default: 'seed/glm-5.2', models: [
            {key, provider, name, model, max_context, reasoning_effort, has_tools}
        ]}
        """
        try:
            from app.routines.user.agents._core import models_store
            models = []
            for key, cfg in models_store.flatten().items():
                provider = key.split('/', 1)[0] if '/' in key else key
                reasoning_cfg = cfg.get('reasoning')
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
            return _Json({'default': models_store.default_key(),
                          'models': models})
        except Exception as exc:
            logger.warning('[http] /models error: %s', exc)
            return _Json({'default': '', 'models': [], 'error': str(exc)})

    @app.get('/routines')
    async def _routines():
        """跨 hub 全量 routine 列表(走 kernel catalog,跟 WS get_routines 一致)."""
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

    @app.get('/library_skills')
    async def _library_skills():
        try:
            from app.routines.user.skills.registry import list_library_skills, list_prime_skills
            skills = list_library_skills()
            prime_skills = list_prime_skills()
            return _Json({'ok': True, 'skills': skills, 'prime_skills': prime_skills})
        except Exception as exc:
            return _Json({'ok': False, 'error': str(exc), 'skills': [], 'prime_skills': []})

    @app.get('/tree')
    async def _tree():
        """模块树静态定义 (转发 tree/get routine), 前端模块树编辑器读取."""
        try:
            return _Json(await inst.call('tree/get'))
        except Exception as exc:
            logger.warning('[http] /tree error: %s', exc)
            return _Json({'ok': False, 'error': str(exc), 'modules': {}})

    @app.post('/tree/apply')
    async def _tree_apply(body: Dict[str, Any] = Body(default_factory=dict)):
        """模块树编辑事务 (转发 tree/apply routine): kernel + tree.json 即时生效.

        body: {op: add|remove|reorder|rename, ...}.
        """
        try:
            return _Json(await inst.call('tree/apply', body))
        except Exception as exc:
            logger.warning('[http] /tree/apply error: %s', exc)
            return _Json({'ok': False, 'error': str(exc)})

    async def _execute_routine(name: str, kwargs: Dict[str, Any],
                                agent_id: str) -> _Json:
        """查 agent rid → req(rid, 'run', {target, kwargs}) 执行 routine.

        agent 注入 from_agent_id 后 call; rid 查询走两跳快查询
        (passive 直击 get_running_routines, 不骑 routine 生命周期).
        """
        from app.routines.user.agents._core.rid import resolve_agent_rid
        try:
            agent_rid, _ = await resolve_agent_rid(inst, agent_id)
        except Exception as exc:
            return _Json({'ok': False, 'error': f'resolve agent rid failed: {exc}'})
        if not agent_rid:
            return _Json({'ok': False, 'error': f"agent '{agent_id}' not live or not found"})
        try:
            result = await inst.req(
                agent_rid, 'run',
                {'target': name, 'kwargs': kwargs}, timeout=600.0,
            )
            return _Json(result)
        except Exception as exc:
            return _Json({'ok': False, 'error': str(exc)})

    @app.post('/run_routine')
    async def _run_routine(body: Dict[str, Any] = Body(default_factory=dict),
                           x_agent_id: str = Header(default='user')):
        """以调用方 agent 身份执行 routine, 名字走 body (格式自由, 可含 '/').

        body: {name: '...', kwargs: {...}}.
        X-Agent-Id: 调用方 agent id (routine_bridge/IPython 注入
        ZERO_AGENT_ID), 缺省 user.
        """
        name = str(body.get('name') or '').strip()
        kwargs = body.get('kwargs') or {}
        if not name:
            return _Json({'ok': False, 'error': 'name is empty'})
        if not isinstance(kwargs, dict):
            return _Json({'ok': False, 'error': 'kwargs must be an object'})
        agent_id = (x_agent_id or 'user').strip() or 'user'
        return await _execute_routine(name, kwargs, agent_id)

    @app.get('/run_routine')
    async def _run_routine_get(name: str = '', kwargs: str = '',
                                x_agent_id: str = Header(default='user')):
        """GET 版 (只读 routine 浏览器直开调试).

        /run_routine?name=pi/list_models&kwargs={"k":"v"}
        (kwargs 为 JSON 字符串, 缺省 {}).
        """
        name = (name or '').strip()
        if not name:
            return _Json({'ok': False, 'error': 'name is empty'})
        try:
            kw = json.loads(kwargs) if kwargs.strip() else {}
        except json.JSONDecodeError as exc:
            return _Json({'ok': False, 'error': f'kwargs invalid json: {exc}'})
        if not isinstance(kw, dict):
            return _Json({'ok': False, 'error': 'kwargs must be an object'})
        agent_id = (x_agent_id or 'user').strip() or 'user'
        return await _execute_routine(name, kw, agent_id)

    # ---- agents: HTTP 等价物,复用 _on_* 内部方法 ----
    # WS handler 签名是 (msg, reply),reply 是 async (dict) -> None.
    # HTTP 端用 box dict 收集 reply 的内容,最后 _Json(box) 返回.
    # 这样 WS / HTTP 走同一份业务 logic,不重复实现.

    async def _run_ws_handler(handler, msg: dict) -> dict:
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
