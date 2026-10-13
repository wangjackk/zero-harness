"""CarWhere ---- 查询小车当前所在区域(AMCL 位姿 → 区域名反查).

定位与区域是两套语义: 位姿是 ROS 的(AMCL 地图坐标), 区域名是人的.
本 routine 取 /amcl_pose 最新一帧(经 ros_bridge 桥挂订阅, 3s 等一拍)
+ 车上区域表做坐标→区域反查, 只返回区域名 ---- 位姿是中间量, 不外露.
车不在线/导航栈未跑/不在任何标定区域内时 area 为 null.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine

from ..hiwonder.bridge_req import bridge_req
from . import zones

_POSE = '/amcl_pose'
_POSE_TYPE = 'geometry_msgs/PoseWithCovarianceStamped'
_NS = 'ros'      # 感知桥转发 namespace(与 ros_bridge._NS 一致)
_WAIT = 3.0      # AMCL 位姿发布频率不高: 等一拍 + 往返余量


class CarWhereInput(BaseModel):
    """car/where input(无参数)."""


class CarWhereOutput(BaseModel):
    """car/where output."""

    area: Optional[str] = Field(
        None, description='当前区域名; null = 不在任何标定区域内或定位不可用')


class CarWhere(Routine):
    """查询小车当前所在区域: AMCL 位姿坐标 → 区域名."""

    name = 'car/where'
    meta: ClassVar[Dict[str, Any]] = {
        'readonly': True,
        'description': (
            '查询小车当前所在区域(区域名): AMCL 位姿最新帧 → 车上区域表 '
            '(前端导航页标定)反查. 车不在线/导航栈未跑/不在任何区域时 '
            '返回 null'),
        'input_schema': CarWhereInput.model_json_schema(),
        'output_schema': CarWhereOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        pose: Optional[Dict[str, Any]] = None
        evt = asyncio.Event()

        async def on_pose(source, data: Dict[str, Any]) -> None:
            nonlocal pose
            pose = data
            evt.set()

        # 先 pubsub 订阅(topic 名静态可知, 不丢首帧), 再让桥挂 ROS 订阅
        await self.subscribe(_POSE, on_pose, namespace=_NS)
        try:
            await bridge_req(self, 'subscribe',
                             {'topic': _POSE, 'type': _POSE_TYPE, 'hz': 5.0})
            try:
                await asyncio.wait_for(evt.wait(), _WAIT)
            except asyncio.TimeoutError:
                pass   # 定位不可用(车离线/导航栈未跑): 位姿保持 None
        finally:
            # 清理路径吞错: 不掩盖 run 主体抛出的原始异常
            try:
                await bridge_req(self, 'unsubscribe', {'topic': _POSE})
            except Exception:
                pass
            await self.unsubscribe(_POSE, namespace=_NS)

        area: Optional[str] = None
        if pose is not None:
            p = pose.get('pose', {}).get('pose', {})
            pos = p.get('position', {})
            x, y = pos.get('x'), pos.get('y')
            if x is not None and y is not None:
                area = zones.locate(float(x), float(y),
                                    await zones.fetch_zones())
        return CarWhereOutput(area=area).model_dump()

