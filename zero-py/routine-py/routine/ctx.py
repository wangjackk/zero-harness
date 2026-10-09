"""RunContext ---- 每次 ``run()`` invocation 的运行上下文.

身份字段 id / name + lifecycle 出口(ack_start / request_stop)+ submit 能力
(routine 调 routine,经 kernel 回环).

submit 流程:发 routine.submit{req_id, name, kwargs, parent_id=self.id} 给
kernel → kernel 建子命令(Create,不 Start)→ 回 routine.submitted{req_id,
child_id, modules} → 拿到 child_id 建 RoutineHandle 注册到 runtime handle 表.

handle.start()/stop() 发 routine.start/stop 给 kernel → kernel 走编排
→ lifecycle.start/stop 回 hub 跑子 routine → 子发 lifecycle.started/stopped
给 kernel → kernel 中转回 hub → on_inbound 按 child_id 路由到 handle 的
notify_started/notify_done(统一经 kernel,不本地抄近路).
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .errors import ReqError, ReqTimeout

from .protocol import (
    ENVELOPE_DATA, ENVELOPE_EVENT, ENVELOPE_REPLY_TO, ENVELOPE_REQ_ID,
    ENVELOPE_STREAM_ID, MESSAGE_REQ, MESSAGE_REQ_REPLY,
    MESSAGE_SEND, MESSAGE_STREAM_OPEN,
)

if TYPE_CHECKING:
    from .handle import RoutineHandle
    from .routine import Routine
    from .runtime import ServerRuntime
    from .transport import Transport


class Phase(str, Enum):
    """invocation 生命周期相位:CREATED(从未 start)→ STARTED(运行中)→
    STOPPED(start 后被 stop/驱逐).守卫据此区分"还没 start"(报错)与
    "start 后被停"(no-op)."""

    CREATED = 'created'
    STARTED = 'started'
    STOPPED = 'stopped'


class RunContext:
    """每次 ``run()`` 重新构造的运行上下文;lifecycle 在 created 之后绑给 routine."""

    ACK_TIMEOUT = 30.0  # submit/acquire/release 等 kernel ack 默认超时

    def __init__(self, *, id: str, name: str,
                 io: 'RoutineHub', routine: 'Routine',
                 runtime: 'ServerRuntime', transport: 'Transport'):
        self.id = id
        self.name = name
        self._io = io
        self._routine = routine
        self._runtime = runtime
        self._transport = transport

    # --- Hub 访问(业务侧入口) ---

    @property
    def hub(self) -> 'RoutineHub':
        """返回当前 RoutineHub 实例(运行时 register/reload/deregister 用)."""
        return self._io

    # --- Lifecycle ack helpers ---

    async def ack_start(self) -> None:
        """发 lifecycle.started 通知调度器进入 Started."""
        await self._io.send_lifecycle_started(id=self.id)

    async def request_stop(self) -> None:
        """请求 hub 发起一次正规 stop 流程."""
        await self._io.request_stop(id=self.id)

    def _require_parent_started(self, action: str) -> None:
        """父 routine 必须 started(ack_start 后)--否则抛 RuntimeError.

        handle 层 start/stop 子 routine 前的守卫.submit/unsubmit 不查(created 即可).
        """
        if self._routine._phase is not Phase.STARTED:
            raise RuntimeError(
                f'{self.name}: must start() before {action} '
                f'(parent routine not started)'
            )

    # --- routine 调 routine(submit) ---

    async def submit(self, name: str,
                     kwargs: Optional[Dict[str, Any]] = None) -> 'RoutineHandle':
        """提交一个子 routine,经 kernel 回环:建命令(created),created 回报后返回
        handle.

        handle.modules = 子 routine on_created() 返回的占用 modules(经 created
        回报 → kernel → submitted 回执带回).编排器据此算冲突
        (``ctx.conflict(h1.modules, h2.modules)``).
        """
        from .handle import RoutineHandle

        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        self._runtime.register_submit_future(req_id, fut)

        try:
            await self._io.send_routine_submit(
                req_id=req_id, parent_id=self.id, name=name,
                kwargs=kwargs or {},
            )
            # submitted 回执:(child_id string, modules list|None)
            child_id, modules = await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            self._runtime.pop_submit_future(req_id)

        handle = RoutineHandle(child_id, name, ctx=self, modules=modules)
        self._io.register_handle(child_id, handle)
        # 拿到 handle 时子一定已 created(kernel 等 created 回报才发 submitted):
        # created 路由表 + ctx 都已就绪----可直接 send / req / publish.
        return handle

    async def call(self, name: str,
                   kwargs: Optional[Dict[str, Any]] = None,
                   priority: int = 0) -> Any:
        """同步拿子 routine 结果:submit → start → wait 一步到位.

        priority: start 优先级(PRI_NORMAL/USER/SAFETY).模块冲突时
        incoming > 全部 holder 才驱逐他们后 start(严格大于, 平级不抢);
        否则抛 StartError.0 等价旧 call(绝不驱逐).

        失败语义:
        - start 失败(模块冲突/父未 started):抛 StartError
        - 子 routine 异常停止:抛 RuntimeError(子 wait 的 error)
        - 子正常 return:返回 result
        """
        if self._routine._phase is not Phase.STARTED:
            raise RuntimeError(
                f'{self.name}: must start() before call() '
                f'(parent routine not started, cannot call child)'
            )
        kw = dict(kwargs or {})
        handle = await self.submit(name, kw)
        await handle.start(priority)
        return await handle

    # --- 运行时占领/释放模块(跟类静态声明同一底层 TryAcquire/Release) ---

    async def acquire(self, modules: List[str]) -> None:
        """运行时占领模块.冲突抛 AcquireError.只 start 期间可用."""
        self._require_started('acquire')
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_acquire(
                req_id=req_id, id=self.id, modules=modules,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_acquire_future(req_id)

    async def release(self, modules: List[str]) -> None:
        """运行时释放指定模块(只这些,不全量).stop 时 kernel 自动全清.

        已 stop/被驱逐后再调为 no-op(kernel 已全量回收),打 warning 不抛错;
        从未 start 过才抛 RuntimeError.
        """
        if self._routine._phase is not Phase.STARTED:
            if self._routine._phase is Phase.STOPPED:
                _ctx_log.warning(
                    '%s: release(%s) after stopped ---- kernel already reclaimed all modules, no-op',
                    self.name, modules,
                )
                return
            raise RuntimeError(
                f'{self.name}: release() before start() (routine never started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_release(
                req_id=req_id, id=self.id, modules=modules,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_acquire_future(req_id)

    async def force_acquire(self, modules: List[str]) -> None:
        """强制占领 modules:驱逐 cone 内第三方 holder(cascade stop,reason='force')
        后自己占住(带驱逐的 acquire,原子无竞态).永不驱逐祖先."""
        self._require_started('force_acquire')
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_force_acquire(
                req_id=req_id, id=self.id, modules=modules,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_acquire_future(req_id)

    async def force_release(self, modules: List[str]) -> None:
        """强制释放 modules:驱逐 cone 内第三方 holder 后空出 modules,**不自己占**.

        跟 force_acquire 的区别:force_acquire 驱逐后自己占住;force_release 只
        清场,驱逐与后续 acquire 间有竞态窗口(调用方自担).
        """
        self._require_started('force_release')
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_force_release(
                req_id=req_id, id=self.id, modules=modules,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_acquire_future(req_id)

    async def load_module(self, parent: str, child: str, name: str = '') -> None:
        """往 parent 模块下加载子模块 child(全局树动态增拓扑).只挂树不占用."""
        self._require_started('load_module')
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_load_future(req_id, fut)
        try:
            await self._io.send_routine_load_module(
                req_id=req_id, parent_id=parent, child_id=child, name=name,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_load_future(req_id)

    async def unload_module(self, child: str) -> None:
        """卸载子模块 child(全局树动态删拓扑).child 有子/被占/不存在抛错."""
        self._require_started('unload_module')
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_unload_future(req_id, fut)
        try:
            await self._io.send_routine_unload_module(
                req_id=req_id, child_id=child,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_unload_future(req_id)

    def _require_started(self, action: str) -> None:
        if self._routine._phase is not Phase.STARTED:
            raise RuntimeError(
                f'{self.name}: must start() before {action} (routine not started)'
            )

    # --- 模块拓扑:静态预测式 conflict(业务侧编排策略用) ---

    def conflict(self, mods_a: List[str], mods_b: List[str]) -> bool:
        """两组 modules 是否冲突(cone 交集非空).纯本地计算,零 round-trip.

        树未缓存时抛 RuntimeError(不静默返回 False----那会让冲突对误并行).
        """
        tree = self._runtime.module_tree
        if tree is None:
            raise RuntimeError(
                f'{self.name}: module tree not cached yet '
                f'(kernel has not pushed module.tree ---- should not happen, '
                f'pushed during catalog window)'
            )
        return tree.conflict(mods_a, mods_b)

    # --- p2p 通信(req / stream_req 骑这条隧道,kernel dumb forward) ---

    def _spawn(self, coro) -> asyncio.Task:
        """起一个后台 task(框架内部:@stream provider gen).委托 runtime task 池."""
        return self._io._spawn(coro)

    async def _send_message(self, target_id: str, send_event: str,
                            data: Optional[Dict[str, Any]] = None) -> None:
        """(内部原语)发 message.* 给 target_id.envelope 全在 data 里,kernel 不解析."""
        await self._io.send_message(
            target_ids=[target_id], send_event=send_event,
            data=data or {}, source_id=self.id,
        )

    async def req(self, target: str, event: str,
                  data: Optional[Dict[str, Any]] = None,
                  timeout: float = 30.0) -> Any:
        """对 target routine 发 request(经 kernel p2p 转发),等回执拿 result.

        target 是对端 routine 的 id(handle.id / get_running_routines 查到的 id).
        对端用 ``@request(event)`` 注册的 handler 处理,返回值即 result.
        handler 抛异常 → raise ReqError;超时 → raise ReqTimeout.
        """
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_req_future(req_id, fut)
        envelope = {
            ENVELOPE_REQ_ID: req_id,
            ENVELOPE_REPLY_TO: self.id,
            ENVELOPE_EVENT: event,
            ENVELOPE_DATA: data or {},
        }
        try:
            await self._send_message(target, MESSAGE_REQ, envelope)
            result = await asyncio.wait_for(fut, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            raise ReqTimeout(
                f'req {event!r} to {target} timeout after {timeout}s',
            )
        finally:
            runtime.pop_req_future(req_id)

    async def get_running_routines(self) -> list:
        """查 kernel 当前所有 running routine 实例 ``[{name, id}]``."""
        return await self._transport.get_running_routines()

    async def get_module_tree(self):
        """主动从 kernel 拉当前 module.tree 并刷新本地缓存."""
        return await self._transport.get_module_tree()

    async def get_routines(self) -> list:
        """查 kernel 全量路由表(catalog 注册的全部 routine)."""
        return await self._transport.get_routines()

    async def stream_req(self, target: str, event: str,
                         data: Optional[Dict[str, Any]] = None,
                         timeout: float = 30.0):
        """对 target routine 发 stream request,返回 ``StreamCtx``(async with → async for).

        用法::

            async with await self.stream_req(rid, 'count', {'n': 5}) as s:
                async for chunk in s: ...
        """
        from ._stream import StreamCtx, StreamReader

        stream_id = _new_req_id()
        reader = StreamReader(stream_id, self, target)
        runtime = self._runtime
        runtime.register_stream_reader(stream_id, reader)
        envelope = {
            ENVELOPE_STREAM_ID: stream_id,
            ENVELOPE_REPLY_TO: self.id,
            ENVELOPE_EVENT: event,
            ENVELOPE_DATA: data or {},
        }
        await self._send_message(target, MESSAGE_STREAM_OPEN, envelope)
        return StreamCtx(reader, timeout)

    # --- lifecycle 观察流 ---

    async def watch_lifecycle(self) -> 'asyncio.Queue[Dict[str, Any]]':
        """开启 kernel 生命周期事件观察(lifecycle 流).

        返回事件 queue:先灌入当前 routine 树快照帧,再收增量帧
        (``{type: created/started/stopped/destroyed, id, parent_id, name, seq}``).
        消费端按 id 幂等 upsert/remove(id 不复用,乱序到达最终状态一致).
        实例停止时框架自动注销;也可显式 ``unwatch_lifecycle()``.
        多 routine 同时观察共存,互不影响.

        用法::

            q = await ctx.watch_lifecycle()
            while True:
                frame = await q.get()
        """
        # 先注册 watcher 再发 enable:enable 之后 kernel 即开始推帧,
        # 晚注册会丢帧(丢的帧由快照按 id 幂等补齐).
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._runtime.register_lifecycle_watcher(self.id, q)

        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        self._runtime.register_lifecycle_future(req_id, fut)
        try:
            await self._io.send_lifecycle_watch(req_id=req_id)
            frames = await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        except asyncio.TimeoutError:
            self._runtime.pop_lifecycle_watcher(self.id)
            raise RuntimeError('watch_lifecycle: lifecycle.snapshot timeout')
        except Exception:
            # 发送失败等:enable 未生效,注销刚注册的 watcher
            self._runtime.pop_lifecycle_watcher(self.id)
            raise
        finally:
            self._runtime.pop_lifecycle_future(req_id)
        for f in frames:
            try:
                q.put_nowait(f)
            except asyncio.QueueFull:
                self._runtime.logger.warning(
                    'lifecycle watcher %s queue full, snapshot truncated',
                    self.id)
        return q

    async def unwatch_lifecycle(self) -> None:
        """注销观察(最后一个观察者注销时自动关流)."""
        if self._runtime.pop_lifecycle_watcher(self.id) is not None \
                and self._runtime.lifecycle_watcher_count() == 0:
            await self._io.send_lifecycle_unwatch()

    # --- pubsub(经 kernel 订阅表 fanout) ---

    async def publish(self, topic: str, data: Any = None, *,
                      namespace: str = '') -> None:
        """发一条 pubsub 消息到 ``(namespace, topic)``.kernel fanout 给所有订阅者."""
        await self._io.send_pubsub_publish(
            topic=topic, data=data, source_id=self.id, namespace=namespace,
        )

    async def subscribe_topic(self, topic: str, *,
                              namespace: str = '') -> None:
        """(内部)发 pubsub.subscribe 注册到 kernel 订阅表."""
        await self._io.send_pubsub_subscribe(
            id=self.id, topic=topic, namespace=namespace,
        )

    async def subscribe(self, topic: str, handler, *,
                        namespace: str = '') -> None:
        """订阅 ``(namespace, topic)``:注册 handler 到本地表 + 发 pubsub.subscribe."""
        runtime = self._runtime
        runtime.register_subscriber(self.id, namespace, topic, handler)
        await self.subscribe_topic(topic, namespace=namespace)

    async def unsubscribe(self, topic: str, *,
                          namespace: str = '') -> None:
        """退订 ``(namespace, topic)``:本地表删 handler + 发 pubsub.unsubscribe."""
        runtime = self._runtime
        topics = runtime._subscribers.get(self.id)
        if topics is not None:
            topics.pop((namespace, topic), None)
            if not topics:
                runtime._subscribers.pop(self.id, None)
        await self._io.send_pubsub_unsubscribe(
            id=self.id, topic=topic, namespace=namespace,
        )

    def namespace(self, ns: str) -> 'Namespace':
        """拿到一个 namespaced 助手."""
        return Namespace(self, ns)

    # --- yield(child 发,kernel dumb forward 给 parent) ---

    async def _send_yield(self, data: Any = None, *,
                          is_final: bool = False,
                          error: Optional[str] = None) -> None:
        """发 routine.yield 给 kernel(child yield 一项 / 收尾 / 异常)."""
        await self._io.send_yield(
            id=self.id, data=data, is_final=is_final, error=error,
        )

    # --- message.* 定向消息(push,调 target 的 on_message) ---

    async def send(self, target: str, data: Any = None) -> None:
        """给 target routine 发一条定向消息(message.send).target 需 created.

        派发是 spawn 并发----对端 on_message 可能并发 fire,乱序到达,业务侧
        自带 id reorder.
        """
        await self._send_message(target, MESSAGE_SEND, data or {})


_req_counter = 0
_ctx_log = logging.getLogger('routine.ctx')


def _new_req_id() -> str:
    global _req_counter
    _req_counter += 1
    return f'r{_req_counter}'


class Namespace:
    """namespaced pub/sub 助手."""

    def __init__(self, ctx: 'RunContext', ns: str):
        self._ctx = ctx
        self._ns = ns

    async def publish(self, topic: str, data: Any = None) -> None:
        await self._ctx.publish(topic, data, namespace=self._ns)

    async def subscribe(self, topic: str, handler) -> None:
        await self._ctx.subscribe(topic, handler, namespace=self._ns)

    async def unsubscribe(self, topic: str) -> None:
        await self._ctx.unsubscribe(topic, namespace=self._ns)
