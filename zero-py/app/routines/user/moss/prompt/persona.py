"""persona —— 人格片段: 我是谁, 交互礼仪.

文本在同目录 ``persona.md``, 装配时读一次 —— 改人格不改代码.
礼仪骨架参考原版 dolores(CTML-first / 言行相配 / voice-first),
按最小对话体的现实裁剪.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict

from routine.routine import Routine

__all__ = ['Persona']


class Persona(Routine):
    """一次性 prompt routine: 产人格文本(读 persona.md)."""

    name = 'prompt/persona'

    meta: ClassVar[Dict[str, Any]] = {
        'description': 'prompt 片段: 人格与交互礼仪',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        return Path(__file__).with_name('persona.md').read_text(
            encoding='utf-8')

    async def stop(self) -> None:
        pass
