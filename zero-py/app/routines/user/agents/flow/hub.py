"""FlowHub ---- flow 生态自己的前门 routine (纯 HTTP + WS 协议适配).

agent 生命周期在 FlowManager (对齐 prime manager 形式): hub 只做
WS/HTTP 协议 ↔ manager req 的适配 + agent pubsub 事件的 WS 广播.
与 web_server / PrimeAgentManager / preset / sqlite Store 零耦合:
- 持久化与 spawn 全在 manager 侧 (EventLog JSONL 唯一真相, 无 DB)
- 前端: Vue 独立页面 (frontend/web/src/flow, http://localhost:5173/flow),
  直连本 hub; hub 只提供 API

HTTP: ``/health``, ``/models`` (自有 llm 配置, 不回 api_key).
WS ``/ws`` 消息: list_agents / create_agent / resume_agent / stop_agent /
delete_agent / chat_message(mode 三档) / interrupt / compact /
set_effort / get_history / pick_dir_native ---- 生命周期与直击 agent 的
操作全部转发 flow_manager.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import sys
from asyncio import Event
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routine import PassiveConfig, Routine, subscribe
from routine.logger import setup_logger

from app.config import network

from .llm import DEFAULT_MODEL, _MODELS

_log = setup_logger('flow.hub')

_MANAGER_NAME = 'flow_manager'
_REQ_TIMEOUT = 10.0

# ws 消息 type -> manager req op -> 回给前端的 resp type
_RESP_TYPE = {
    'create_agent': 'agent_created',
    'resume_agent': 'agent_resumed',
    'stop_agent': 'agent_stopped',
    'delete_agent': 'agent_deleted',
    'chat_message': 'chat_result',
    'interrupt': 'interrupt_result',
    'compact': 'compact_result',
    'set_effort': 'set_effort_result',
    'get_history': 'history',
}


class FlowHubInput(BaseModel):
    """缺省值取 app/config.yaml (network.flow) ---- 网络配置唯一事实源."""

    host: str = Field(default_factory=lambda: network().flow.host)
    port: int = Field(default_factory=lambda: network().flow.port, ge=1, le=65535)


class FlowHub(Routine):
    """flow 前门: 独立端口, WS/HTTP ↔ flow_manager req 协议适配."""

    # passive + auto-start 默认入参 (app/config.yaml network.flow)
    is_passive = PassiveConfig(kwargs={
        'host': network().flow.host,
        'port': network().flow.port,
    })

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': FlowHubInput.model_json_schema(),
        'output_schema': {'type': 'object'},
        'description': 'Standalone front door for flow agents (HTTP+WS+UI).',
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop_event: Optional[Event] = None
        # uvicorn server + serve() 任务: instance 持有, run 起 / stop 优雅关
        self._server: Optional['uvicorn.Server'] = None
        self._serve_task: Optional[asyncio.Task] = None
        self._ws_clients: set[WebSocket] = set()
        self._mgr_rid: Optional[str] = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._stop_event = Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        cfg = FlowHubInput.model_validate(kwargs or {})
        app = _build_app(self)
        config = uvicorn.Config(
            app, host=cfg.host, port=cfg.port,
            log_level='warning', access_log=False,
            lifespan='off',  # 无 lifespan 事件, 免退出噪音
        )
        server = uvicorn.Server(config)
        # guest server 不抢进程 SIGINT (Ctrl+C 归 app 入口), 屏蔽
        # capture_signals() 的信号劫持 (install_signal_handlers 已移除).
        server.capture_signals = contextlib.nullcontext  # type: ignore[method-assign]
        self._server = server
        self._logger.info('flow hub on http://%s:%d', cfg.host, cfg.port)

        async def _serve() -> None:
            try:
                await server.serve()
            except SystemExit as exc:
                # uvicorn bind 失败走 sys.exit(STARTUP_FAILURE), SystemExit
                # 继承 BaseException, except Exception 接不住 ---- 穿透会把
                # 整个 loop 上的任务炸成 traceback 噪音.
                self._logger.error('flow hub startup failed (exit %s) -- '
                                   'port busy? addr=%s:%s',
                                   exc.code, cfg.host, cfg.port)
            except Exception as exc:
                self._logger.error('flow hub server error: %r', exc)
            finally:
                # server 挂了(端口占用等): 放行 run(), 不留僵尸常驻.
                self._stop_event.set()

        self._serve_task = asyncio.create_task(_serve())
        await self._stop_event.wait()

    async def stop(self) -> None:
        # agent children 归 FlowManager 停; 这里只关前门自身:
        # should_exit → serve() 关监听 socket + 逐个关 WS 连接,
        # 5s 超时硬取消兜底.
        server, task = self._server, self._serve_task
        self._server = self._serve_task = None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except Exception:
                task.cancel()
        if self._stop_event is not None:
            self._stop_event.set()

    # ── manager 解析 + 转发 ──

    async def _find_manager(self) -> Optional[str]:
        if self._mgr_rid is None:
            for r in await self.ctx.get_running_routines():
                if str(r.get('name') or '') == _MANAGER_NAME:
                    self._mgr_rid = str(r.get('id') or '') or None
                    break
        return self._mgr_rid

    async def _mgr_req(self, op: str, data: dict) -> dict:
        """req flow_manager (rid 按名解析 + 缓存; 失败清缓存重解析一次)."""
        for attempt in range(2):
            rid = await self._find_manager()
            if rid is None:
                return {'ok': False, 'error': f'{_MANAGER_NAME} not running'}
            try:
                return dict(await self.ctx.req(
                    rid, op, data, timeout=_REQ_TIMEOUT) or {})
            except Exception as exc:
                if attempt:
                    return {'ok': False, 'error': str(exc)}
                self._mgr_rid = None  # manager 可能换实例, 重解析重试
        return {'ok': False, 'error': 'unreachable'}

    # ── 框架事件 → WS 广播 ──

    @subscribe('assistant_output')
    async def on_assistant_output(self, source, data: dict) -> None:
        await self._broadcast({'type': 'assistant_output', **(data or {})})

    @subscribe('feedback')
    async def on_feedback(self, source, data: dict) -> None:
        await self._broadcast({'type': 'feedback', **(data or {})})

    @subscribe('usage')
    async def on_usage(self, source, data: dict) -> None:
        await self._broadcast({'type': 'usage', **(data or {})})

    @subscribe('stats')
    async def on_stats(self, source, data: dict) -> None:
        await self._broadcast({'type': 'stats', **(data or {})})

    @subscribe('session_changed')
    async def on_session_changed(self, source, data: dict) -> None:
        await self._broadcast({'type': 'session_changed', **(data or {})})

    @subscribe('incoming_message')
    async def on_incoming_message(self, source, data: dict) -> None:
        await self._broadcast({'type': 'incoming_message', **(data or {})})

    @subscribe('turn_event')
    async def on_turn_event(self, source, data: dict) -> None:
        await self._broadcast({'type': 'turn_event', **(data or {})})

    # manager 生命周期变更 → WS 广播
    @subscribe('agents_changed')
    async def on_agents_changed(self, source, data: dict) -> None:
        await self._broadcast({'type': 'agents_changed'})

    @subscribe('conversation_open')
    async def on_conversation_open(self, source, data: dict) -> None:
        await self._broadcast({'type': 'conversation_open', **(data or {})})

    # ── WS 客户端 ──

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

    async def _on_client_connected(self, reply) -> None:
        snap = await self._mgr_req('connect_snapshot', {})
        await reply({'type': 'agents', 'agents': snap.get('agents') or []})
        for s in snap.get('sessions') or []:
            await self._broadcast({'type': 'session_changed', **s})

    async def _on_client_message(self, msg: dict, reply) -> None:
        t = msg.get('type')
        resp_type = _RESP_TYPE.get(str(t))
        if resp_type is not None:
            result = await self._mgr_req(str(t), msg)
            await reply({'type': resp_type, 'id': msg.get('id'), **result})
        elif t == 'list_agents':
            result = await self._mgr_req('list_agents', {})
            await reply({'type': 'agents', 'id': msg.get('id'),
                         'agents': result.get('agents') or []})
        elif t == 'pick_dir_native':
            await self._pick_dir_native(msg, reply)
        else:
            _log.warning('unknown ws message type: %s', t)

    # ── 目录选择 (dsh directory-picker-native: 系统原生窗口) ──

    async def _pick_dir_native(self, msg: dict, reply) -> None:
        """弹 OS 原生目录选择窗口; path=null 表示用户取消."""
        req_id = msg.get('id')

        def work() -> str | None:
            if sys.platform == 'win32':
                return _win_pick_directory()
            return _unix_pick_directory()

        try:
            path = await asyncio.to_thread(work)
            await reply({'type': 'pick_dir_native', 'id': req_id,
                         'ok': True, 'path': path})
        except Exception as exc:
            await reply({'type': 'pick_dir_native', 'id': req_id,
                         'ok': False, 'error': str(exc)})


# ── 系统原生目录选择窗口 (dsh directory-picker-native 语义: 弹在 host 机器) ──

_WIN_DIALOG_TITLE = '选择工作目录'
_CREATE_NO_WINDOW = 0x08000000

# 子进程脚本: 纯 stdlib, IFileOpenDialog + FOS_PICKFOLDERS.
# 对齐 dsh: 对话框在独立子进程弹出 —— 是该进程的第一个窗口, Windows 直接
# 激活它, 任务栏显示对话框自身图标而非 python 控制台图标; CREATE_NO_WINDOW
# 保证不闪黑窗. stdout 输出单行 JSON: {"path": ...} / {"path": null} (取消)
# / {"error": ...}; 路径经 ensure_ascii 转义, 无编码歧义.
_WIN_DIALOG_SCRIPT = r'''
import ctypes, json, sys
import ctypes.wintypes as wt

TITLE = sys.argv[1] if len(sys.argv) > 1 else 'Select Directory'
HRESULT_CANCELLED = 0x800704C7   # HRESULT_FROM_WIN32(ERROR_CANCELLED)
FOS = 0x20 | 0x40 | 0x8          # PICKFOLDERS | FORCEFILESYSTEM | NOCHANGEDIR
SIGDN_FILESYSPATH = 0x80058000
SLOT_RELEASE, SLOT_SHOW, SLOT_SET_OPTIONS = 2, 3, 9
SLOT_SET_TITLE, SLOT_GET_RESULT, SLOT_GET_DISPLAY_NAME = 17, 20, 5


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]


pguid = ctypes.POINTER(GUID)
ppv = ctypes.POINTER(ctypes.c_void_p)


def hr(hr, what):
    """S_OK/S_FALSE 过; 取消返回 False; 其他负值抛 OSError."""
    if hr & 0xFFFFFFFF == HRESULT_CANCELLED:
        return False
    if hr < 0:
        raise OSError(hr, '%s failed: 0x%08X' % (what, hr & 0xFFFFFFFF))
    return True


def method(obj, slot, *argtypes):
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(table[slot])

    def call(*args):
        return fn(obj, *args)

    return call


def run():
    # DPI: per-monitor-v2 → per-monitor → system (best-effort, pre-1607 无此 API)
    try:
        set_dpi = ctypes.WinDLL('user32').SetThreadDpiAwarenessContext
        set_dpi.restype = ctypes.c_void_p
        set_dpi.argtypes = [ctypes.c_ssize_t]
        for ctx in (-4, -3, -2):
            if set_dpi(ctx) is not None:
                break
    except AttributeError:
        pass

    ole32 = ctypes.WinDLL('ole32')
    ole32.CLSIDFromString.restype = ctypes.HRESULT
    ole32.CLSIDFromString.argtypes = [wt.LPCWSTR, pguid]
    ole32.CoInitializeEx.restype = ctypes.HRESULT
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoCreateInstance.restype = ctypes.HRESULT
    ole32.CoCreateInstance.argtypes = [pguid, ctypes.c_void_p, ctypes.c_uint32,
                                       pguid, ppv]
    ole32.CoTaskMemFree.restype = None
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    def guid(s):
        g = GUID()
        if ole32.CLSIDFromString(s, ctypes.byref(g)) < 0:
            raise OSError('CLSIDFromString %s' % s)
        return g

    try:
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
        dialog = ctypes.c_void_p()
        hr(ole32.CoCreateInstance(
            guid('{DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7}'),  # CLSID_FileOpenDialog
            None, 0x1,  # CLSCTX_INPROC_SERVER
            guid('{D57C7288-D4AD-4768-BE02-9D969532D960}'),  # IID_IFileOpenDialog
            ctypes.byref(dialog)), 'CoCreateInstance')
        show = method(dialog, SLOT_SHOW, wt.HWND)
        set_opts = method(dialog, SLOT_SET_OPTIONS, ctypes.c_uint32)
        set_title = method(dialog, SLOT_SET_TITLE, wt.LPCWSTR)
        get_result = method(dialog, SLOT_GET_RESULT, ppv)
        hr(set_opts(ctypes.c_uint32(FOS)), 'SetOptions')
        hr(set_title(TITLE), 'SetTitle')

        out = {'path': None}
        if hr(show(None), 'Show'):
            item = ctypes.c_void_p()
            hr(get_result(ctypes.byref(item)), 'GetResult')
            get_name = method(item, SLOT_GET_DISPLAY_NAME,
                              ctypes.c_uint, ctypes.POINTER(ctypes.c_wchar_p))
            buf = ctypes.c_wchar_p()
            hr(get_name(SIGDN_FILESYSPATH, ctypes.byref(buf)), 'GetDisplayName')
            out['path'] = buf.value
            method(item, SLOT_RELEASE)()
        print(json.dumps(out, ensure_ascii=True))
    except OSError as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True))
    finally:
        ole32.CoUninitialize()


if __name__ == '__main__':
    run()
'''


def _win_pick_directory() -> str | None:
    """Windows: 独立子进程弹 IFileOpenDialog (任务栏图标正确)."""
    import subprocess
    proc = subprocess.run(  # noqa: S603
        [sys.executable, '-c', _WIN_DIALOG_SCRIPT, _WIN_DIALOG_TITLE],
        capture_output=True, text=True,
        creationflags=_CREATE_NO_WINDOW, timeout=120)
    line = next((l for l in proc.stdout.splitlines() if l.startswith('{')), '')
    if line:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            if 'error' in data:
                raise RuntimeError(str(data['error']))
            return data.get('path')
    raise RuntimeError(proc.stderr.strip() or 'picker exited without output')


def _unix_pick_directory() -> str | None:
    """macOS osascript choose folder / Linux zenity/kdialog."""
    import subprocess
    if sys.platform == 'darwin':
        cmd = ['osascript', '-e',
               'POSIX path of (choose folder with prompt '
               '"Select Workspace Directory")']
    elif shutil.which('zenity'):
        cmd = ['zenity', '--file-selection', '--directory']
    elif shutil.which('kdialog'):
        cmd = ['kdialog', '--getexistingdirectory', str(Path.home())]
    else:
        return None
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip().rstrip('/') or None


# ── HTTP/WS app (路由闭包捕获 FlowHub instance, 与 server 同生命周期) ──


def _build_app(hub: 'FlowHub') -> FastAPI:
    app = FastAPI(title='flow', docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
        allow_headers=['*'],
    )

    @app.get('/health')
    async def _health():
        return JSONResponse({'ok': True})

    @app.get('/models')
    async def _models():
        """自有 llm 配置 → 前端建 agent 选模型 (不回 api_key)."""
        models = []
        for key, cfg in _MODELS.items():
            models.append({
                'key': key,
                'name': cfg.get('model', key),
                'max_context': int(cfg.get('max_context', 0)),
                'reasoning_effort': cfg.get('default_effort'),
                'has_tools': bool(cfg.get('tools')),
            })
        return JSONResponse({'default': DEFAULT_MODEL, 'models': models})

    @app.websocket('/ws')
    async def _ws(ws: WebSocket):
        await ws.accept()
        hub._ws_clients.add(ws)

        async def reply(data: dict) -> None:
            await ws.send_text(json.dumps(data, ensure_ascii=False))

        try:
            await hub._on_client_connected(reply)
        except Exception as exc:
            _log.warning('connect handler: %r', exc)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    await reply({'type': 'error', 'message': 'invalid json'})
                    continue
                await hub._on_client_message(msg, reply)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            _log.warning('ws connection: %r', exc)
        finally:
            hub._ws_clients.discard(ws)

    return app
