"""车控业务 ---- /controller/cmd_vel Twist 编码 + 动作代际互斥 + 零速保证.

纯业务层, 零连接管理: 常驻连接/断线自愈/advertise 幂等缓存在 ros_client
单例(跨 routine 热重载存活), 本模块只调 ``ros_client.publish``. 感知
订阅(ROS 话题 → pubsub)在 ros_bridge 桥.

动作代际(中断机制): 物理上只有一台车, 模块级单调计数, 最新动作胜出.
``publish_twist_fn`` 进入时取新代号并使所有旧动作失效; car/stop / 新
动作 bump 代际 → 旧心跳循环下一拍退出(finally 零速).
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Tuple

from ..hiwonder import ros_client

_CMD_VEL = '/controller/cmd_vel'
_TWIST_TYPE = 'geometry_msgs/Twist'

_generation: int = 0


def current_generation() -> int:
    """当前动作代号(供调用方检测自己是否已被更新的动作取代)."""
    return _generation


def bump_generation() -> None:
    """使所有在跑动作失效(下一拍退出, finally 自带零速).

    同步纯计数器操作, 无 I/O ---- 供需要"打断但不出新动作"的调用方
    (如 car/wake_track 收到新唤醒)在事件回调里原子打断, 不与后续
    新动作的代际竞争.
    """
    global _generation
    _generation += 1


def _twist(vx: float = 0.0, vy: float = 0.0, wz: float = 0.0) -> Dict[str, Any]:
    return {
        'linear': {'x': vx, 'y': vy, 'z': 0.0},
        'angular': {'x': 0.0, 'y': 0.0, 'z': wz},
    }


async def publish_twist(vx: float, vy: float, wz: float,
                        duration: float, rate: float = 10.0) -> None:
    """恒速指令包装(详见 publish_twist_fn)."""
    await publish_twist_fn(lambda t: (vx, vy, wz), duration, rate)


async def publish_twist_fn(fn: Callable[[float], Tuple[float, float, float]],
                           duration: float, rate: float = 10.0) -> None:
    """以 rate Hz 持续发布速度指令 duration 秒, 每拍速度由 fn(t) 现算.

    fn: t(动作开始后秒数) -> (vx, vy, wz); 供时变动作(car/shake 正弦
    摆动)用, 恒速动作走 publish_twist 包装.
    结束(含异常/被打断)必发零速; 打断: stop_robot 或新动作使本动作
    下一拍退出. 车不可达时 ros_client 等重连上限后抛 ConnectionError.
    """
    global _generation
    _generation += 1
    mine = _generation
    period = 1.0 / rate
    t0 = asyncio.get_running_loop().time()
    try:
        while True:
            if _generation != mine:
                break   # 被 stop / 新动作取代
            vx, vy, wz = fn(asyncio.get_running_loop().time() - t0)
            await ros_client.publish(_CMD_VEL, _twist(vx, vy, wz),
                                     msg_type=_TWIST_TYPE)
            remaining = (t0 + duration) - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(period, remaining))
    finally:
        try:
            await ros_client.publish(_CMD_VEL, _twist(), msg_type=_TWIST_TYPE)
            await asyncio.sleep(0.1)   # 给零速帧一拍出站时间
        except Exception:
            pass   # 零速帧失败 = 连接不可达, 不掩盖动作本身的异常


async def stop_robot() -> None:
    """急停: bump 代际使所有在跑动作退出, 并立即补发零速.

    车不可达时抛 ConnectionError ---- 急停失败必须对 caller 可见.
    """
    global _generation
    _generation += 1
    await ros_client.publish(_CMD_VEL, _twist(), msg_type=_TWIST_TYPE)
    await asyncio.sleep(0.1)
