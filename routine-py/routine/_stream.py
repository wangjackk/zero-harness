"""StreamReader / StreamCtx ---- streamreq 的消费侧.

消费侧 async iterable + async context manager.
provider 侧(@stream 装饰器把 async gen 拆成 __stream_data__ 帧)在 routine.py.

消费方用法::

    async with self.stream_req('count', {'n': 5}, to=rid, timeout=30) as s:
        async for chunk in s:
            ...

帧流(全骑 p2p 隧道,kernel dumb forward):
- 开流:消费方发 ``__stream_open__``{__stream_id__, __reply_to__, event, data} 给 provider
- 数据:provider 每 yield 发 ``__stream_data__``{__stream_id__, chunk}
- 结束:provider 发 ``__stream_data__``{__stream_id__, __eof__: done|error}
- 取消:消费方退出 async with → 发 ``__stream_data__``{__stream_id__, __cancel__: true}
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, TYPE_CHECKING

from .errors import StreamCancelled, StreamError, StreamTimeout

if TYPE_CHECKING:
    from .ctx import RunContext


class StreamReader:
    """消费侧 async iterable:被 on_inbound 的 STREAM_DATA 帧投喂."""

    def __init__(self, stream_id: str, ctx: 'RunContext', target_id: str):
        self.stream_id = stream_id
        self._ctx = ctx
        self._target_id = target_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._first_frame: asyncio.Event = asyncio.Event()
        self._done: bool = False
        self._error: Optional[BaseException] = None

    def __aiter__(self) -> 'StreamReader':
        return self

    async def __anext__(self) -> Any:
        if self._done and self._queue.empty():
            if self._error is not None:
                raise self._error
            raise StopAsyncIteration
        item = await self._queue.get()
        kind = item[0]
        if kind == 'chunk':
            return item[1]
        if kind == 'eof':
            self._done = True
            eof = item[1]
            err = item[2]
            if eof == 'error':
                self._error = StreamError(err or 'stream provider error')
                raise self._error
            if eof == 'cancelled':
                self._error = StreamCancelled('stream cancelled')
                raise self._error
            # done
            raise StopAsyncIteration
        # 不应到达
        raise StopAsyncIteration

    # --- 投喂入口(server.on_inbound 按 __stream_id__ 路由调用) ---

    def feed_chunk(self, chunk: Any) -> None:
        self._queue.put_nowait(('chunk', chunk))
        self._first_frame.set()

    def feed_eof(self, eof: str, error: Optional[str] = None) -> None:
        self._queue.put_nowait(('eof', eof, error))
        self._first_frame.set()


class StreamCtx:
    """``async with stream_req(...) as s`` 的句柄.__aenter__ 等首帧握手,__aexit__ 取消."""

    def __init__(self, reader: StreamReader, timeout: float):
        self._reader = reader
        self._timeout = timeout
        self._entered = False

    async def __aenter__(self) -> StreamReader:
        self._entered = True
        try:
            await asyncio.wait_for(
                self._reader._first_frame.wait(), timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise StreamTimeout(
                f'stream {self._reader.stream_id} open handshake timeout',
            )
        # 若首帧即是 eof/error,触发一次 __anext__ 让它抛出
        if self._reader._done:
            await self._reader.__anext__()
        return self._reader

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if not self._reader._done:
            # 主动取消:发 message.stream_cancel 给 provider
            from .protocol import ENVELOPE_CANCEL, ENVELOPE_STREAM_ID, MESSAGE_STREAM_CANCEL
            try:
                await self._reader._ctx._send_message(
                    self._reader._target_id, MESSAGE_STREAM_CANCEL,
                    {ENVELOPE_STREAM_ID: self._reader.stream_id, ENVELOPE_CANCEL: True},
                )
            except Exception:
                pass
            # 排空到 eof(避免 provider 后续帧泄漏到队列)
            try:
                while not self._reader._done:
                    await asyncio.wait_for(self._reader.__anext__(), timeout=self._timeout)
            except (StopAsyncIteration, StreamCancelled, StreamError, asyncio.TimeoutError):
                pass
        return False
