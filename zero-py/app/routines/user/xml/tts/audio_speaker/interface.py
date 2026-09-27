from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..interface import AudioChunk


class AudioSpeaker(ABC):
    @abstractmethod
    async def play(self, chunks: AsyncIterator[AudioChunk]) -> None: ...
