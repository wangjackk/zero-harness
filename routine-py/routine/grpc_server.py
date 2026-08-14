"""GrpcServerTransport ---- routine 当 grpc server(dial-out 模型).

kernel 主动 dial 进来;每条 Stream = 一个 peer_id = context.peer().对标原
RoutineServiceServicer + StreamController + OutboundTransport 三者,抽成 Transport 实现:
入站 reader → server.dispatch_inbound(event 分发);出站 send_event → per-peer out queue →
Stream yield;peer 断开 → server.on_peer_down → lifecycle.force_stop_peer.

跟 GrpcClientTransport 对称:两者都实现 Transport,RoutineHub 不区分方向.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import grpc
from google.protobuf.json_format import MessageToDict
from google.protobuf.struct_pb2 import Struct

from .grpc import routine_pb2_grpc
from .protocol import ROUTINE_GET_MODULE_TREE, ROUTINE_GET_RUNNING, dict_to_struct
from .transport import Transport
from uuid import uuid4


class _Servicer(routine_pb2_grpc.RoutineServiceServicer):
    """grpc servicer 薄壳:委托给 transport."""

    def __init__(self, transport: 'GrpcServerTransport'):
        self._t = transport

    async def Stream(self, request_iterator, context):
        async for msg in self._t.serve_stream(request_iterator, context):
            yield msg

    async def Req(self, request, context):
        return await self._t.serve_req(request)


class GrpcServerTransport(Transport):
    """routine 作为 grpc server 监听 address.每条接入 Stream 一个 peer."""

    def __init__(self, address: str = '0.0.0.0:50051'):
        super().__init__()
        self.address = address
        self._grpc_server: Optional[grpc.aio.Server] = None
        self._server = None  # RoutineHub,attach 时设
        # per-peer 出站队列(原 runtime.peer_to_queue,搬进 transport----传输层状态)
        self._out_queues: Dict[str, asyncio.Queue] = {}
        self.bound_port: int = 0  # bind 后的实际端口(:0 随机端口测试用)
        # dial-out get_running 请求-回执 future 表:req_id -> future.
        # kernel 回 routine.get_running_reply 时经 server.dispatch_inbound -> _resolve_get_running 解.
        self._get_running_futures: Dict[str, asyncio.Future] = {}
        # dial-out get_module_tree 请求-回执 future 表:req_id -> future(同上,kernel 回
        # routine.get_module_tree_reply 时经 dispatch_inbound -> resolve_get_module_tree 解).
        self._get_module_tree_futures: Dict[str, asyncio.Future] = {}

    def attach(self, server) -> None:
        """RoutineHub 建好后调:注入 dispatch/peer_down/req 回调."""
        self._server = server
        self.set_inbound(server.dispatch_inbound)
        self.set_peer_down(server.on_peer_down)
        self.set_req_handler(server.query.handle_req)

    # --- Transport impl ---

    async def start(self) -> None:
        # 不配 keepalive(grpc 默认行为),跟 kernel *Client 一致.
        # 非阻塞:建 server + bind + start 后返回(bound_port 可读).阻塞等停由 wait().
        # attach-before-start 契约:start 要 runtime 做 logging,未 attach 报清晰错误
        # (不像 GrpcClientTransport 可裸测 set_inbound 不带 server--server 侧总是带 RoutineHub).
        if self._server is None:
            raise RuntimeError('GrpcServerTransport.start requires attach(server) first')
        self._grpc_server = grpc.aio.server()
        routine_pb2_grpc.add_RoutineServiceServicer_to_server(
            _Servicer(self), self._grpc_server)
        self.bound_port = self._grpc_server.add_insecure_port(self.address)
        if self.bound_port == 0:
            raise RuntimeError(f'Cannot bind to address: {self.address}')
        await self._grpc_server.start()
        runtime = self._server.runtime
        runtime.logger.info(f'routine server started: {self.address}')

    async def wait(self) -> None:
        if self._grpc_server is not None:
            await self._grpc_server.wait_for_termination()

    async def stop(self) -> None:
        if self._grpc_server is not None:
            await self._grpc_server.stop(grace=None)

    async def send_event(self, payload: Dict[str, Any],
                         peer_id: Optional[str] = None) -> None:
        message = dict_to_struct(payload)
        if peer_id is None:
            for q in list(self._out_queues.values()):
                await q.put(message)
            return
        q = self._out_queues.get(peer_id)
        if q is not None:
            await q.put(message)

    async def get_running_routines(self) -> list:
        """dial-out:经 Stream 请求-回执问 kernel(routine 当 server,无 kernel client stub,
        Req 方向矛盾不支持,只能骑 Stream).对标 submit/submitted:发 routine.get_running
        {req_id} -> kernel 回 routine.get_running_reply {req_id, routines}.

        多个 kernel peer 连上时广播(send_event peer_id=None),首个回执 resolve,
        其余忽略(future.done()).没 peer 连上直接返 [](agent 轮询重试,不阻塞等超时).
        """
        if not self._out_queues:
            return []
        req_id = uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._get_running_futures[req_id] = fut
        try:
            await self.send_event({'event': ROUTINE_GET_RUNNING, 'req_id': req_id})
            return await asyncio.wait_for(fut, timeout=2.0)
        except asyncio.TimeoutError:
            return []
        finally:
            self._get_running_futures.pop(req_id, None)

    def resolve_get_running(self, msg: Dict[str, Any]) -> None:
        """收 routine.get_running_reply:按 req_id resolve future.dispatch_inbound 路由到此."""
        req_id = str(msg.get('req_id') or '')
        fut = self._get_running_futures.pop(req_id, None)
        if fut is not None and not fut.done():
            routines = msg.get('routines') or []
            fut.set_result(routines if isinstance(routines, list) else [])

    async def get_module_tree(self):
        """dial-out:经 Stream 请求-回执问 kernel 拉 module.tree(routine 当 server,无
        kernel client stub,Req 方向矛盾不支持,只能骑 Stream).对标 get_running:发
        routine.get_module_tree{req_id} -> kernel 回 routine.get_module_tree_reply{req_id,
        ok, tree}.回执后刷新 runtime.module_tree 缓存,返回 ModuleTree.

        多个 kernel peer 连上时广播(send_event peer_id=None),首个回执 resolve,
        其余忽略(future.done()).没 peer 连上 / 超时返当前缓存(不阻塞等超时).
        """
        if self._server is None:
            return None
        if not self._out_queues:
            return self._server.runtime.module_tree
        req_id = uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._get_module_tree_futures[req_id] = fut
        try:
            await self.send_event({'event': ROUTINE_GET_MODULE_TREE, 'req_id': req_id})
            reply = await asyncio.wait_for(fut, timeout=2.0)
        except asyncio.TimeoutError:
            return self._server.runtime.module_tree
        finally:
            self._get_module_tree_futures.pop(req_id, None)
        # reply = {ok, tree}(成功)或 {ok:false, error}(kernel 侧 tree 未初始化).
        # 缓存逻辑跟 push / dial-out Req 共用 cache_module_tree(读 reply['tree']).
        if reply.get('ok'):
            self._server.query.cache_module_tree(reply)
        return self._server.runtime.module_tree

    def resolve_get_module_tree(self, msg: Dict[str, Any]) -> None:
        """收 routine.get_module_tree_reply:按 req_id resolve future.dispatch_inbound 路由到此."""
        req_id = str(msg.get('req_id') or '')
        fut = self._get_module_tree_futures.pop(req_id, None)
        if fut is not None and not fut.done():
            fut.set_result(msg)

    # --- servicer 实现 ---

    async def serve_stream(self, request_iterator, context):
        peer_id = context.peer()
        runtime = self._server.runtime
        runtime.logger.info(f'🔗 [Stream] connected: {peer_id}')
        outgoing = self._ensure_queue(peer_id)
        stop_event = asyncio.Event()

        async def reader() -> None:
            try:
                async for item in request_iterator:
                    msg = MessageToDict(item)
                    try:
                        await self._inbound(peer_id, msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # 单条消息处理异常不拆整条 Stream:记日志继续下一条
                        # (如 dispatch_inbound 的 cache_module_tree 遇畸形 tree 抛 ValueError)
                        runtime.logger.warning(f'inbound dispatch error: {exc}')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                runtime.logger.warning(f'stream inbound error: {exc}')
            finally:
                stop_event.set()

        async def closer() -> None:
            await stop_event.wait()
            await asyncio.sleep(0.05)
            await outgoing.put(None)

        reader_task = asyncio.create_task(reader())
        closer_task = asyncio.create_task(closer())
        try:
            while True:
                msg = await outgoing.get()
                if msg is None:
                    break
                yield msg
        finally:
            reader_task.cancel()
            closer_task.cancel()
            self._out_queues.pop(peer_id, None)
            # peer 断连:强制清理该 peer 的所有 routine instance(server 侧 instance 不清会泄漏).
            n = sum(1 for prid in runtime.running_instances
                    if prid.startswith(f'{peer_id}:'))
            if self._peer_down is not None:
                await self._peer_down(peer_id)
            runtime.logger.info(
                f'❌ [Stream] disconnected: {peer_id} (stopped {n} instance(s))')

    async def serve_req(self, request: Struct) -> Struct:
        if self._req_handler is None:
            return dict_to_struct({'error': 'no req handler'})
        return await self._req_handler(request)

    def _ensure_queue(self, peer_id: str) -> asyncio.Queue:
        q = self._out_queues.get(peer_id)
        if q is None:
            q = asyncio.Queue()
            self._out_queues[peer_id] = q
        return q
