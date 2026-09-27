"""wav_player -- sounddevice(PortAudio) 播放, 单后端纯 Python.

每实例一个播放 session: 独立 OutputStream(Windows 共享模式多流自动混音,
提示音可叠在音乐上); 阻塞写跑在 worker thread, stop() 经 threading.Event
在块粒度(~0.1s)内中止.

替代老版 zero 的 ffplay/pyaudio 双后端工厂: sounddevice wheel 自带 PortAudio,
无外部二进制依赖; 即 pyaudio 的现代封装, 单实现不再需要后端探测.
"""
from __future__ import annotations

import asyncio
import threading
from logging import Logger
from pathlib import Path
from typing import Optional, Union

import numpy as np
import sounddevice as sd
import soundfile as sf

# 写块粒度(帧数 ≈ 0.1s @ 44.1kHz): stop() 的最大中止延迟
_CHUNK = 4410


class WavPlayer:
    """一次播放 = 一个 session; stop() 后实例弃用(旧 zero 同语义).

    play() 正常播完 / 被 stop() 打断 → 均正常返回; 后端出错(无声卡等) → raise.
    """

    def __init__(self, logger: Logger):
        self._logger = logger
        self._abort = threading.Event()

    async def play(self, source: Union[Path, np.ndarray],
                   samplerate: int = 44100,
                   duration: Optional[float] = None) -> None:
        """阻塞播放音频文件或 float32 ndarray(shape (n,) 单声道 | (n, ch)).

        duration > 0 只播前 duration 秒; None 播完整段.
        """
        if isinstance(source, Path):
            data, sr = sf.read(source, dtype='float32', always_2d=True)
        else:
            data, sr = source, samplerate
            if data.ndim == 1:
                data = data.reshape(-1, 1)
        if duration is not None:
            data = data[: int(duration * sr)]
        if len(data) == 0:
            return
        self._logger.info('wav_player: play %d frames @%dHz', len(data), sr)
        try:
            await asyncio.to_thread(self._run, data, sr)
        except asyncio.CancelledError:
            # 调用方被 cancel: 通知 worker 尽快中止再抛
            self._abort.set()
            raise

    def _run(self, data: np.ndarray, sr: int) -> None:
        # with 退出 → close() → stop(): Pa_Stop 语义, drain 完缓冲再关,
        # 末块自然播完. (不能等 stream.active ---- 它表示"流开着"恒 True.)
        with sd.OutputStream(samplerate=sr, channels=data.shape[1]) as stream:
            for i in range(0, len(data), _CHUNK):
                if self._abort.is_set():
                    stream.abort()
                    return
                stream.write(data[i:i + _CHUNK])

    async def stop(self) -> None:
        """请求中止; worker 在 ≤ 一个写块内退出.重复调用安全."""
        self._abort.set()
