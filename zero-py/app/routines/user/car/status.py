"""CarStatus ---- 小车状态查询(经感知桥动态订阅, 手册 §4 话题).

让 ros_bridge 桥挂 ROS 订阅(battery + odom, odom 节流 5Hz), 在 pubsub
namespace 'ros' 下按 ROS 原名收原始帧(桥只转发不解析): 电池原始 mV +
里程计原始四元数, 解释在本 routine ---- 电量估算与 θ 换算是小车业务,
不进桥. 桥寻址 req 走 bridge_req 小工具(rid 缓存 + 失效重试).

电池估算(手册 §4 3S 锂电): 电量% ≈ (V − 10) / (12.3 − 10) × 100, 满电
约 12.3V. USB 供电时读数约 3.7V 是背馈假象, 不代表真实电量.
车不在线时字段为 null(桥静默重连, 不报错).
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine

from ..hiwonder.bridge_req import bridge_req

_NS = 'ros'   # 感知桥转发 namespace(与 ros_bridge._NS 一致)
_BATTERY = '/ros_robot_controller/battery'
_ODOM = '/odom'
_WAIT = 3.0   # 电池 1Hz: 等一拍 + pubsub 往返余量


class CarStatusInput(BaseModel):
    """car/status input(无参数)."""


class CarStatusOutput(BaseModel):
    """car/status output."""

    voltage_v: Optional[float] = Field(None, description='电池端子电压 (V)')
    battery_pct: Optional[float] = Field(None, description='估算电量 (%)')
    odom_x: Optional[float] = Field(None, description='里程计 x (m)')
    odom_y: Optional[float] = Field(None, description='里程计 y (m)')
    odom_theta: Optional[float] = Field(None, description='里程计朝向 θ (rad)')


class CarStatus(Routine):
    """查询小车电池电压/电量与里程计位姿(订阅感知桥取最新帧)."""

    name = 'car/status'
    meta: ClassVar[Dict[str, Any]] = {
        'readonly': True,
        'description': (
            '查询小车状态: 电池电压/估算电量 + 里程计 x/y/θ '
            '(感知桥最新帧; 车不在线时字段为 null. '
            'USB 供电时电压是背馈假象(~3.7V))'),
        'input_schema': CarStatusInput.model_json_schema(),
        'output_schema': CarStatusOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        battery: Optional[Dict[str, Any]] = None
        odom: Optional[Dict[str, Any]] = None
        battery_evt = asyncio.Event()
        odom_evt = asyncio.Event()

        async def on_battery(source, data: Dict[str, Any]) -> None:
            nonlocal battery
            battery = data
            battery_evt.set()

        async def on_odom(source, data: Dict[str, Any]) -> None:
            nonlocal odom
            odom = data
            odom_evt.set()

        # 先 pubsub 订阅(topic 名静态可知 == ROS 原名, 不丢首帧),
        # 再让桥挂 ROS 订阅
        await self.subscribe(_BATTERY, on_battery, namespace=_NS)
        await self.subscribe(_ODOM, on_odom, namespace=_NS)
        try:
            await bridge_req(self, 'subscribe', {
                'topic': _BATTERY, 'type': 'std_msgs/UInt16'})
            await bridge_req(self, 'subscribe', {
                'topic': _ODOM, 'type': 'nav_msgs/Odometry', 'hz': 5.0})

            async def _wait(evt: asyncio.Event) -> None:
                try:
                    await asyncio.wait_for(evt.wait(), _WAIT)
                except asyncio.TimeoutError:
                    pass   # 话题静默(车不在线): 对应字段保持 None
            await asyncio.gather(_wait(battery_evt), _wait(odom_evt))
        finally:
            # 清理路径吞错: 不掩盖 run 主体抛出的原始异常
            for topic in (_BATTERY, _ODOM):
                try:
                    await bridge_req(self, 'unsubscribe', {'topic': topic})
                except Exception:
                    pass
            await self.unsubscribe(_BATTERY, namespace=_NS)
            await self.unsubscribe(_ODOM, namespace=_NS)

        out = CarStatusOutput()
        if battery is not None:
            out.voltage_v = float(battery.get('data', 0)) / 1000.0
            out.battery_pct = max(0.0, min(100.0,
                (out.voltage_v - 10.0) / 2.3 * 100.0))
        if odom is not None:
            pose = odom.get('pose', {}).get('pose', {})
            pos = pose.get('position', {})
            quat = pose.get('orientation', {})
            out.odom_x = pos.get('x')
            out.odom_y = pos.get('y')
            out.odom_theta = 2.0 * math.atan2(
                float(quat.get('z', 0.0)), float(quat.get('w', 1.0)))
        return out.model_dump()
