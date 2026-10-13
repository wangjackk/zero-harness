"""CarTurn ---- 小车原地旋转(rosbridge WS → /controller/cmd_vel).

turn_rate 恒为角速度 rad/s(REP 103: 正 = 左转/逆时针, 负 = 右转).
10Hz 心跳发布, 结束(含异常)必发零速.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field, model_validator

from .motion import MotionOutput, MotionRoutine


class CarTurnInput(BaseModel):
    """car/turn input."""

    turn_rate: float = Field(
        0.5, ge=-2.0, le=2.0,
        description='角速度 rad/s, 正=左转(逆时针), 负=右转; 默认 0.5')
    duration: float = Field(
        2.0, ge=0.5, le=60.0, description='持续秒数, 结束自动发零速; 默认 2')

    @model_validator(mode='after')
    def _nonzero(self) -> 'CarTurnInput':
        if self.turn_rate == 0:
            raise ValueError('turn_rate 须非零(静止无意义)')
        return self


class CarTurn(MotionRoutine):
    """原地旋转: 指定角速度与时长, 结束自动停."""

    name = 'car/turn'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '小车原地旋转',
        'input_schema': CarTurnInput.model_json_schema(),
        'output_schema': MotionOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarTurnInput.model_validate(kwargs)
        command = f'turn rate={inp.turn_rate:g} dur={inp.duration:g}s'
        return {
            'result': await self._execute(0.0, 0.0, inp.turn_rate, inp.duration, command),
            'content': None
        }
