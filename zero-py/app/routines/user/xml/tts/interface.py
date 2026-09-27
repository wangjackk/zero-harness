from abc import ABC, abstractmethod
from typing import AsyncIterator

from pydantic import BaseModel


class AudioChunk(BaseModel):
    data: bytes
    timestamp: float = 0.0


class TTS(ABC):
    @abstractmethod
    async def feed(self, text: str) -> None: ...

    @abstractmethod
    async def finish(self) -> None: ...

    @abstractmethod
    def audio_chunks(self) -> AsyncIterator[AudioChunk]: ...
