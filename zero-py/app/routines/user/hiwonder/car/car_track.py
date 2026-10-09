"""CarTrack ---- 小车水平跟踪人(脸优先, 身体兜底).

订阅设备总线的 vision.pose.raw(高频 pose 流, bus_listener 不订, 本
routine 自持 SUB 只留最新帧), 每控制拍(10Hz)按目标 x 偏差原地旋转,
使人保持在画面水平中心. 纯旋转不位移(vx=0).

目标选择: 人脸优先 ---- 鼻/眼关键点置信达标(kpts 为 COCO 17, 0=鼻 1/2=眼)
算有脸, 取其均值 x; 全场无人脸时退化为身体(box 中心 x). 多人按
(有脸, 面积) 择优.

防抖(容忍度)三件套, 缺一不可:
  1. EMA 平滑目标 x ---- 吸收逐帧检测抖动;
  2. 死区 + 滞回 ---- 偏差超出 dead_enter 才起转, 收敛到 dead_exit
     内才停转; 中间带不动作, 人在容忍环带里小幅晃动小车无反应;
  3. 面部置信门 ---- conf < face_conf 的脸关键点不算脸(防误检).

丢失语义: pose 流断供超 1s(服务停/网络断)或当前无人, 原地待命
(wz=0)不追鬼; 整体由 max_run_s 兜底, car/stop 与新动作照常打断,
零速保证与代际互斥见 motion.py / car_ctrl.py.

声源粗调合一(align_deg): 给定声源角时, 视觉目标出现前先开环原地
转向该角度, 目标一进画面立即交棒视觉闭环(EMA 直接播种, 免预热)
---- 同一条速度波内完成粗调+精调, 不存在"car/turn 结束发零速 →
car/track 重新起步"的中途停顿(实测两段式接缝会死站一下再启动).
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

import zmq
import zmq.asyncio

from routine.logger import setup_logger

from app.config import network

from .motion import MotionRoutine

_log = setup_logger('car.track')

# pose 帧宽: 对齐 poseserver_nano.py 的 W(640x400); 载荷坐标是该坐标系
_FRAME_W = 640.0

# 总线 topic: topic 前缀 + 4B seq + JSON {seq, persons:[{box, conf, kpts}]}
_TOPIC = b'vision.pose.raw'

# COCO 17 关键点中的脸部索引: 0=鼻, 1/2=眼
_FACE_KPTS = (0, 1, 2)

# pose 断供判定: 超过该秒数视为无新鲜感知, 待命
_STALE_S = 1.0


class CarTrackInput(BaseModel):
    """car/track input."""

    max_run_s: float = Field(
        120, ge=10, le=600,
        description='最长跟踪秒数(兜底); car/stop 或新动作可随时打断')
    face_conf: float = Field(
        0.3, ge=0.05, le=0.95,
        description='脸关键点(鼻/眼)置信门, 低于不算脸 -> 用身体')
    dead_enter: float = Field(
        0.07, ge=0.01, le=0.3,
        description='起转死区(帧宽比例): 偏差超出才开始转')
    dead_exit: float = Field(
        0.03, ge=0.0, le=0.3,
        description='停转死区(帧宽比例): 收敛到此内才停(须小于 dead_enter)')
    gain: float = Field(
        2.0, ge=0.1, le=8.0,
        description='P 控制增益: wz = gain * |偏差|, 再限幅到 max_rate')
    max_rate: float = Field(
        0.6, ge=0.05, le=1.5,
        description='最大角速度 rad/s')
    smooth: float = Field(
        0.4, ge=0.05, le=1.0,
        description='目标 x 的 EMA 系数(越大跟得越紧, 越小越稳)')
    align_deg: float = Field(
        0.0, ge=-180.0, le=180.0,
        description='声源粗调角(度, 已含欠调余量): 视觉目标出现前先'
                    '开环转向该角度; 0=纯视觉无粗调')
    align_rate: float = Field(
        0.5, ge=0.1, le=1.5,
        description='开环粗调角速度 rad/s')

    @model_validator(mode='after')
    def _hysteresis(self) -> 'CarTrackInput':
        if self.dead_exit >= self.dead_enter:
            raise ValueError('dead_exit 须小于 dead_enter(否则滞回退化)')
        return self


class CarTrack(MotionRoutine):
    """水平跟踪人: 脸优先/身体兜底, 死区滞回防抖, 原地旋转对准."""

    name = 'car/track'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '小车水平跟踪人(人脸优先, 无脸用身体), 防抖容忍环带',
        'input_schema': CarTrackInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        # pose 流状态(feed task 写, control 读; 同一 event loop 无锁)
        self._persons: Optional[List[Dict[str, Any]]] = None
        self._recv_monotonic: float = 0.0
        # 控制状态(control 独占)
        self._target_x: Optional[float] = None
        self._rotating: bool = False
        # 开环粗调段状态
        self._align_left: float = 0.0    # 剩余粗调秒数
        self._align_sign: float = 1.0    # +1 右转 / -1 左转
        self._t: Optional[float] = None  # 上一拍 t(算 dt 用)

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarTrackInput.model_validate(kwargs or {})
        self._persons = None
        self._target_x = None
        self._rotating = False
        self._align_left = (math.radians(abs(inp.align_deg))
                            / inp.align_rate) if inp.align_deg else 0.0
        self._align_sign = 1.0 if inp.align_deg >= 0 else -1.0
        self._t = None
        feed = asyncio.create_task(self._pose_feed())
        try:
            command = (f'track face-first dead {inp.dead_enter:g}/'
                       f'{inp.dead_exit:g} gain={inp.gain:g} '
                       f'max_run={inp.max_run_s:g}s')
            return await self._execute_wave(
                lambda t: self._control(inp, t), inp.max_run_s, command)
        finally:
            feed.cancel()

    # ── pose 流 ───────────────────────────────────────────────────

    async def _pose_feed(self) -> None:
        """ZMQ SUB vision.pose.raw, 排空积压只留最新帧."""
        zctx = zmq.asyncio.Context()
        sub = zctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, _TOPIC)
        sub.setsockopt(zmq.RCVHWM, 4)
        sub.connect(network().device_bus)
        _log.info('pose feed up: %s (%s)', network().device_bus,
                  _TOPIC.decode())
        try:
            while True:
                msg = await sub.recv()
                self._store(msg)
                # 排空积压, 只留最新帧. zmq.asyncio 的 recv() 调用点不抛
                # Again(异常发生在返回的 Future 里), 必须用 await poll(0)
                # 探可读, 不能照抄同步 socket 的 DONTWAIT+except 写法.
                while await sub.poll(0):
                    msg = await sub.recv()
                    self._store(msg)
        finally:
            sub.close(linger=0)
            zctx.term()

    def _store(self, msg: bytes) -> None:
        """解析单帧(4B seq + JSON), 失败丢弃不致命."""
        try:
            evt = json.loads(msg[len(_TOPIC) + 4:].decode('utf-8'))
            persons = evt.get('persons')
            if isinstance(persons, list):
                self._persons = persons
                self._recv_monotonic = time.monotonic()
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            _log.debug('bad pose frame: %r', e)

    # ── 控制 ──────────────────────────────────────────────────────

    def _control(self, inp: CarTrackInput, t: float) -> Tuple[float, float, float]:
        """单拍控制: (vx, vy, wz). vx=vy=0 纯原地转."""
        dt = t - self._t if self._t is not None else 0.0
        self._t = t

        # 开环粗调段: 视觉目标出现前按 align_rate 转向声源角.
        # 目标一进画面立即交棒 ---- EMA 直接播种为当前 tx, 跳过
        # 预热期, 粗调与视觉闭环之间零速缝为零, 运动不断档.
        if self._align_left > 0.0:
            self._align_left -= dt
            tx = None
            if (self._persons is not None
                    and time.monotonic() - self._recv_monotonic <= _STALE_S):
                tx = self._pick_target_x(inp.face_conf)
            if tx is not None:
                self._target_x = tx
                self._align_left = 0.0
            else:
                # REP 103: 声源在右(align_sign>0) -> 右转 wz 负
                wz = -inp.align_rate if self._align_sign > 0 else inp.align_rate
                return (0.0, 0.0, wz)

        if (self._persons is None
                or time.monotonic() - self._recv_monotonic > _STALE_S):
            self._target_x = None   # 断供/无人: 状态清零, 恢复后冷启动
            self._rotating = False
            return (0.0, 0.0, 0.0)

        tx = self._pick_target_x(inp.face_conf)
        if tx is None:
            self._target_x = None
            self._rotating = False
            return (0.0, 0.0, 0.0)

        # EMA 平滑: 吸收逐帧抖动
        if self._target_x is None:
            self._target_x = tx
        else:
            self._target_x += inp.smooth * (tx - self._target_x)

        e = (self._target_x - _FRAME_W / 2) / _FRAME_W
        ae = abs(e)
        # 滞回死区: 起转看 dead_enter, 停转看 dead_exit
        if self._rotating:
            if ae <= inp.dead_exit:
                self._rotating = False
        elif ae >= inp.dead_enter:
            self._rotating = True
        if not self._rotating:
            return (0.0, 0.0, 0.0)

        # 偏差带符号: e>0 = 人在画面右 -> 右转(angular.z 负);
        # e<0 = 人在左 -> 左转(正). REP 103
        rate = min(inp.max_rate, inp.gain * ae)
        wz = -rate if e > 0 else rate
        return (0.0, 0.0, wz)

    def _pick_target_x(self, face_conf: float) -> Optional[float]:
        """按 (有脸, 面积) 择优选人; 脸优先取脸 x, 否则 box 中心 x."""
        best: Optional[Tuple[Tuple[int, float], Optional[float], float]] = None
        for p in self._persons or []:
            box = p.get('box') or []
            kpts = p.get('kpts') or []
            if len(box) < 4 or len(kpts) < 5:
                continue
            area = max(0.0, (box[2] - box[0]) * (box[3] - box[1]))
            face_x = self._face_x(kpts, face_conf)
            score = (1 if face_x is not None else 0, area)
            if best is None or score > best[0]:
                best = (score, face_x, (box[0] + box[2]) / 2)
        if best is None:
            return None
        return best[1] if best[1] is not None else best[2]

    @staticmethod
    def _face_x(kpts: List[List[float]], face_conf: float) -> Optional[float]:
        """脸 x: 置信达标的鼻/眼关键点均值; 全不达标 = 无脸."""
        xs = [k[0] for i in _FACE_KPTS
              if len(k := kpts[i]) >= 3 and k[2] >= face_conf]
        return sum(xs) / len(xs) if xs else None
