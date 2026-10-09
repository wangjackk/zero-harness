"""CarWakeTrack ---- 声源定向粗调 + 视觉跟踪精调的复合 routine.

武装窗口(armed_s)内监听 voice 域 wake 事件(bus_listener 转发, 携 mic
DOA 标定角 cal_angle); 命中后单次调用 car/track: 开环转向声源方位
(粗调), 目标进画面即无缝交棒 pose 视觉闭环(精调). 显式运行才武装
---- 不做常开被动监听, 每次听到唤醒词就动太危险; 窗口到期自动解除.

角度语义: cal_angle 0=正前, 顺时针增(罗盘式), signed ∈ (-180, 180];
|signed| <= near_deg 视为已对准, 直接纯视觉. REP 103: 顺时针(向右) =
angular.z 负. 粗调角按 align_margin 欠调(底盘转速标定误差会过调, 全转
后被视觉甩回 ---- "转过头再转回来")传给 car/track.align_deg, 由其
开环段转向; 目标进画面即无缝交棒视觉闭环收敛.

重复唤醒即时响应: 唤醒回调同步 bump 动作代际, 打断在跑的运动(下一拍
自退, 零速由其 finally 保证), 新角度立即重来 ---- 不等上一轮 track_s
跑完(实测会排队 4.4s+).

全部运动走单次 car/track 调用(align_deg + max_run_s) ---- 粗调与视觉
在同一条速度波内交接, 无两段式零速缝; car/stop / 新动作的代际互斥与
零速保证由被调 routine 自带, 本 routine 不发 twist.
"""
from __future__ import annotations

import asyncio
import time
from asyncio import Event
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine, subscribe
from routine.logger import setup_logger

from . import car_ctrl

_log = setup_logger('car.wake_track')


class CarWakeTrackInput(BaseModel):
    """car/wake_track input."""

    armed_s: float = Field(
        300, ge=10, le=3600,
        description='武装窗口秒数: 期间监听唤醒词, 到期自动解除')
    track_s: float = Field(
        30, ge=5, le=600,
        description='每次唤醒后的视觉跟踪秒数(car/track)')
    align_rate: float = Field(
        1.0, ge=0.1, le=1.5,
        description='声源粗调角速度 rad/s(0.5 时 160 度唤醒要 4.7s, '
                    '实测体感过慢; 1.0 减半且在底盘安全转速内)')
    max_align_deg: float = Field(
        180, ge=30, le=180,
        description='单次粗调角差上限(度). 须允许到 180: 正后方唤醒'
                    '(±179°)若被砍到 160, 欠调后人在 FOV 外, 视觉接不住'
                    '(实测"唤醒了没跟"); 异常角最多引发一次有界回转, '
                    '无人则待命, 不会失控')
    near_deg: float = Field(
        8, ge=0, le=45,
        description='声源角差小于此值视为已对准, 跳过粗调直接进视觉')
    align_margin: float = Field(
        0.85, ge=0.5, le=1.0,
        description='粗调角比例(欠调余量): 只转声源角的该比例, 剩余由'
                    '视觉闭环收敛 ---- 开环全转会因底盘转速标定误差过调,'
                    '再被视觉甩回来(转过头再转回头的根因); 0.85 保证'
                    '160 度档欠调后仍差 24 度, 人在 FOV 内接得住')


class CarWakeTrack(Routine):
    """唤醒联动: wake 事件带 DOA 角 -> 转向声源 -> 视觉跟踪接管."""

    name = 'car/wake_track'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '武装窗口内听到唤醒词: 先转向声源方位, 再视觉跟踪人',
        'input_schema': CarWakeTrackInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        self._armed: bool = False
        self._wake_evt: Optional[Event] = None
        self._pending_deg: Optional[float] = None
        self._pending_ts: Optional[float] = None

    @subscribe('voice')
    async def on_voice(self, source, data: Dict[str, Any]) -> None:
        """wake 帧(bus_listener 转发) -> 记下声源角, 唤醒等待循环."""
        if not self._armed or self._wake_evt is None:
            return
        frame = data or {}
        if frame.get('kind') != 'wake':
            return
        deg = frame.get('cal_angle')
        if isinstance(deg, (int, float)):
            self._pending_deg = float(deg)
            self._pending_ts = frame.get('ts')
            # 同步 bump 代际: 打断在跑的 align/track(下一拍自退, finally
            # 零速自带). 必须同步且无 I/O ---- pubsub 分发保证本回调先于
            # run 循环恢复执行, 新 align 的代际必然最新; 若走 car/stop
            # 异步调用, 其 bump 与新 align 的 bump 存在竞态, 晚到一步
            # 会把刚起跑的新 align 一拍打死(实测"唤醒了却不动").
            car_ctrl.bump_generation()
            self._wake_evt.set()

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarWakeTrackInput.model_validate(kwargs or {})
        self._armed = True
        self._wake_evt = Event()
        aligns: List[float] = []
        latencies: List[float] = []
        wakes = 0
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + inp.armed_s
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._wake_evt.wait(), remaining)
                except asyncio.TimeoutError:
                    break
                self._wake_evt.clear()
                deg, self._pending_deg = self._pending_deg, None
                wake_ts, self._pending_ts = self._pending_ts, None
                if deg is None:
                    continue
                wakes += 1
                # 时延探针: 设备侧唤醒时间戳 -> 本 routine 收到
                if isinstance(wake_ts, (int, float)):
                    latency = time.time() - wake_ts
                    _log.info('wake latency: %.2fs', latency)
                    latencies.append(round(latency, 2))
                # 罗盘角 -> 带符号角差: 0=正前, 顺时针为正, 并限幅
                signed = deg if deg <= 180 else deg - 360
                signed = max(-inp.max_align_deg,
                             min(inp.max_align_deg, signed))
                if abs(signed) <= inp.near_deg:
                    _log.info('wake @%.0f deg: within near band, pure visual',
                              signed)
                    await self.call('car/track',
                                    {'max_run_s': inp.track_s})
                else:
                    # 单次调用合并粗调+精调: car/track 同一条速度波内
                    # 开环转向声源角(欠调 margin 余量), 目标进画面即
                    # 无缝交棒视觉闭环 ---- 两段式(car/turn 结束发零速
                    # 再 car/track 重新起步)的中途死站已被实测证伪.
                    align_deg = signed * inp.align_margin
                    _log.info('wake @%.0f deg: unified align %.0f -> track',
                              signed, align_deg)
                    await self.call('car/track',
                                    {'max_run_s': inp.track_s,
                                     'align_deg': align_deg,
                                     'align_rate': inp.align_rate})
                    aligns.append(signed)
        finally:
            self._armed = False
            self._wake_evt = None
        return {
            'command': (f'wake_track armed {inp.armed_s:g}s '
                        f'track {inp.track_s:g}s'),
            'armed_s': inp.armed_s,
            'wakes': wakes,
            'aligns_deg': aligns,
            'wake_latencies': latencies,
        }
