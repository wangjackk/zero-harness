"""用户侧的线: 一条 EIO=4 / Socket.IO v4 的 websocket.

只做三件事: 连上、推 ``bb:up{type:"actions"}``、收 ``bb:objects`` 快照.
断线自己退避重连 —— 协议是"重连不补发", 但连接本身应该自己回来.

包格式(EIO=4, 纯文本帧)::

    服务端 → 0{"sid":...}            握手(带 pingInterval)
    客户端 → 40{"role":"user"}       连接默认命名空间 + auth(不带就是 viewer)
    服务端 → 40{"sid":...}           连接确认(收到它才算连通)
    服务端 → 2  /  客户端 → 3        ping / pong(服务端发起)
    客户端 → 42["bb:up",{...}]       推动作
    服务端 → 42["bb:objects",{...}]  对象表快照

auth 走 CONNECT 包里的 JSON —— 服务端拿它当 ``handshake.auth``. 不带 role
的连接是 viewer(黑板侧), 推上去的 actions 会被**静默丢弃**, 所以这里永远带.

不引 python-socketio: 只用 websocket 一条传输(协议明确不降级 polling),
包里就上面这五种前缀, 与 ``asr/sauc.py`` 手写 WS 协议同一个路子.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import websockets

from routine.logger import setup_logger

__all__ = ['Client']

_log = setup_logger('moss.board')

#: 退避重连的上界(秒)
_BACKOFF_MAX = 8.0
_RECV_HELLO_S = 5.0


def _endpoint(url: str) -> str:
    """http(s)://host:port → ws(s)://host:port/socket.io/?EIO=4&transport=websocket."""
    u = str(url or '').strip().rstrip('/')
    if u.startswith('http://'):
        u = 'ws://' + u[len('http://'):]
    elif u.startswith('https://'):
        u = 'wss://' + u[len('https://'):]
    elif not (u.startswith('ws://') or u.startswith('wss://')):
        u = 'ws://' + u
    if u.endswith('/socket.io'):
        return u + '/?EIO=4&transport=websocket'
    return u + '/socket.io/?EIO=4&transport=websocket'


class Client:
    """一块黑板的用户侧连接(常驻, 惰性启动)."""

    def __init__(self, url: str) -> None:
        self.url = _endpoint(url)
        self._ws = None
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._ready = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._snapshot: Dict[str, Any] = {}
        self._error = ''
        self.stats = {'connects': 0, 'sent': 0, 'snapshots': 0, 'errors': 0}

    # ── 对外 ──────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ready.is_set()

    @property
    def error(self) -> str:
        return self._error

    def snapshot(self) -> Dict[str, Any]:
        """最近一次对象表快照(全量, 不是增量); 没收到过就是空 dict."""
        return dict(self._snapshot)

    def start(self) -> None:
        if self._task is None:
            self._closed = False
            self._task = asyncio.ensure_future(self._loop())

    async def ensure(self, timeout: float) -> None:
        """保证连上(首次调用才真正开始连); 超时就报错, 不无限等."""
        self.start()
        if self.connected:
            return
        try:
            await asyncio.wait_for(self._ready.wait(), timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(
                f'黑板连不上({self.url}): {self._error or "服务端没起?"}'
            ) from None

    async def emit_actions(self, items: List[Dict[str, Any]]) -> None:
        """推一批动作. 一条消息里的动作在黑板侧**同时开始** —— 顺序由调用方分条."""
        ws = self._ws
        if ws is None or not self._ready.is_set():
            raise RuntimeError(
                f'黑板没连上({self.url}): {self._error or "服务端没起?"}')
        packet = '42' + json.dumps(
            ['bb:up', {'type': 'actions', 'items': items}],
            ensure_ascii=False, separators=(',', ':'))
        async with self._send_lock:
            await ws.send(packet)
        self.stats['sent'] += len(items)

    async def stop(self) -> None:
        self._closed = True
        self._ready.clear()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    # ── 内部 ──────────────────────────────────────────────────────

    async def _loop(self) -> None:
        backoff = 0.5
        while not self._closed:
            try:
                async with websockets.connect(
                        self.url, ping_interval=None, max_size=2 ** 22) as ws:
                    self._ws = ws
                    await self._session(ws)
                    backoff = 0.5           # 连上过就复位
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._error = f'{type(exc).__name__}: {exc}'
                self.stats['errors'] += 1
                _log.warning('board session died: %s', self._error)
            finally:
                self._ws = None
                self._ready.clear()
            if self._closed:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _session(self, ws) -> None:
        hello = await asyncio.wait_for(ws.recv(), _RECV_HELLO_S)
        if not (isinstance(hello, str) and hello.startswith('0')):
            raise RuntimeError(f'不是 EIO=4 握手: {str(hello)[:60]!r}')
        await ws.send('40' + json.dumps({'role': 'user'},
                                        separators=(',', ':')))
        async for raw in ws:
            if not isinstance(raw, str) or not raw:
                continue
            if raw[0] == '2':               # ping → pong
                await ws.send('3')
            elif raw.startswith('40'):      # 命名空间已连上
                self._ready.set()
                self.stats['connects'] += 1
                _log.info('board connected: %s', self.url)
            elif raw.startswith('44'):      # connect_error
                raise RuntimeError(f'连接被拒: {raw[2:][:120]}')
            elif raw.startswith('42'):
                self._on_event(raw[2:])
            elif raw.startswith('41'):      # 服务端断开命名空间
                raise RuntimeError('服务端断开命名空间')

    def _on_event(self, body: str) -> None:
        if body.startswith('/'):            # 命名空间前缀(本项目不用)
            _, _, body = body.partition(',')
        try:
            frame = json.loads(body)
        except json.JSONDecodeError:
            return
        if not (isinstance(frame, list) and len(frame) >= 2):
            return
        if frame[0] != 'bb:objects':
            return
        data = frame[1] if isinstance(frame[1], dict) else {}
        self._snapshot = data
        self.stats['snapshots'] += 1
