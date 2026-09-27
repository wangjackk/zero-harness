"""VoiceListener ---- 语音总线 listener (ZMQ SUB → pubsub + 日志).

被动常驻: 连 sherpa-onnx services 的 zmqproxy 总线订阅端, 只读不发布
(命令 topic 是服务间控制面, 误发会干扰状态机). 事件不持久 ---- 不在线
就错过, ZMQ 自动重连, 无需就绪探测.

事件流 (帧 = topic 前缀 + JSON 载荷, 单帧无分隔符, 前缀互不为彼此前缀,
所有 topic 带领域前缀 voice.):
  voice.wake      唤醒词命中 {word, ts}
  voice.orch.evt  type=result   一轮识别完成 {text, engine}   ← 最终文本
                 type=aborted  轮次放弃 {reason}
                 type=state    状态机变迁 {state, engine}

消费: 全部事件归一化后 publish 到 pubsub topic 'voice' (帧: {kind,
...原始字段}) 并打日志; 暂不转发 agent (后续接通).
总线地址缺省取 app/config.yaml (network.voice_bus) ---- 网络配置唯一
事实源.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
import zmq
import zmq.asyncio

from routine import Routine, request
from routine.logger import setup_logger

from app.config import network

_log = setup_logger('voice.listen')

# 只订阅事件 topic (audio 每秒 10 帧 PCM 会淹没事件处理; asr.evt 的
# partial/final 是引擎层细节, 编排层消费 orch.evt 的 result 即可)
_TOPICS = (b'voice.wake', b'voice.orch.evt')

# recv 轮询粒度: stop() 的最大退出延迟
_POLL_S = 0.5

# wake 置信分阈值: 低于此值按噪声忽略(缺 score 一并按噪声)
_WAKE_SCORE_MIN = 800


def _split_topic(msg: bytes) -> tuple[bytes, bytes]:
    """按已知前缀切分总线帧; 未知 topic 抛 ValueError."""
    for t in _TOPICS:
        if msg.startswith(t):
            return t, msg[len(t):]
    raise ValueError(f'unknown topic: {msg[:16]!r}')


class VoiceListenInput(BaseModel):
    """voice_listen input."""

    bus: str = Field(
        default_factory=lambda: network().voice_bus,
        description='语音总线 ZMQ 订阅端地址 (zmqproxy SUB 端)')


class VoiceListener(Routine):
    """监听语音总线: 唤醒/识别事件 → pubsub 'voice' + 日志打印."""

    name = 'voice_listener'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '语音总线 listener: 唤醒词与识别事件 publish 到 pubsub '
            "topic 'voice' 并打日志. 暂不转发 agent (后续接通)."),
        'input_schema': VoiceListenInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = asyncio.Event()
        self._frames = 0

    async def run(self, kwargs: Dict[str, Any]) -> None:
        inp = VoiceListenInput.model_validate(kwargs or {})
        self._frames = 0
        zctx = zmq.asyncio.Context()
        sub = zctx.socket(zmq.SUB)
        sub.connect(inp.bus)
        for t in _TOPICS:
            sub.setsockopt(zmq.SUBSCRIBE, t)
        _log.info('voice bus up: %s (topics: voice.wake, voice.orch.evt)',
                          inp.bus)
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
        """单帧分发: 解析 → publish 'voice' + 日志打印."""
        try:
            topic, body = _split_topic(msg)
            evt = json.loads(body.decode('utf-8') or '{}')
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
            _log.warning('bad frame on voice bus: %r', e)
            return
        if not isinstance(evt, dict):
            _log.warning('bad frame payload: %r', evt)
            return

        if topic == b'voice.wake':
            # 低分 wake 按噪声忽略(缺 score 一并按噪声)
            if evt.get('score', 0) < _WAKE_SCORE_MIN:
                return
            frame = {'kind': 'wake', 'word': evt.get('word'),
                     'ts': evt.get('ts')}
            _log.info('wake word: %s', evt)
            # wake 提示音 fire-and-forget: 播放约 1s, await 会把 publish
            # 和 recv 循环一起卡到播完, 订阅者延迟即源于此
            asyncio.create_task(self._wake_beep())
        else:  # voice.orch.evt
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

        await self.publish('voice', frame)

    async def _wake_beep(self) -> None:
        """唤醒提示音: audio 被在播音乐占(barage-in teardown)时 StartError,
        打日志放弃即可----提示音非关键路径."""
        try:
            await self.call('music/play', kwargs={'name': 'wake'})
        except Exception as exc:
            _log.warning('wake beep skipped: %s', exc)

    async def stop(self) -> None:
        self._stop_event.set()
