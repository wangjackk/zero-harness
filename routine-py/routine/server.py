"""RoutineHub ---- routine 生命周期 + 跨 routine 通信(精简版,传输无关).

只保留 Req(查询)+ Stream(lifecycle 双向流 + routine 调 routine 反向事件),
无 business / shell_req / router / watchdog.

传输经 Transport 抽象(GrpcServerTransport dial-out / GrpcClientTransport dial-in)----
本类不关心 wire:入站 transport → dispatch_inbound 按 event 分发(lifecycle.start/stop/
destroy/created → LifecycleManager;其余 → on_inbound);出站 send_* helpers build payload
后调 transport.send_event.peer 断开 → on_peer_down → lifecycle.force_stop_peer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import (
    AcquireError, DeregisterError, LoadModuleError, RegisterError, ReloadError,
    ReleaseError, SubmitError, UnloadModuleError,
)
from .grpc_client import GrpcClientTransport
from .grpc_server import GrpcServerTransport
from .lifecycle import LifecycleManager
from .protocol import (
    ROUTINE_YIELD, ROUTINE_YIELDED, CATALOG_PUSH, CATALOG_PUSHED,
    CATALOG_REGISTER, CATALOG_REGISTERED, CATALOG_RELOAD, CATALOG_RELOADED,
    CATALOG_DEREGISTER, CATALOG_DEREGISTER_CMD, CATALOG_DEREGISTER_CMD_ACK,
    CATALOG_DEREGISTERED, ControlDoneReason,
    ENVELOPE_CANCEL, ENVELOPE_CHUNK, ENVELOPE_DATA, ENVELOPE_EOF, ENVELOPE_ERROR,
    ENVELOPE_OK, ENVELOPE_REQ_ID, ENVELOPE_STREAM_ID,
    LIFECYCLE_CREATED, LIFECYCLE_DESTROY, LIFECYCLE_START,
    LIFECYCLE_STARTED, LIFECYCLE_STOP, LIFECYCLE_STOPPED, MODULE_TREE,
    MESSAGE_DELIVERED, MESSAGE_REQ_DELIVERED, MESSAGE_REQ_REPLY_DELIVERED,
    MESSAGE_STREAM_CANCEL_DELIVERED, MESSAGE_STREAM_DATA_DELIVERED,
    MESSAGE_STREAM_OPEN_DELIVERED, PUBSUB_DELIVERED, PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE,
    PUBSUB_UNSUBSCRIBE, ROUTINE_ACQUIRE, ROUTINE_ACQUIRED,
    ROUTINE_FORCE_RELEASE, ROUTINE_FORCE_ACQUIRE, ROUTINE_FORCE_START, ROUTINE_GET_MODULE_TREE_REPLY,
    ROUTINE_GET_RUNNING_REPLY,
    ROUTINE_LOAD_MODULE, ROUTINE_MODULE_LOADED, ROUTINE_MODULE_UNLOADED,
    ROUTINE_RELEASE, ROUTINE_RELEASED, ROUTINE_UNLOAD_MODULE,
)
from .query import QueryService
from .routine import RoutineSource, Routines, passive_wire
from .runtime import ServerRuntime
from .transport import Transport

# routine 调 routine 反向事件(py→kernel)
ROUTINE_SUBMIT = 'routine.submit'
ROUTINE_START = 'routine.start'
ROUTINE_STOP = 'routine.stop'
ROUTINE_UNSUBMIT = 'routine.unsubmit'  # py→kernel: 撤销提交(清 created 态)
ROUTINE_SUBMITTED = 'routine.submitted'
ROUTINE_REJECTED = 'routine.rejected'  # kernel→py: start/stop 被拒

# catalog.register/deregister req_id 计数器(进程级单例,前缀 'cat' 跟 ctx 的 'r' 区分).
_catalog_req_counter = 0


def _new_catalog_req_id() -> str:
    global _catalog_req_counter
    _catalog_req_counter += 1
    return f'cat{_catalog_req_counter}'


class RoutineHub:
    # catalog.register/deregister 等 kernel ack 默认超时(对标 ctx.ACK_TIMEOUT).
    # 半开 / 多 kernel 路由下 kernel 不回 ack 时不永久 hang;正常 < 1s.
    ACK_TIMEOUT = 30.0

    def __init__(self, routines: Routines,
                 modules: Optional[List[str]] = None,
                 *, transport: Optional[Transport] = None,
                 hub_id: str):
        if not hub_id:
            raise ValueError('hub_id is required (non-empty string, e.g. "zero"/"one")')
        self.runtime = ServerRuntime(routines, modules=modules)
        self._logger = self.runtime.logger
        # transport 由调用方传入(start_server/start_client 建对应实现).None 仅供
        # 不走 wire 的极端构造场景;正常路径必须有 transport.
        self.transport = transport
        # hub_id: 进程级稳定身份(如 "zero"/"one"),随 catalog.push 发给 kernel.
        # kernel 校验唯一性,重复则拒绝连接(list_routines 用 hub_id 标识归属,
        # 不再暴露内部 conn_id).
        self.hub_id = hub_id
        self.lifecycle = LifecycleManager(self, self.runtime)
        self.query = QueryService(self, self.runtime)
        # 启动统计
        self.runtime.print_summary()
        # on_inbound 分发表:event -> async handler(msg).每个 wire 回向事件独立方法,
        # 改一处不动长链.handler 全 async(sync 的 _on_message_req_reply_delivered /
        # _on_message_stream_data_delivered 也 async 化统一契约).未知 event table-miss
        # -> no-op(对标原 if/return 长链 fall-through).
        self._inbound_handlers = {
            MESSAGE_DELIVERED: self._on_message_delivered,
            ROUTINE_YIELDED: self._on_yielded,
            PUBSUB_DELIVERED: self._on_pubsub_delivered,
            MESSAGE_REQ_DELIVERED: self._on_message_req_delivered,
            MESSAGE_REQ_REPLY_DELIVERED: self._on_message_req_reply_delivered,
            MESSAGE_STREAM_OPEN_DELIVERED: self._on_message_stream_open_delivered,
            MESSAGE_STREAM_DATA_DELIVERED: self._on_message_stream_data_delivered,
            MESSAGE_STREAM_CANCEL_DELIVERED: self._on_message_stream_cancel_delivered,
            LIFECYCLE_STARTED: self._on_lifecycle_started,
            LIFECYCLE_STOPPED: self._on_lifecycle_stopped,
            ROUTINE_REJECTED: self._on_routine_rejected,
            ROUTINE_SUBMITTED: self._on_routine_submitted,
            ROUTINE_ACQUIRED: self._on_routine_acquired,
            ROUTINE_MODULE_LOADED: self._on_routine_module_loaded,
            ROUTINE_MODULE_UNLOADED: self._on_routine_module_unloaded,
            ROUTINE_RELEASED: self._on_routine_released,
            CATALOG_REGISTERED: self._on_catalog_registered,
            CATALOG_RELOADED: self._on_catalog_reloaded,
            CATALOG_DEREGISTER_CMD: self._on_catalog_deregister_cmd,
            CATALOG_DEREGISTERED: self._on_catalog_deregistered,
            CATALOG_PUSHED: self._on_catalog_pushed,
        }

    # --- 出站:build payload → transport.send_event ---

    async def _send_event(self, payload: Dict[str, Any],
                          peer_id: Optional[str] = None) -> None:
        await self.transport.send_event(payload, peer_id)

    async def request_stop(self, *, id: str,
                           peer_id: Optional[str] = None) -> None:
        if peer_id is None:
            raise RuntimeError('request_stop requires peer_id')
        await self.lifecycle.handle_stop(peer_id, {'id': id})

    async def send_lifecycle_started(self, *, id: str,
                                     peer_id: Optional[str] = None) -> None:
        # 只发 kernel;父 handle 的 notify_started 由 kernel 中转回 lifecycle.started
        # 时在 on_inbound 里做(统一经 kernel,不本地抄近路).
        await self._send_event({'event': LIFECYCLE_STARTED, 'id': id}, peer_id)

    async def send_lifecycle_created(self, *, id: str,
                                   modules: Optional[List[str]] = None,
                                   peer_id: Optional[str] = None) -> None:
        # created 回报给 kernel:kernel 等 this 回报后才发 routine.submitted 给父,
        # 所以父 submit 拿到 id 时 instance 一定已 created(无需 wait_created).
        # 带 modules:on_created() 钩子的返回值(实例级,static 返固定 list,dynamic
        # 按 kwargs 现算)回带----kernel 存进 node.declared(Start 的 TryAcquire 用)
        # + 经 submitted 回执带给父 handle(编排器算冲突).单一真理源,无 kernel→server RPC.
        payload: Dict[str, Any] = {'event': LIFECYCLE_CREATED, 'id': id}
        if modules is not None:
            payload['modules'] = modules
        await self._send_event(payload, peer_id)

    async def send_lifecycle_stopped(self, *, id: str,
                                     reason: ControlDoneReason = ControlDoneReason.UNKNOWN,
                                     result: Any = None,
                                     error: Optional[str] = None,
                                     peer_id: Optional[str] = None) -> None:
        # 同上:只发 kernel.父 handle 的 notify_done + pop 在 on_inbound 收到
        # kernel 中转的 lifecycle.stopped 时做.
        payload: Dict[str, Any] = {
            'event': LIFECYCLE_STOPPED,
            'id': id,
            'reason': reason.value if hasattr(reason, 'value') else str(reason),
        }
        if result is not None:
            payload['result'] = result
        if error is not None:
            payload['error'] = error
        await self._send_event(payload, peer_id)

    # --- routine 调 routine 反向事件(py→kernel) ---

    async def send_routine_submit(self, *, req_id: str, parent_id: str,
                                  name: str, kwargs: Dict[str, Any],
                                  peer_id: Optional[str] = None) -> None:
        # modules 不传----对标老版 push_quick 只发 name+kwargs,kernel 用 name 查
        # catalog 路由表拿 modules(单一真理源).
        await self._send_event({
            'event': ROUTINE_SUBMIT,
            'req_id': req_id,
            'parent_id': parent_id,
            'name': name,
            'kwargs': kwargs,
        }, peer_id)

    async def send_routine_start(self, *, child_id: str,
                                try_start: bool = False,
                                peer_id: Optional[str] = None) -> None:
        # 不带 kwargs----start 用 submit 时存入 cmd.Kwargs 的那份(submit kwargs 单一
        # 来源,created 和 start 共用).kernel 侧 OnStartChild → Start 用 cmd.Kwargs.
        # try_start=true:失败保留可重试;false:失败 kernel 清 node+订阅,本侧清 instance.
        payload: Dict[str, Any] = {'event': ROUTINE_START, 'child_id': child_id}
        if try_start:
            payload['try'] = True
        await self._send_event(payload, peer_id)

    async def send_routine_stop(self, *, child_id: str,
                                peer_id: Optional[str] = None) -> None:
        await self._send_event(
            {'event': ROUTINE_STOP, 'child_id': child_id}, peer_id,
        )

    async def send_routine_unsubmit(self, *, child_id: str,
                                    peer_id: Optional[str] = None) -> None:
        # 撤销提交:清 created 态子命令.跟 send_routine_submit 对称.
        await self._send_event(
            {'event': ROUTINE_UNSUBMIT, 'child_id': child_id}, peer_id,
        )

    async def send_routine_acquire(self, *, req_id: str, id: str,
                                   modules: list,
                                   peer_id: Optional[str] = None) -> None:
        # 运行时占领模块(跟静态声明同一底层 TryAcquire,start 体里主动调).
        await self._send_event({
            'event': ROUTINE_ACQUIRE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        }, peer_id)

    async def send_routine_release(self, *, req_id: str, id: str,
                                   modules: list,
                                   peer_id: Optional[str] = None) -> None:
        await self._send_event({
            'event': ROUTINE_RELEASE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        }, peer_id)

    async def send_routine_force_release(self, *, req_id: str, id: str,
                                         modules: list,
                                         peer_id: Optional[str] = None) -> None:
        # 强制释放:kernel 驱逐 cone 内第三方 holder 后空出,不自己占.
        # ack 走 routine.released(同 release 的 _acquire_futures 表).
        await self._send_event({
            'event': ROUTINE_FORCE_RELEASE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        }, peer_id)

    async def send_routine_force_acquire(self, *, req_id: str, id: str,
                                         modules: list,
                                         peer_id: Optional[str] = None) -> None:
        # 强制占领:kernel 驱逐 cone 内第三方 holder 后 rid 自己占住(带驱逐的 acquire).
        # ack 走 routine.acquired(同 acquire 的 _acquire_futures 表).
        await self._send_event({
            'event': ROUTINE_FORCE_ACQUIRE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        }, peer_id)

    async def send_routine_force_start(self, *, child_id: str,
                                       peer_id: Optional[str] = None) -> None:
        # 抢占式 start 子:kernel 驱逐占住子 declared 模块的第三方后 start.
        # 成功走 lifecycle.started(resolve child_ack None);失败 routine.rejected
        # op=force_start(resolve child_ack err → StartError).
        await self._send_event(
            {'event': ROUTINE_FORCE_START, 'child_id': child_id}, peer_id,
        )

    async def send_routine_load_module(self, *, req_id: str, parent_id: str,
                                       child_id: str, name: str = '',
                                       peer_id: Optional[str] = None) -> None:
        # 往父模块加载子模块(全局树动态增拓扑).只挂树不占用.ack 走 routine.module_loaded.
        # name 可重复(渲染用,如左右手都有"大拇指"),空则 kernel 用 child_id.
        await self._send_event({
            'event': ROUTINE_LOAD_MODULE,
            'req_id': req_id,
            'parent_id': parent_id,
            'child_id': child_id,
            'name': name,
        }, peer_id)

    async def send_routine_unload_module(self, *, req_id: str, child_id: str,
                                         peer_id: Optional[str] = None) -> None:
        # 卸载子模块(全局树动态删拓扑).ack 走 routine.module_unloaded.
        await self._send_event({
            'event': ROUTINE_UNLOAD_MODULE,
            'req_id': req_id,
            'child_id': child_id,
        }, peer_id)

    def register_handle(self, child_id: str, handle) -> None:
        self.runtime.register_handle(child_id, handle)

    def _spawn(self, coro) -> 'asyncio.Task':
        return self.runtime._spawn(coro)

    # --- 运行时 routine 注册/移除(对外 API, 单条 register/deregister 同步 kernel) ---

    async def register_routine(self, *routines: 'Type[Routine] | Routines') -> None:
        """运行时注册 routine 类(动态 per-agent skill routine 走此入口).async,等 kernel 回执.

        流程(kernel 是唯一真理源,对称 reload_routine / deregister_routine):
        1. 有 transport:对每个新 routine 发 ``catalog.register{req_id}`` → 等回执
           ``catalog.registered{ok}`` → ok=true 本地 ``Routines.register``;ok=false
           (同名冲突)抛 ``RegisterError``,本地不 register.
        2. 无 transport:直接本地 ``Routines.register``(降级;transport 连上后
           ``_post_connect`` 重连首帧的 ``catalog.push`` 全量兜底推给 kernel).

        非原子:逐个注册,一个失败抛异常停止,前面已成功注册的保留(文档说明).
        **同名一律 fail**(不区分 conn----无论同 conn 还是跨 conn,name 已存在就拒绝).
        覆盖语义走 ``reload_routine``(不区分 conn 覆盖).
        """
        import asyncio

        if self.transport is None:
            # 降级:无 transport 直接本地(不校验)
            self.runtime.routines.register(*routines)
            return

        # 有 transport:逐个发请求等回执(kernel 校验唯一性)
        for cls in self._flatten_register_args(routines):
            req_id = _new_catalog_req_id()
            fut = asyncio.get_running_loop().create_future()
            self.runtime.register_register_future(req_id, fut)
            try:
                await self.send_catalog_register(cls, req_id=req_id)
                # ok=true -> resolve None;ok=false -> 抛 RegisterError
                await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
                # ok=true:本地 register(kernel 已确认)
                self.runtime.routines.register(cls)
            finally:
                self.runtime.pop_register_future(req_id)

    async def reload_routine(self, *routines: 'Type[Routine] | Routines') -> None:
        """运行时重载 routine 类(routine 代码更新后重注册走此入口).async,等 kernel 回执.

        对称 ``register_routine`` 但语义不同:**不区分 conn,同名覆盖**(无论原归属是
        哪个 conn,新 reload 请求都覆盖路由).用于开发期迭代----改了 routine 代码后
        reload 替换旧类,无需先 deregister.

        流程:
        1. 有 transport:对每个 routine 发 ``catalog.reload{req_id}`` → 等回执
           ``catalog.reloaded{ok}`` → ok=true 本地 ``Routines.register``(同名覆盖);
           ok=false(罕见----name 为空等参数错)抛 ``ReloadError``,本地不动.
        2. 无 transport:直接本地 ``Routines.register``(降级,同名覆盖).

        非原子:逐个重载,一个失败抛异常停止,前面已成功重载的保留.
        """
        import asyncio

        if self.transport is None:
            # 降级:无 transport 直接本地(同名覆盖)
            self.runtime.routines.register(*routines)
            return

        # 有 transport:逐个发请求等回执(kernel 覆盖路由)
        for cls in self._flatten_register_args(routines):
            req_id = _new_catalog_req_id()
            fut = asyncio.get_running_loop().create_future()
            self.runtime.register_reload_future(req_id, fut)
            try:
                await self.send_catalog_reload(cls, req_id=req_id)
                # ok=true -> resolve None;ok=false -> 抛 ReloadError(罕见)
                await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
                # ok=true:本地 register(同名覆盖,kernel 已确认)
                self.runtime.routines.register(cls)
            finally:
                self.runtime.pop_reload_future(req_id)

    async def deregister_routine(self, name: str) -> Optional['Type[Routine]']:
        """运行时移除 routine 类(agent 销毁 / 清理失效 routine).async,等 kernel 回执.

        两跳流程(kernel 协调,支持跨 hub dereg):
        1. 请求者发 ``catalog.deregister{req_id, name}`` → kernel
        2. kernel 查路由找持有者 → 发 ``catalog.deregister.cmd{req_id, name}`` 给持有者
        3. 持有者收到 cmd → 本地 ``Routines.deregister`` → 发 ``catalog.deregister.cmd.ack{req_id, ok}`` 给 kernel
        4. kernel 收到 ack → 删自身路由 → 发 ``catalog.deregistered{req_id, ok}`` 给请求者
        5. 请求者收到 ok=true → resolve(若请求者==持有者,从 ``_deregister_results`` 取被移除的类)

        ok=false(name 不在 kernel 路由表 / 持有者本地 dereg 失败)抛 ``DeregisterError``,本地不动.
        无 transport 时直接本地删(降级).

        返回被移除的 routine 类(请求者==持有者时),或 None(请求者≠持有者,拿不到类).
        """
        import asyncio

        if self.transport is None:
            # 降级:无 transport 直接本地删
            return self.runtime.routines.deregister(name)

        req_id = _new_catalog_req_id()
        fut = asyncio.get_running_loop().create_future()
        self.runtime.register_deregister_future(req_id, fut)
        try:
            await self.send_catalog_deregister(name, req_id=req_id)
            # ok=true -> resolve None;ok=false -> 抛 DeregisterError
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
            # ok=true:本地 dereg 已在 cmd handler 里完成(请求者==持有者时).
            # 从 _deregister_results 取被移除的类(请求者==持有者时有值;否则 None).
            return self.runtime.pop_deregister_result(req_id)
        finally:
            self.runtime.pop_deregister_future(req_id)

    @staticmethod
    def _flatten_register_args(routines) -> list:
        """register_routine 接受 ``Type[Routine] | Routines`` 可变参数,展开成 list[Type[Routine]].

        Routines 实例 → 取其 .get_routines();单个类 → 直接入列表.
        """
        from .routine import Routines as _Routines
        out = []
        for r in routines:
            if isinstance(r, _Routines):
                out.extend(r.get_routines())
            else:
                out.append(r)
        return out

    async def send_catalog_register(self, cls: 'Type[Routine]',
                                    *, req_id: str = '',
                                    peer_id: Optional[str] = None) -> None:
        """发 catalog.register 单条增量注册给 kernel(带 req_id,等 catalog.registered 回执).

        payload: {event, req_id, name, is_passive, meta}.
        kernel ``handleCatalogRegister`` → 校验唯一性(同名 fail)→ ``RegisterRoutine`` → 回执.
        """
        payload: Dict[str, Any] = {
            'event': CATALOG_REGISTER,
            'name': cls.name,
            'is_passive': passive_wire(cls),
            'meta': getattr(cls, 'meta', {}),
        }
        if req_id:
            payload['req_id'] = req_id
        await self._send_event(payload, peer_id)

    async def send_catalog_reload(self, cls: 'Type[Routine]',
                                  *, req_id: str = '',
                                  peer_id: Optional[str] = None) -> None:
        """发 catalog.reload 单条重载给 kernel(带 req_id,等 catalog.reloaded 回执).

        payload: {event, req_id, name, is_passive, meta}.
        kernel ``handleCatalogReload`` → ``ReloadRoutine``(不区分 conn 覆盖)→ 回执 ok=true.
        """
        payload: Dict[str, Any] = {
            'event': CATALOG_RELOAD,
            'name': cls.name,
            'is_passive': passive_wire(cls),
            'meta': getattr(cls, 'meta', {}),
        }
        if req_id:
            payload['req_id'] = req_id
        await self._send_event(payload, peer_id)

    async def send_catalog_deregister(self, name: str,
                                      *, req_id: str = '',
                                      peer_id: Optional[str] = None) -> None:
        """发 catalog.deregister 请求移除给 kernel(带 req_id,等 catalog.deregistered 回执).

        两跳流程:kernel 收到后不直接删路由,而是发 catalog.deregister.cmd 给持有者 →
        持有者本地 dereg → 回执 catalog.deregister.cmd.ack → kernel 删路由 +
        回执 catalog.deregistered 给请求者.
        """
        payload: Dict[str, Any] = {
            'event': CATALOG_DEREGISTER,
            'name': name,
        }
        if req_id:
            payload['req_id'] = req_id
        await self._send_event(payload, peer_id)

    async def send_catalog_deregister_cmd_ack(self, *, req_id: str, ok: bool,
                                               error: Optional[str] = None,
                                               peer_id: Optional[str] = None) -> None:
        """发 catalog.deregister.cmd.ack 回执给 kernel(持有者本地 dereg 后).

        kernel 收到后:ok=true → 删自身路由 + 回执请求者 ok=true;ok=false → 不删路由,
        回执请求者 ok=false.
        """
        payload: Dict[str, Any] = {
            'event': CATALOG_DEREGISTER_CMD_ACK,
            'req_id': req_id,
            'ok': ok,
        }
        if error:
            payload['error'] = error
        await self._send_event(payload, peer_id)

    # --- p2p 通信(py→kernel) ---

    async def send_message(self, *, target_ids: list, send_event: str,
                           data, source_id: str,
                           peer_id=None) -> None:
        """发 message.* 事件(send/req/req_reply/stream_open/stream_data/stream_cancel).

        envelope 全在 data 里,kernel 不解析只按 target_ids 转发成对应的 *delivered.
        source_id 是发送方 routine 的 id(kernel 回环时填进 delivered.source.id).
        """
        await self._send_event({
            'event': send_event,
            'target_ids': target_ids,
            'data': data,
            'source_id': source_id,
        }, peer_id)

    # --- pubsub 通信(py→kernel) ---

    async def send_pubsub_subscribe(self, *, id: str, topic: str,
                                    namespace: str = '',
                                    peer_id: Optional[str] = None) -> None:
        await self._send_event({
            'event': PUBSUB_SUBSCRIBE, 'id': id, 'topic': topic,
            'namespace': namespace,
        }, peer_id)

    async def send_pubsub_unsubscribe(self, *, id: str, topic: str,
                                      namespace: str = '',
                                      peer_id: Optional[str] = None) -> None:
        await self._send_event({
            'event': PUBSUB_UNSUBSCRIBE, 'id': id, 'topic': topic,
            'namespace': namespace,
        }, peer_id)

    async def send_pubsub_publish(self, *, topic: str, data: Any,
                                  source_id: str, namespace: str = '',
                                  peer_id: Optional[str] = None) -> None:
        payload = {
            'event': PUBSUB_PUBLISH, 'topic': topic, 'source_id': source_id,
            'namespace': namespace,
        }
        if data is not None:
            payload['data'] = data
        await self._send_event(payload, peer_id)

    # --- yield(child→kernel) ---

    async def send_yield(self, *, id: str, data: Any = None,
                                 is_final: bool = False, error: Optional[str] = None,
                                 peer_id: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            'event': ROUTINE_YIELD, 'id': id, 'is_final': is_final,
        }
        if data is not None:
            payload['data'] = data
        if error is not None:
            payload['error'] = error
        await self._send_event(payload, peer_id)

    async def send_catalog_push(self,
                                peer_id: Optional[str] = None) -> None:
        """dial-in routine 连上后主动 push catalog 给 kernel(routines + modules).

        对标 dial-out 的 Req(get_modules/get_routines),但方向反过来----routine 主动
        发,kernel 收到注册路由表(handleCatalogPush)+ 回推 module.tree.modules 实例
        级不在此存(由 created 回报回带),这里只上报 routines 路由 + passive 收集
        + modules 池(kernel log 用).

        带 req_id 发送 catalog.pushed 回执:kernel 处理完全量注册后回
        ``{registered: [...], skipped: [...]}``.fire-and-forget(不阻塞等回执)----
        ``_post_connect`` 在 ``_recv_loop`` 之前执行,此时 recv loop 还没运行,
        阻塞等 ack 会死锁(回执到达需 recv loop dispatch).回执由 ``_on_catalog_pushed``
        收到后直接打印.
        """
        req_id = _new_catalog_req_id()
        count = len(self.runtime.routines.get_routines())
        payload: Dict[str, Any] = {
            'event': CATALOG_PUSH,
            'req_id': req_id,
            'routines': self.query.build_routines(),
            'modules': self.runtime.modules,
            'hub_id': self.hub_id,
        }
        await self._send_event(payload, peer_id)
        self._logger.info('catalog.push sent (req_id=%s, hub_id=%s): %d routines',
                          req_id, self.hub_id, count)

    # --- 入站分发(transport 调) ---

    async def dispatch_inbound(self, peer_id: str, msg: Dict[str, Any]) -> None:
        """transport 收到一条入站命令 → 按 event 分发(对标原 StreamController.reader).

        lifecycle.start/stop/destroy/created(带 name) → LifecycleManager(spawn 不阻塞
        reader);其余 → on_inbound(kernel→py 回向事件,spawn 跑).
        """
        event = msg.get('event', '')
        if event == MODULE_TREE:
            # dial-in:kernel 推模块树拓扑(走 Stream;dial-out 走 Req→handle_req).
            # 同步缓存(from_dict 快,不阻塞 reader).
            self.query.cache_module_tree(msg)
            return
        if event == ROUTINE_GET_RUNNING_REPLY:
            # dial-out:routine.get_running 回执 -> transport 解 future(dial-in 走 req 不到此).
            self.transport.resolve_get_running(msg)
            return
        if event == ROUTINE_GET_MODULE_TREE_REPLY:
            # dial-out:routine.get_module_tree 回执 -> transport 解 future(dial-in 走 req 不到此).
            self.transport.resolve_get_module_tree(msg)
            return
        if event == LIFECYCLE_START:
            self.runtime._spawn(self.lifecycle.handle_start(peer_id, msg))
        elif event == LIFECYCLE_STOP:
            self.runtime._spawn(self.lifecycle.handle_stop(peer_id, msg))
        elif event == LIFECYCLE_DESTROY:
            self.runtime._spawn(self.lifecycle.handle_destroy(peer_id, msg))
        elif event == LIFECYCLE_CREATED and msg.get('name'):
            # kernel→server 命令(带 name):实例化+注册+建 inbox+auto_subscribe.
            self.runtime._spawn(self.lifecycle.handle_created(peer_id, msg))
        else:
            self.runtime._spawn(self.on_inbound(msg))

    async def on_peer_down(self, peer_id: str) -> None:
        """peer 断开(transport 通知)→ 强制清理该 peer 的所有 running instance."""
        self.runtime._spawn(self.lifecycle.force_stop_peer(peer_id))

    # --- reader 路由 handle lifecycle + submit 回执(由 on_inbound 处理) ---

    async def on_inbound(self, msg: Dict[str, Any]) -> None:
        """kernel->py 回向事件.按 event 查 ``_inbound_handlers`` 分发表派发,每个
        event 独立 ``_on_*`` 方法(lifecycle.started/stopped notify handle,
        routine.submitted/acquired/released resolve ack future,message.*/pubsub.*/body.*
        各自 _on_*).未知 event -> table-miss -> no-op(对标原 if/return 长链 fall-through).

        wire 契约:kernel(Go)发来的 id / child_id 一律是 string(Go 侧发送边界
        strconv.Itoa).本地注册表(handle / child_ack / created_by_rid)的 key 也是
        string(handle.id = str(command_id)),直接查表即可.
        """
        handler = self._inbound_handlers.get(msg.get('event', ''))
        if handler is not None:
            await handler(msg)

    # --- on_inbound 分发表各 event handler(全 async,统一 ``await handler(msg)`` 契约)---

    async def _on_lifecycle_started(self, msg: Dict[str, Any]) -> None:
        cid = msg.get('id', '')
        handle = self.runtime.get_handle(cid)
        if handle is not None:
            handle.notify_started()
            # resolve handle._ack(start/force_start 成功回执;非 start 路径 _ack 为 None,幂等)
            handle._resolve_ack(None)

    async def _on_lifecycle_stopped(self, msg: Dict[str, Any]) -> None:
        cid = msg.get('id', '')
        handle = self.runtime.get_handle(cid)
        if handle is not None:
            reason = msg.get('reason', '')
            # error 优先用 stopped 带的(routine 抛异常时 str(exc));
            # 没带则按 reason 退回通用文本(兼容旧 kernel/无 error 场景).
            error = msg.get('error')
            if not error and reason == ControlDoneReason.ERROR.value:
                error = f'routine stopped with reason={reason}'
            # reason 透传给 handle(父侧可据此分流 force/disconnect/auto/...).
            handle.notify_done(result=msg.get('result'), error=error, reason=reason)
            # 先 resolve handle._ack(stop/unsubmit 成功回执)再 pop_handle --
            # ack 在 handle 上,pop 后丢引用拿不到.fire 路径 _ack 为 None,幂等.
            handle._resolve_ack(None)
            self.runtime.pop_handle(cid)
        # instance 清理由各自路径负责:created 态走 handle_destroy(_cleanup),
        # running 态走 runner 退出(_cleanup).这里只管 handle.

    async def _on_routine_rejected(self, msg: Dict[str, Any]) -> None:
        cid = msg.get('child_id', '')
        err = str(msg.get('error', 'rejected'))
        op = str(msg.get('op', ''))
        # rejected 不 pop handle(只 _cleanup instance) -- handle 仍在 _handles 表,必命中.
        handle = self.runtime.get_handle(cid)
        if op in ('start', 'force_start'):
            # start/try_start/force_start 失败:返回 error 不抛(模块冲突是正常业务
            # 情况,占住者未释放属预期,不该打断调用方 start() 体).
            if handle is not None:
                handle._resolve_ack(err)  # str error -> start 返回 StartError
            # start/force_start(非 try)失败:清理 created instance(Go 侧已清
            # node+订阅),handle 失效不可重试.try_start 失败:不清,保留可重试.
            # rejected 不带 peer_id:扫 running_instances 找 rid 匹配的 prid.
            is_try = bool(msg.get('try'))
            if not is_try and cid:
                for prid in list(self.runtime.running_instances):
                    if prid.rsplit(':', 1)[-1] == cid:
                        self.lifecycle._cleanup(prid)
                        break
        else:
            # stop/unsubmit 失败:逻辑错误(已 start 调 unsubmit 等),抛异常
            if handle is not None:
                handle._reject_ack(RuntimeError(err))

    async def _on_routine_submitted(self, msg: Dict[str, Any]) -> None:
        req_id = msg.get('req_id', '')
        fut = self.runtime.pop_submit_future(req_id)
        if fut is not None and not fut.done():
            if msg.get('error'):
                fut.set_exception(SubmitError(str(msg['error'])))
            else:
                # child_id (string) + modules (list|None).modules 是 kernel 确定
                # 的占用模块(static=catalog 缓存,dynamic=kwargs 现算),带给 handle
                # 供编排器算冲突.ctx.submit 解包.
                child_id = str(msg.get('child_id', ''))
                modules = msg.get('modules')
                if not isinstance(modules, list):
                    modules = None
                fut.set_result((child_id, modules))

    def _resolve_ack_future(self, fut, msg, exc_type, err_default) -> None:
        """acquired/released 共享模板:ok=false -> 抛 ``exc_type``;ok=true -> resolve.
        acquire/release ack 都走 runtime._acquire_futures 表(release 复用同一通路,
        wire 上 routine.released 复用 routine.acquired 的 ack future)."""
        if fut is not None and not fut.done():
            if msg.get('ok', True) is False:
                fut.set_exception(exc_type(str(msg.get('error', err_default))))
            else:
                fut.set_result(None)

    async def _on_routine_acquired(self, msg: Dict[str, Any]) -> None:
        # acquire ack:ok=false 带 error(ConflictError) -> future 抛 AcquireError;ok=true -> resolve
        fut = self.runtime.pop_acquire_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, AcquireError, 'acquire failed')

    async def _on_routine_released(self, msg: Dict[str, Any]) -> None:
        # release ack:ok=false 带 error(罕见,release 一般不冲突);ok=true -> resolve
        fut = self.runtime.pop_acquire_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, ReleaseError, 'release failed')

    async def _on_routine_module_loaded(self, msg: Dict[str, Any]) -> None:
        # load_module ack:ok=false(child 已存在 / parent 不存在)-> future 抛 LoadModuleError
        fut = self.runtime.pop_load_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, LoadModuleError, 'load_module failed')

    async def _on_routine_module_unloaded(self, msg: Dict[str, Any]) -> None:
        # unload_module ack:ok=false(有子 / 被占 / 不存在)-> future 抛 UnloadModuleError
        fut = self.runtime.pop_unload_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, UnloadModuleError, 'unload_module failed')

    async def _on_catalog_registered(self, msg: Dict[str, Any]) -> None:
        # catalog.register ack:ok=false(同名冲突)-> future 抛 RegisterError;
        # ok=true -> resolve(None).py 收到 ok=true 才本地 Routines.register.
        fut = self.runtime.pop_register_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, RegisterError, 'register rejected by kernel')

    async def _on_catalog_reloaded(self, msg: Dict[str, Any]) -> None:
        # catalog.reload ack:ok=false(罕见----name 为空等参数错)-> future 抛 ReloadError;
        # ok=true -> resolve(None).py 收到 ok=true 才本地 Routines.register(同名覆盖).
        # reload 总 ok=true(kernel 不区分 conn 覆盖,不冲突).
        fut = self.runtime.pop_reload_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, ReloadError, 'reload rejected by kernel')

    async def _on_catalog_deregistered(self, msg: Dict[str, Any]) -> None:
        # catalog.deregister ack(请求者收到):ok=false(name 不在 kernel 路由表 / 持有者
        # 本地 dereg 失败)-> future 抛 DeregisterError;ok=true -> resolve(None).
        # 本地 dereg 已在持有者的 _on_catalog_deregister_cmd 里完成(请求者==持有者时),
        # 请求者≠持有者时请求者本地本就没有该 routine,无需操作.
        fut = self.runtime.pop_deregister_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, DeregisterError, 'deregister rejected by kernel')

    async def _on_catalog_pushed(self, msg: Dict[str, Any]) -> None:
        # catalog.pushed ack:kernel 处理完 catalog.push 全量注册后回执,带
        # {registered: [...], skipped: [...]}.send_catalog_push 是 fire-and-forget
        # (不阻塞等),此处收到回执后直接打印结果(成功 N 条 / 跳过 M 条同名冲突).
        registered = msg.get('registered', [])
        skipped = msg.get('skipped', [])
        req_id = msg.get('req_id', '')
        if skipped:
            self._logger.warning(
                'catalog.pushed (req_id=%s): registered=%d, skipped=%d %s',
                req_id, len(registered), len(skipped), skipped,
            )
        else:
            self._logger.info(
                'catalog.pushed (req_id=%s): registered=%d, skipped=0',
                req_id, len(registered),
            )

    async def _on_catalog_deregister_cmd(self, msg: Dict[str, Any]) -> None:
        # catalog.deregister.cmd(持有者收到):kernel 通知本 hub 本地 dereg 某 routine.
        # 本地 Routines.deregister → 存结果到 _deregister_results(供请求者==持有者时
        # deregister_routine 取)→ 发 catalog.deregister.cmd.ack{ok} 给 kernel.
        # kernel 收到 ack 后删路由 + 回执请求者.
        req_id = msg.get('req_id', '')
        name = msg.get('name', '')
        if not name:
            # 参数错,发 ack ok=false
            await self.send_catalog_deregister_cmd_ack(
                req_id=req_id, ok=False, error='name is required',
            )
            return
        # 本地 dereg(返回被移除的类,None 表示不存在)
        removed = self.runtime.routines.deregister(name)
        # 存结果(供请求者==持有者时 deregister_routine 取)
        if req_id:
            self.runtime.set_deregister_result(req_id, removed)
        # 发 ack 给 kernel(总 ok=true,本地已删;即使 removed=None 也 ok=true----kernel 删路由)
        await self.send_catalog_deregister_cmd_ack(req_id=req_id, ok=True)
        self._logger.info('catalog.deregister.cmd: local dereg %s (removed=%s)',
                          name, removed.__name__ if removed else None)


    def _resolve_source(self, msg):
        """从 delivered 消息的 source 字段构造 RoutineSource(补 name:source 在本
        server 则能查到 name,跨 server 只有 id).对标老版 p2p/inbox 的 source 构造."""
        src = msg.get('source') or {}
        source_id = str(src.get('id', ''))
        src_inst = self.runtime.get_created(source_id)
        if src_inst is not None:
            return RoutineSource(id=source_id, name=src_inst.name)
        return RoutineSource(id=source_id)

    async def _on_message_delivered(self, msg):
        """message.delivered{target_id, data, source}:纯定向消息,调 target 的
        ``on_message(source, data)``.created 后即可收(不必 start).

        派发是 spawn 并发--对端 on_message 可能并发 fire,乱序到达,业务侧自带 id reorder.
        target 未 created(created_by_rid 查不到)则丢弃.
        """
        target_id = msg.get('target_id', '')
        inst = self.runtime.get_created(target_id)
        if inst is None:
            return
        source = self._resolve_source(msg)
        self.runtime._spawn(inst.on_message(source, msg.get('data')))

    async def _on_message_req_delivered(self, msg):
        """message.req_delivered{target_id, data, source}:req 到达 provider,按
        envelope event 路由到 ``@request`` handler.envelope 全在 data 里."""
        target_id = msg.get('target_id', '')
        inst = self.runtime.get_created(target_id)
        if inst is None:
            return
        source = self._resolve_source(msg)
        await inst._serve_request(msg.get('data') or {}, source)

    async def _on_message_req_reply_delivered(self, msg):
        """message.req_reply_delivered{data}:req 回执到达 caller,resolve req future."""
        data = msg.get('data') or {}
        req_id = data.get(ENVELOPE_REQ_ID, '')
        fut = self.runtime.pop_req_future(req_id)
        if fut is not None and not fut.done():
            if data.get(ENVELOPE_OK):
                fut.set_result(data.get(ENVELOPE_DATA))
            else:
                from .errors import ReqError
                fut.set_exception(
                    ReqError(str(data.get(ENVELOPE_ERROR, 'req failed'))),
                )

    async def _on_message_stream_open_delivered(self, msg):
        """message.stream_open_delivered{target_id, data, source}:开流到达 provider,
        spawn provider task 产 message.stream_data 帧."""
        target_id = msg.get('target_id', '')
        inst = self.runtime.get_created(target_id)
        if inst is None:
            return
        source = self._resolve_source(msg)
        await inst._serve_stream(msg.get('data') or {}, source)

    async def _on_message_stream_data_delivered(self, msg):
        """message.stream_data_delivered{data}:chunk / eof 到达 caller,喂 StreamReader."""
        data = msg.get('data') or {}
        stream_id = data.get(ENVELOPE_STREAM_ID, '')
        reader = self.runtime.get_stream_reader(stream_id)
        if reader is None:
            return
        if data.get(ENVELOPE_EOF):
            reader.feed_eof(data.get(ENVELOPE_EOF), data.get(ENVELOPE_ERROR))
            self.runtime.pop_stream_reader(stream_id)
        else:
            reader.feed_chunk(data.get(ENVELOPE_CHUNK))

    async def _on_message_stream_cancel_delivered(self, msg):
        """message.stream_cancel_delivered{data}:caller 取消 stream,取消 provider gen."""
        data = msg.get('data') or {}
        stream_id = data.get(ENVELOPE_STREAM_ID, '')
        task = self.runtime.get_provider_stream(stream_id)
        if task is not None and not task.done():
            task.cancel()
        self.runtime.pop_provider_stream(stream_id)

    async def _on_pubsub_delivered(self, msg: Dict[str, Any]) -> None:
        """kernel fanout 回来的 pubsub.delivered{subscriber_id, topic, namespace, data, source}.

        按 subscriber_id + (namespace, topic) 找本地注册的 handler(@subscribe /
        ctx.subscribe),调 ``handler(source, data)``.source 补 name(若 source
        instance 在本 server).
        """
        subscriber_id = msg.get('subscriber_id', '')
        topic = msg.get('topic', '')
        namespace = msg.get('namespace', '') or ''
        data = msg.get('data')
        src = msg.get('source') or {}
        source = RoutineSource(id=str(src.get('id', '')))
        src_inst = self.runtime.get_created(source.id)
        if src_inst is not None:
            source = RoutineSource(id=source.id, name=src_inst.name)
        handler = self.runtime.get_subscriber_handler(subscriber_id, namespace, topic)
        if handler is None:
            return
        try:
            await handler(source, data)
        except Exception as exc:
            self._logger.exception(
                f'subscriber {subscriber_id} @subscribe({topic!r}) failed: {exc}',
            )

    async def _on_yielded(self, msg: Dict[str, Any]) -> None:
        """kernel 中转回来的 routine.yielded{id, data, is_final, error}.

        按 id(child_id)找父侧 submit 时注册的 handle,喂它的 yield 迭代队列.
        """
        child_id = msg.get('id', '')
        handle = self.runtime.get_handle(child_id)
        if handle is None:
            return
        is_final = bool(msg.get('is_final', False))
        error = msg.get('error')
        data = msg.get('data') if not is_final else None
        handle._on_yield_chunk(data=data, is_final=is_final, error=error)


async def start_server(routines: Routines,
                       modules: Optional[List[str]] = None,
                       address: str = '0.0.0.0:50051',
                       *,
                       hub_id: str) -> None:
    """dial-out 模型:routine 当 grpc server,kernel 主动 dial 进来."""
    transport = GrpcServerTransport(address)
    server = RoutineHub(routines, modules=modules, transport=transport, hub_id=hub_id)
    transport.attach(server)
    await transport.start()
    try:
        await transport.wait()
    finally:
        await transport.stop()


async def start_client(routines: Routines,
                       modules: Optional[List[str]] = None,
                       address: str = '127.0.0.1:50051',
                       *,
                       hub_id: str) -> None:
    """dial-in 模型:routine 当 grpc client,主动 dial kernel server.

    连上后 transport 自动 Req 拉 module.tree + push catalog(_post_connect),
    kernel 收到注册路由表后即可 Execute.kernel 关闭/重启时 transport 自动重连 +
    重新注册(见 GrpcClientTransport._run 重连 loop)."""
    transport = GrpcClientTransport(address)
    server = RoutineHub(routines, modules=modules, transport=transport, hub_id=hub_id)
    transport.attach(server)
    await transport.start()
    try:
        await transport.wait()
    finally:
        await transport.stop()
