"""CarStop ---- 小车急停(rosbridge WS, 手册 §1 主链路).

bump 动作代际使所有在跑 car/move 心跳退出, 并立即补发零速.
对 ctrl 走模块属性访问(部分热重载下函数级 from-import 持旧函数).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine

from . import ctrl


class CarStopInput(BaseModel):
    """car/stop input(无参数)."""


class CarStopOutput(BaseModel):
    """car/stop output."""

    stopped: bool = Field(description='零速已发')


class CarStop(Routine):
    """小车急停: 立即发零速, 优先级最高."""

    name = 'car/stop'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '小车急停(打断在跑动作并立即发零速). 底盘异常/失控时的第一反应',
        'input_schema': CarStopInput.model_json_schema(),
        'output_schema': CarStopOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await ctrl.stop_robot()
        return CarStopOutput(stopped=True).model_dump()
