"""RunContext ---- 每次 ``run()`` invocation 的运行上下文(精简版).

身份字段 id / name / peer_id + lifecycle 出口(ack_start / request_stop)+
submit 能力(routine 调 routine,经 kernel 回环).

submit 流程:发 routine.submit{req_id, name, kwargs, modules, parent_id=self.id}
给 kernel → kernel 建子命令(Create,不 Start)→ 回 routine.submitted{req_id,
child_id} → 拿到 child_id 建 RoutineHandle 注册到 server handle 表.

handle.start()/stop() 发 routine.start/stop 给 kernel → kernel 走 Manager.
StartChild/StopChild → lifecycle.start/stop 回 server 跑子 routine → 子发
lifecycle.started/stopped 给 kernel → kernel 中转回 server → on_inbound 按 child_id
路由到 handle 的 notify_started/notify_done(统一经 kernel,不本地抄近路).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
    from .handle import RoutineHandle
    from .module_tree import ModuleTree
    from .routine import Routine, Routines
    from .runtime import ServerRuntime
    from .transport import Transport

from .errors import LoadModuleError, StartError, UnloadModuleError

from .protocol import (
    ENVELOPE_DATA, ENVELOPE_EVENT, ENVELOPE_REPLY_TO, ENVELOPE_REQ_ID,
    ENVELOPE_STREAM_ID, MESSAGE_REQ, MESSAGE_REQ_REPLY, MESSAGE_SEND,
    MESSAGE_STREAM_CANCEL, MESSAGE_STREAM_DATA, MESSAGE_STREAM_OPEN,
)


class RoutineIO(Protocol):
    """service 层注入的 wire 出口(RoutineHub 实现)."""

    async def send_lifecycle_started(self, *, id: str,
                                     peer_id: Optional[str] = None) -> None: ...

    async def send_lifecycle_created(self, *, id: str,
                                   modules: Optional[List[str]] = None,
                                   peer_id: Optional[str] = None) -> None: ...

    async def send_lifecycle_stopped(self, *, id: str,
                                     reason: Any = None,
                                     result: Any = None,
                                     error: Optional[str] = None,
                                     peer_id: Optional[str] = None) -> None: ...

    async def request_stop(self, *, id: str,
                           peer_id: Optional[str] = None) -> None: ...

    # --- routine 调 routine 反向事件(py→kernel,走同一条 Stream) ---

    async def send_routine_submit(self, *, req_id: str, parent_id: str,
                                  name: str, kwargs: Dict[str, Any],
                                  peer_id: Optional[str] = None) -> None: ...

    async def send_routine_start(self, *, child_id: str,
                                try_start: bool = False,
                                peer_id: Optional[str] = None) -> None: ...

    async def send_routine_stop(self, *, child_id: str,
                                peer_id: Optional[str] = None) -> None: ...

    async def send_routine_unsubmit(self, *, child_id: str,
                                    peer_id: Optional[str] = None) -> None: ...

    async def send_routine_force_release(self, *, req_id: str, id: str,
                                         modules: list,
                                         peer_id: Optional[str] = None) -> None: ...

    async def send_routine_force_acquire(self, *, req_id: str, id: str,
                                         modules: list,
                                         peer_id: Optional[str] = None) -> None: ...

    async def send_routine_force_start(self, *, child_id: str,
                                       peer_id: Optional[str] = None) -> None: ...

    # --- 运行时占领/释放模块(py→kernel,走同一条 Stream;跟静态声明同一底层 TryAcquire) ---

    async def send_routine_acquire(self, *, req_id: str, id: str,
                                   modules: list,
                                   peer_id: Optional[str] = None) -> None: ...

    async def send_routine_release(self, *, req_id: str, id: str,
                                   modules: list,
                                   peer_id: Optional[str] = None) -> None: ...

    async def send_routine_load_module(self, *, req_id: str, parent_id: str,
                                       child_id: str, name: str = '',
                                       peer_id: Optional[str] = None) -> None: ...

    async def send_routine_unload_module(self, *, req_id: str, child_id: str,
                                         peer_id: Optional[str] = None) -> None: ...

    # --- message.* 通信(py→kernel→py,kernel dumb forward by target_id) ---
    # 消息类全归 message 前缀,各子类型独立 wire,字段自洽.
    # send_event 是 message.send / message.req / message.req_reply / message.stream_open /
    # message.stream_data / message.stream_cancel 之一.envelope 全在 data 里,kernel 不解析.

    async def send_message(self, *, target_ids: list, send_event: str,
                           data: Any, source_id: str,
                           peer_id: Optional[str] = None) -> None: ...

    # --- pubsub 通信(py→kernel→py,kernel 维护订阅表 fanout) ---

    async def send_pubsub_subscribe(self, *, id: str, topic: str,
                                    namespace: str = '',
                                    peer_id: Optional[str] = None) -> None: ...

    async def send_pubsub_unsubscribe(self, *, id: str, topic: str,
                                      namespace: str = '',
                                      peer_id: Optional[str] = None) -> None: ...

    async def send_pubsub_publish(self, *, topic: str, data: Any,
                                  source_id: str, namespace: str = '',
                                  peer_id: Optional[str] = None) -> None: ...

    # --- yield(child→kernel→parent,kernel dumb forward) ---

    async def send_yield(self, *, id: str, data: Any = None,
                         is_final: bool = False, error: Optional[str] = None,
                         peer_id: Optional[str] = None) -> None: ...

    # --- handle 注册(server 侧 handle 表,reader 按 child_id 路由 lifecycle) ---

    def register_handle(self, child_id: str, handle: 'RoutineHandle') -> None: ...

    # --- task 池(框架内部用: @stream provider gen / lifecycle 后台协程.
    # 业务侧不应调用, 用 asyncio.create_task 自管 task. 下划线前缀 = 内部 API.)

    def _spawn(self, coro) -> asyncio.Task: ...


@runtime_checkable
class RoutineHubLike(RoutineIO, Protocol):
    """RoutineHub 的结构化契约----业务侧经 ``ctx.hub`` 拿到此对象.

    ``RoutineIO`` 是 ctx 的 wire 出口(发送方);``RoutineHubLike`` 在其上扩展
    运行时 routine 管理(register/reload/deregister)+ ``runtime`` 注册表访问.
    业务侧工具(LLM tool / HTTP 前门 / bridge)需要查注册表或动态注册 routine 时,
    经 ``ctx.hub`` 拿到此 Protocol 类型对象,避免散落的 ``ctx._io`` 鸭式判断.

    实现端(RoutineHub)满足本 Protocol 即可,无需显式 inherit.``runtime_checkable``
    让 ``isinstance`` 可用----``RunContext.hub`` 据此判断 ``_io`` 是否真为 hub
    (mock / 极端构造场景下 _io 可能只是 RoutineIO 而非 hub,此时返回 None).
    """

    @property
    def runtime(self) -> 'ServerRuntime': ...

    async def register_routine(self, *routines) -> None: ...

    async def reload_routine(self, *routines) -> None: ...

    async def deregister_routine(self, name: str) -> Optional['Type[Routine]']: ...


class RunContext:
    """每次 ``run()`` 重新构造的运行上下文;service 在 lifecycle.start 之后绑给 routine."""

    ACK_TIMEOUT = 30.0  # submit/acquire/release 等 kernel ack 默认超时
    # (对标 req 的 30s):半开 / 多 kernel 路由下 kernel 不回 ack 时不永久 hang.
    # 正常流程 < 1s 不受影响;超时抛 asyncio.TimeoutError(future 已 finally pop,
    # kernel 迟到 ack 因 server 的 done() 守卫被忽略).

    def __init__(self, *, id: str, name: str, peer_id: str,
                 io: 'RoutineIO', routine: 'Routine',
                 runtime: 'ServerRuntime', transport: 'Transport'):
        self.id = id
        self.name = name
        self.peer_id = peer_id
        self._io = io
        self._routine = routine
        self._runtime = runtime
        self._transport = transport

    # --- Hub 访问(业务侧入口) ---

    @property
    def hub(self) -> 'Optional[RoutineHubLike]':
        """返回当前 RoutineHub 实例(若 _io 实现了 hub 接口).

        业务侧(LLM tool / HTTP 前门 / bridge)需要查注册表或动态 register/reload/
        deregister routine 时用此 property,替代散落的 ``self.ctx._io`` + ``hasattr``
        鸭式判断.``_io`` 不是 hub(mock / 极端构造场景)时返回 None,调用方据此降级.

        框架内部(server.py / lifecycle.py)仍走 ``self._io`` 拿 RoutineIO 视图,
        不需要 hub 的运行时管理能力----两者职责分离,``_io`` 是 wire 出口,
        ``hub`` 是 routine 生命周期管理入口.
        """
        io = self._io
        if isinstance(io, RoutineHubLike):
            return io
        return None

    # --- Lifecycle ack helpers ---

    async def ack_start(self) -> None:
        """发 lifecycle.started 通知调度器进入 Started."""
        await self._io.send_lifecycle_started(id=self.id, peer_id=self.peer_id)

    async def request_stop(self) -> None:
        """请求 runtime 发起一次正规 stop 流程."""
        await self._io.request_stop(id=self.id, peer_id=self.peer_id)

    def _require_parent_started(self, action: str) -> None:
        """父 routine 必须 started(ack_start 后)--否则抛 RuntimeError.

        handle 层 start/stop 子 routine 前的守卫(对标原 ``start_child``/``stop_child``
        开头的 ``self._routine._started`` 检查).submit/unsubmit 不查(created 即可).
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before {action} '
                f'(父 routine 未 started)'
            )

    # --- routine 调 routine(submit) ---

    async def submit(self, name: str,
                     kwargs: Optional[Dict[str, Any]] = None) -> 'RoutineHandle':
        """提交一个子 routine,经 kernel 回环:建命令(created),created 时返回 handle.

        handle.id = kernel 分配的 command id.拿到 handle 后调 ``await handle.start()``
        让 kernel start 子命令,``await handle.wait()`` 等子完成拿 result.

        handle.modules = 子 routine on_created() 返回的占用 modules(经 created 回报 →
        kernel → submitted 回执带回).编排器据此算冲突
        (``ctx.conflict(h1.modules, h2.modules)``).

        modules 不在 submit 时传给 kernel----对标老版 push_quick 只发 name+kwargs,
        kernel 不查 catalog 也不 RPC 现算,由 server 在 on_created() 本地算好经 created
        回报回带(单一真理源,无 kernel→server RPC).
        """
        from .handle import RoutineHandle

        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        self._runtime.register_submit_future(req_id, fut)

        try:
            await self._io.send_routine_submit(
                req_id=req_id, parent_id=self.id, name=name,
                kwargs=kwargs or {}, peer_id=self.peer_id,
            )
            # submitted 回执:(child_id string, modules list|None)
            child_id, modules = await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            self._runtime.pop_submit_future(req_id)

        # wire 契约:child_id 是 string(Go 侧发送边界 strconv.Itoa).
        handle = RoutineHandle(child_id, name, ctx=self, modules=modules)
        self._io.register_handle(child_id, handle)
        # 拿到 handle 时子一定已 created(kernel 等 created 回报才发 submitted):
        # created 路由表 + ctx 都已就绪----可直接 send / req / publish(子不必 start
        # 就能收 message.* 定向消息).只有子要**收 pubsub** 需 start 后
        # (auto_subscribe 在 start 发订阅给 kernel).
        return handle

    async def call(self, name: str,
                   kwargs: Optional[Dict[str, Any]] = None) -> Any:
        """同步拿子 routine 结果:submit → start → wait 一步到位.

        等价于::

            handle = await self.submit(name, kwargs)
            await handle.start()
            return await handle   # 等价 await handle.wait(),对标老版 push_and_wait

        失败语义:
        - start 失败(模块冲突/父未 started):抛 StartError
        - 子 routine 异常停止:抛 RuntimeError(子 wait 的 error)
        - 子正常 return:返回 result

        用本方法意味着不需要保留 handle(不中途 stop / 不迭代 body).
        需要更细控制时分别调 submit/start/wait.
        """
        if not self._routine._started:
            # created 阶段(父未 ack_start)调 run = 误用----run 含 start 子,
            # 父都没 started 不能 start 子.提前抛,避免 submit 建了子 instance/
            # handle 后 start 抛异常导致泄漏(created 阶段无级联清理).
            raise RuntimeError(
                f'{self.name}: must start() before call() '
                f'(父 routine 未 started,不能 call 子)'
            )
        handle = await self.submit(name, kwargs)
        await handle.start()  # 失败 raise StartError(与 ReqError 一致)
        return await handle

    async def force_release(self, modules: List[str]) -> None:
        """强制释放 modules:驱逐 cone 内第三方 holder(cascade stop,带 reason='force'
        透传给被驱逐者)后空出 modules,**不自己占**.要占住另调 ``acquire`` / ``force_acquire``.

        跟 force_acquire 的区别:force_acquire 驱逐后自己占住(原子无竞态);
        force_release 只清场,驱逐与后续 acquire 间有竞态窗口(调用方自担).

        永不驱逐祖先(打断父亲自己也死).单轮驱逐不重试.需本 routine Started.
        ack 走 routine.released(同 release 的 future 表).失败(罕见--rid 未 started)抛
        ``ReleaseError``.
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before force_release '
                f'(本 routine 未 started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_force_release(
                req_id=req_id, id=self.id, modules=modules, peer_id=self.peer_id,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)  # 等 routine.released
        finally:
            runtime.pop_acquire_future(req_id)

    async def force_acquire(self, modules: List[str]) -> None:
        """强制占领 modules:驱逐 cone 内第三方 holder(cascade stop,带 reason='force'
        透传给被驱逐者)后,本 routine 自己占住 modules(带驱逐的 acquire,原子无竞态).

        跟 acquire 的区别:acquire 冲突直接抛(等占住者自然释放);force_acquire 主动
        打断占住者抢过来.跟 force_release 的区别:force_release 只驱逐不占(空出模块);
        force_acquire 驱逐后自己占住.

        失败(驱逐后仍冲突--竞态,被别人抢了)抛 ``AcquireError``.
        永不驱逐祖先(打断父亲自己也死).单轮驱逐不重试.需本 routine Started.
        ack 走 routine.acquired(同 acquire 的 future 表).
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before force_acquire '
                f'(本 routine 未 started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_force_acquire(
                req_id=req_id, id=self.id, modules=modules, peer_id=self.peer_id,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)  # 等 routine.acquired
        finally:
            runtime.pop_acquire_future(req_id)

    
    async def force_call(self, name: str,
                         kwargs: Optional[Dict[str, Any]] = None) -> Any:
        """同步抢占式拿子 routine 结果:submit → force_start → wait 一步到位.

        跟 run 的区别:start 换成 force_start----子要占的模块被第三方占时,先打断
        第三方再 start.失败时抛异常:StartError(force_start 失败)/ RuntimeError
        (子异常停止).用本方法意味着不需要保留 handle.
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before force_call() '
                f'(父 routine 未 started,不能 call 子)'
            )
        handle = await self.submit(name, kwargs)
        await handle.force_start()  # 失败 raise StartError
        return await handle

    # --- 运行时占领/释放模块(跟类静态声明同一底层 TryAcquire/Release) ---

    async def acquire(self, modules: List[str]) -> None:
        """运行时占领模块.只 start 期间可用(未 started 抛 RuntimeError).

        底层跟类创建时的静态 ``modules()`` 声明同一 kernel ``TryAcquire``----
        静态声明只是 shell.Start 自动调本方法的语法糖.冲突抛 RuntimeError
        (kernel 返回 ConflictError).stop 时未释放的由 kernel 自动全清.
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before acquire '
                f'(本 routine 未 started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_acquire(
                req_id=req_id, id=self.id, modules=modules, peer_id=self.peer_id,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)  # 等 routine.acquired
        finally:
            runtime.pop_acquire_future(req_id)

    async def load_module(self, parent: str, child: str, name: str = '') -> None:
        """往 parent 模块下加载子模块 child(全局树动态增拓扑).name 是显示名(可重复,
        如左右手都有"大拇指"),空则用 child.只挂树不占用--占用另调 ``acquire``.
        只 start 期间可用.失败(child 已存在 / parent 不存在)抛 ``LoadModuleError``.

        底层跟 kernel ``LoadModule`` 同一.成功后 kernel 重推 module.tree 给所有 conn,
        本地缓存随之刷新(下一轮 push 到达 / 主动 ``get_module_tree``).
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before load_module '
                f'(本 routine 未 started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_load_future(req_id, fut)
        try:
            await self._io.send_routine_load_module(
                req_id=req_id, parent_id=parent, child_id=child,
                name=name, peer_id=self.peer_id,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_load_future(req_id)

    async def unload_module(self, child: str) -> None:
        """卸载子模块 child(全局树动态删拓扑).child 有子模块 / 被占用 / 不存在抛
        ``UnloadModuleError``.只 start 期间可用.

        底层跟 kernel ``UnloadModule`` 同一.成功后 kernel 重推 module.tree.
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before unload_module '
                f'(本 routine 未 started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_unload_future(req_id, fut)
        try:
            await self._io.send_routine_unload_module(
                req_id=req_id, child_id=child, peer_id=self.peer_id,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)
        finally:
            runtime.pop_unload_future(req_id)

    async def release(self, modules: List[str]) -> None:
        """运行时释放指定模块(只这些,不全量).只 start 期间可用.

        底层跟 kernel ``ReleaseModules`` 同一.stop 时 kernel 会全量释放该
        routine 占的所有模块(runRemote defer Release),无需手动 release 兜底.
        """
        if not self._routine._started:
            raise RuntimeError(
                f'{self.name}: must start() before release '
                f'(本 routine 未 started)'
            )
        req_id = _new_req_id()
        fut = asyncio.get_running_loop().create_future()
        runtime = self._runtime
        runtime.register_acquire_future(req_id, fut)
        try:
            await self._io.send_routine_release(
                req_id=req_id, id=self.id, modules=modules, peer_id=self.peer_id,
            )
            await asyncio.wait_for(fut, timeout=self.ACK_TIMEOUT)  # 等 routine.released
        finally:
            runtime.pop_acquire_future(req_id)

    # --- 模块拓扑:静态预测式 conflict(业务侧编排策略用) ---
    # 跟上面 acquire/force_release 的区别:那些是运行时真正占/抢模块(走 kernel);
    # 这个是纯本地预测----读 runtime 缓存的模块树拓扑(kernel 推的 module.tree),
    # 零 round-trip.modules 由调用方传入(编排器用父 handle.modules,实例级).
    # 业务侧编排器(如 AutoSP 自动串并行)据此分组:conflict=True→串行,False→并行.

    def conflict(self, mods_a: List[str], mods_b: List[str]) -> bool:
        """两组 modules 是否冲突(cone 交集非空).纯本地计算.

        跟 kernel ``TryAcquire`` 的 cone 检查同语义:``conflict(a,b)=False`` 意味着
        (无竞态时)a,b 能并行 acquire 不撞;``=True`` 意味着并行必有一个被拒,
        业务侧应串行化.模块树由 kernel 连接时通过 ``module.tree`` 事件推来缓存.

        树未缓存时抛 RuntimeError(不静默返回 False----那会让冲突对误并行).
        """
        tree = self._runtime.module_tree
        if tree is None:
            raise RuntimeError(
                f'{self.name}: module tree not cached yet '
                f'(kernel 未推 module.tree----不应发生,catalog 窗口已推)'
            )
        return tree.conflict(mods_a, mods_b)

    # --- p2p 通信(req / streamreq 骑这条隧道,kernel dumb forward) ---

    def _spawn(self, coro) -> asyncio.Task:
        """起一个后台 task(框架内部: @stream provider gen / lifecycle 后台协程).
        委托 runtime task 池. 业务侧用 asyncio.create_task 自管 task.
        """
        return self._io._spawn(coro)

    async def _send_message(self, target_id: str, send_event: str,
                            data: Optional[Dict[str, Any]] = None) -> None:
        """(内部原语)发 message.* 给 target_id.

        send_event 是 message.send / message.req / message.req_reply /
        message.stream_open / message.stream_data / message.stream_cancel 之一.
        data 是 envelope(带 __req_id__ / __stream_id__ / event 等),kernel 不解析.
        req / stream_req / @request 回执 / @stream 帧都用它;用户单向消息用 send(→ message.send).
        """
        await self._io.send_message(
            target_ids=[target_id], send_event=send_event,
            data=data or {}, source_id=self.id, peer_id=self.peer_id,
        )

    async def req(self, target: str, event: str,
                  data: Optional[Dict[str, Any]] = None,
                  timeout: float = 30.0) -> Any:
        """对 target routine 发 request(经 kernel p2p 转发),等回执拿 result.

        target 是对端 routine 的 id(submit 拿到的 handle.id 或 lifecycle.started 的 id).
        对端用 ``@request(event)`` 注册的 handler 处理,返回值即 result.
        handler 抛异常 → raise ReqError;超时 → raise ReqTimeout.
        """
        from .errors import ReqError, ReqTimeout

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
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise ReqTimeout(
                f'req {event!r} to {target} timeout after {timeout}s',
            )
        finally:
            runtime.pop_req_future(req_id)

    async def get_running_routines(self) -> list:
        """查 kernel 当前所有 running routine 实例 ``[{name, id}]``(跨进程正确).

        统一经 Transport 实现:dial-in 走 Req unary,dial-out 走 Stream 请求-回执
        (两种模式都问 kernel,不读本地 runtime--kernel 是唯一有全局 nodes 视图的角色).
        用于按 name 找对端 routine 的 id(如 agent 找 bridge).req 是实现层细节
        (dial-out 不支持),但本查询两种模式都支持,故可在抽象层暴露.
        """
        transport = self._transport
        return await transport.get_running_routines()

    async def get_module_tree(self) -> "Optional[ModuleTree]":
        """主动从 kernel 拉当前 ``module.tree`` 并刷新本地缓存,返回 ``ModuleTree``.

        两种模式都问 kernel(唯一真理源):dial-in 走 Req,dial-out 走 Stream 请求-回执.
        平时靠 kernel 推送缓存(``module.tree`` 事件 / dial-out 同步 Req);本方法用于
        推送未到或想主动刷新的场景--刷后 ``conflict`` 即可用(不再因树未缓存抛 RuntimeError).
        """
        transport = self._transport
        return await transport.get_module_tree()

    async def get_routines(self) -> list:
        """查 kernel 全量路由表(catalog 注册的全部 routine,跨所有 conn).

        返回 ``[{name, conn_id, is_passive}, ...]``.dial-in 走 Req unary
        (kernel HandleReq 处理 get_routines -> ListRoutines 遍历 routineClients).
        dial-out 暂未实现(后续按需补 Stream 请求-回执).
        """
        transport = self._transport
        return await transport.get_routines()

    async def stream_req(self, target: str, event: str,
                         data: Optional[Dict[str, Any]] = None,
                         timeout: float = 30.0):
        """对 target routine 发 stream request,返回 ``StreamCtx``(async with → async for).

        对端用 ``@stream(event)`` 注册的 async-generator handler 产数据.
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

    # --- pubsub(经 kernel 订阅表 fanout) ---

    async def publish(self, topic: str, data: Any = None, *,
                      namespace: str = '') -> None:
        """发一条 pubsub 消息到 ``(namespace, topic)``.kernel fanout 给所有订阅者."""
        await self._io.send_pubsub_publish(
            topic=topic, data=data, source_id=self.id, namespace=namespace,
            peer_id=self.peer_id,
        )

    async def subscribe_topic(self, topic: str, *,
                             namespace: str = '') -> None:
        """(内部)发 pubsub.subscribe 注册到 kernel 订阅表.

        handler 注册由调用方(Routine._auto_subscribe / subscribe)在 runtime 本地表完成.
        """
        await self._io.send_pubsub_subscribe(
            id=self.id, topic=topic, namespace=namespace, peer_id=self.peer_id,
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
        # 本地表:从该 rid 的 (namespace, topic)→handler map 删
        topics = runtime._subscribers.get(self.id)
        if topics is not None:
            topics.pop((namespace, topic), None)
            if not topics:
                runtime._subscribers.pop(self.id, None)
        await self._io.send_pubsub_unsubscribe(
            id=self.id, topic=topic, namespace=namespace, peer_id=self.peer_id,
        )

    def namespace(self, ns: str) -> 'Namespace':
        """拿到一个 namespaced 助手:``self.namespace('agent.x').publish(e, d)``
        等价于 ``self.publish(e, d, namespace='agent.x')``;``ns.subscribe(e, h)``
        等价于 ``self.subscribe(e, h, namespace='agent.x')``."""
        return Namespace(self, ns)

    # --- yield(child 发,kernel dumb forward 给 parent) ---

    async def _send_yield(self, data: Any = None, *,
                          is_final: bool = False,
                          error: Optional[str] = None) -> None:
        """发 routine.yield 给 kernel(child yield 一项 / 收尾 / 异常).

        id = self.id(child 的 rid,parent 据此路由到 submit 拿到的 handle).
        """
        await self._io.send_yield(
            id=self.id, data=data, is_final=is_final, error=error,
            peer_id=self.peer_id,
        )

    # --- message.* 定向消息(push,调 target 的 on_message) ---

    async def send(self, target: str, data: Any = None) -> None:
        """给 target routine 发一条定向消息(message.send).target 需 created.

        派发是 spawn 并发----对端 on_message 可能并发 fire,乱序到达,业务侧自带
        id reorder.created 后即可收.区别于 req(等回执)/ stream_req(开流)/
        publish(广播).
        """
        await self._send_message(target, MESSAGE_SEND, data or {})


_req_counter = 0


def _new_req_id() -> str:
    global _req_counter
    _req_counter += 1
    return f'r{_req_counter}'


class Namespace:
    """namespaced pub/sub 助手.``self.namespace('agent.x').publish(e, d)``
    等价于 ``self.publish(e, d, namespace='agent.x')``;``ns.subscribe(e, h)``
    等价于 ``self.subscribe(e, h, namespace='agent.x')``."""

    def __init__(self, ctx: 'RunContext', ns: str):
        self._ctx = ctx
        self._ns = ns

    async def publish(self, topic: str, data: Any = None) -> None:
        await self._ctx.publish(topic, data, namespace=self._ns)

    async def subscribe(self, topic: str, handler) -> None:
        await self._ctx.subscribe(topic, handler, namespace=self._ns)

    async def unsubscribe(self, topic: str) -> None:
        await self._ctx.unsubscribe(topic, namespace=self._ns)
