"""CarShake ---- 小车正弦摆动(rosbridge WS -> /controller/cmd_vel).

"摇头晃脑": 速度按 amp*sin(2*pi*freq*t) 连续变化, 头尾速度天然归零,
平滑无冲击. 三种模式: yaw 原地摇头(默认) / x 前后点头 / y 左右扭腰.
实测底盘持续旋转饱和 ~1.1 rad/s, 短促摆动可跟踪到 ~8Hz(见会话标定).
"""
from __future__ import annotations

import math
from typing import Any, ClassVar, Dict, Literal, Tuple

from pydantic import BaseModel, Field, model_validator

from .motion import MotionOutput, MotionRoutine


class CarShakeInput(BaseModel):
    """car/shake input."""

    mode: Literal['yaw', 'x', 'y'] = Field(
        'yaw', description='摆动轴: yaw=原地摇头(默认), x=前后点头, y=左右扭腰')
    freq: float = Field(
        2.0, ge=0.5, le=10.0,
        description='摆动频率 Hz; 默认 2; >8Hz 底盘基本跟不上')
    amp: float = Field(
        1.5, ge=0.05, le=2.0,
        description='速度峰值: yaw 模式为 rad/s(<=2), x/y 模式为 m/s(<=0.6)')
    cycles: int = Field(
        5, ge=1, le=20, description='摆动周期数(来回次数); 默认 5')

    @model_validator(mode='after')
    def _check(self) -> 'CarShakeInput':
        # 线性轴受 car/move 同款限幅; 角轴受 turn_rate 同款限幅
        if self.mode != 'yaw' and self.amp > 0.6:
            raise ValueError('x/y 模式 amp 须 <= 0.6 m/s')
        return self


class CarShake(MotionRoutine):
    """正弦摆动: 速度 = amp*sin(2*pi*freq*t), 摆 cycles 个周期自动停."""

    name = 'car/shake'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '小车正弦摆动(摇头/点头/扭腰)',
        'input_schema': CarShakeInput.model_json_schema(),
        'output_schema': MotionOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarShakeInput.model_validate(kwargs)
        w = 2.0 * math.pi * inp.freq

        def fn(t: float) -> Tuple[float, float, float]:
            s = inp.amp * math.sin(w * t)
            if inp.mode == 'yaw':
                return 0.0, 0.0, s
            if inp.mode == 'x':
                return s, 0.0, 0.0
            return 0.0, s, 0.0  # y

        axis = {'yaw': '摇头', 'x': '点头', 'y': '扭腰'}[inp.mode]
        command = (f'shake {axis} freq={inp.freq:g}Hz amp={inp.amp:g} '
                   f'cycles={inp.cycles}')
        duration = inp.cycles / inp.freq
        return await self._execute_wave(fn, duration, command)
