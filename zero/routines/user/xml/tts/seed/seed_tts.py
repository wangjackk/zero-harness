"""
SeedTTS — 用 Speaker 实现 TTS 基类。

内部用 asyncio.Queue 桥接同步式的 feed()/finish() 调用与异步 text_iter。

典型用法：
    tts = SeedTTS(speaker)
    audio_stream = tts.audio_chunks()   # 启动迭代

    # 另一个协程（或同一协程交替）：
    await tts.feed("你好，")
    await tts.feed("世界。")
    await tts.finish()

    async for chunk in audio_stream:
        play(chunk.data)
"""

import asyncio
from typing import AsyncIterator

from ..interface import AudioChunk, TTS
from .speaker import Speaker


class SeedTTS(TTS):
    def __init__(self, speaker: Speaker) -> None:
        self._speaker = speaker
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()

    # ── TTS 基类实现 ───────────────────────────────────────────────────────────

    async def feed(self, text: str) -> None:
        """将文本片段送入队列，由 audio_chunks 迭代器消费。"""
        await self._queue.put(text)

    async def finish(self) -> None:
        """声明文本输入结束，发送 sentinel 使迭代器结束。"""
        await self._queue.put(None)

    def audio_chunks(self) -> AsyncIterator[AudioChunk]:
        """返回音频块异步迭代器；调用后即开始消费 feed() 送入的文本。"""
        return self._speaker.speak(self._text_iter())

    # ── 内部 ──────────────────────────────────────────────────────────────────

    async def _text_iter(self) -> AsyncIterator[str]:
        while True:
            text = await self._queue.get()
            if text is None:
                return
            yield text
