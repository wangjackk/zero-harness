"""dynamic 实验场的最小探针: 验证包能被加载、注册与真实调用."""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from routine import Routine

__all__ = ['DynamicProbe']


class DynamicProbe(Routine):
    """返回固定结果; 不进 Moss 词表, 只作 dynamic 加载锚点."""

    name = 'moss_dynamic_probe'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': 'Moss dynamic 实验场探针(不进 CTML 词表)',
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {'content': 'moss dynamic is ready', 'details': {'ok': True}}

    async def stop(self) -> None:
        pass
