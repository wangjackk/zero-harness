"""ros_client ---- rosbridge WS 纯传输单例(常驻连接 + 自愈 + 订阅表).

非 routine 文件: watcher 只热重载 routine 类文件, 本单例跨重载存活 ----
控制路径(car/stop 级联零速)不依赖任何 routine 实例的生死.

职责边界(零业务语义):
- 常驻 WS 连接, 断线后台重连(重连后自动重发全部订阅, 清 advertise 缓存)
- 连不上时按 config network.ros_bridge_start 经 SSH 拉起远端桥(冷却 60s;
  设备整套 bringup, 不只 rosbridge ---- 拉起命令是设备运维配置)
- publish(topic, msg, msg_type): 单帧发送; advertise 幂等缓存; 连接未建立
  时等一拍重连, 超时抛 ConnectionError(caller 可见, 不静默丢)
- add_topic / drop_topic: 订阅表(引用计数, 回调集清空才真正 unsubscribe),
  publish 帧按 topic 分发给回调

业务在别处: 车控(Twist 编码/代际互斥/零速保证)在 car/car_ctrl.py;
感知转发(ROS 话题 → pubsub, 纯中转)在 ros_bridge routine. 地址取
app/config.yaml network.ros_bridge.
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Any, Awaitable, Callable, Dict, Set

import websockets
from websockets.asyncio.client import ClientConnection

from app.config import network
from routine.logger import setup_logger

_log = setup_logger('ros.client')

_CONNECT_TIMEOUT = 5.0
_RECONNECT_MIN = 1.0
_RECONNECT_MAX = 5.0
_WAIT_CONNECTED = 6.0   # publish 等待连接建立的上限(含一次重连尝试)
_START_COOLDOWN = 60.0  # 自动拉起冷却: 防反复 restart 打爆设备
_SSH_TIMEOUT = 20.0     # ssh 拉起命令本身的执行上限

# 帧回调: msg dict -> Awaitable(reader 循环内逐帧 await)
TopicCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def url() -> str:
    """rosbridge WS 地址(config.yaml network.ros_bridge, 唯一事实源)."""
    return network().ros_bridge


class _SubEntry:
    """单 topic 订阅项: ROS 消息类型 + 回调集(清空才 unsubscribe)."""

    __slots__ = ('type', 'callbacks')

    def __init__(self, type_: str) -> None:
        self.type = type_
        self.callbacks: Set[TopicCallback] = set()


async def _send(ws: ClientConnection, op: str, **fields: Any) -> None:
    await ws.send(json.dumps({'op': op, **fields}))


class RosClient:
    """常驻 rosbridge 连接: 后台重连循环 + 订阅分发 + 单帧发布."""

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task | None = None
        self._subs: Dict[str, _SubEntry] = {}
        self._advertised: Set[str] = set()
        self._ever_connected = False
        self._lost_logged = False
        self._last_start = -math.inf   # 上次自动拉起时刻(loop.time 基准)

    # ── 生命周期 ────────────────────────────────────────────────────────────

    def ensure_started(self) -> None:
        """幂等启动重连循环(lazy: 首个 publish/订阅方触发)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _maybe_start_bridge(self) -> None:
        """连接失败时按配置 SSH 拉起远端桥(冷却 _START_COOLDOWN).

        设备整套 bringup 由远端命令自理; ssh 不通(设备关机)静默, 冷却后
        下轮连接失败再试. 未配置 = no-op.
        """
        cfg = network().ros_bridge_start
        if cfg is None:
            return
        loop = asyncio.get_running_loop()
        if loop.time() - self._last_start < _START_COOLDOWN:
            return
        self._last_start = loop.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                'ssh', cfg.ssh, cfg.cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), timeout=_SSH_TIMEOUT)
            _log.info('ros bridge start requested: ssh %s %s',
                      cfg.ssh, cfg.cmd)
        except Exception as e:
            _log.debug('ros bridge start attempt failed: %s', e)

    async def _run(self) -> None:
        """重连主循环: 连接 → 重发订阅 → 分发帧 → 断线退避重试."""
        backoff = _RECONNECT_MIN
        while True:
            try:
                ws = await asyncio.wait_for(websockets.connect(url()),
                                            timeout=_CONNECT_TIMEOUT)
            except Exception as e:
                # 车不在线是常态(未上电): 首次及重试都低调, 只在"曾连上后又
                # 失联"时 warning 一次提醒
                if self._ever_connected and not self._lost_logged:
                    _log.warning('ros bridge unreachable: %s', e)
                    self._lost_logged = True
                else:
                    _log.debug('ros bridge unreachable (%s), retrying', e)
                await self._maybe_start_bridge()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _RECONNECT_MAX)
                continue
            backoff = _RECONNECT_MIN
            self._lost_logged = False
            first = not self._ever_connected
            self._ever_connected = True
            self._ws = ws
            self._advertised.clear()
            _log.info('ros bridge %s: %s',
                      'connected' if first else 'reconnected', url())
            for topic, entry in list(self._subs.items()):
                try:
                    await _send(ws, 'subscribe', topic=topic, type=entry.type)
                except Exception:
                    break   # 刚连上又断, 走外层重连
            try:
                async for raw in ws:
                    await self._dispatch(raw)
            except Exception as e:
                _log.warning('ros bridge connection lost (%s), reconnecting', e)
            finally:
                self._ws = None
                try:
                    await ws.close()
                except Exception:
                    pass
            await asyncio.sleep(backoff)

    async def _dispatch(self, raw: str | bytes) -> None:
        """单帧分发: 只认 publish op, 按 topic 找订阅回调."""
        try:
            frame = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(frame, dict) or frame.get('op') != 'publish':
            return   # status/service 响应等, 传输层不关心
        entry = self._subs.get(str(frame.get('topic', '')))
        if entry is None:
            return
        msg = frame.get('msg')
        if not isinstance(msg, dict):
            return
        for cb in list(entry.callbacks):
            try:
                await cb(msg)
            except Exception:
                _log.exception('topic callback failed')

    # ── 发布(车控路径) ──────────────────────────────────────────────────────

    async def _wait_ws(self, timeout: float) -> ClientConnection:
        """等连接建立(重连进行中); 超时抛 ConnectionError."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while self._ws is None or self._ws.close_code is not None:
            if loop.time() >= deadline:
                raise ConnectionError(f'ros bridge not connected: {url()}')
            await asyncio.sleep(0.05)
        return self._ws

    async def publish(self, topic: str, msg: Dict[str, Any],
                      msg_type: str = '') -> None:
        """往 ROS 话题发一帧. 连接未建立时等重连(上限 6s)后失败可见.

        msg_type: 首次发布该 topic 时的 advertise 类型(幂等缓存, 重连后
        自动重新 advertise); 省略则假定已 advertise 过.
        """
        self.ensure_started()
        ws = self._ws
        if ws is None or ws.close_code is not None:
            ws = await self._wait_ws(_WAIT_CONNECTED)
        if topic not in self._advertised:
            if msg_type:
                await _send(ws, 'advertise', topic=topic, type=msg_type)
            self._advertised.add(topic)
        await _send(ws, 'publish', topic=topic, msg=msg)

    # ── 订阅(感知路径) ──────────────────────────────────────────────────────

    def add_topic(self, topic: str, type_: str, cb: TopicCallback) -> None:
        """注册订阅: 回调集从空到非空时发 subscribe op(重连后自动补发)."""
        self.ensure_started()
        entry = self._subs.get(topic)
        if entry is None:
            entry = _SubEntry(type_)
            self._subs[topic] = entry
            ws = self._ws
            if ws is not None and ws.close_code is None:
                asyncio.create_task(_send_ok(ws, 'subscribe',
                                             topic=topic, type=type_))
        entry.callbacks.add(cb)

    def drop_topic(self, topic: str, cb: TopicCallback) -> None:
        """注销回调: 回调集清空才真正 unsubscribe 并移除订阅项."""
        entry = self._subs.get(topic)
        if entry is None:
            return
        entry.callbacks.discard(cb)
        if not entry.callbacks:
            del self._subs[topic]
            ws = self._ws
            if ws is not None and ws.close_code is None:
                asyncio.create_task(_send_ok(ws, 'unsubscribe', topic=topic))


async def _send_ok(ws: ClientConnection, op: str, **fields: Any) -> None:
    """后台补发订阅/退订 op: 失败只记日志(重连循环会补)."""
    try:
        await _send(ws, op, **fields)
    except Exception as e:
        _log.debug('subscribe op %s %r failed: %s', op, fields.get('topic'), e)


# ── 模块级单例(跨 routine 热重载存活) ─────────────────────────────────────
# 本文件自身被 watcher reload 时模块 dict 复用: globals().get 保留旧实例,
# 不重建第二条连接(reload 会把 `_client = None` 重新执行一遍).
_client: RosClient | None = globals().get('_client')


def client() -> RosClient:
    global _client
    if _client is None:
        _client = RosClient()
    return _client


async def publish(topic: str, msg: Dict[str, Any], msg_type: str = '') -> None:
    await client().publish(topic, msg, msg_type)


def ensure_started() -> None:
    """幂等启动重连循环(桥 routine 启动时调: zero 起来即开始连设备)."""
    client().ensure_started()


def add_topic(topic: str, type_: str, cb: TopicCallback) -> None:
    client().add_topic(topic, type_, cb)


def drop_topic(topic: str, cb: TopicCallback) -> None:
    client().drop_topic(topic, cb)
