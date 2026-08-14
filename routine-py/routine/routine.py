"""Routine 基类 + Routines 注册表 + 通信装饰器.

生命周期核心:``run`` / ``stop`` / ``on_stopped`` / ``on_created``
/ ``modules`` / ``name``.通信(req/streamreq 骑 p2p 隧道,kernel dumb forward)
经 ``@request``/``@stream`` 装饰器 + ``RunContext.req``/``stream_req`` 暴露.

routine 体由远端 routine server(本 SDK)实例化运行;调度器(kernel)只通过
gRPC lifecycle 事件驱动 create/start/stop,并 dumb-forward p2p 帧.
"""
from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from .handle import RoutineHandle

from .ctx import RunContext
from .protocol import (
    ENVELOPE_CANCEL, ENVELOPE_CHUNK, ENVELOPE_DATA, ENVELOPE_EOF,
    ENVELOPE_ERROR, ENVELOPE_EVENT, ENVELOPE_OK, ENVELOPE_REPLY_TO,
    ENVELOPE_REQ_ID, ENVELOPE_STREAM_ID, MESSAGE_REQ_REPLY, MESSAGE_STREAM_DATA,
)


class Modules(list):
    """routine 声明占用的模块列表(``on_created()`` 返回值).

    ``Modules(['body'])`` 声明占用;``on_created()`` 不返回 / 返回 ``None`` 表示不占模块
    (框架当 ``Modules()`` 空).是 ``list`` 子类----可直接当 list 用,``conflict`` /
    kernel wire 都按 list 处理.

    单一真理源:``on_created()`` 返回值经 lifecycle.created 回报回带 kernel
    (``node.declared``,Start 的 TryAcquire 用)+ 经 submitted 回执带给父
    (``handle.modules``,编排器据此算冲突).
    """

    def __init__(self, modules: Optional[Iterable[str]] = None):
        super().__init__(modules or [])


@dataclass(frozen=True)
class RoutineSource:
    """发送方 routine 引用(@request/@stream/on_message 的 source 参数)."""

    id: str
    name: Optional[str] = None


def request(event: str):
    """装饰 ``async def`` 方法为 ``@request`` handler:收到 p2p topic=event 时调用,
    返回值作为 ``__req_reply__`` 的 data 回给 source.抛异常则回 ok=false."""

    def deco(fn):
        fn._request_event = event
        return fn

    return deco


def stream(event: str):
    """装饰 ``async def``(async generator)方法为 ``@stream`` handler:每 yield 一项
    发一个 ``__stream_data__`` 帧,结束发 ``__eof__:done``."""

    def deco(fn):
        fn._stream_event = event
        return fn

    return deco


def subscribe(topic: str, *, namespace: str = ''):
    """装饰 ``async def`` 方法为 ``@subscribe`` handler:instance created 时自动订阅
    ``(namespace, topic)``,收到匹配的 ``pubsub.delivered`` 时调 ``handler(source, data)``."""

    def deco(fn):
        fn._subscribe_topic = topic
        fn._subscribe_namespace = namespace
        return fn

    return deco


def passive_wire(cls: 'Type[Routine]') -> Dict[str, Any]:
    """is_passive(bool | dict) 序列化成 wire 单字段 ``{flag, kwargs}``.

    类声明 ``is_passive = True`` 本质是 ``{flag: true, kwargs: {}}`` 的语法糖;
    dict 形态 = passive + auto-start 默认 kwargs.序列化统一嵌套结构,wire 上
    ``is_passive`` 恒为 map(kernel 侧不用类型分支,直接取 flag/kwargs).
    """
    ip = getattr(cls, 'is_passive', False)
    if isinstance(ip, dict):
        return {'flag': True, 'kwargs': dict(ip)}
    return {'flag': bool(ip), 'kwargs': {}}


class Routine(ABC):
    """routine 基类:子类 override ``run``/``stop``,可选 ``on_stopped``/``on_created``/``modules``.

    ``run`` 是 routine 的主体工作(跑到完成或 yield body),名字区别于 ``handle.start``
    (推子到 started 态):started 事件在 ``run`` 之前就发了(lifecycle 先 ack_start 报
    started,再调 ``run``),所以 ``run`` 不是"启动"动作,是"跑起来".
    """

    enable: bool = True
    # passive 声明 + auto-start 默认入参(kernel 连上自动 Execute, kwargs 就是它).
    # True: passive 无默认参; dict: passive + 默认 kwargs(常驻服务的启动配置,
    # 如 WebServer 的 host/port); False: 普通 routine.
    is_passive: bool | dict = False
    # routine 命令名 ---- 类字段,子类可直接覆盖::
    #
    #     class Edit(Routine):
    #         name = 'edit'
    #
    # 不写时由 __init_subclass__ 从 __name__ 蛇形转换自动填充(如 EditRoutine -> 'edit_routine').
    name: ClassVar[str | None] = None
    # 自由扩展元数据 ---- 类级别静态声明,框架不强制 schema.
    # 子类按需覆盖自己的 dict(不要原地改继承来的默认值)::
    #
    #     class Edit(Routine):
    #         meta = {'readonly': False, 'tags': ['fs']}
    #
    # ``get_routines`` 查询时随 routine 信息一起序列化到 wire(Go 侧 dumb
    # forward,透传不解析).
    meta: ClassVar[Dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 子类没显式赋值 name 时, 从 __name__ 蛇形转换自动填充.
        # 用 cls.__dict__.get 避免继承到父类的 name (只看子类自己有没有写).
        if cls.__dict__.get('name') is None:
            s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', cls.__name__)
            cls.name = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s).lower()

    def __init__(self):
        self._active_ctx: Optional[RunContext] = None
        self._main_task: Optional[asyncio.Task] = None
        self._stop_requested: bool = False
        self._stop_in_progress: bool = False
        self._stop_finalized: bool = False
        # stopped 回报幂等标志:runner 与 stop_runner/force_stop 并发都会调
        # _send_stopped,靠它保证一条 invocation 只发一次(随 instance GC 自动回收,
        # 不用全局 set 永久堆积).
        self._stopped_sent: bool = False
        # start/stop 子 routine 的门槛:ack_start 后置 True,stop 后置 False.
        # submit 在 created 即可(不查此标志);handle.start()/stop() 查父的此标志,
        # 未 started 抛 RuntimeError(kernel 侧也会硬拦截双保险).
        self._started: bool = False
        # submit 入参(routine 的唯一入参来源):created 时由 lifecycle.created 投递
        # 并存储(on_created hook 用 / 可经 self._init_kwargs 取).run() 复用这同一份
        # (handle_start 从 _init_kwargs 灌进 run()),不再从 lifecycle.start 事件取.
        self._init_kwargs: Dict[str, Any] = {}
        # 父 routine id(kernel 经 lifecycle.created 带来,0/空=无父 root).
        # routine 通过 self.parent_rid 反向 req 父(如 tool routine 获取 agent state).
        self._parent_rid: Optional[str] = None
        # 用 setup_logger:格式跟 Go 侧一致(ANSI 着色 + caller),propagate=False.
        # 同名 routine 实例共享一个 logger(logging.getLogger 单例).
        from .logger import setup_logger
        self._logger = setup_logger(f'[ROUTINE] {self.name}')
        # 扫描 @request / @stream / @subscribe 装饰的方法,建 event→bound method 分发表.
        # _subscribe_handlers 的 key 是 (namespace, topic) 元组(支持 namespace 分域).
        self._request_handlers: Dict[str, Any] = {}
        self._stream_handlers: Dict[str, Any] = {}
        self._subscribe_handlers: Dict[tuple, Any] = {}
        for klass in type(self).__mro__:
            for attr_name, attr in klass.__dict__.items():
                if not callable(attr):
                    continue
                ev = getattr(attr, '_request_event', None)
                if ev is not None and ev not in self._request_handlers:
                    self._request_handlers[ev] = getattr(self, attr_name)
                ev = getattr(attr, '_stream_event', None)
                if ev is not None and ev not in self._stream_handlers:
                    self._stream_handlers[ev] = getattr(self, attr_name)
                topic = getattr(attr, '_subscribe_topic', None)
                if topic is not None:
                    ns = getattr(attr, '_subscribe_namespace', '') or ''
                    key = (ns, topic)
                    if key not in self._subscribe_handlers:
                        self._subscribe_handlers[key] = getattr(self, attr_name)

    # --- Name ---

    # name 现在是类字段 (见上方 ClassVar[str | None]), 由 __init_subclass__ 自动填充.
    # 子类直接覆盖: name = 'xxx' (不用重写方法).

    @property
    def id(self):
        # routine 的 id(kernel 分配的 command id,string).created 前未绑 ctx → None.
        # 直接读 _active_ctx 而不走 self.ctx(后者未绑时抛 RuntimeError,让 created 前
        # 的 __init__ / 早期日志能安全取 self.id).
        ctx = self._active_ctx
        return ctx.id if ctx is not None else None

    def __repr__(self) -> str:
        return self.name

    # --- Module declaration ---

    # --- Lifecycle (to be overridden) ---

    @abstractmethod
    async def run(self, kwargs: Dict[str, Any]):
        ...

    async def stop(self) -> None:
        """正规 stop 流程;routine 在此 set event 让 ``run`` 退出.可返回结果."""
        pass

    async def submit(self, routine_name: str,
                     kwargs: Optional[Dict[str, Any]] = None) -> 'RoutineHandle':
        """提交子 routine(经 kernel 回环).详见 ``ctx.submit``."""
        return await self.ctx.submit(routine_name, kwargs)

    async def call(self, routine_name: str,
                   kwargs: Optional[Dict[str, Any]] = None) -> Any:
        """同步拿子 routine 结果(submit->start->wait 一步到位).详见 ``ctx.call``."""
        return await self.ctx.call(routine_name, kwargs)

    async def force_call(self, routine_name: str,
                         kwargs: Optional[Dict[str, Any]] = None) -> Any:
        """抢占式拿子 routine 结果(submit->force_start->wait).详见 ``ctx.force_call``."""
        return await self.ctx.force_call(routine_name, kwargs)

    # --- 运行时模块占领/释放(跟类静态声明同一底层 TryAcquire/Release) ---

    async def acquire(self, modules: List[str]) -> None:
        """运行时占领模块.详见 ``ctx.acquire``."""
        return await self.ctx.acquire(modules)

    async def release(self, modules: List[str]) -> None:
        """运行时释放指定模块.详见 ``ctx.release``."""
        return await self.ctx.release(modules)

    async def force_release(self, modules: List[str]) -> None:
        """强制释放模块(驱逐第三方后空出,不自己占).详见 ``ctx.force_release``."""
        return await self.ctx.force_release(modules)

    async def force_acquire(self, modules: List[str]) -> None:
        """强制占领模块(驱逐第三方后自己占住,带驱逐的 acquire).详见 ``ctx.force_acquire``."""
        return await self.ctx.force_acquire(modules)

    async def load_module(self, parent: str, child: str, name: str = '') -> None:
        """往父模块加载子模块(全局树动态增拓扑,只挂树不占用).name 可重复(渲染用),
        空=child.详见 ``ctx.load_module``."""
        return await self.ctx.load_module(parent, child, name)

    async def unload_module(self, child: str) -> None:
        """卸载子模块(全局树动态删拓扑).详见 ``ctx.unload_module``."""
        return await self.ctx.unload_module(child)

    # --- p2p 通信(req/streamreq 骑 p2p 隧道,kernel dumb forward) ---

    async def req(self, target: str, event: str,
                  data: Optional[Dict[str, Any]] = None,
                  timeout: float = 30.0) -> Any:
        """对 target routine 发 request 等回执.详见 ``ctx.req``."""
        return await self.ctx.req(target, event, data, timeout)

    async def get_running_routines(self) -> list:
        """查 kernel 当前所有 running routine 实例.详见 ``ctx.get_running_routines``."""
        return await self.ctx.get_running_routines()

    async def get_module_tree(self):
        """主动从 kernel 拉当前 module.tree 并刷新缓存.详见 ``ctx.get_module_tree``."""
        return await self.ctx.get_module_tree()

    async def get_routines(self) -> list:
        """查 kernel 全量路由表(catalog 注册的全部 routine,跨所有 conn).
        详见 ``ctx.get_routines``."""
        return await self.ctx.get_routines()

    async def stream_req(self, target: str, event: str,
                         data: Optional[Dict[str, Any]] = None,
                         timeout: float = 30.0):
        """对 target 发 stream request,返回 StreamCtx.详见 ``ctx.stream_req``."""
        return await self.ctx.stream_req(target, event, data, timeout)

    # --- pubsub(经 kernel 订阅表 fanout) ---

    async def publish(self, topic: str, data: Any = None, *,
                      namespace: str = '') -> None:
        """发一条 pubsub 消息.详见 ``ctx.publish``."""
        await self.ctx.publish(topic, data, namespace=namespace)

    async def subscribe(self, topic: str, handler, *,
                         namespace: str = '') -> None:
        """订阅 ``(namespace, topic)``:收到 delivered 时调 ``await handler(source, data)``,本方法用于动态订阅.详见 ``ctx.subscribe``."""
        await self.ctx.subscribe(topic, handler, namespace=namespace)

    async def unsubscribe(self, topic: str, *,
                          namespace: str = '') -> None:
        """退订 ``(namespace, topic)``.详见 ``ctx.unsubscribe``."""
        await self.ctx.unsubscribe(topic, namespace=namespace)

    def namespace(self, ns: str):
        """拿到 namespaced 助手.详见 ``ctx.namespace``."""
        return self.ctx.namespace(ns)

    async def _send_yield(self, data: Any = None, *,
                          is_final: bool = False,
                          error: Optional[str] = None) -> None:
        """发 routine.yield(yield 一项给 parent).框架在 run 是 async generator
        时自动调,通常不手动调."""
        await self.ctx._send_yield(data, is_final=is_final, error=error)

    # --- message.* 定向消息(push,调 target 的 on_message;created 后即可收) ---

    async def send(self, target: str, data: Any = None) -> None:
        """给 target routine 发定向消息.详见 ``ctx.send``."""
        await self.ctx.send(target, data)

    async def on_message(self, source: 'RoutineSource', data: Any) -> None:
        """收到 message.delivered 的回调(基类空实现,子类 override).

        ``source`` 是发送方 RoutineSource(id + name),``data`` 是发送方传入的任意值.
        可能并发 fire,乱序到达----业务侧自带 id reorder 后处理.created 后即可收
        (不必 start).
        """
        pass

    async def _auto_subscribe(self) -> None:
        """instance created 时调:对每个 ``@subscribe`` topic 发 pubsub.subscribe +
        注册本地 handler.由 LifecycleManager 在 created 回报前同步调.
        """
        rid = self.ctx.id
        for (namespace, topic), handler in self._subscribe_handlers.items():
            self.ctx._runtime.register_subscriber(
                rid, namespace, topic, handler,
            )
            await self.ctx.subscribe_topic(topic, namespace=namespace)

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> Optional[Modules]:
        """on_created 钩子:routine 被创建时调一次(早于 start).

        返回值是本 routine 声明占用的 modules----``Modules([...])`` 声明占用;
        返回 ``None`` 或不 return(默认)表示不占模块(框架当空).单一真理源:经
        lifecycle.created 回报回带,kernel 存进 node.declared(Start 的 TryAcquire
        用)+ 经 submitted 回执带给父 handle(``handle.modules``,编排器据此算冲突).

        static routine 返回固定 ``Modules([...])``;dynamic routine 按 kwargs 现算返回.
        **注意**:created 回报会 await 本钩子完成才发----所以 on_created() 应只做轻量
        初始化 + 算 modules,慢逻辑放 run().

        rid 是 routine id(kernel 分配的 command id,string),跟 self.id / handle.id
        一致.明确叫 rid 是为了避免跟 Python 内部别的 id 概念混淆.
        """
        return None

    async def on_started(self) -> None:
        """started 钩子:lifecycle.started 已回报,父已 started(可 start/stop 子)后,
        run() 之前调.适合做「父 started 才能做」的一次性初始化(区别于 on_created,
        on_created 时父未必 started).基类空实现."""
        pass

    async def on_stopped(self, reason: str = 'auto', result: Any = None,
                      detail: str = '') -> None:
        """run 完成或退出后调用(lifecycle.stopped 发出前).reason: 'auto' | 'error' | 'stop' | 'cancel' | 'force' | 'disconnect'."""
        pass

    # --- Context ---

    @property
    def ctx(self) -> RunContext:
        if self._active_ctx is None:
            raise RuntimeError(f'{self.name}: no active context.')
        return self._active_ctx

    # --- Stop control ---

    def _request_stop(self) -> bool:
        if self._stop_requested:
            return False
        self._stop_requested = True
        return True

    def _set_main_task(self, task: asyncio.Task) -> None:
        self._main_task = task

    async def _cancel_main_task(self, reason: str = 'stop'):
        if self._main_task and not self._main_task.done():
            self._logger.warning(f'{self} canceling main task ({reason})')
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._logger.error(f'{self.name} main task cancel error: {e}')

    # --- Internal lifecycle state(LifecycleManager 用,收口 _ 前缀字段访问)---
    # lifecycle.py 原跨类直写 Routine 的 _stop_*/_started/_active_ctx/_init_kwargs/
    # _main_task 等 25+ 处,_ 前缀封装形同虚设.下面这组方法把字段访问收口,让 _
    # 前缀恢复成真实边界(lifecycle 只调方法,不碰字段).

    def reset_for_start(self) -> None:
        """runner 开头重置 stop 状态(_stop_requested/_stop_in_progress/_started
        归 False).不清 _stop_finalized--runner 开头守卫要查它判断是否被 force 接管."""
        self._stop_requested = False
        self._stop_in_progress = False
        self._started = False

    def mark_started(self) -> None:
        """ack_start 后置 _started=True:父已 started,可 start/stop 子 routine."""
        self._started = True

    def mark_not_started(self) -> None:
        """stop / cleanup 置 _started=False:不能再 start/stop 子."""
        self._started = False

    def begin_stop(self) -> bool:
        """接管 stop:已 in-progress / finalized 返 False(别人在收,让出),否则置
        _stop_in_progress=True 返 True(本次接管完整收尾)."""
        if self._stop_in_progress or self._stop_finalized:
            return False
        self._stop_in_progress = True
        return True

    def finalize_stop(self) -> None:
        """置 _stop_finalized=True:让 runner 的 CancelledError 分支跳过 cleanup
        (stop_runner / force_stop 已接管完整收尾)."""
        self._stop_finalized = True

    def clear_stop_finalized(self) -> None:
        """restart/复用时清终态(created 实例化调;区别于 reset_for_start:后者
        在 runner 开头,不动 _stop_finalized)."""
        self._stop_finalized = False

    def clear_stop_in_progress(self) -> None:
        """stop_runner / force_stop 的 finally 清 _stop_in_progress=False."""
        self._stop_in_progress = False

    def _mark_stopped_sent(self) -> bool:
        """stopped 回报幂等:首次调返 True(调用方应发 lifecycle.stopped),
        后续并发调返 False(已发过,调用方 no-op).runner 与 stop_runner/force_stop
        竞态时保证只发一次.标志随 instance GC 回收,无全局 set 泄漏."""
        if self._stopped_sent:
            return False
        self._stopped_sent = True
        return True

    def is_stop_finalized(self) -> bool:
        return self._stop_finalized

    def is_stop_in_progress(self) -> bool:
        return self._stop_in_progress

    def is_stop_requested(self) -> bool:
        return self._stop_requested

    def bind_ctx(self, ctx: RunContext) -> None:
        """created 实例化时绑 RunContext(on_created hook + 所有发送能力随之可用)."""
        self._active_ctx = ctx

    def set_init_kwargs(self, kwargs: Dict[str, Any]) -> None:
        """created 时存 submit 入参(on_created + run 共用同一份)."""
        self._init_kwargs = kwargs

    def _set_parent_rid(self, parent_rid: str) -> None:
        """created 时存父 routine id(kernel 经 lifecycle.created 带来).
        routine 通过 self.parent_rid 反向 req 父(如 tool routine 获取 agent state)."""
        self._parent_rid = parent_rid

    @property
    def init_kwargs(self) -> Dict[str, Any]:
        """读 _init_kwargs(handle_start 灌进 run() 的入参)."""
        return self._init_kwargs

    @property
    def parent_rid(self) -> Optional[str]:
        """父 routine id(无父=root 时为 None).created 时由 lifecycle 注入,
        routine 据此反向 req 父(如 tool routine 获取 agent state)."""
        return self._parent_rid

    def _pending_main_task(self) -> Optional[asyncio.Task]:
        """返未完成的 main task(供 lifecycle await 其自然结束),或 None(无 task
        / 已 done).await-with-timeout + stop_result 覆盖 + 多分支 except 的控制流
        留在 lifecycle(涉及 runtime.logger),本方法只收口 _main_task 字段读."""
        if self._main_task is not None and not self._main_task.done():
            return self._main_task
        return None

    # --- p2p 通信派发(server.on_inbound 按 target_id 找到 instance 后调用) ---

    async def _serve_request(self, envelope: Dict[str, Any],
                             source: RoutineSource) -> None:
        """req 到达 provider:按 envelope 的 event 路由到 @request handler,回执走
        message.req_reply.__req_id__ / __reply_to__ / event / data 都在 envelope."""
        event = envelope.get(ENVELOPE_EVENT, '')
        handler = self._request_handlers.get(event)
        req_id = envelope.get(ENVELOPE_REQ_ID, '')
        reply_to = envelope.get(ENVELOPE_REPLY_TO, '')
        payload = envelope.get(ENVELOPE_DATA, {})
        if handler is None:
            self._logger.warning(f'{self.name} @request({event!r}) no handler')
            await self.ctx._send_message(reply_to, MESSAGE_REQ_REPLY, {
                ENVELOPE_REQ_ID: req_id, ENVELOPE_OK: False,
                ENVELOPE_ERROR: f'no @request handler for {event!r}',
            })
            return
        try:
            result = await handler(source, payload)
            await self.ctx._send_message(reply_to, MESSAGE_REQ_REPLY, {
                ENVELOPE_REQ_ID: req_id, ENVELOPE_OK: True, ENVELOPE_DATA: result,
            })
        except Exception as exc:
            self._logger.exception(f'{self.name} @request({event!r}) failed: {exc}')
            await self.ctx._send_message(reply_to, MESSAGE_REQ_REPLY, {
                ENVELOPE_REQ_ID: req_id, ENVELOPE_OK: False,
                ENVELOPE_ERROR: str(exc),
            })

    async def _serve_stream(self, envelope: Dict[str, Any],
                            source: RoutineSource) -> None:
        """stream 开流到达 provider:按 envelope 的 event 路由到 @stream handler,
        spawn provider task 产 message.stream_data 帧."""
        event = envelope.get(ENVELOPE_EVENT, '')
        handler = self._stream_handlers.get(event)
        stream_id = envelope.get(ENVELOPE_STREAM_ID, '')
        reply_to = envelope.get(ENVELOPE_REPLY_TO, '')
        payload = envelope.get(ENVELOPE_DATA, {})
        if handler is None:
            self._logger.warning(f'{self.name} @stream({event!r}) no handler')
            return
        task = self.ctx._spawn(
            self._run_stream(handler, source, payload, stream_id, reply_to),
        )
        # 登记 provider task:消费方 cancel 时据 stream_id cancel 这个 gen
        self.ctx._runtime.register_provider_stream(stream_id, task)

    async def _run_stream(self, handler, source: RoutineSource, payload: Dict[str, Any],
                          stream_id: str, reply_to: str) -> None:
        try:
            async for chunk in handler(source, payload):
                await self.ctx._send_message(reply_to, MESSAGE_STREAM_DATA, {
                    ENVELOPE_STREAM_ID: stream_id, ENVELOPE_CHUNK: chunk,
                })
            await self.ctx._send_message(reply_to, MESSAGE_STREAM_DATA, {
                ENVELOPE_STREAM_ID: stream_id, ENVELOPE_EOF: 'done',
            })
        except asyncio.CancelledError:
            # 消费方 cancel:尽量发一个 cancelled eof(best-effort,可能对端也已退)
            try:
                await self.ctx._send_message(reply_to, MESSAGE_STREAM_DATA, {
                    ENVELOPE_STREAM_ID: stream_id, ENVELOPE_EOF: 'cancelled',
                })
            except Exception:
                pass
            raise
        except Exception as exc:
            self._logger.exception(f'{self.name} @stream({stream_id}) error: {exc}')
            await self.ctx._send_message(reply_to, MESSAGE_STREAM_DATA, {
                ENVELOPE_STREAM_ID: stream_id, ENVELOPE_EOF: 'error',
                ENVELOPE_ERROR: str(exc),
            })
        finally:
            self.ctx._runtime.pop_provider_stream(stream_id)


class Routines:
    """routine 注册表:存 class(不存实例),start 时按需实例化."""

    _logger = logging.getLogger('routine.Routines')

    def __init__(self):
        self._routines: Dict[str, Type[Routine]] = {}

    def register(self, *routines: 'Type[Routine] | Routines'):
        """注册 routine 类或 Routines 组.同名覆盖(打 warn).enable=False 跳过."""
        for item in routines:
            if isinstance(item, Routines):
                for cls in item.get_routines():
                    self._register_one(cls)
            else:
                if not item.enable:
                    continue
                self._register_one(item)

    def _register_one(self, cls: 'Type[Routine]') -> None:
        """注册单个 routine 类.同名覆盖时打 warning(开发期发现重复定义)."""
        existing = self._routines.get(cls.name)
        if existing is not None and existing is not cls:
            self._logger.warning(
                'Routines.register: name=%r overwritten %s -> %s',
                cls.name, existing.__qualname__, cls.__qualname__,
            )
        self._routines[cls.name] = cls

    def get_routine(self, name: str) -> Optional[Type[Routine]]:
        return self._routines.get(name)

    def get_routines(self) -> List[Type[Routine]]:
        return list(self._routines.values())

    def get_routine_names(self) -> List[str]:
        """所有已注册 routine 的 name 列表(RoutineHub.register_routine 算 diff 用)."""
        return list(self._routines.keys())

    def deregister(self, name: str) -> Optional[Type[Routine]]:
        """移除已注册的 routine 类. 返回被移除的类 (没找到返回 None).

        用于 per-agent 动态注册的 routine 在 agent 销毁时清理, 避免全局
        routine 表累积失效类.
        """
        return self._routines.pop(name, None)

    def __repr__(self) -> str:
        return f"Routines({', '.join(sorted(self._routines))})"
