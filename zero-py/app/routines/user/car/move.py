"""CarMove ---- 小车平移控制(rosbridge WS → /controller/cmd_vel, 手册 §1 主链路).

前后/左右横移(麦轮). 旋转走 car/turn, 弧线走 car/steer ---- 按参数形状拆分,
speed 在本接口恒为平移速度 m/s. 10Hz 心跳发布, 结束(含异常)必发零速.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field

from .motion import MotionOutput, MotionRoutine


class CarMoveInput(BaseModel):
    """car/move input."""

    action: Literal['forward', 'back', 'left', 'right'] = Field(
        description='平移方向: forward/back 前后, left/right 横移(麦轮)')
    speed: float = Field(
        0.1, ge=0.05, le=0.6, description='平移速度 m/s; 默认 0.1')
    duration: float = Field(
        2.0, ge=0.5, le=60.0, description='持续秒数, 结束自动发零速; 默认 2')


class CarMove(MotionRoutine):
    """控制小车平移: 前进/后退/左移/右移, 指定速度与时长, 结束自动停."""

    name = 'car/move'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '小车平移: forward/back 前后, left/right 横移(麦轮), ',
        'input_schema': CarMoveInput.model_json_schema(),
        'output_schema': MotionOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarMoveInput.model_validate(kwargs)
        vx, vy, wz = {
            'forward': (inp.speed, 0.0, 0.0),
            'back': (-inp.speed, 0.0, 0.0),
            'left': (0.0, inp.speed, 0.0),
            'right': (0.0, -inp.speed, 0.0),
        }[inp.action]
        command = f'{inp.action} speed={inp.speed:g} dur={inp.duration:g}s'
        return await self._execute(vx, vy, wz, inp.duration, command)
