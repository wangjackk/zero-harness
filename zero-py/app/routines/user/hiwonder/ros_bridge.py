"""RosBridge ---- ROS 感知桥 (ros_client ↔ pubsub 双向中转, 零业务).

被动常驻, 两个 req 接口:
- subscribe {topic, type, hz?}: 注册转发 ROS topic → pubsub
  (namespace='ros', topic = ROS 原名; 引用计数: 多个 routine 订阅同一
  topic 只挂一条 ROS 订阅; hz 取首个订阅者的值, 后来者不生效 ---- 想改
  节流先 unsubscribe 归零再重订); hz = pubsub 节流上限(传输级 QoS,
  省 = 原频). 配对 unsubscribe. 返回 {topic, namespace}: 消费方按此订阅.
- publish {topic, msg, type?}: 往 ROS 话题发一帧(经 ros_client 常驻
  连接; 失败 = req error, 对 caller 可见).

命名隔离: ROS 话题名经 pubsub namespace 'ros' 隔离(kernel 订阅表按
(namespace, topic) 精确匹配), topic 名保持 ROS 原名零改写; 载荷 = msg
dict 原样, 电池电量估算/四元数换算等解释逻辑全在消费方. 车控 cmd_vel
不走本桥(安全语义在 car/car_ctrl 直发). 车不在线是常态: ros_client
后台静默重连.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine, request

from . import ros_client

_NS = 'ros'   # pubsub namespace: 隔离 ROS 与 harness 原生命名空间


class SubscribeInput(BaseModel):
    """ros_bridge subscribe req 入参."""

    topic: str = Field(description='ROS 话题名(如 /odom), pubsub 同名')
    type: str = Field(description='ROS 消息类型(如 nav_msgs/Odometry)')
    hz: Optional[float] = Field(
        None, gt=0, description='pubsub 节流上限 Hz; 省 = 原频转发')


class PublishInput(BaseModel):
    """ros_bridge publish req 入参."""

    topic: str = Field(description='ROS 话题名')
    msg: Dict[str, Any] = Field(description='ROS 消息(rosbridge msg dict)')
    type: str = Field(
        '', description='首次发布该话题的 advertise 类型; 省略则假定已广告')


class RosBridge(Routine):
    """ROS 感知桥: 动态订阅转发(pubsub 同名) + ROS 发布中转."""

    name = 'ros_bridge'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'readonly': True,
        'description': (
            "ROS 感知桥(被动常驻): subscribe req 注册 ROS 话题转发到 "
            "pubsub namespace 'ros'(topic = ROS 原名零改写, 载荷原样, "
            'hz 可节流, 引用计数, 返回 {topic, namespace}), publish req '
            '往 ROS 话题发帧; 零业务纯中转, 解释逻辑在消费方'),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = asyncio.Event()
        self._last_pub: Dict[str, float] = {}   # topic → 上次转发时刻(节流用)
        # topic → [引用计数, 转发回调]; 归零才真正退订
        self._entries: Dict[str, List[Any]] = {}

    async def run(self, kwargs: Dict[str, Any]) -> None:
        # zero 启动即开始连桥(连不上按配置自动拉起远端, 冷却 60s)
        ros_client.ensure_started()
        await self._stop_event.wait()

    # ── 订阅转发(ROS → pubsub) ───────────────────────────────────────────

    @request('subscribe')
    async def _req_subscribe(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """注册转发: ROS topic → pubsub namespace 'ros'. 首次挂, 之后计数."""
        inp = SubscribeInput.model_validate(data or {})
        ent = self._entries.get(inp.topic)
        if ent is None:
            hz = inp.hz
            period = 1.0 / hz if hz is not None else None

            async def cb(msg: Dict[str, Any]) -> None:
                if period is not None:
                    now = asyncio.get_running_loop().time()
                    if now - self._last_pub.get(inp.topic, 0.0) < period:
                        return
                    self._last_pub[inp.topic] = now
                await self.publish(inp.topic, msg, namespace=_NS)

            ros_client.add_topic(inp.topic, inp.type, cb)
            self._entries[inp.topic] = [1, cb]
        else:
            ent[0] += 1   # 复用在挂订阅(同一 topic 只一条 ROS 订阅)
        return {'ok': True, 'topic': inp.topic, 'namespace': _NS}

    @request('unsubscribe')
    async def _req_unsubscribe(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """退订(配对 subscribe): 计数归零才真正退 ROS 订阅. 未订阅 = no-op."""
        topic = str((data or {}).get('topic') or '')
        ent = self._entries.get(topic)
        if ent is not None:
            ent[0] -= 1
            if ent[0] <= 0:
                del self._entries[topic]
                ros_client.drop_topic(topic, ent[1])
        return {'ok': True, 'topic': topic, 'namespace': _NS}

    # ── 发布中转(pubsub 消费方 → ROS) ───────────────────────────────────

    @request('publish')
    async def _req_publish(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """往 ROS 话题发一帧(经 ros_client; 车不可达 = req error 可见)."""
        inp = PublishInput.model_validate(data or {})
        await ros_client.publish(inp.topic, inp.msg, inp.type)
        return {'ok': True, 'topic': inp.topic}

    async def stop(self) -> None:
        for topic, ent in list(self._entries.items()):
            ros_client.drop_topic(topic, ent[1])
        self._entries.clear()
        self._last_pub.clear()
        self._stop_event.set()
