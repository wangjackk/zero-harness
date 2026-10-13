"""MotionRoutine ---- 小车动作 routine 公共执行骨架.

动作 routine(car/move 平移 / car/turn 原地旋转 / car/steer 弧线 /
car/shake 正弦摆动)共享:
起停提示音(起步并发不阻塞发车, 结束音在零速帧后) + 10Hz 心跳发布 +
零速保证(含异常) + 中断上报(car/stop/新动作 bump 代际) + stop() 钩子.

本模块不注册(未在 __init__ re-export); 各 routine 对 ctrl 函数一律
模块属性访问 ---- 部分热重载时函数级 from-import 会持有旧函数对象.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from pydantic import BaseModel, Field

from routine import Modules, Routine

from app.modules import MODULE_WHEELS

from . import ctrl


class MotionOutput(BaseModel):
    """动作 routine 公共 output."""

    command: str = Field(description='等效指令描述')
    stopped: bool = Field(description='结束后零速已发')
    interrupted: bool = Field(
        False, description='动作被打断(car/stop 或更新的动作)')


class MotionRoutine(Routine):
    """动作执行骨架: 子类解析入参算出 (vx, vy, wz) 后调 _execute.

    全族(shake/move/turn/steer/track)占用 wheels 模块 ---- 一套底盘同时
    只听一个动作, kernel 按档位仲裁: 平级 StartError(连发动作须 wait 间隔),
    高档 FORCE 驱逐. 用户显式动作走默认档 10, 后台注视(car/gaze)用 5 垫底.
    """

    @classmethod
    def modules(cls, kwargs=None) -> Modules:
        """占用 wheels 模块(底盘互斥, kernel 档位仲裁)."""
        return Modules([MODULE_WHEELS])

    # 是否已有动作在跑(stop() 钩子用于补发零速)
    _active: bool = False

    async def _execute(self, vx: float, vy: float, wz: float,
                       duration: float, command: str) -> Dict[str, Any]:
        """恒速动作包装(详见 _execute_wave)."""
        return await self._execute_wave(lambda t: (vx, vy, wz), duration, command)

    async def _execute_wave(self, fn, duration: float,
                            command: str) -> Dict[str, Any]:
        """时变速度动作: fn(t) -> (vx, vy, wz) 每拍现算.

        生命周期: 起步提示音与运动并发(不阻塞发车), 结束提示音在零速帧
        之后; 零速保证与中断上报在 ctrl.publish_twist_fn 内.
        """
        self._active = True
        mine = ctrl.current_generation() + 1
        beep = asyncio.create_task(self._beep('start'))
        try:
            await ctrl.publish_twist_fn(fn, duration)
        finally:
            beep.cancel()
            self._active = False
        interrupted = ctrl.current_generation() != mine
        if not interrupted:
            # 被打断(car/stop/新动作/父级联停)不响结束音: 语义上不是"完成",
            # 且父实例可能已在拆除(_started=False), call() 会被拒.
            await self._beep('stop')
        return MotionOutput(
            command=command,
            stopped=True,
            interrupted=interrupted,
        ).model_dump()

    async def _beep(self, effect: str) -> None:
        """提示音尽力而为: 失败只记日志, 绝不影响运动链路."""
        try:
            await self.call('sound_effect', {'effect': effect})
        except Exception as e:
            self._logger.warning('sound_effect %s failed: %s', effect, e)

    async def stop(self) -> None:
        """框架停止钩子: 实例停止时立即零速(bump 代际 + 主动补发一帧)."""
        if not self._active:
            return
        try:
            await ctrl.stop_robot()
        except Exception:
            pass   # 停止路径吞错: 不掩盖导致停止的原始异常
