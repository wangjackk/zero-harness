"""GrpcClientTransport ---- routine 当 grpc client(dial-in 模型),带重连.

routine 主动 dial kernel 的 grpc server,开一条 bidi Stream.对标 Go 侧 *Client
(dial-out)的 wire 行为:两者都是 RoutineService.Stream 的 grpc client 调用方.

重连:kernel 关闭/重启时 stream 断开 → 退避重试 channel_ready + 重开 Stream +
重发 module.tree Req + catalog.push.kernel 回来后 routine 自动恢复注册,
Execute 可继续.对标 Go 侧 monitorConnect + connect 的退避重连.

入站:recv loop 收 Frame → json.loads → inbound(peer_id, msg).
出站:send_event(payload) → out queue → send loop 写 Frame 到 stream.
peer_id 固定常量(dial-in 单 kernel,routine 的 peer 路由退化为一路).

不实现 req_handler:dial-in 下 routine 不收 kernel→routine Req(方向矛盾,catalog
走 catalog.push,module.tree 走 routine→kernel Req 拉).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import grpc

from .grpc import routine_pb2_grpc
from .protocol import dict_to_frame, frame_to_dict
from .transport import Transport


class GrpcClientTransport(Transport):
    """routine 作为 grpc client 拨 kernel server.单 peer(kernel),peer_id 固定.

    断线自动重连:stream 断 → peer_down 清 instance → backoff → 重连 + 重发
    catalog.push.主动 stop() 不重连.
    """

    # dial-in 单 kernel:peer_id 固定常量.prid = f'{peer_id}:{rid}' 仍唯一(rid 全局唯一).
    PEER_ID = 'kernel'

    # 重连退避:初值 200ms,每次 ×1.5,上限 5s(对标 Go *Client.connect backoff).
    BACKOFF_INITIAL = 0.2
    BACKOFF_MAX = 5.0
    BACKOFF_FACTOR = 1.5
    # Req unary 超时:半开 kernel(进程存活但 Req handler 卡死)下不永久 hang,
    # 超时抛 asyncio.TimeoutError 让 _run 的 except 捕获走重连.跟作者为相邻路径
    # 已加的超时一致(_connect_once channel_ready 的 BACKOFF_MAX,dial-out get_running
    # 的 2.0,ctx.req 的 30)--req 拉的是小查询,5s 足够.
    REQ_TIMEOUT = 5.0
    # 出站队列上限:断连期间 send_event 堆积到此上限后丢最老(陈旧 wire 消息重连
    # 补发也无人接,丢了让上层重试,好过无界堆积内存膨胀).
    _OUT_Q_MAXSIZE = 256
    # 连接存活过此阈值才算健康(断开是正常生命周期),重置 backoff 让下次快重连;
    # 连上即断(flapping kernel)保留 backoff 继续指数退避,避免 0.2s 间隔重连风暴.
    _STABLE_SECONDS = 2.0

    def __init__(self, address: str, peer_id: str = PEER_ID):
        super().__init__()
        self.address = address
        self.peer_id = peer_id
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub = None
        self._call = None  # bidi StreamStreamCall
        self._server = None  # RoutineHub,attach 时设(测试可不设,用 set_inbound 裸测)
        self._out_q: asyncio.Queue = asyncio.Queue(maxsize=self._OUT_Q_MAXSIZE)
        self._run_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None
        self._stopped = False
        # _ready 在每次连接成功时 set;断线时 clear.send_event 在重连期间可能堆积,
        # 重连后 send loop 继续消费.不阻塞 send_event 调用方(fire-and-forget).
        self._ready = asyncio.Event()

    def attach(self, server) -> None:
        self._server = server
        self.set_inbound(server.dispatch_inbound)
        self.set_peer_down(server.on_peer_down)
        # 不设 req_handler----dial-in 不收 kernel→routine Req

    def _log(self, msg: str) -> None:
        if self._server is not None:
            self._server.runtime.logger.info(msg)

    def _warn(self, msg: str) -> None:
        if self._server is not None:
            self._server.runtime.logger.warning(msg)

    # --- Transport impl ---

    async def start(self) -> None:
        # 非阻塞:起 _run task(含重连 loop)后返回._run 负责首次连接 + 后续重连.
        # 首次连接成功后 _ready set,让等此处的调用方(如 start_client)可继续.
        self._run_task = asyncio.create_task(self._run())

    async def wait(self) -> None:
        # 等 _run task 退出(只在主动 stop 时退出).
        if self._run_task is not None:
            await self._run_task

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
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):
                pass

    async def send_event(self, payload: Dict[str, Any],
                         peer_id: Optional[str] = None) -> None:
        # fire-and-forget:put_nowait 进有界 queue.断连期间堆积到上限时丢最老
        # (陈旧 wire 消息:req_id 早 cancel 的 ack / 过时 pubsub 状态,重连补发也
        # 无人接,丢了让上层重试).stopped 后丢弃(防 stop 后还塞消息).
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
            self._warn('out_q saturated, dropping send')

    async def req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """routine→kernel Req 查询(dial-in 下 routine 是 grpc client,主动发 Req 拉
        kernel 信息,方向跟 dial-out 的 kernel→routine Req 相反).复用已连的 _stub.

        用途:连上后拉 module.tree(kernel 不能 Req routine 推,反过来 routine Req 拉).
        """
        s = dict_to_frame(msg)
        resp = await asyncio.wait_for(self._stub.Req(s), timeout=self.REQ_TIMEOUT)
        return frame_to_dict(resp)

    async def get_running_routines(self) -> list:
        """dial-in:经 Req unary 问 kernel(routine 是 client,有 kernel stub).
        kernel HandleReq 处理 get_running_routines -> RunningRoutines() 全局 nodes."""
        if self._stub is None:
            return []
        try:
            resp = await self.req({'event': 'get_running_routines'})
        except Exception as exc:
            self._warn(f'get_running_routines failed: {exc!r}')
            return []
        routines = resp.get('routines') or []
        return routines if isinstance(routines, list) else []

    async def get_routines(self) -> list:
        """dial-in:经 Req unary 问 kernel 拉全量路由表(catalog 注册的全部 routine,
        跨所有 conn).kernel HandleReq 处理 get_routines -> ListRoutines().
        返回 [{name, conn_id, is_passive}, ...].
        """
        if self._stub is None:
            return []
        try:
            resp = await self.req({'event': 'get_routines'})
        except Exception as exc:
            self._warn(f'get_routines failed: {exc!r}')
            return []
        routines = resp.get('routines') or []
        return routines if isinstance(routines, list) else []

    async def get_module_tree(self):
        """dial-in:经 Req unary 问 kernel 拉 module.tree,刷新 runtime.module_tree
        缓存后返回 ModuleTree.

        跟 ``_post_connect`` 的首次拉取同路径(req get_module_tree -> cache_module_tree),
        只是暴露成可重触发--业务按需刷新缓存.
        """
        if self._server is None:
            return None
        if self._stub is None:
            return self._server.runtime.module_tree
        try:
            resp = await self.req({'event': 'get_module_tree'})
        except Exception as exc:
            self._warn(f'get_module_tree failed: {exc!r}')
            return self._server.runtime.module_tree
        self._server.query.cache_module_tree(resp)
        return self._server.runtime.module_tree

    # --- 重连 loop ---

    async def _run(self) -> None:
        """外层重连 loop:连接 → 起 send/recv loop → 等断 → peer_down + backoff → 重来.

        只在 _stopped 时退出(主动 stop 或 channel 不可恢复).每次连接成功调
        _post_connect(Req 拉 tree + push catalog)----跟 start_client 初次对称.
        """
        backoff = self.BACKOFF_INITIAL
        while not self._stopped:
            connected_at = time.monotonic()
            try:
                await self._connect_once()
                await self._post_connect()
                # 等 recv loop 退出(stream 断).
                await self._recv_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warn(f'连接异常: {exc!r}')
            finally:
                self._ready.clear()
                await self._cleanup_call()
            if self._stopped:
                break
            # 断开:peer_down 清 instance(kernel 侧已死的 routine 不留),退避重连.
            await self._handle_disconnect()
            # 连接存活过阈值 = 健康(断开是正常生命周期),重置 backoff 让下次快重连;
            # 连上即断(flapping kernel:TCP+HTTP2 通但 post_connect/recv 立即失败)
            # 则保留 backoff 继续指数退避,避免 0.2s 间隔重连风暴.
            if time.monotonic() - connected_at >= self._STABLE_SECONDS:
                backoff = self.BACKOFF_INITIAL
            self._log(f'🔄 {backoff:.1f}s 后重连...')
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise
            backoff = min(backoff * self.BACKOFF_FACTOR, self.BACKOFF_MAX)

    async def _connect_once(self) -> None:
        """建 channel + 开 Stream + 起 send loop.失败抛异常让外层重试."""
        self._channel = grpc.aio.insecure_channel(self.address)
        # channel_ready 等 TCP+HTTP2 握手成功;kernel 没起会一直等(或 grpc 内部
        # backoff).给超时让外层 backoff 接管,避免卡死.
        await asyncio.wait_for(self._channel.channel_ready(),
                               timeout=self.BACKOFF_MAX)
        self._stub = routine_pb2_grpc.RoutineServiceStub(self._channel)
        self._call = self._stub.Stream()
        self._ready.set()
        self._log(f'🔗 [client] connected to kernel: {self.address}')
        # 起 send loop 消费出站队列(每次连接一条新 task;断线时随 _call 一起结束).
        if self._send_task is not None and not self._send_task.done():
            self._send_task.cancel()
            try:
                await self._send_task
            except (asyncio.CancelledError, Exception):
                pass
        self._send_task = asyncio.create_task(self._send_loop())

    async def _post_connect(self) -> None:
        """连接成功后的初始化(初次 + 重连共用):
        Req 拉 module.tree 缓存 + push catalog 让 kernel 注册路由.
        server 未 attach(裸测)时跳过----只验 wire.
        """
        if self._server is None:
            return
        await self.get_module_tree()
        await self._server.send_catalog_push()

    async def _recv_loop(self) -> None:
        """收 kernel 发来的事件.stream 断(async for 结束/异常)即返回,外层重连."""
        try:
            async for item in self._call:
                msg = frame_to_dict(item)
                if self._inbound is not None:
                    try:
                        await self._inbound(self.peer_id, msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._warn(f'inbound dispatch error: {exc}')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._warn(f'client recv error: {exc}')

    async def _send_loop(self) -> None:
        """出站:queue → stream.write.stream 断时 write 抛异常,本 loop 退出
        (recv loop 也会退出,外层重连)."""
        try:
            while True:
                payload = await self._out_q.get()
                await self._call.write(dict_to_frame(payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._warn(f'client send error: {exc}')

    async def _handle_disconnect(self) -> None:
        """stream 断开后:通知 server 清理该 peer 的所有 running instance
        (peer_down → force_stop_peer),让实例不泄漏.server 未 attach 跳过.
        """
        if self._peer_down is not None:
            try:
                await self._peer_down(self.peer_id)
            except Exception as exc:
                self._warn(f'peer_down handler error: {exc}')

    async def _cleanup_call(self) -> None:
        """断线后清理当前 call + send task,为重连准备."""
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

