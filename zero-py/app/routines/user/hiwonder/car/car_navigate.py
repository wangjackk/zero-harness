"""CarNavigate ---- 导航到命名区域(区域→停泊坐标 → /nav_goal 一帧).

区域名是人语义, 坐标是车语义: 本 routine 只负责映射(车上 :8001 区域
表, 前端导航页标定/设点维护), 车只接受坐标 ---- 目标经 ros_bridge
publish 到 /nav_goal(geometry_msgs/PoseStamped, frame=map), 导航栈
(脱困中继 + move_base)接管, 到点即停不管朝向. 发目标前预检导航栈
状态: 没跑直接报错(不静默发一帧没人接), 引导先 car/navigate/manage.
是否到达/耗时是导航栈的事, 本 routine 只管"发对坐标".
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from httpx import AsyncClient
from pydantic import BaseModel, Field

from routine import Routine

from ..bridge_req import bridge_req
from . import zones

_GOAL_TOPIC = '/nav_goal'
_GOAL_TYPE = 'geometry_msgs/PoseStamped'


class CarNavigateInput(BaseModel):
    """car/navigate input."""

    area: str = Field(description='区域名, 如 "客厅"/"主卧" (前端导航页标定)')


class CarNavigateOutput(BaseModel):
    """car/navigate output."""

    area: str = Field(description='命中的区域名')
    x: float = Field(description='目标 x (m, 地图坐标)')
    y: float = Field(description='目标 y (m, 地图坐标)')
    sent: bool = Field(description='目标帧已发到 /nav_goal')


class CarNavigate(Routine):
    """导航到命名区域: 区域→停泊坐标映射, /nav_goal 一帧后导航栈接管."""

    name = 'car/navigate'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '导航到命名区域(如 客厅/主卧): 查车上区域表取停泊坐标 '
            '(前端导航页"设点"维护, 未设点用区域中心), 发 /nav_goal 一帧, '
            '导航栈接管(含充电位脱困). 导航栈没跑时直接报错, 先 '
            'car/navigate/manage 启动(车需在充电位). 未知区域名报错并列出'
            '可用区域'),
        'input_schema': CarNavigateInput.model_json_schema(),
        'output_schema': CarNavigateOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarNavigateInput.model_validate(kwargs)
        await self._require_nav()
        zs = await zones.fetch_zones()
        z = zones.find_zone(inp.area, zs)
        if z is None:
            names = ', '.join(str(x.get('name', '')) for x in zs) or '(无已标定区域)'
            raise ValueError(f'未知区域 "{inp.area}"; 可用: {names}')
        x, y = zones.zone_target(z)
        await bridge_req(self, 'publish', {
            'topic': _GOAL_TOPIC, 'type': _GOAL_TYPE,
            'msg': {
                'header': {'frame_id': 'map'},
                'pose': {
                    'position': {'x': x, 'y': y, 'z': 0.0},
                    'orientation': {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0},
                },
            },
        })
        return CarNavigateOutput(
            area=str(z.get('name', inp.area)), x=x, y=y, sent=True).model_dump()

    @staticmethod
    async def _require_nav() -> None:
        """预检导航栈在跑; 没跑报错(发 /nav_goal 也没人接, 不静默失败)."""
        async with AsyncClient(timeout=5.0) as c:
            r = await c.get(zones._origin() + '/api/nav/status')
            r.raise_for_status()
            active = bool(r.json().get('active'))
        if not active:
            raise RuntimeError(
                '导航栈未启动(slam-nav), 目标没人接收; '
                '先 car/navigate/manage {"action": "start"} 启动 '
                '(AMCL 初始位姿为墙角充电位, 车需先归位)')
