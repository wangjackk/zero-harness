"""GrpcKernelServer ---- kernel 的 grpc server(dial-in 模型),移植 zt/grpcServer.ts.

外部 routine hub(routine SDK 的 GrpcClientTransport)主动 dial 进来,
每条 Stream = 一个 peer conn.入站 Frame → JSON parse → kernel.dispatch(msg, peerId)
(per-peer FIFO worker 串行,对齐 inproc link 的逐条顺序投递);出站经
kernel.register_conn 注册的 KernelConn → stream write.

Req(unary): routine→kernel 查询(get_module_tree / get_running_routines /
get_routines),直达 kernel.handle_req,方向与 dial-in 对齐(catalog 走 push 不走 Req).

wire: RoutineService { Req(Frame), Stream(stream Frame) },Frame.payload = 完整
事件消息的 JSON 文本(契约: kernel/routine.proto).入站 payload
做 JSON + 非空 event 校验,协议违规当场抛错暴露(坏帧记日志跳过,连接存活,对齐 Go
readLoop);protobuf varint 编解码在 routine/protocol.py 手写(Frame 只有一个
string field,~15 行,不引入 protobuf).

断线: stream 结束/异常 → kernel.drop_conn(peerId)(fail 该 owner 的节点 + 清路由).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Dict

import grpc

from ..protocol import frame_decode, frame_encode

from .manager import Kernel, KernelConn

log = logging.getLogger('kernel.grpc')

# dial-in peer conn id 自增,"s" 前缀(对齐 Go serverConnIDCounter).
_peer_counter = itertools.count(1)

# 手机等拨入端用 20s 空连接 ping 探测半开连接。服务端既允许无活动 RPC 的
# keepalive,也把接收频率门槛放宽到 10s(给 20s 周期留调度抖动余量)。
#
# 参数名坑: 1.6x 之后叫 min_ping_interval_without_data_ms —— 前缀是
# min_ping(不是 min_recv_ping),末尾必须带 _ms。gRPC 对不认识的 channel arg
# 是**静默忽略**的,写错不报错也不告警。此前这里少写 _ms 又用了旧前缀,整行
# 从未生效,服务端门槛仍是默认 5min,于是 20s 一 ping 的 kt 端攒到第 3 个就被
# 判 too_many_pings 直接 GOAWAY(ts/rs 不发 idle ping,所以只有 kt 中招)。
# max_ping_strikes=0 关掉"按 ping 次数直接断连": 频率不合规的 ping 只记日志,
# 链路保留。真要限流靠 min_ping_interval 那行,不靠断连。
_SERVER_OPTIONS = (
    ('grpc.keepalive_permit_without_calls', 1),
    ('grpc.http2.min_ping_interval_without_data_ms', 10_000),
    ('grpc.http2.max_ping_strikes', 0),
)


def _wire_serializer(msg) -> bytes:
    """response 序列化: handler/write 直接产出 wire bytes(透传).

    grpc.aio 中间层会把 str 返回值先 utf-8 encode 成 bytes 再喂 serializer,
    因此 handler 侧统一产 wire bytes; str 输入兜底按 payload 编码(不应发生).
    """
    if isinstance(msg, bytes):
        return msg
    return frame_encode(msg)


def _parse_inbound(payload: str) -> Dict[str, Any]:
    """入站统一入口: payload JSON → msg 校验(必须携带非空 event),违规当场抛."""
    msg = json.loads(payload)
    if not isinstance(msg, dict):
        raise ValueError('inbound msg must be a JSON object')
    event = msg.get('event')
    if not isinstance(event, str) or not event:
        raise ValueError('inbound msg must carry non-empty event')
    return msg


class GrpcKernelServer:
    """kernel 的 gRPC server: 外部 hub 拨入(dial-in),每条 Stream = 一个 peer conn."""

    def __init__(self, kernel: Kernel, addr: str) -> None:
        self._kernel = kernel
        self._addr = addr
        self._bound = False
        self._server = grpc.aio.server(options=_SERVER_OPTIONS)
        req_handler = grpc.unary_unary_rpc_method_handler(
            self._on_req,
            request_deserializer=frame_decode,
            response_serializer=_wire_serializer,
        )
        stream_handler = grpc.stream_stream_rpc_method_handler(
            self._on_stream,
            request_deserializer=frame_decode,
            response_serializer=_wire_serializer,
        )
        self._server.add_generic_rpc_handlers((
            grpc.method_handlers_generic_handler(
                'routine.RoutineService',
                {'Req': req_handler, 'Stream': stream_handler},
            ),
        ))

    @property
    def address(self) -> str:
        return self._addr

    async def start(self) -> None:
        port = self._server.add_insecure_port(self._addr)
        if port == 0:
            raise RuntimeError(f'grpc bind {self._addr}: failed')
        self._addr = f'127.0.0.1:{port}'
        await self._server.start()
        self._bound = True
        log.info('grpc kernel server on %s (dial-in)', self._addr)

    async def stop(self, grace: float = 2.0) -> None:
        """停 server. ``grace`` = 等 dial-in peer 自己关流的上限.

        peer 是常驻重连的, 不会自发结束, 所以几乎总要吃满 grace 再强断(对齐
        TS tryShutdown→forceShutdown). **进程退出时**没有理由陪它: 对端反正
        要重连, 这段等待只让 Ctrl+C 更慢 —— ``Connection.close`` 传更短的 grace.
        """
        if not self._bound:
            return
        self._bound = False
        await self._server.stop(grace)

    # --- Req(unary): routine→kernel 查询 ---

    async def _on_req(self, payload: str, context) -> bytes:
        try:
            msg = _parse_inbound(payload)
        except Exception as exc:
            raise ValueError(f'bad frame payload: {exc}') from exc
        reply = await self._kernel.handle_req(msg)
        return frame_encode(json.dumps(reply))

    # --- Stream: 一条 Stream = 一个 peer conn ---

    async def _on_stream(self, request_iterator, context) -> None:
        peer_id = f's{next(_peer_counter)}'

        # 出站队列: kernel conn 入队, writer loop 逐条 context.write
        # (grpc.aio 的 write 自带流控背压,单 writer 保序).
        out_q: asyncio.Queue = asyncio.Queue()
        # 入站: per-peer FIFO worker 串行 dispatch(对齐 inproc link kernelLoop).
        in_q: asyncio.Queue = asyncio.Queue()
        dropped = False
        # 拒绝连接信号: catalog.push 身份冲突时置位, handler 退出结束 RPC.
        # grpc.aio ServicerContext 没有 cancel() ---- 主动收尾只能
        # set_code 后从 handler return(reject 回调, 见 _reject).
        reject_evt = asyncio.Event()

        def drop() -> None:
            nonlocal dropped
            if dropped:
                return
            dropped = True
            out_q.put_nowait(None)   # 哨兵: writer 退出
            in_q.put_nowait(None)    # 哨兵: worker 退出
            self._kernel.drop_conn(peer_id)
            log.info('peer %s disconnected', peer_id)

        async def writer() -> None:
            while True:
                msg = await out_q.get()
                try:
                    if msg is None:
                        break
                    try:
                        # write 产 wire bytes(str 会被中间层 utf-8 encode 破坏 wire)
                        await context.write(frame_encode(json.dumps(msg)))
                    except Exception as exc:
                        log.warning('peer %s send failed: %s', peer_id, exc)
                        drop()
                        break
                finally:
                    out_q.task_done()

        async def worker() -> None:
            while True:
                msg = await in_q.get()
                if msg is None:
                    break
                try:
                    await self._kernel.dispatch(msg, peer_id)
                except Exception:
                    log.exception('peer %s dispatch failed: %r', peer_id, msg)

        def _reject() -> None:
            """拒绝连接(kernel conn.close 回调): flush 错误回执后以
            ALREADY_EXISTS 收尾 stream ---- 等 writer 把已入队的回执写出
            (out_q.join), 再标记状态并退出读循环."""
            async def _finish() -> None:
                try:
                    await out_q.join()
                    context.set_code(grpc.StatusCode.ALREADY_EXISTS)
                    context.set_details('hub_id already connected')
                except Exception as exc:
                    log.warning('peer %s reject finish: %s', peer_id, exc)
                reject_evt.set()
            asyncio.create_task(_finish())

        async def reader() -> None:
            try:
                async for payload in request_iterator:
                    try:
                        in_q.put_nowait(_parse_inbound(payload))
                    except Exception as exc:
                        # 坏帧/协议违规跳过(报错暴露),连接本身还活着(对齐 Go readLoop)
                        log.warning('peer %s bad frame: %s', peer_id, exc)
            except Exception as exc:
                log.warning('peer %s stream error: %s', peer_id, exc)

        writer_t = asyncio.create_task(writer())
        worker_t = asyncio.create_task(worker())
        reader_t = asyncio.create_task(reader())
        reject_t = asyncio.create_task(reject_evt.wait())

        def _send(msg: Dict[str, Any]) -> None:
            if not dropped:
                out_q.put_nowait(msg)

        self._kernel.register_conn(KernelConn(
            id=peer_id, send=_send, close=_reject))
        log.info('peer %s connected', peer_id)

        try:
            # 正常路径: 客户端断开(reader 完成); 拒绝路径: reject_evt 置位.
            await asyncio.wait({reader_t, reject_t},
                               return_when=asyncio.FIRST_COMPLETED)
            drop()
            # 优雅收尾: 给 writer/worker 2s 把已入队帧写完/派完(shield:
            # 超时由下面统一取消, 不在这里 cancel, 否则任务是 cancelled
            # 但没人收尸)
            for t in (writer_t, worker_t):
                if t.done():
                    continue
                try:
                    await asyncio.wait_for(asyncio.shield(t), timeout=2.0)
                except (asyncio.TimeoutError, Exception):
                    pass
        finally:
            # gRPC 也会直接取消整个 handler(尤其网络断开时), 不能只把清理
            # 写在正常 return 路径。无论从哪里退出都先注销 conn, 再把四个
            # 连接任务 cancel + await; 漏掉 reject_t 就会留下 Event.wait().
            drop()
            pending = [t for t in (reader_t, reject_t, writer_t, worker_t)
                       if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
