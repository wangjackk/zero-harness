"""Noop —— 故意不作为: 原版标准原语 ``noop()``.

它存在的理由是**账面可区分**:

- 模型什么都不写 = 这一拍没有身体动作(可能只是旁白, 也可能是流被截断);
- 模型写 ``<noop/>`` = 明确宣布"我听见了, 选择不插嘴".

两者在灵魂看来是不同的事实, 但在没有 noop 时都表现为"没有动作帧"。
本动作把沉默变成一个可记账的动作(一条 action 帧, 不带注意债 —— 沉默
不该把自己叫醒)。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from routine.routine import Routine

__all__ = ['Noop']


class Noop(Routine):
    """故意不作为: 立刻成功返回, 结束这一拍."""

    name = 'noop'

    meta: ClassVar[Dict[str, Any]] = {
        'description': ('故意不作为: 立即结束这一拍(在听、不想插嘴时用), '
                        '与"没有输出"在账面上可区分'),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {}   # 什么都没发生, 也就没什么可说: content 缺省 'ok'

    async def stop(self) -> None:
        pass
