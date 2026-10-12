"""FastAPI app 构造:HTTP 路由 + WS 端点.

由 ``server.WebServer._start_server`` 调用.设计成独立模块,让 server.py
只关心 routine 生命周期 + 框架事件订阅,@app 路由定义集中在此.

路由直接闭包捕获 WebServer instance(app 与 uvicorn server 同生命周期,
instance stop 即 server 优雅关闭, 路由不会 outlive instance).

HTTP 面最小化: 所有 routine 能力统一走 /run_routine (routine 本身就是
HTTP 与业务间的唯一契约), 专用路由只保留基础设施 (健康/文件传输).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import Body, FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

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
