"""
SeedWSConnection — 管理一条 Seed WS 双向流连接，支持跨 session 复用。

生命周期：
  async with SeedWSConnection(...) as conn:
      async for chunk in conn.run_session(speaker_id, audio_params, text_iter):
          ...

同一条连接不支持并发 session，多个 speaker 通过内部 asyncio.Lock 自动排队。
"""

import asyncio
import time
import uuid
from typing import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.protocol import State

from routine.logger import setup_logger
from ..interface import AudioChunk
from .protocol import (
    Event,
    ParsedFrame,
    build_frame,
    build_task_request,
    parse_frame,
)

logger = setup_logger('tts')

# plan 网关 (agent plan 套餐通道; 老 /api/v3/tts/bidirection 已 401, 同 zero-rs config.toml)
_WS_URL = "wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection"

_DEFAULT_AUDIO_PARAMS: dict = {
    "format": "pcm",
    "sample_rate": 24000,
}


class SeedWSConnection:
    def __init__(
        self,
        api_key: str,
        resource_id: str = "seed-tts-2.0",
    ) -> None:
        self._api_key     = api_key
        self._resource_id = resource_id
        self._ws: ClientConnection | None = None
        self._lock = asyncio.Lock()

    # ── context manager ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "SeedWSConnection":
        await self._connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._disconnect()

    async def _ensure_connected(self) -> None:
        """懒连接：首次使用或检测到旧连接已关闭/关闭中时，建立新连接。"""
        if self._ws is not None and self._ws.state is State.OPEN:
            return
        if self._ws is not None:
            logger.info("SeedWSConnection: stale ws (state=%s), reconnecting", self._ws.state.name)
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        await self._connect()

    # ── connect / disconnect ───────────────────────────────────────────────────

    async def _connect(self) -> None:
        headers = {
            "X-Api-Key": self._api_key,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        self._ws = await websockets.connect(_WS_URL, additional_headers=headers)
        await self._send_frame(build_frame(Event.StartConnection))
        await self._wait_event(Event.ConnectionStarted)
        logger.debug("SeedWSConnection: connected")

    async def _disconnect(self) -> None:
        if self._ws is None:
            return
        try:
            await self._send_frame(build_frame(Event.FinishConnection))
            await self._wait_event(Event.ConnectionFinished)
        except Exception:
            pass
        finally:
            await self._ws.close()
            self._ws = None
            logger.debug("SeedWSConnection: disconnected")

    # ── low-level send / recv ──────────────────────────────────────────────────

    async def _send_frame(self, data: bytes) -> None:
        assert self._ws is not None
        await self._ws.send(data)

    async def _recv_frame(self) -> ParsedFrame:
        assert self._ws is not None
        raw = await self._ws.recv()
        if isinstance(raw, str):
            # 服务端文本帧通常是错误信息
            raise RuntimeError(f"unexpected text frame: {raw}")
        return parse_frame(raw)

    async def _wait_event(self, expected: Event) -> ParsedFrame:
        while True:
            frame = await self._recv_frame()
            if frame.is_error:
                raise RuntimeError(
                    f"server error (code={frame.error_code}): "
                    f"{frame.payload_json()}"
                )
            if frame.event == expected:
                return frame
            logger.debug("_wait_event: skip event=%s waiting for %s", frame.event, expected)

    # ── session ────────────────────────────────────────────────────────────────

    async def run_session(
        self,
        speaker_id: str,
        text_iter: AsyncIterator[str],
        audio_params: dict | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """
        排队执行一次 TTS session，yield AudioChunk。

        内部并发两个 task：
          sender   — 从 text_iter 消费文本并发 TaskRequest，完成后发 FinishSession
          receiver — 收 TTSResponse 帧，封装为 AudioChunk 放入内部队列
        主协程从队列取出 AudioChunk yield 给调用方。
        """
        async with self._lock:
            await self._ensure_connected()
            async for chunk in self._run_session_locked(
                speaker_id, text_iter, audio_params or _DEFAULT_AUDIO_PARAMS
            ):
                yield chunk

    async def _run_session_locked(
        self,
        speaker_id: str,
        text_iter: AsyncIterator[str],
        audio_params: dict,
    ) -> AsyncIterator[AudioChunk]:
        session_id = str(uuid.uuid4())
        audio_queue: asyncio.Queue[AudioChunk | None] = asyncio.Queue()

        # StartSession
        start_payload = {
            "user": {"uid": "zero"},
            "event": int(Event.StartSession),
            "req_params": {
                "speaker": speaker_id,
                "audio_params": audio_params,
            },
        }
        await self._send_frame(build_frame(Event.StartSession, start_payload, session_id))
        await self._wait_event(Event.SessionStarted)
        logger.debug("session %s started", session_id)

        sender_done   = asyncio.Event()
        receiver_done = asyncio.Event()

        async def sender() -> None:
            try:
                async for text in text_iter:
                    if not text:
                        continue
                    await self._send_frame(build_task_request(text, session_id))
                await self._send_frame(
                    build_frame(Event.FinishSession, {}, session_id)
                )
                logger.debug("session %s: FinishSession sent", session_id)
            except Exception as e:
                logger.exception("sender error: %s", e)
                await audio_queue.put(None)  # 异常时也结束消费方
            finally:
                sender_done.set()

        async def receiver() -> None:
            n_audio = 0
            t_start = time.monotonic()
            try:
                while True:
                    frame = await self._recv_frame()
                    if frame.is_error:
                        raise RuntimeError(
                            f"session error (code={frame.error_code}): "
                            f"{frame.payload_json()}"
                        )
                    if frame.event == Event.TTSResponse:
                        if frame.payload_raw:
                            if n_audio == 0:
                                ttfa_ms = (time.monotonic() - t_start) * 1000
                                logger.info("TTS ttfa=%.0fms", ttfa_ms)
                            n_audio += 1
                            await audio_queue.put(
                                AudioChunk(data=frame.payload_raw)
                            )
                    elif frame.event in (Event.SessionFinished, Event.SessionCanceled):
                        total_ms = (time.monotonic() - t_start) * 1000
                        logger.info("TTS session done: %d audio frames total=%.0fms", n_audio, total_ms)
                        break
                    elif frame.event == Event.SessionFailed:
                        raise RuntimeError(
                            f"session failed: {frame.payload_json()}"
                        )
            except Exception as e:
                logger.exception("receiver error: %s", e)
            finally:
                await audio_queue.put(None)  # sentinel
                receiver_done.set()

        sender_task   = asyncio.create_task(sender())
        receiver_task = asyncio.create_task(receiver())

        cancelled = False
        try:
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                yield chunk
        except (asyncio.CancelledError, GeneratorExit):
            cancelled = True
            raise
        finally:
            await asyncio.shield(self._cleanup_session(
                session_id, sender_task, receiver_task, cancelled
            ))

    async def _cleanup_session(
        self,
        session_id: str,
        sender_task: asyncio.Task,
        receiver_task: asyncio.Task,
        cancelled: bool,
    ) -> None:
        """会话收尾。被打断时主动 CancelSession 让服务端释放 session 配额。"""
        sender_task.cancel()
        try:
            await sender_task
        except BaseException:
            pass

        if cancelled and self._ws is not None and self._ws.state is State.OPEN:
            try:
                await self._send_frame(
                    build_frame(Event.CancelSession, {}, session_id)
                )
                logger.info("session %s: CancelSession sent", session_id)
                try:
                    await asyncio.wait_for(receiver_task, timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                except BaseException:
                    pass
            except Exception as e:
                logger.warning("session %s: CancelSession send failed: %s", session_id, e)

        receiver_task.cancel()
        await asyncio.gather(receiver_task, return_exceptions=True)
        logger.debug("session %s: tasks cleaned up", session_id)
