"""FastAPI app 构造:HTTP 路由 + WS 端点.

由 ``server.WebServer._start_server`` 调用.设计成独立模块,让 server.py
只关心 routine 生命周期 + 框架事件订阅,@app 路由定义集中在此.

路由直接闭包捕获 WebServer instance(app 与 uvicorn server 同生命周期,
instance stop 即 server 优雅关闭, 路由不会 outlive instance).

HTTP 面最小化: 所有 routine 能力统一走 /run_routine (routine 本身就是
HTTP 与业务间的唯一契约), 专用路由只保留基础设施:

  /health                   健康检查
  /files/upload             通用文件上传 (raw body, ?name=)
  /files/download/{name}    强制附件下载
  /files/{name}             inline 读取 (图片/附件回显)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Dict
from uuid import uuid4

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

    @app.post('/files/upload')
    async def _files_upload(request: Request, name: str = ''):
        """统一文件上传: raw body + ``?name=foo.apk``, 流式落盘 runtime/uploads。

        不限类型 (图片/APK/日志/导出包同一通道), 上限 ``MAX_FILE_BYTES``,
        边写边算 sha256, 不把整个 body 读进内存。返回的 ``url`` 是 inline
        读取路径 (图片 ``<img src>`` 直接用), ``download`` 强制附件下载。
        """
        from app.routines.user.agents._core.uploads import (
            MAX_FILE_BYTES, UPLOADS_DIR, stored_name,
        )

        content_length = request.headers.get('content-length')
        if content_length:
            try:
                if int(content_length) > MAX_FILE_BYTES:
                    return _Json({'ok': False, 'error': 'file too large'}, status_code=413)
            except ValueError:
                return _Json({'ok': False, 'error': 'invalid content-length'}, status_code=400)

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        tmp = UPLOADS_DIR / f'.upload-{uuid4().hex}.part'
        digest = hashlib.sha256()
        size = 0
        try:
            with tmp.open('wb') as f:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        return _Json({'ok': False, 'error': 'file too large'}, status_code=413)
                    f.write(chunk)
                    digest.update(chunk)
            if size == 0:
                return _Json({'ok': False, 'error': 'empty body'}, status_code=400)
            # 内容寻址: 前缀用 sha256 前 12 位, 同内容重复上传落到同一个名字
            # (截图重放 / 幂等重传不堆文件), 不同内容同名文件也不会互相覆盖。
            stored = stored_name(
                name or request.query_params.get('name', ''),
                request.headers.get('content-type', ''),
                digest.hexdigest(),
            )
            target = UPLOADS_DIR / stored
            if target.is_file():
                tmp.unlink(missing_ok=True)
            else:
                os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)

        return _Json({
            'ok': True,
            'name': stored,
            'size': size,
            'sha256': digest.hexdigest(),
            # 服务端绝对路径: 本机调用方 (ipython kernel 里的 routine_bridge)
            # 要拿它当本地文件引用 (read_image / 归档), 前端用不到。
            'path': str(UPLOADS_DIR / stored),
            'url': f'/files/{stored}',
            'download': f'/files/download/{stored}',
        })

    @app.get('/files/download/{name}')
    async def _files_download(name: str):
        """强制附件下载 (Content-Disposition: attachment), MIME 按扩展名。"""
        from app.routines.user.agents._core.uploads import (
            UPLOADS_DIR, file_mime, safe_existing_name,
        )

        safe = safe_existing_name(name)
        if safe is None:
            return _Json({'ok': False, 'error': 'invalid file name'}, status_code=400)
        file = UPLOADS_DIR / safe
        if not file.is_file():
            return _Json({'ok': False, 'error': 'not found'}, status_code=404)
        return FileResponse(
            file,
            media_type=file_mime(safe),
            filename=safe,
            headers={'Cache-Control': 'no-store'},
        )

    @app.get('/files/{name}')
    async def _files(name: str):
        """inline 读取上传的文件 (图片消息 / agent 附件回显用)。

        消息里存的就是这个 ``/files/<name>`` 相对路径, 与 download 分开是
        因为 ``<img src>`` 需要 inline 语义, 而下载需要 attachment。
        """
        from app.routines.user.agents._core.uploads import (
            UPLOADS_DIR, file_mime, safe_existing_name,
        )
        safe = safe_existing_name(name)
        if safe is None:
            return _Json({'ok': False, 'error': 'invalid file name'}, status_code=400)
        file = UPLOADS_DIR / safe
        if not file.is_file():
            return _Json({'ok': False, 'error': 'not found'}, status_code=404)
        return FileResponse(file, media_type=file_mime(safe))

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
