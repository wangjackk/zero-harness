"""LocalSpeaker — 本地扬声器播放（sounddevice + PCM 直通）。

三级流水线，首帧到达即开始播放：
  AudioChunk → raw_q → PCM对齐线程 → pcm_q → sounddevice callback → 扬声器

依赖：pip install sounddevice numpy
"""

import asyncio
import queue
import threading
from typing import AsyncIterator

import numpy as np
import sounddevice as sd

from ..interface import AudioChunk
from .interface import AudioSpeaker


class LocalSpeaker(AudioSpeaker):
    def __init__(
        self,
        sample_rate: int = 24000,
        channels: int = 1,
        blocksize: int = 2048,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels    = channels
        self._blocksize   = blocksize
        # 当前播放的 abort 信号 (play 设, stop 触发), None = 无播放在途
        self._abort: asyncio.Event | None = None

    def stop(self) -> None:
        """立即中止当前播放: set abort 让 play 的 playback_done.wait() 解除,
        触发 with sd.OutputStream 退出 → 关 stream 停止音频输出."""
        if self._abort is not None:
            self._abort.set()

    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None:
        loop = asyncio.get_running_loop()

        raw_q: queue.Queue[bytes | None]      = queue.Queue()
        pcm_q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=32)
        playback_done = asyncio.Event()
        self._abort = playback_done

        # ── 阶段 1：async producer ─────────────────────────────────────────
        async def producer() -> None:
            async for chunk in chunks:
                if chunk.data:
                    raw_q.put(chunk.data)
            raw_q.put(None)

        # ── 阶段 2：PCM 对齐线程（每帧到达立即推送，无解码开销）──────────
        def pcm_thread() -> None:
            step     = self._blocksize * self._channels * 2  # int16 = 2 bytes
            leftover = b""
            while True:
                data = raw_q.get()
                if data is None:
                    if leftover:
                        pcm_q.put(np.frombuffer(leftover, dtype=np.int16).copy())
                    break
                data    = leftover + data
                usable  = (len(data) // step) * step
                if usable:
                    pcm_q.put(np.frombuffer(data[:usable], dtype=np.int16).copy())
                leftover = data[usable:]
            pcm_q.put(None)

        # ── 阶段 3：sounddevice callback（实时线程，严禁阻塞）─────────────
        buf: np.ndarray = np.array([], dtype=np.int16)
        eof = False

        def callback(outdata: np.ndarray, frames: int, _time, _status) -> None:
            nonlocal buf, eof
            needed = frames * self._channels

            while len(buf) < needed and not eof:
                try:
                    block = pcm_q.get_nowait()
                    if block is None:
                        eof = True
                        break
                    buf = np.concatenate([buf, block])
                except queue.Empty:
                    break

            have     = min(len(buf), needed)
            n_frames = have // self._channels
            if n_frames > 0:
                outdata[:n_frames] = buf[:have].reshape(n_frames, self._channels)
                buf = buf[have:]
            if n_frames < frames:
                outdata[n_frames:] = 0
            if eof and len(buf) == 0:
                raise sd.CallbackStop()

        def on_finished() -> None:
            loop.call_soon_threadsafe(playback_done.set)

        # ── 启动 ──────────────────────────────────────────────────────────
        t = threading.Thread(target=pcm_thread, daemon=True)
        t.start()

        try:
            with sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                dtype="int16",
                blocksize=self._blocksize,
                callback=callback,
                finished_callback=on_finished,
            ):
                try:
                    await producer()
                except asyncio.CancelledError:
                    raw_q.put(None)  # 解除 pcm_thread 阻塞
                    raise
                await playback_done.wait()
        finally:
            self._abort = None
            # 确保 pcm_thread 能正常退出（raw_q 可能还没收到 sentinel）
            raw_q.put(None)
            t.join(timeout=1)
