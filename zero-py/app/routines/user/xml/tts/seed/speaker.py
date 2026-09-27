"""
Speaker — 持有 speaker_id 和音频参数，每次 speak() 创建一个新 session。

多个 Speaker 共享同一条 SeedWSConnection 时，并发 speak() 会自动排队（连接内部 Lock）。
"""

from typing import AsyncIterator

from ..interface import AudioChunk
from .connection import SeedWSConnection, _DEFAULT_AUDIO_PARAMS


class Speaker:
    def __init__(
        self,
        conn: SeedWSConnection,
        speaker_id: str,
        audio_params: dict | None = None,
    ) -> None:
        self._conn         = conn
        self._speaker_id   = speaker_id
        self._audio_params = audio_params or dict(_DEFAULT_AUDIO_PARAMS)

    @property
    def speaker_id(self) -> str:
        return self._speaker_id

    def speak(self, text_iter: AsyncIterator[str]) -> AsyncIterator[AudioChunk]:
        """
        将文本流送入 TTS，返回音频块异步迭代器。

        run_session 本身是 async generator，直接返回其迭代器对象，
        调用方可直接 `async for chunk in speaker.speak(...):`。
        """
        return self._conn.run_session(
            speaker_id=self._speaker_id,
            text_iter=text_iter,
            audio_params=self._audio_params,
        )
