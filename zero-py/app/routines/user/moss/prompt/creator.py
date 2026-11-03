"""creator —— 教 Moss 在 dynamic 实验场创作并热更新 routine.

正文在同目录 ``creator.md``; 与其他 prompt 片段一样, 装配时读一次.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict

from routine.routine import Routine

__all__ = ['Creator']


class Creator(Routine):
    """一次性 prompt routine: dynamic 创作闭环与热重载契约."""

    name = 'prompt/creator'

    meta: ClassVar[Dict[str, Any]] = {
        'description': 'prompt 片段: 在 Moss dynamic 实验场创建、挂载并自验 routine',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        return Path(__file__).with_name('creator.md').read_text(
            encoding='utf-8')

    async def stop(self) -> None:
        pass
