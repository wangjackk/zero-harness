"""CarSteer ---- 小车车辆式弧线行驶(前进 + 转向, rosbridge WS → /controller/cmd_vel).

speed 前进速度 m/s, turn_rate 转向角速度 rad/s(正=左转), 转弯半径 =
speed / turn_rate. 10Hz 心跳发布, 结束(含异常)必发零速.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field, model_validator

from .motion import MotionOutput, MotionRoutine


class CarSteerInput(BaseModel):
    """car/steer input."""

    speed: float = Field(
        0.1, ge=0.05, le=0.6, description='前进速度 m/s; 默认 0.1')
    turn_rate: float = Field(
        0.5, ge=-2.0, le=2.0,
        description='转向角速度 rad/s, 正=左转(逆时针), 负=右转; 默认 0.5; 非零')
    duration: float = Field(
        2.0, ge=0.5, le=60.0, description='持续秒数, 结束自动发零速; 默认 2')

    @model_validator(mode='after')
    def _nonzero(self) -> 'CarSteerInput':
        if self.turn_rate == 0:
            raise ValueError('turn_rate 须非零(不转向的行驶用 car/move)')
        return self


class CarSteer(MotionRoutine):
    """车辆式弧线行驶: 前进速度 + 转向角速度, 转弯半径 = speed / turn_rate."""

    name = 'car/steer'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '小车车辆式弧线行驶',
        'input_schema': CarSteerInput.model_json_schema(),
        'output_schema': MotionOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarSteerInput.model_validate(kwargs)
        command = (f'steer speed={inp.speed:g} rate={inp.turn_rate:g} '
                   f'r={inp.speed / inp.turn_rate:g}m dur={inp.duration:g}s')
        return await self._execute(inp.speed, 0.0, inp.turn_rate, inp.duration, command)
