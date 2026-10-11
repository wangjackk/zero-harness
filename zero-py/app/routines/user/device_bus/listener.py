"""BusListener ---- 设备事件总线统一收发 (SUB 收事件 + PUB 发 cmd).

被动常驻: 连车端 broker (sherpa-onnx services zmqproxy) 订阅端收低频
事件, 发布端发 vision cmd. cmd 是语义化 req 面(pose_enable/disable/
status) ---- topic/payload 总线协议细节内聚于此, 调用方零协议知识;
常驻 PUB 无逐次握手. 事件不持久 ---- 不在线就错过, ZMQ 自动重连,
无需就绪探测.

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
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Tuple

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field
import zmq
import zmq.asyncio

from routine import Routine, request
from routine.logger import setup_logger

from app.config import network

_log = setup_logger('bus.listen')

# 订阅表: topic → (pubsub 域, 归一化 kind). 只列低频事件 topic;
# 高频帧/命令面 (mic.frame / *.raw / *.cmd) 一律不订(cmd 走语义化 req).
_TOPICS: Dict[bytes, Tuple[str, str]] = {
    b'voice.wake': ('voice', 'wake'),
    b'voice.orch.evt': ('voice', 'orch'),
    b'vision.cam.evt': ('vision', 'cam'),
    b'vision.pose.evt': ('vision', 'pose'),
}

# pose 推理门控: 单轮回执等待窗(state evt 即回, 留网络余量)与
# 发送轮数(首发 PUB→XSUB 慢连接丢包自愈重发一轮)
_POSE_ACK_S = 1.5
_POSE_ATTEMPTS = 2

# 拍照: rgb 流临时订阅期=拍照期, 排干积压只留最新一帧.
# 存 JPG(quality 85): 拍照是"看"的场景, 有损无感, 体积比 PNG 小
# ~10 倍(进 LLM 上下文/传输更快); 无 alpha(RGB 三通道).
_SNAP_TOPIC = b'vision.cam.rgb.raw'
_SNAP_W, _SNAP_H = 640, 400   # camserver mode_index=2, 尺寸不符即协议违规
# device_bus/listener.py -> parents[4] = zero-py/
_SNAP_DIR = Path(__file__).resolve().parents[4] / 'runtime' / 'snaps'

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


async def _snap_latest(sub, timeout: float) -> Tuple[Optional[int], Optional[bytes]]:
    """等第一帧后排干积压, 返回最新 (seq, BGR payload); 超时 (None, None).

    DONTWAIT 在 pyzmq asyncio 走同步短路, 排干不挂事件循环.
    """
    try:
        msg = await asyncio.wait_for(sub.recv(), timeout)
    except asyncio.TimeoutError:
        return None, None
    while True:   # 排干积压只留最新(snap 语义: 拍"现在")
        try:
            msg = await sub.recv(zmq.NOBLOCK)
        except zmq.Again:
            break
    body = msg[len(_SNAP_TOPIC):]
    if len(body) < 4:
        raise RuntimeError(f'truncated rgb frame ({len(body)}B)')
    return int.from_bytes(body[:4], 'big'), body[4:]


def _save_jpg(path: Path, bgr: bytes) -> int:
    """BGR888 原始帧 -> JPG(quality 85)落盘, 返回字节数(纯 worker 线程)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.frombuffer(bgr, dtype=np.uint8).reshape(_SNAP_H, _SNAP_W, 3)
    Image.fromarray(np.ascontiguousarray(arr[:, :, ::-1])).save(
        path, format='JPEG', quality=85)
    return path.stat().st_size


def _hsize(n: int) -> str:
    """字节数人类可读(二进制 KB/MB)."""
    return f'{n / 1048576:.1f} MB' if n >= 1048576 else f'{n / 1024:.1f} KB'


class BusListenInput(BaseModel):
    """bus_listen input."""

    bus: str = Field(
        default_factory=lambda: network().device_bus,
        description='设备事件总线 ZMQ 订阅端地址 (zmqproxy SUB 端)')
    pub: str = Field(
        default_factory=lambda: network().device_bus_pub,
        description='设备事件总线 ZMQ 发布端地址 (zmqproxy XSUB 端, '
                    'vision cmd 白名单发布)')


class BusListener(Routine):
    """设备事件总线统一收发: evt → pubsub 'voice'/'vision' + 语义化 req
    发设备 cmd (pose 门控 / snap 拍照)."""

    name = 'bus_listener'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '设备事件总线 listener: voice/vision 域事件按域 publish 到 '
            "pubsub 'voice'/'vision' 并打日志; req pose_enable/"
            'pose_disable/pose_status 推理即开即关门控, req snap '
            '车头相机拍照(rgb 流最新帧存 JPG 返路径)'),
        'input_schema': BusListenInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = asyncio.Event()
        self._frames = 0
        self._pub: Any = None   # 常驻 cmd PUB(run 建立/回收)
        # pose state 快照(evt 循环写, req handler 读; 同一 loop 无锁)
        self._pose_state: Optional[Dict[str, Any]] = None
        self._pose_evt: Optional[asyncio.Event] = None   # run 里建立
        self._bus_addr: Optional[str] = None   # run 里记(供 snap 临时 SUB)

    async def run(self, kwargs: Dict[str, Any]) -> None:
        inp = BusListenInput.model_validate(kwargs or {})
        self._frames = 0
        self._pose_state = None
        self._pose_evt = asyncio.Event()
        self._bus_addr = inp.bus
        zctx = zmq.asyncio.Context()
        sub = zctx.socket(zmq.SUB)
        sub.connect(inp.bus)
        pub = zctx.socket(zmq.PUB)
        pub.connect(inp.pub)
        self._pub = pub
        for t in _TOPICS:
            sub.setsockopt(zmq.SUBSCRIBE, t)
        _log.info('device bus up: %s / cmd pub: %s (topics: %s)',
                  inp.bus, inp.pub, ', '.join(t.decode() for t in _TOPICS))
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
            self._pub = None
            self._bus_addr = None
            self._pose_evt = None
            sub.close(linger=0)
            pub.close(linger=0)
            zctx.term()

    @request('ping')
    async def _req_ping(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """诊断: run 循环状态 + 已收帧数."""
        return {'ok': True, 'frames': self._frames,
                'running': not self._stop_event.is_set()}

    # ── pose 推理门控 req 面 ───────────────────────────────────────

    @request('pose_enable')
    async def _req_pose_enable(self, source,
                               data: Dict[str, Any]) -> Dict[str, Any]:
        """开推理(省电即时门 <150ms): 发 cmd 等 state 回执确认."""
        return await self._pose_set(True)

    @request('pose_disable')
    async def _req_pose_disable(self, source,
                               data: Dict[str, Any]) -> Dict[str, Any]:
        """关推理(GPU 立即空闲): 发 cmd 等 state 回执确认."""
        return await self._pose_set(False)

    @request('pose_status')
    async def _req_pose_status(self, source,
                               data: Dict[str, Any]) -> Dict[str, Any]:
        """查 pose 服务状态(action:get 拉新, 回不来给缓存快照)."""
        await self._vision_cmd(b'vision.pose.cmd', {'action': 'get'})
        await self._pose_wait(lambda s: True, 1.0)
        return {'ok': self._pose_state is not None,
                'state': self._pose_state}

    async def _pose_set(self, on: bool) -> Dict[str, Any]:
        """infer 门控原语: 发 cmd → 等 state 快照到位, 超时重发一轮."""
        for attempt in range(1, _POSE_ATTEMPTS + 1):
            await self._vision_cmd(b'vision.pose.cmd', {'infer': on})
            if await self._pose_wait(
                    lambda s: bool(s.get('infer')) == on, _POSE_ACK_S):
                return {'ok': True, 'state': self._pose_state}
            _log.warning('pose cmd ack timeout (attempt %d/%d)',
                         attempt, _POSE_ATTEMPTS)
        return {'ok': False, 'state': self._pose_state}

    async def _vision_cmd(self, topic: bytes, payload: Dict[str, Any]) -> None:
        """单帧拼接(topic+JSON)发 XSUB, fire-and-forget."""
        pub = self._pub
        if pub is None:
            raise RuntimeError('bus publisher not started')
        await pub.send(topic + json.dumps(payload).encode('utf-8'))

    async def _pose_wait(self, match, timeout: float) -> bool:
        """等 pose state 快照满足 match(任意 pose state evt 唤醒)."""
        deadline = time.monotonic() + timeout
        while not (self._pose_state and match(self._pose_state)):
            remain = deadline - time.monotonic()
            if remain <= 0:
                return False
            self._pose_evt.clear()
            try:
                await asyncio.wait_for(self._pose_evt.wait(), remain)
            except asyncio.TimeoutError:
                return False
        return True

    # ── 拍照 req 面 ────────────────────────────────────────────────

    @request('snap')
    async def _req_snap(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """拍照: rgb 流最新一帧存 JPG, 返回 content+details.

        临时 SUB 直连 XPUB(订阅期=拍照期, 拍完取消订阅 ---- 高频帧
        ~11MB/s 不进本 listener 主循环)排干积压只留最新: 拍的是"现在".
        无帧(rgb 流被关)时发 cam cmd 开流重试一轮; 拍完不关流 ----
        相机开关是长周期决策(camserver 开机默认 rgb), 归调用方.
        data: {out: 输出路径}(可省).
        """
        data = data or {}
        timeout = 3.0   # 等帧窗口(含 XPUB slow-joiner ~1s), 固定不暴露
        if self._bus_addr is None:
            return {'ok': False, 'error': 'listener not running'}
        zctx = zmq.asyncio.Context()
        sub = zctx.socket(zmq.SUB)
        sub.setsockopt(zmq.RCVHWM, 4)   # 排干时旧帧快点丢, 不涨内存
        sub.connect(self._bus_addr)
        sub.setsockopt(zmq.SUBSCRIBE, _SNAP_TOPIC)
        try:
            seq, frame = await _snap_latest(sub, timeout)
            if frame is None:
                _log.warning('no rgb frame in %.1fs, retry with rgb on',
                             timeout)
                await self._vision_cmd(b'vision.cam.cmd', {'rgb': True})
                seq, frame = await _snap_latest(sub, timeout)
            if frame is None:
                return {'ok': False, 'error':
                        f'no rgb frame within {timeout}s (stream off? '
                        'camserver/bus unreachable?)'}
        finally:
            sub.close(linger=0)
            zctx.term()
        if len(frame) != _SNAP_W * _SNAP_H * 3:
            return {'ok': False, 'error':
                    f'bad rgb frame size {len(frame)} '
                    f'(expected {_SNAP_W * _SNAP_H * 3})'}

        out = data.get('out')
        if out:
            path = Path(out).expanduser().resolve()
        else:
            path = _SNAP_DIR / (f'snap_{time.strftime("%Y%m%d_%H%M%S")}_'
                                f'{seq}.jpg')
        taken_at = time.strftime('%Y-%m-%d %H:%M:%S')
        size = await asyncio.to_thread(_save_jpg, path, frame)
        size_h = _hsize(size)
        return {'ok': True, 'content': (
                    f'Photo taken at {taken_at}, saved {size_h} '
                    f'{_SNAP_W}x{_SNAP_H} JPG: {path}'),
                'details': {'path': str(path), 'taken_at': taken_at,
                            'width': _SNAP_W, 'height': _SNAP_H,
                            'size': size_h}}

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
            if kind == 'pose' and evt.get('type') == 'state':
                # pose state 快照: 门控 req 面的回执依据
                self._pose_state = evt
                if self._pose_evt is not None:
                    self._pose_evt.set()
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
