"""BusListener ---- 设备事件总线 listener (ZMQ SUB → pubsub + 日志).

被动常驻: 连车端 broker (sherpa-onnx services zmqproxy) 的订阅端, 只读不发布
(命令 topic 是服务间控制面, 误发会干扰状态机). 事件不持久 ---- 不在线
就错过, ZMQ 自动重连, 无需就绪探测.

总线是全设备事件总线, 不止语音. 订阅表按域 curate, 只订低频事件 topic;
高频帧 (voice.mic.frame / vision.cam.rgb.raw / vision.pose.raw) 不订,
PCM/图像帧会淹没事件处理.

帧 = topic 前缀 + JSON 载荷, 单帧无分隔符. 归一化后按域 publish:
  voice.wake       唤醒词命中 {word, ts}          → pubsub 'voice'
  voice.orch.evt   type=result/aborted/state       → pubsub 'voice'
  vision.cam.evt   相机事件 {action, fps, ...}     → pubsub 'vision'
  vision.pose.evt  姿态事件                       → pubsub 'vision'

总线地址缺省取 app/config.yaml (network.device_bus) ---- 网络配置唯一
事实源.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar, Dict, Tuple

from pydantic import BaseModel, Field
import zmq
import zmq.asyncio

from routine import Routine, request
from routine.logger import setup_logger

from app.config import network

_log = setup_logger('bus.listen')

# 订阅表: topic → (pubsub 域, 归一化 kind). 只列低频事件 topic;
# 高频帧/命令面 (mic.frame / *.raw / *.cmd) 一律不订.
_TOPICS: Dict[bytes, Tuple[str, str]] = {
    b'voice.wake': ('voice', 'wake'),
    b'voice.orch.evt': ('voice', 'orch'),
    b'vision.cam.evt': ('vision', 'cam'),
    b'vision.pose.evt': ('vision', 'pose'),
}

# recv 轮询粒度: stop() 的最大退出延迟
_POLL_S = 0.5

# wake 置信分阈值: 低于此值按噪声忽略(缺 score 一并按噪声)
_WAKE_SCORE_MIN = 800


def _split_topic(msg: bytes) -> Tuple[bytes, bytes]:
    """按已知前缀切分总线帧; 未知 topic 抛 ValueError."""
    for t in _TOPICS:
        if msg.startswith(t):
            return t, msg[len(t):]
    raise ValueError(f'unknown topic: {msg[:16]!r}')


class BusListenInput(BaseModel):
    """bus_listen input."""

    bus: str = Field(
        default_factory=lambda: network().device_bus,
        description='设备事件总线 ZMQ 订阅端地址 (zmqproxy SUB 端)')


class BusListener(Routine):
    """监听设备事件总线: voice/vision 域事件 → pubsub 'voice'/'vision' + 日志."""

    name = 'bus_listener'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '设备事件总线 listener: voice/vision 域事件按域 publish 到 '
            "pubsub 'voice'/'vision' 并打日志. 暂不转发 agent (后续接通)."),
        'input_schema': BusListenInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = asyncio.Event()
        self._frames = 0

    async def run(self, kwargs: Dict[str, Any]) -> None:
        inp = BusListenInput.model_validate(kwargs or {})
        self._frames = 0
        zctx = zmq.asyncio.Context()
        sub = zctx.socket(zmq.SUB)
        sub.connect(inp.bus)
        for t in _TOPICS:
            sub.setsockopt(zmq.SUBSCRIBE, t)
        _log.info('device bus up: %s (topics: %s)',
                  inp.bus, ', '.join(t.decode() for t in _TOPICS))
        try:
            while not self._stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(sub.recv(), _POLL_S)
                except asyncio.TimeoutError:
                    continue
                self._frames += 1
                await self._on_frame(msg)
        finally:
            # linger=0: 不等对端确认直接关, 免退出挂住
            sub.close(linger=0)
            zctx.term()

    @request('ping')
    async def _req_ping(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """诊断: run 循环状态 + 已收帧数."""
        return {'ok': True, 'frames': self._frames,
                'running': not self._stop_event.is_set()}

    async def _on_frame(self, msg: bytes) -> None:
        """单帧分发: 解析 → 按域归一化 → publish + 日志打印."""
        try:
            topic, body = _split_topic(msg)
            evt = json.loads(body.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
            _log.warning('bad frame on device bus: %r', e)
            return
        if not isinstance(evt, dict):
            _log.warning('bad frame payload: %r', evt)
            return

        domain, kind = _TOPICS[topic]
        if kind == 'wake':
            # 低分 wake 按噪声忽略(缺 score 一并按噪声)
            if evt.get('score', 0) < _WAKE_SCORE_MIN:
                return
            frame = {'kind': 'wake', 'word': evt.get('word'),
                     'ts': evt.get('ts'), 'cal_angle': evt.get('cal_angle')}
            _log.info('wake word: %s', evt)
            # wake 提示音 fire-and-forget: 播放约 1s, await 会把 publish
            # 和 recv 循环一起卡到播完, 订阅者延迟即源于此
            asyncio.create_task(self._wake_beep())
        elif kind == 'orch':  # voice.orch.evt 内部按 type 细分
            etype = str(evt.get('type') or '')
            if etype == 'result':
                frame = {'kind': 'result', 'text': evt.get('text'),
                         'engine': evt.get('engine')}
                _log.info('asr final: %s (engine=%s)',
                          frame.get('text'), frame.get('engine'))
            elif etype == 'aborted':
                frame = {'kind': 'aborted', 'reason': evt.get('reason')}
            else:  # state 变迁 / 未来新增类型: 原样带 type
                frame = {'kind': 'state', 'state': evt.get('state'),
                         'engine': evt.get('engine'), 'type': etype}
        else:  # vision 域: 事件结构由各视觉服务自定, 原样透传
            frame = {'kind': kind, **evt}
            _log.debug('%s evt: %s', kind, evt)

        await self.publish(domain, frame)

    async def _wake_beep(self) -> None:
        """唤醒提示音: audio 被在播音乐占(barage-in teardown)时 StartError,
        打日志放弃即可----提示音非关键路径."""
        try:
            await self.call('music/play', kwargs={'name': 'wake'})
        except Exception as exc:
            _log.warning('wake beep skipped: %s', exc)

    async def stop(self) -> None:
        self._stop_event.set()
