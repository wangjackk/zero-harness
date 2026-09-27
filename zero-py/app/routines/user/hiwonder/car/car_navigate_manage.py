"""CarNavigateManage ---- 导航栈生命周期管理(启动/停止/重启).

导航栈(slam-nav.service: map_server+AMCL+move_base+脱困中继)在车上
经 :8001 控制, 与前端导航页"真车"开关同一通道; 本 routine 把它暴露
给 AI. 注意: 栈(重)启后 AMCL 初始位姿固定为墙角充电位 ---- 车不在
充电位时先归位再重启, 否则定位错. car/navigate 只发目标, 栈得先跑.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Literal

from httpx import AsyncClient
from pydantic import BaseModel, Field

from routine import Routine

from . import zones


class CarNavigateManageInput(BaseModel):
    """car/navigate/manage input."""

    action: Literal['start', 'stop', 'restart'] = Field(
        description='start=启动导航栈(AMCL 初始位姿为墙角充电位, 车需先'
                    '归位) stop=停止 restart=重启(改参数/消除定位漂移用)')


class CarNavigateManageOutput(BaseModel):
    """car/navigate/manage output."""

    action: str = Field(description='执行的动作')
    active: bool = Field(description='导航栈最终是否在跑')


class CarNavigateManage(Routine):
    """导航栈管理: 启动/停止/重启(车上 slam-nav.service)."""

    name = 'car/navigate/manage'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '管理小车导航栈(启动/停止/重启, 与前端导航页"真车"开关同一'
            '通道). 导航栈在跑 car/navigate 才有效; (重)启后 AMCL 初始'
            '位姿为墙角充电位, 车需先停回充电位'),
        'input_schema': CarNavigateManageInput.model_json_schema(),
        'output_schema': CarNavigateManageOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarNavigateManageInput.model_validate(kwargs)
        origin = zones._origin()
        async with AsyncClient(timeout=10.0) as c:
            if inp.action in ('stop', 'restart'):
                await self._post(c, origin, 'stop')
                await self._wait(c, origin, False, 10.0)
            if inp.action in ('start', 'restart'):
                await self._post(c, origin, 'start')
                await self._wait(c, origin, True, 20.0)
            active = await self._status(c, origin)
        return CarNavigateManageOutput(
            action=inp.action, active=active).model_dump()

    @staticmethod
    async def _status(c: AsyncClient, origin: str) -> bool:
        r = await c.get(origin + '/api/nav/status')
        r.raise_for_status()
        return bool(r.json().get('active'))

    @staticmethod
    async def _post(c: AsyncClient, origin: str, act: str) -> None:
        r = await c.post(origin + f'/api/nav/{act}')
        r.raise_for_status()
        body = r.json()
        if not body.get('ok'):
            raise RuntimeError(f'nav {act} failed: {body.get("err", "")}')

    @staticmethod
    async def _wait(c: AsyncClient, origin: str, want: bool,
                    timeout: float) -> None:
        """轮询直到状态到位; 超时不抛错, 由最终 active 说话."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await CarNavigateManage._status(c, origin) == want:
                return
            await asyncio.sleep(0.5)
