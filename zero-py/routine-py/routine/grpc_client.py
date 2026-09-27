"""GrpcClientTransport ---- routine hub 当 grpc client(dial-in 模型),带重连.

移植自老版 routine-py(已移除)的 grpc_client, wire 用 routine/protocol.py 手写 Frame 编解码
(与 kernel/grpc_server.py 的 GrpcKernelServer 对接, 不依赖 protobuf 生成代码):

  - hub 主动 dial kernel 的 grpc server, 开一条 bidi Stream = 一个 peer.
  - 入站: recv loop 收 Frame → json.loads → inbound(peer_id, msg).
  - 出站: send_event(payload) → out queue → send loop 写 Frame(有界 256 丢最老;
    write 抛错/背压超时视为半死流, cancel 强断促重连).
  - req: unary '/routine.RoutineService/Req' 直查 kernel(get_running_routines /
    get_routines / get_module_tree).
  - 重连: stream 断 → peer_down 清实例 → backoff(0.2s×1.5 上限 5s) → 重连 +
    Req 拉 module.tree + 重发 catalog.push(kernel 回来后自动恢复注册).

不实现 kernel→hub 的 Req 接收: dial-in 下方向矛盾(catalog 走 push,
module.tree 走 hub→kernel Req 拉).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import grpc

from .protocol import (
    frame_decode, frame_encode,
    REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING,
)
from .transport import Transport

_log = logging.getLogger('routine.grpc_client')

_REQ_PATH = '/routine.RoutineService/Req'
_STREAM_PATH = '/routine.RoutineService/Stream'


class GrpcClientTransport(Transport):
    """hub 作为 grpc client 拨 kernel server.单 peer(kernel), peer_id 固定.

    断线自动重连: stream 断 → peer_down 清实例 → backoff → 重连 + 重发
    catalog.push. 主动 stop() 不重连.
    """

    # dial-in 单 kernel: peer_id 固定常量
    PEER_ID = 'kernel'

    # 重连退避: 初值 200ms, 每次 ×1.5, 上限 5s
    BACKOFF_INITIAL = 0.2
    BACKOFF_MAX = 5.0
    BACKOFF_FACTOR = 1.5
    # Req unary 超时: 半开 kernel(进程存活但 handler 卡死)下不永久 hang
    REQ_TIMEOUT = 5.0
    # 出站队列上限: 断连期间堆积到上限后丢最老(陈旧 wire 消息重连补发也无人接)
    _OUT_Q_MAXSIZE = 256
    # 连接存活过此阈值才算健康, 重置 backoff; 连上即断保留退避防重连风暴
    _STABLE_SECONDS = 2.0

    def __init__(self, address: str, peer_id: str = PEER_ID):
        super().__init__()
        self.address = address
        self.peer_id = peer_id
        self._channel: Optional[grpc.aio.Channel] = None
        self._hub = None            # RoutineHub, attach 时设(裸测可不设)
        self._call = None           # bidi StreamStreamCall
        self._out_q: asyncio.Queue = asyncio.Queue(maxsize=self._OUT_Q_MAXSIZE)
        self._run_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None
        self._stopped = False
        self._ready = asyncio.Event()

    def attach(self, hub) -> None:
        """绑定 hub: 入站走 hub.dispatch_inbound, 断线走 hub 清理."""
        self._hub = hub
        self.set_inbound(hub.dispatch_inbound)

    # --- Transport impl ---

    async def start(self) -> None:
        # 非阻塞: 起 _run(含重连 loop)后返回, 首连成功 _ready set.
        self._run_task = asyncio.create_task(self._run())

    async def wait_ready(self, timeout: float = BACKOFF_MAX) -> None:
        """等首连完成(超时抛 TimeoutError)."""
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._ready.clear()
        if self._call is not None:
            try:
                await self._call.done_writing()
            except Exception:
                pass
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass
            self._run_task = None

    async def send_event(self, payload: Dict[str, Any],
                         peer_id: Optional[str] = None) -> None:
        # fire-and-forget: 有界 queue, 满丢最老; stopped 后丢弃
        if self._stopped:
            return
        if self._out_q.full():
            try:
                self._out_q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._out_q.put_nowait(payload)
        except asyncio.QueueFull:
            _log.warning('out_q saturated, dropping send')

    async def req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """hub→kernel Req 查询(unary).复用已连 channel."""
        if self._channel is None:
            raise RuntimeError('grpc client not connected')
        call = self._channel.unary_unary(
            _REQ_PATH,
            request_serializer=frame_encode,
            response_deserializer=frame_decode,
        )
        resp = await asyncio.wait_for(
            call(json.dumps(msg)), timeout=self.REQ_TIMEOUT)
        return json.loads(resp)

    async def get_running_routines(self) -> list:
        try:
            resp = await self.req({'event': REQ_GET_RUNNING})
        except Exception as exc:
            _log.warning('get_running_routines failed: %r', exc)
            return []
        routines = resp.get('routines') or []
        return routines if isinstance(routines, list) else []

    async def get_routines(self) -> list:
        try:
            resp = await self.req({'event': REQ_GET_ROUTINES})
        except Exception as exc:
            _log.warning('get_routines failed: %r', exc)
            return []
        routines = resp.get('routines') or []
        return routines if isinstance(routines, list) else []

    async def get_module_tree(self):
        from .module_tree import ModuleTree
        if self._hub is None:
            return None
        try:
            resp = await self.req({'event': REQ_GET_MODULE_TREE})
        except Exception as exc:
            _log.warning('get_module_tree failed: %r', exc)
            return self._hub.runtime.module_tree
        self._hub._cache_module_tree(resp)
        return self._hub.runtime.module_tree

    # --- 重连 loop ---

    async def _run(self) -> None:
        """外层重连 loop: 连接 → post_connect → recv loop → 断开清理 → backoff → 重来."""
        backoff = self.BACKOFF_INITIAL
        while not self._stopped:
            connected_at = time.monotonic()
            try:
                await self._connect_once()
                await self._post_connect()
                await self._recv_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning('connect error: %r', exc)
            finally:
                self._ready.clear()
                await self._cleanup_call()
            if self._stopped:
                break
            await self._handle_disconnect()
            if time.monotonic() - connected_at >= self._STABLE_SECONDS:
                backoff = self.BACKOFF_INITIAL
            _log.info('reconnect in %.1fs...', backoff)
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * self.BACKOFF_FACTOR, self.BACKOFF_MAX)

    async def _connect_once(self) -> None:
        self._channel = grpc.aio.insecure_channel(self.address)
        await asyncio.wait_for(self._channel.channel_ready(),
                               timeout=self.BACKOFF_MAX)
        call = self._channel.stream_stream(
            _STREAM_PATH,
            request_serializer=frame_encode,
            response_deserializer=frame_decode,
        )
        self._call = call(None)  # request_iterator 由 send loop 驱动
        self._ready.set()
        _log.info('connected to kernel: %s', self.address)
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except (asyncio.CancelledError, Exception):
                pass
        self._send_task = asyncio.create_task(self._send_loop())

    async def _post_connect(self) -> None:
        """连接成功后的初始化(初次 + 重连共用): 拉 tree + push catalog."""
        if self._hub is None:
            return
        await self.get_module_tree()
        await self._hub.send_catalog_push()

    async def _recv_loop(self) -> None:
        """收 kernel 发来的事件. stream 断(async for 结束/异常)即返回, 外层重连."""
        try:
            async for payload in self._call:
                msg = json.loads(payload)
                if self._inbound is not None:
                    try:
                        await self._inbound(self.peer_id, msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        _log.warning('inbound dispatch error: %r', exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning('recv error: %r', exc)

    async def _send_loop(self) -> None:
        """出站: queue → stream write.

        半死流(发送方向死, recv 侧未察觉)必须 fail-fast: write 抛错或背压
        超过 REQ_TIMEOUT 都 cancel call 逼 recv loop 结束 → 外层重连. 不撕裂
        的话 out_q 堆到上限后开始静默丢消息, 连接永远半死.
        """
        try:
            while True:
                payload = await self._out_q.get()
                await asyncio.wait_for(
                    self._call.write(json.dumps(payload)),
                    timeout=self.REQ_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            _log.warning('send backpressure %.0fs; stream dead, forcing reconnect',
                         self.REQ_TIMEOUT)
            await self._cancel_call()
        except Exception as exc:
            _log.warning('send error: %r', exc)
            await self._cancel_call()

    async def _cancel_call(self) -> None:
        """cancel 当前 call: 逼 recv loop(async for)退出, 外层 _run 重连."""
        call = self._call
        if call is None:
            return
        try:
            maybe = call.cancel()
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:
            pass

    async def _handle_disconnect(self) -> None:
        """stream 断开后通知 peer_down(清该 peer 的 running instance)."""
        if self._peer_down is not None:
            try:
                await self._peer_down(self.peer_id)
            except Exception as exc:
                _log.warning('peer_down handler error: %r', exc)

    async def _cleanup_call(self) -> None:
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except (asyncio.CancelledError, Exception):
                pass
        self._send_task = None
        if self._call is not None:
            try:
                await self._call.done_writing()
            except Exception:
                pass
            self._call = None
        self._out_q = asyncio.Queue(maxsize=self._OUT_Q_MAXSIZE)
