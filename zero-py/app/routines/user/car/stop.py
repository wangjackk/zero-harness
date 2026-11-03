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
        # 安全动作: "以为停了其实没停"是最坏的臆造, 所以出厂声明 observe
        # —— 零速真的发出去了才回到下一拍, 模型不用猜也不用等下句话.
        'observe': True,
        'description': '小车急停(打断在跑动作并立即发零速). 底盘异常/失控时的第一反应',
        'input_schema': CarStopInput.model_json_schema(),
        'output_schema': CarStopOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await ctrl.stop_robot()
        return {'content': '已急停, 零速已发',
                'details': CarStopOutput(stopped=True).model_dump()}
