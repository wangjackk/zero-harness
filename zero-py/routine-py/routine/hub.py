"""RoutineHub ---- routine 生命周期 + 跨 routine 通信(传输无关).

传输经 Transport 抽象(InProcessTransport / GrpcClientTransport)----本类
不关心 wire:入站 transport → dispatch_inbound 按 event 分发(lifecycle.start/
stop/destroy/created → LifecycleManager;module.tree → 缓存;其余 →
on_inbound);出站 send_* helpers build payload 后调 transport.send_event.

组装入口 ``launch()`` 在 kernel 包(Tree + Kernel + InProcessLink + RoutineHub
装配, kernel 起 root、hub push catalog、passive 自动拉起).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Type

from .errors import (
    AcquireError, DeregisterError, LoadModuleError, RegisterError, ReloadError,
    ReleaseError, SubmitError, UnloadModuleError,
)
from .lifecycle import LifecycleManager
from .module_tree import ModuleTree
from .protocol import (
    ROUTINE_YIELD, ROUTINE_YIELDED, CATALOG_PUSH, CATALOG_PUSHED,
    CATALOG_REGISTER, CATALOG_REGISTERED, CATALOG_RELOAD, CATALOG_RELOADED,
    CATALOG_DEREGISTER, CATALOG_DEREGISTER_CMD, CATALOG_DEREGISTER_CMD_ACK,
    CATALOG_DEREGISTERED, ControlDoneReason,
    LIFECYCLE_EVENT, ENVELOPE_CANCEL, ENVELOPE_CHUNK, ENVELOPE_DATA, LIFECYCLE_UNWATCH,
    LIFECYCLE_WATCH, ENVELOPE_EOF, ENVELOPE_ERROR, ENVELOPE_OK,
    ENVELOPE_REQ_ID, LIFECYCLE_SNAPSHOT, ENVELOPE_STREAM_ID,
    LIFECYCLE_CREATED, LIFECYCLE_DESTROY, LIFECYCLE_START,
    LIFECYCLE_STARTED, LIFECYCLE_STOP, LIFECYCLE_STOPPED, MODULE_TREE,
    MESSAGE_DELIVERED, MESSAGE_REQ_DELIVERED, MESSAGE_REQ_REPLY_DELIVERED,
    MESSAGE_STREAM_CANCEL_DELIVERED, MESSAGE_STREAM_DATA_DELIVERED,
    MESSAGE_STREAM_OPEN_DELIVERED, PUBSUB_DELIVERED, PUBSUB_PUBLISH,
    PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE, ROUTINE_ACQUIRE, ROUTINE_ACQUIRED,
    ROUTINE_FORCE_RELEASE, ROUTINE_FORCE_ACQUIRE, ROUTINE_FORCE_START,
    ROUTINE_LOAD_MODULE, ROUTINE_MODULE_LOADED, ROUTINE_MODULE_UNLOADED,
    ROUTINE_RELEASE, ROUTINE_RELEASED, ROUTINE_SUBMIT, ROUTINE_SUBMITTED,
    ROUTINE_REJECTED, ROUTINE_START, ROUTINE_STOP, ROUTINE_UNLOAD_MODULE,
    ROUTINE_UNSUBMIT,
)
from .routine import RoutineSource, Routines, passive_wire
from .runtime import ServerRuntime
from .transport import Transport

# catalog req_id 计数器(前缀 'cat' 跟 ctx 的 'r' 区分)
_catalog_req_counter = 0


def _new_catalog_req_id() -> str:
    global _catalog_req_counter
    _catalog_req_counter += 1
    return f'cat{_catalog_req_counter}'


class RoutineHub:
    # catalog.register/deregister 等 kernel ack 默认超时(对标 ctx.ACK_TIMEOUT)
    ACK_TIMEOUT = 30.0

    def __init__(self, routines: Routines,
                 modules: Optional[List[str]] = None, *,
                 transport: Optional[Transport] = None,
                 hub_id: str = 'local'):
        self.runtime = ServerRuntime(routines, modules=modules)
        self._logger = self.runtime.logger
        # transport 由 connect() 注入(inproc link 或 grpc client);构造期
        # 可缺省,connect 之前调用出站方法会 AttributeError(早暴露).
        self.transport = transport
        self.hub_id = hub_id
        self.lifecycle = LifecycleManager(self, self.runtime)
        self.runtime.print_summary()
        # on_inbound 分发表:event -> async handler(msg).未知 event table-miss
        # -> no-op.
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

    async def connect(self, kernel) -> 'Connection':
        """接入 kernel(KernelRef: kernel.create(modules) 的进程内实体或
        kernel.remote(addr) 的远程引用),完成传输组装 + catalog push,返回
        Connection(stop 级联关停).

        实现在 kernel.launch.connect(hub, kernel)----此处经 duck-typed
        参数转发,避免 hub → kernel 的 import 环.
        """
        from .kernel.launch import connect as _connect
        return await _connect(self, kernel)

    # --- 出站:build payload → transport.send_event ---

    async def _send_event(self, payload: Dict[str, Any]) -> None:
        await self.transport.send_event(payload)

    async def request_stop(self, *, id: str) -> None:
        """routine 自杀请求:本地直接走 handle_stop(不经 kernel 绕圈)."""
        await self.lifecycle.handle_stop({'id': id})

    async def handle_peer_down(self, peer_id: str) -> None:
        """transport 断线通知: dial 的 kernel 世界已死 ---- handle 全作废 +
        本地实例全量销毁 (不区分 passive: 老实例以 stale rid 继续发 req, 只会
        污染注册表; 重连后 catalog push 重建, 拿新 rid).

        await 销毁完成才返回 ---- 调用方(transport 重连 loop)等它结束后再
        进入重连期, 避免销毁期与重建期重叠撞 rid."""
        self._logger.warning(
            'peer %s down; invalidating handles, destroying local instances',
            peer_id)
        self.runtime.peer_down_all(f'transport peer down ({peer_id})')
        await self.lifecycle.stop_all_local()

    async def send_lifecycle_started(self, *, id: str) -> None:
        # 只发 kernel;父 handle 的 notify_started 由 kernel 中转回
        # lifecycle.started 时在 on_inbound 里做(统一经 kernel,不本地抄近路).
        await self._send_event({'event': LIFECYCLE_STARTED, 'id': id})

    async def send_lifecycle_created(self, *, id: str,
                                     modules: Optional[List[str]] = None) -> None:
        # created 回报给 kernel:kernel 等 this 回报后才发 routine.submitted 给父,
        # 所以父 submit 拿到 id 时 instance 一定已 created.带 modules(on_created
        # 钩子返回值)----kernel 存进 node.declared(Start 的 TryAcquire 用)+ 经
        # submitted 回执带给父 handle.
        payload: Dict[str, Any] = {'event': LIFECYCLE_CREATED, 'id': id}
        if modules is not None:
            payload['modules'] = modules
        await self._send_event(payload)

    async def send_lifecycle_stopped(self, *, id: str,
                                     reason: ControlDoneReason = ControlDoneReason.UNKNOWN,
                                     result: Any = None,
                                     error: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            'event': LIFECYCLE_STOPPED,
            'id': id,
            'reason': reason.value if hasattr(reason, 'value') else str(reason),
        }
        if result is not None:
            payload['result'] = result
        if error is not None:
            payload['error'] = error
        await self._send_event(payload)

    # --- routine 调 routine 反向事件(hub→kernel) ---

    async def send_routine_submit(self, *, req_id: str, parent_id: str,
                                  name: str, kwargs: Dict[str, Any]) -> None:
        await self._send_event({
            'event': ROUTINE_SUBMIT,
            'req_id': req_id,
            'parent_id': parent_id,
            'name': name,
            'kwargs': kwargs,
        })

    async def send_routine_start(self, *, child_id: str,
                                 try_start: bool = False) -> None:
        payload: Dict[str, Any] = {'event': ROUTINE_START, 'child_id': child_id}
        if try_start:
            payload['try'] = True
        await self._send_event(payload)

    async def send_routine_stop(self, *, child_id: str) -> None:
        await self._send_event({'event': ROUTINE_STOP, 'child_id': child_id})

    async def send_routine_unsubmit(self, *, child_id: str) -> None:
        await self._send_event({'event': ROUTINE_UNSUBMIT, 'child_id': child_id})

    async def send_routine_acquire(self, *, req_id: str, id: str,
                                   modules: list) -> None:
        await self._send_event({
            'event': ROUTINE_ACQUIRE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        })

    async def send_routine_release(self, *, req_id: str, id: str,
                                   modules: list) -> None:
        await self._send_event({
            'event': ROUTINE_RELEASE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        })

    async def send_routine_force_release(self, *, req_id: str, id: str,
                                         modules: list) -> None:
        await self._send_event({
            'event': ROUTINE_FORCE_RELEASE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        })

    async def send_routine_force_acquire(self, *, req_id: str, id: str,
                                         modules: list) -> None:
        await self._send_event({
            'event': ROUTINE_FORCE_ACQUIRE,
            'req_id': req_id,
            'id': id,
            'modules': modules,
        })

    async def send_routine_force_start(self, *, child_id: str) -> None:
        await self._send_event({'event': ROUTINE_FORCE_START, 'child_id': child_id})

    async def send_routine_load_module(self, *, req_id: str, parent_id: str,
                                       child_id: str, name: str = '') -> None:
        await self._send_event({
            'event': ROUTINE_LOAD_MODULE,
            'req_id': req_id,
            'parent_id': parent_id,
            'child_id': child_id,
            'name': name,
        })

    async def send_routine_unload_module(self, *, req_id: str,
                                         child_id: str) -> None:
        await self._send_event({
            'event': ROUTINE_UNLOAD_MODULE,
            'req_id': req_id,
            'child_id': child_id,
        })

    def register_handle(self, child_id: str, handle) -> None:
        self.runtime.register_handle(child_id, handle)

    def _spawn(self, coro) -> asyncio.Task:
        return self.runtime._spawn(coro)

    # --- 运行时 routine 注册/移除(对外 API,等 kernel 回执) ---

    async def register_routine(self, *routines: 'Type[Routine] | Routines') -> None:
        """运行时注册 routine 类.等 kernel catalog.registered 回执 ok=true 才本地
        register;同名冲突抛 RegisterError.非原子:逐个注册,一个失败停止."""
        for cls in self._flatten_register_args(routines):
            req_id = _new_catalog_req_id()
            fut = asyncio.get_running_loop().create_future()
            self.runtime.register_register_future(req_id, fut)
            try:
                await self.send_catalog_register(cls, req_id=req_id)
                await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
                self.runtime.routines.register(cls)
            finally:
                self.runtime.pop_register_future(req_id)

    async def reload_routine(self, *routines: 'Type[Routine] | Routines') -> None:
        """运行时重载 routine 类(不区分归属,同名覆盖).等 kernel reloaded 回执."""
        for cls in self._flatten_register_args(routines):
            req_id = _new_catalog_req_id()
            fut = asyncio.get_running_loop().create_future()
            self.runtime.register_reload_future(req_id, fut)
            try:
                await self.send_catalog_reload(cls, req_id=req_id)
                await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
                self.runtime.routines.register(cls)
            finally:
                self.runtime.pop_reload_future(req_id)

    async def deregister_routine(self, name: str) -> Optional['Type[Routine]']:
        """运行时移除 routine 类.两跳流程(kernel 协调,in-proc 下请求者==持有者):
        deregister → kernel 发 cmd → 本地 dereg → ack → kernel 删路由+停实例 →
        deregistered 回执.返回被移除的类."""
        req_id = _new_catalog_req_id()
        fut = asyncio.get_running_loop().create_future()
        self.runtime.register_deregister_future(req_id, fut)
        try:
            await self.send_catalog_deregister(name, req_id=req_id)
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
            return self.runtime.pop_deregister_result(req_id)
        finally:
            self.runtime.pop_deregister_future(req_id)

    @staticmethod
    def _flatten_register_args(routines) -> list:
        out = []
        for r in routines:
            if isinstance(r, Routines):
                out.extend(r.get_routines())
            else:
                out.append(r)
        return out

    async def send_catalog_register(self, cls: 'Type[Routine]',
                                    *, req_id: str = '') -> None:
        await self._send_event({
            'event': CATALOG_REGISTER,
            'name': cls.name,
            'is_passive': passive_wire(cls),
            'meta': getattr(cls, 'meta', {}),
            'req_id': req_id,
        })

    async def send_catalog_reload(self, cls: 'Type[Routine]',
                                  *, req_id: str = '') -> None:
        await self._send_event({
            'event': CATALOG_RELOAD,
            'name': cls.name,
            'is_passive': passive_wire(cls),
            'meta': getattr(cls, 'meta', {}),
            'req_id': req_id,
        })

    async def send_catalog_deregister(self, name: str,
                                      *, req_id: str = '') -> None:
        await self._send_event({
            'event': CATALOG_DEREGISTER,
            'name': name,
            'req_id': req_id,
        })

    async def send_catalog_deregister_cmd_ack(self, *, req_id: str, ok: bool,
                                              error: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            'event': CATALOG_DEREGISTER_CMD_ACK,
            'req_id': req_id,
            'ok': ok,
        }
        if error:
            payload['error'] = error
        await self._send_event(payload)

    # --- p2p / pubsub / yield 通信(hub→kernel) ---

    async def send_message(self, *, target_ids: list, send_event: str,
                           data: Any, source_id: str) -> None:
        """发 message.* 事件.envelope 全在 data 里,kernel 不解析只按 target_ids
        转发成对应的 *delivered."""
        await self._send_event({
            'event': send_event,
            'target_ids': target_ids,
            'data': data,
            'source_id': source_id,
        })

    async def send_pubsub_subscribe(self, *, id: str, topic: str,
                                    namespace: str = '') -> None:
        await self._send_event({
            'event': PUBSUB_SUBSCRIBE, 'id': id, 'topic': topic,
            'namespace': namespace,
        })

    async def send_pubsub_unsubscribe(self, *, id: str, topic: str,
                                      namespace: str = '') -> None:
        await self._send_event({
            'event': PUBSUB_UNSUBSCRIBE, 'id': id, 'topic': topic,
            'namespace': namespace,
        })

    async def send_pubsub_publish(self, *, topic: str, data: Any,
                                  source_id: str, namespace: str = '') -> None:
        payload = {
            'event': PUBSUB_PUBLISH, 'topic': topic, 'source_id': source_id,
            'namespace': namespace,
        }
        if data is not None:
            payload['data'] = data
        await self._send_event(payload)

    async def send_yield(self, *, id: str, data: Any = None,
                         is_final: bool = False,
                         error: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {
            'event': ROUTINE_YIELD, 'id': id, 'is_final': is_final,
        }
        if data is not None:
            payload['data'] = data
        if error is not None:
            payload['error'] = error
        await self._send_event(payload)

    # --- lifecycle 观察流(hub→kernel) ---

    async def send_lifecycle_watch(self, *, req_id: str) -> None:
        """发 lifecycle.watch(开启生命周期观察,幂等)."""
        await self._send_event({'event': LIFECYCLE_WATCH, 'req_id': req_id})

    async def send_lifecycle_unwatch(self) -> None:
        """发 lifecycle.unwatch(关闭观察,最后一个观察者注销时发)."""
        await self._send_event({'event': LIFECYCLE_UNWATCH})

    async def send_catalog_push(self) -> None:
        """启动后主动 push catalog 给 kernel(routines 路由 + passive + modules).

        fire-and-forget(不阻塞等回执)----回执 CATALOG_PUSHED 由
        _on_catalog_pushed 收到后打印.kernel 注册完即推 module.tree + 起 passive.
        """
        req_id = _new_catalog_req_id()
        payload: Dict[str, Any] = {
            'event': CATALOG_PUSH,
            'req_id': req_id,
            'routines': self.build_routines(),
            'modules': self.runtime.modules,
            'hub_id': self.hub_id,
        }
        await self._send_event(payload)
        self._logger.info('catalog.push sent (req_id=%s, hub_id=%s): %d routines',
                          req_id, self.hub_id, len(payload['routines']))

    def build_routines(self) -> List[Dict[str, Any]]:
        """catalog.push 的 routines 载荷:name + is_passive wire + meta."""
        return [
            {'name': cls.name, 'is_passive': passive_wire(cls),
             'meta': getattr(cls, 'meta', {})}
            for cls in self.runtime.routines.get_routines()
        ]

    # --- 入站分发(link worker 调) ---

    async def dispatch_inbound(self, peer_id: str, msg: Dict[str, Any]) -> None:
        """link 收到一条 kernel→hub 消息 → 按 event 分发.

        lifecycle.start/stop/destroy/created(带 name) → LifecycleManager
        (spawn 不阻塞 worker);module.tree → 同步缓存;其余 → on_inbound.
        """
        event = msg.get('event', '')
        if event == MODULE_TREE:
            self._cache_module_tree(msg)
            return
        if event == LIFECYCLE_EVENT:
            # 框架事件帧:inline 非阻塞 fanout(只做 queue put,不堵 worker).
            self.runtime.fanout_lifecycle(msg)
            return
        if event == LIFECYCLE_SNAPSHOT:
            # enable 回执:resolve 请求 future(快照帧由请求者灌进自己的队列).
            self._on_lifecycle_snapshot(msg)
            return
        if event == LIFECYCLE_START:
            self.runtime._spawn(self.lifecycle.handle_start(msg))
        elif event == LIFECYCLE_STOP:
            self.runtime._spawn(self.lifecycle.handle_stop(msg))
        elif event == LIFECYCLE_DESTROY:
            self.runtime._spawn(self.lifecycle.handle_destroy(msg))
        elif event == LIFECYCLE_CREATED and msg.get('name'):
            self.runtime._spawn(self.lifecycle.handle_created(msg))
        else:
            self.runtime._spawn(self.on_inbound(msg))

    def _cache_module_tree(self, msg: Dict[str, Any]) -> None:
        """kernel 推来的模块树拓扑 → 重建本地缓存(ctx.conflict 用)."""
        try:
            self.runtime.module_tree = ModuleTree.from_dict(msg.get('tree') or {})
        except ValueError as exc:
            self._logger.warning('module.tree cache failed: %s', exc)

    def _on_lifecycle_snapshot(self, msg: Dict[str, Any]) -> None:
        """lifecycle.snapshot 回执:快照 items 转成 lifecycle 事件帧格式 resolve 请求
        future(watch_lifecycle 灌进请求者的观察队列)."""
        fut = self.runtime.pop_lifecycle_future(str(msg.get('req_id', '')))
        if fut is None or fut.done():
            return
        seq = msg.get('seq')
        frames = []
        for it in (msg.get('routines') or []):
            state = str(it.get('state', ''))
            ev_type = 'started' if state in ('running', 'starting') else 'created'
            frames.append({
                'event': LIFECYCLE_EVENT, 'type': ev_type,
                'id': str(it.get('id', '')),
                'parent_id': str(it.get('parent_id', '0')),
                'name': it.get('name', ''), 'seq': seq,
            })
        fut.set_result(frames)

    # --- reader 路由(kernel→hub 回向事件) ---

    async def on_inbound(self, msg: Dict[str, Any]) -> None:
        """kernel→hub 回向事件.按 event 查 ``_inbound_handlers`` 分发表派发,
        未知 event -> table-miss -> no-op."""
        handler = self._inbound_handlers.get(msg.get('event', ''))
        if handler is not None:
            await handler(msg)

    async def _on_lifecycle_started(self, msg: Dict[str, Any]) -> None:
        cid = msg.get('id', '')
        handle = self.runtime.get_handle(cid)
        if handle is not None:
            handle.notify_started()
            handle._resolve_ack(None)

    async def _on_lifecycle_stopped(self, msg: Dict[str, Any]) -> None:
        cid = msg.get('id', '')
        handle = self.runtime.get_handle(cid)
        if handle is not None:
            reason = msg.get('reason', '')
            # error 优先用 stopped 带的;没带则按 reason 退回通用文本
            error = msg.get('error')
            if not error and reason == ControlDoneReason.ERROR.value:
                error = f'routine stopped with reason={reason}'
            handle.notify_done(result=msg.get('result'), error=error, reason=reason)
            # 先 resolve handle._ack(stop/unsubmit 成功回执)再 pop_handle
            handle._resolve_ack(None)
            self.runtime.pop_handle(cid)
        # instance 清理由各自路径负责:created 态走 handle_destroy(_cleanup),
        # running 态走 runner 退出(_cleanup).这里只管 handle.

    async def _on_routine_rejected(self, msg: Dict[str, Any]) -> None:
        cid = msg.get('child_id', '')
        err = str(msg.get('error', 'rejected'))
        op = str(msg.get('op', ''))
        handle = self.runtime.get_handle(cid)
        if op in ('start', 'force_start'):
            # start/try_start/force_start 失败:resolve str error(调方包成
            # StartError).start/force_start(非 try)失败:kernel 已清 node,本侧
            # 清 created instance(handle 失效不可重试).try 失败:保留可重试.
            if handle is not None:
                handle._resolve_ack(err)
            is_try = bool(msg.get('try'))
            if not is_try and cid:
                self.lifecycle._cleanup(cid)
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
                child_id = str(msg.get('child_id', ''))
                modules = msg.get('modules')
                if not isinstance(modules, list):
                    modules = None
                fut.set_result((child_id, modules))

    def _resolve_ack_future(self, fut, msg, exc_type, err_default) -> None:
        """acquired/released 类共享模板:ok=false -> 抛 exc_type;ok=true -> resolve."""
        if fut is not None and not fut.done():
            if msg.get('ok', True) is False:
                fut.set_exception(exc_type(str(msg.get('error', err_default))))
            else:
                fut.set_result(None)

    async def _on_routine_acquired(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_acquire_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, AcquireError, 'acquire failed')

    async def _on_routine_released(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_acquire_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, ReleaseError, 'release failed')

    async def _on_routine_module_loaded(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_load_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, LoadModuleError, 'load_module failed')

    async def _on_routine_module_unloaded(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_unload_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, UnloadModuleError, 'unload_module failed')

    async def _on_catalog_registered(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_register_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, RegisterError, 'register rejected by kernel')

    async def _on_catalog_reloaded(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_reload_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, ReloadError, 'reload rejected by kernel')

    async def _on_catalog_deregistered(self, msg: Dict[str, Any]) -> None:
        fut = self.runtime.pop_deregister_future(msg.get('req_id', ''))
        self._resolve_ack_future(fut, msg, DeregisterError,
                                 'deregister rejected by kernel')

    async def _on_catalog_pushed(self, msg: Dict[str, Any]) -> None:
        error = msg.get('error', '')
        if error:
            # kernel 拒绝连接(hub_id 重复 / 缺失): 重连无意义, 停止 transport.
            # stop() 会 cancel _run task, 不能在本回调(_run 栈内)直接 await,
            # 独立 task 执行.
            self._logger.error('catalog.push rejected: %s', error)
            asyncio.create_task(self.transport.stop())
            return
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
        """catalog.deregister.cmd(持有者收到,kernel 发来):本地 dereg → 存结果 →
        发 cmd.ack 给 kernel.kernel 收到 ack 后删路由 + 停实例 + 回执请求者."""
        req_id = msg.get('req_id', '')
        name = msg.get('name', '')
        if not name:
            await self.send_catalog_deregister_cmd_ack(
                req_id=req_id, ok=False, error='name is required',
            )
            return
        removed = self.runtime.routines.deregister(name)
        if req_id:
            self.runtime.set_deregister_result(req_id, removed)
        await self.send_catalog_deregister_cmd_ack(req_id=req_id, ok=True)
        self._logger.info('catalog.deregister.cmd: local dereg %s (removed=%s)',
                          name, removed.__name__ if removed else None)

    # --- message.* / pubsub / yield 入站 ---

    def _resolve_source(self, msg) -> RoutineSource:
        """从 delivered 消息的 source 字段构造 RoutineSource(补 name:source 在
        本 hub 则能查到 name)."""
        src = msg.get('source') or {}
        source_id = str(src.get('id', ''))
        src_inst = self.runtime.get_created(source_id)
        if src_inst is not None:
            return RoutineSource(id=source_id, name=src_inst.name)
        return RoutineSource(id=source_id)

    async def _on_message_delivered(self, msg):
        """message.delivered{target_id, data, source}:纯定向消息 → 实例
        mailbox 串行投递. created 后即可收; 无 mailbox(未 created/已销毁)
        丢弃."""
        target_id = msg.get('target_id', '')
        if not self.runtime.get_created(target_id):
            return
        source = self._resolve_source(msg)
        self.runtime.deliver_mail(target_id, source, msg.get('data'))

    async def _on_message_req_delivered(self, msg):
        """message.req_delivered:req 到达 provider → 立即 spawn 处理.独立
        事务(req_id 配对回执), 相互并行 ---- HTTP 语义, 慢 handler 不堵
        同实例后续 req."""
        target_id = msg.get('target_id', '')
        inst = self.runtime.get_created(target_id)
        if inst is None:
            return
        source = self._resolve_source(msg)
        self.runtime._spawn(inst._serve_request(msg.get('data') or {}, source))

    async def _on_message_req_reply_delivered(self, msg):
        """message.req_reply_delivered:req 回执到达 caller,resolve req future."""
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
        """message.stream_open_delivered:开流到达 provider,spawn provider task."""
        target_id = msg.get('target_id', '')
        inst = self.runtime.get_created(target_id)
        if inst is None:
            return
        source = self._resolve_source(msg)
        await inst._serve_stream(msg.get('data') or {}, source)

    async def _on_message_stream_data_delivered(self, msg):
        """message.stream_data_delivered:chunk / eof 到达 caller,喂 StreamReader."""
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
        """message.stream_cancel_delivered:caller 取消 stream,取消 provider gen."""
        data = msg.get('data') or {}
        stream_id = data.get(ENVELOPE_STREAM_ID, '')
        task = self.runtime.get_provider_stream(stream_id)
        if task is not None and not task.done():
            task.cancel()
        self.runtime.pop_provider_stream(stream_id)

    async def _on_pubsub_delivered(self, msg: Dict[str, Any]) -> None:
        """kernel fanout 回来的 pubsub.delivered(已按 hub 分组, 一条含
        subscriber_ids 列表).逐个 subscriber 按 (namespace, topic) 找本地
        handler 调 ``handler(source, data)``."""
        topic = msg.get('topic', '')
        namespace = msg.get('namespace', '') or ''
        data = msg.get('data')
        src = msg.get('source') or {}
        source = RoutineSource(id=str(src.get('id', '')))
        src_inst = self.runtime.get_created(source.id)
        if src_inst is not None:
            source = RoutineSource(id=source.id, name=src_inst.name)
        for rid in msg.get('subscriber_ids') or ():
            handler = self.runtime.get_subscriber_handler(
                rid, namespace, topic)
            if handler is None:
                continue
            # pubsub 是离散通知: 每帧每订阅者立即起 task 消费, 不进 mailbox
            # 串行 ---- 慢 handler 不堵 inbound 循环, 也不积压后续帧
            self.runtime._spawn(handler(source, data))

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
