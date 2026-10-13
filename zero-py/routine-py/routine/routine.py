"""Routine 基类 + Routines 注册表 + 通信装饰器.

生命周期核心:``run`` / ``stop`` / ``on_stopped`` / ``on_created`` / ``name``.
通信(req/stream_req 骑 p2p 隧道,kernel dumb forward)经 ``@request``/``@stream``
装饰器 + ``RunContext.req``/``stream_req`` 暴露.

routine 体由 hub(同进程)实例化运行;kernel 只通过 lifecycle 事件驱动
create/start/stop,并 dumb-forward p2p 帧.
"""
from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, ClassVar, Dict, Iterable, List, Optional, Type

from .ctx import Phase, RunContext
from .protocol import (
    ENVELOPE_CHUNK, ENVELOPE_DATA, ENVELOPE_EOF, ENVELOPE_ERROR,
    ENVELOPE_EVENT, ENVELOPE_OK, ENVELOPE_REQ_ID, ENVELOPE_REPLY_TO,
    ENVELOPE_STREAM_ID, MESSAGE_REQ_REPLY, MESSAGE_STREAM_DATA,
)

# --- start 优先级(模块冲突时的抢占裁决, 只在 start 上生效) ---
# 规则: incoming.priority > holder.priority 才驱逐(严格大于, 平级不抢);
# <= 直接 StartError. 语义分层: 10=常规业务(默认) / 20=用户显式意图
# (抢占在跑的常规动作, 如唤醒转向打断当前跟踪) / 100=安全兜底
# (抢占一切, 如急停). 0~9 留给低档(后台/可牺牲类, 可被默认档驱逐);
# 0 为最低档, 永无驱逐资格.


class Modules(list):
    """routine 声明占用的模块列表(``modules()`` 类方法返回值).

    ``Modules(['body'])`` 声明占用;默认不占(空).单一真理源:经
    lifecycle.created 回报回带 kernel(node.declared,Start 的 TryAcquire 用)
    + 经 created 回执带给父(handle.modules,编排器据此算冲突).
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
    返回值作为 req_reply 的 data 回给 source.抛异常则回 ok=false."""

    def deco(fn):
        fn._request_event = event
        return fn

    return deco


def stream(event: str):
    """装饰 ``async def``(async generator)方法为 ``@stream`` handler:每 yield 一项
    发一个 stream_data 帧,结束发 ``__eof__:done``."""

    def deco(fn):
        fn._stream_event = event
        return fn

    return deco


def subscribe(topic: str, *, namespace: str = ''):
    """装饰 ``async def`` 方法为 ``@subscribe`` handler:instance created 时自动订阅
    ``(namespace, topic)``,收到匹配的 pubsub.delivered 时调 ``handler(source, data)``."""

    def deco(fn):
        fn._subscribe_topic = topic
        fn._subscribe_namespace = namespace
        return fn

    return deco


@dataclass(frozen=True)
class PassiveConfig:
    """``is_passive`` 带参形态: passive + auto-start 默认入参.

    kwargs: 常驻服务的启动配置(如 WebServer 的 host/port).
    priority: 拉起时的 start 优先级 ---- passive 没有 call-site(kernel 是
        拉起发起者), 声明处即调用点. 档位同 start(10=常规默认档,
        0 为最低档永无驱逐资格).
    """

    kwargs: Dict[str, Any]
    priority: int = 10


def passive_wire(cls: 'Type[Routine]') -> Dict[str, Any]:
    """is_passive(bool | PassiveConfig) 序列化成 wire 单字段
    ``{flag, kwargs[, priority]}``(priority 缺省 10 不写字段).

    裸 dict 等杂型注册即 TypeError(协议违规立即暴露,不留兼容分支).
    """
    ip = getattr(cls, 'is_passive', False)
    if isinstance(ip, PassiveConfig):
        w = {'flag': True, 'kwargs': dict(ip.kwargs)}
        if ip.priority != 10:
            w['priority'] = ip.priority
        return w
    if isinstance(ip, bool):
        return {'flag': ip, 'kwargs': {}}
    raise TypeError(
        f'{cls.__name__}.is_passive only accepts bool | PassiveConfig, '
        f'got {type(ip).__name__}: {ip!r}')


class Routine(ABC):
    """routine 基类:子类 override ``run``/``stop``,可选 ``on_stopped``/``on_created``.

    ``run`` 是 routine 的主体工作(跑到完成或 yield body).started 事件在 ``run``
    之前就发了(lifecycle 先 ack_start 报 started,再调 ``run``).
    """

    enable: bool = True
    # passive 声明 + auto-start 默认入参(kernel catalog push 后自动 Execute).
    is_passive: bool | PassiveConfig = False
    # routine 命令名 ---- 类字段,子类可直接覆盖;不写时从 __name__ 蛇形转换.
    name: ClassVar[str | None] = None
    # 自由扩展元数据 ---- 类级别静态声明,框架不强制 schema.
    meta: ClassVar[Dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get('name') is None:
            s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1_\2', cls.__name__)
            cls.name = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s).lower()

    def __init__(self):
        self._active_ctx: Optional[RunContext] = None
        self._main_task: Optional[asyncio.Task] = None
        self._stop_requested: bool = False
        # force 驱逐标志 + 驱逐者 rid:lifecycle.stop(reason=force) 时置,
        # runner 与 stop_runner 都读它----run() 快速退出时 runner 先发
        # stopped,reason 必须从 instance 读而非 stop_runner 局部变量.
        self._stop_force: bool = False
        self._stop_by: str = ''
        self._stop_in_progress: bool = False
        self._stop_finalized: bool = False
        # stopped 回报幂等标志:runner 与 stop_runner 并发都调 _send_stopped,
        # 靠它保证一条 invocation 只发一次.
        self._stopped_sent: bool = False
        # 生命周期相位:CREATED(从未 start)→STARTED(运行中)→STOPPED(start 后
        # 被 stop/驱逐).ctx 守卫据此区分"还没 start"(报错)与"已停"(no-op).
        self._phase: Phase = Phase.CREATED
        # create 入参(routine 的唯一入参来源):created 时投递存储,run() 复用.
        self._init_kwargs: Dict[str, Any] = {}
        # 父 routine id(kernel 经 lifecycle.created 带来,None=无父 root).
        self._parent_rid: Optional[str] = None
        self._logger = logging.getLogger(f'routine.routine.{self.name}')
        # 扫描 @request / @stream / @subscribe 装饰的方法,建分发表.
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

    @property
    def id(self) -> Optional[str]:
        # routine 的 id(kernel 分配的 command id,string).created 前未绑 ctx → None.
        ctx = self._active_ctx
        return ctx.id if ctx is not None else None

    def __repr__(self) -> str:
        return self.name

    # --- Lifecycle (to be overridden) ---

    @abstractmethod
    async def run(self, kwargs: Dict[str, Any]):
        ...

    async def stop(self) -> None:
        """正规 stop 流程;routine 在此 set event 让 ``run`` 退出.可返回结果."""
        pass

    async def destroy(self) -> None:
        """destroy hook: created 态被销毁时的资源撤销(与 stop 分野).

        仅在 create 之后、未 start 的实例被 destroy 时调用(started 实例
        的终止走 stop).start 前实例可能已收 body 持有资源 ---- 在此撤销.
        """
        pass

    async def create(self, routine_name: str,
                     kwargs: Optional[Dict[str, Any]] = None) -> 'Any':
        """提交子 routine(经 kernel 回环).详见 ``ctx.create``."""
        return await self.ctx.create(routine_name, kwargs)

    async def call(self, routine_name: str,
                   kwargs: Optional[Dict[str, Any]] = None,
                   priority: int = 10) -> Any:
        """同步拿子 routine 结果(create→start→wait 一步到位).详见 ``ctx.call``."""
        return await self.ctx.call(routine_name, kwargs, priority)

    # --- 运行时模块占领/释放(跟类静态声明同一底层 TryAcquire/Release) ---

    async def acquire(self, modules: List[str]) -> None:
        return await self.ctx.acquire(modules)

    async def release(self, modules: List[str]) -> None:
        return await self.ctx.release(modules)

    async def load_module(self, parent: str, child: str, name: str = '') -> None:
        return await self.ctx.load_module(parent, child, name)

    async def unload_module(self, child: str) -> None:
        return await self.ctx.unload_module(child)

    # --- p2p 通信(req/stream_req 骑 p2p 隧道,kernel dumb forward) ---

    async def req(self, target: str, event: str,
                  data: Optional[Dict[str, Any]] = None,
                  timeout: float = 30.0) -> Any:
        return await self.ctx.req(target, event, data, timeout)

    async def get_running_routines(self) -> list:
        return await self.ctx.get_running_routines()

    async def get_module_tree(self):
        return await self.ctx.get_module_tree()

    async def get_routines(self) -> list:
        return await self.ctx.get_routines()

    async def stream_req(self, target: str, event: str,
                         data: Optional[Dict[str, Any]] = None,
                         timeout: float = 30.0):
        return await self.ctx.stream_req(target, event, data, timeout)

    # --- pubsub(经 kernel 订阅表 fanout) ---

    async def publish(self, topic: str, data: Any = None, *,
                      namespace: str = '') -> None:
        await self.ctx.publish(topic, data, namespace=namespace)

    async def subscribe(self, topic: str, handler, *,
                        namespace: str = '') -> None:
        await self.ctx.subscribe(topic, handler, namespace=namespace)

    async def unsubscribe(self, topic: str, *,
                          namespace: str = '') -> None:
        await self.ctx.unsubscribe(topic, namespace=namespace)

    def namespace(self, ns: str):
        return self.ctx.namespace(ns)

    async def _send_yield(self, data: Any = None, *,
                          is_final: bool = False,
                          error: Optional[str] = None) -> None:
        """发 routine.yield(yield 一项给 parent).run 是 async generator 时框架
        自动调,通常不手动调."""
        await self.ctx._send_yield(data, is_final=is_final, error=error)

    # --- message.* 定向消息(push,调 target 的 on_message;created 后即可收) ---

    async def send(self, target: str, data: Any = None) -> None:
        await self.ctx.send(target, data)

    async def on_message(self, source: 'RoutineSource', data: Any) -> None:
        """收到 message.delivered 的回调(基类空实现,子类 override).

        可能并发 fire,乱序到达----业务侧自带 id reorder 后处理.
        """
        pass

    async def _auto_subscribe(self) -> None:
        """instance created 时调:对每个 ``@subscribe`` topic 发 pubsub.subscribe +
        注册本地 handler.由 LifecycleManager 在 created 回报前同步调."""
        rid = self.ctx.id
        for (namespace, topic), handler in self._subscribe_handlers.items():
            self.ctx._runtime.register_subscriber(rid, namespace, topic, handler)
            await self.ctx.subscribe_topic(topic, namespace=namespace)

    @classmethod
    def modules(cls, kwargs: Optional[Dict[str, Any]] = None) -> Modules:
        """声明占用的模块(类方法,无实例可调).

        modules 是"类型 + 入参"的纯函数,与实例状态无关----
        静态 routine 忽略 kwargs 返回常量;动态 routine 按 kwargs 算
        (kwargs=None 问静态默认,注册期/清单用).默认不占.
        单一真理源:created 回报经本方法取值回带 kernel(node.declared)
        + 经 created 回执带给父(handle.modules,编排器静态冲突预测用).
        """
        return Modules()

    async def on_created(self, rid: str,
                         kwargs: Dict[str, Any]) -> None:
        """on_created 钩子:routine 被创建时调一次(早于 start),纯轻量初始化.

        模块声明走 ``modules()`` 类方法,本钩子不返回值(返回非 None 是
        协议违规,立即报错).created 回报会 await 本钩子完成才发----
        慢逻辑放 run().
        """
        return None

    async def on_started(self) -> None:
        """started 钩子:lifecycle.started 已回报,run() 之前调.基类空实现."""
        pass

    async def on_stopped(self, reason: str = 'auto', result: Any = None,
                         detail: str = '') -> None:
        """run 完成或退出后调用(lifecycle.stopped 发出前).
        reason: 'auto' | 'error' | 'stop' | 'cancel' | 'force'."""
        pass

    # --- Context ---

    @property
    def ctx(self) -> RunContext:
        if self._active_ctx is None:
            raise RuntimeError(f'{self.name}: no active context.')
        return self._active_ctx

    # --- Stop control ---

    def _request_stop(self, force: bool = False, by: str = '') -> bool:
        # force/by 无条件升级记录(已请求过普通 stop 再来 force 也要覆盖).
        if force:
            self._stop_force = True
        if by:
            self._stop_by = by
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

    # --- Internal lifecycle state(LifecycleManager 用,收口 _ 前缀字段访问) ---

    def reset_for_start(self) -> None:
        """runner 开头重置 stop 状态.不清 _stop_finalized----runner 开头守卫要查它."""
        self._stop_requested = False
        self._stop_force = False
        self._stop_by = ''
        self._stop_in_progress = False
        self._phase = Phase.CREATED

    def mark_started(self) -> None:
        """ack_start 后置 STARTED:父已 started,可 start/stop 子 routine."""
        self._phase = Phase.STARTED

    def mark_not_started(self) -> None:
        """stop/驱逐后置 STOPPED.从未 STARTED 的保持 CREATED(release 守卫要
        区分"还没 start"与"start 后被停")."""
        if self._phase is Phase.STARTED:
            self._phase = Phase.STOPPED

    def begin_stop(self) -> bool:
        """接管 stop:已 in-progress / finalized 返 False,否则置 True 返 True."""
        if self._stop_in_progress or self._stop_finalized:
            return False
        self._stop_in_progress = True
        return True

    def finalize_stop(self) -> None:
        """置 _stop_finalized=True:让 runner 的 CancelledError 分支跳过 cleanup."""
        self._stop_finalized = True

    def clear_stop_finalized(self) -> None:
        """restart/复用时清终态(created 实例化调)."""
        self._stop_finalized = False

    def clear_stop_in_progress(self) -> None:
        self._stop_in_progress = False

    def _mark_stopped_sent(self) -> bool:
        """stopped 回报幂等:首次调返 True(调用方应发 lifecycle.stopped)."""
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

    def is_force_stop(self) -> bool:
        return self._stop_force

    def stop_by(self) -> str:
        """force 驱逐者 rid(非 force 为空串)."""
        return self._stop_by

    def bind_ctx(self, ctx: RunContext) -> None:
        """created 实例化时绑 RunContext(on_created hook + 发送能力随之可用)."""
        self._active_ctx = ctx

    def set_init_kwargs(self, kwargs: Dict[str, Any]) -> None:
        self._init_kwargs = kwargs

    def _set_parent_rid(self, parent_rid: str) -> None:
        self._parent_rid = parent_rid

    @property
    def init_kwargs(self) -> Dict[str, Any]:
        return self._init_kwargs

    @property
    def parent_rid(self) -> Optional[str]:
        """父 routine id(无父=root 时为 None).routine 据此反向 req 父."""
        return self._parent_rid

    def _pending_main_task(self) -> Optional[asyncio.Task]:
        """返未完成的 main task(供 lifecycle await 其自然结束),或 None."""
        if self._main_task is not None and not self._main_task.done():
            return self._main_task
        return None

    # --- p2p 通信派发(hub.on_inbound 按 target_id 找到 instance 后调用) ---

    async def _serve_request(self, envelope: Dict[str, Any],
                             source: RoutineSource) -> None:
        """req 到达 provider:按 envelope 的 event 路由到 @request handler."""
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
        spawn provider task 产 stream_data 帧."""
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
        self.ctx._runtime.register_provider_stream(stream_id, task)

    async def _run_stream(self, handler, source: RoutineSource,
                          payload: Dict[str, Any],
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
            # 消费方 cancel:尽量发一个 cancelled eof(best-effort)
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
    """routine 注册表:存 class(不存实例),created 时按需实例化."""

    _logger = logging.getLogger('routine.routines')

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

    def _register_one(self, cls: Type[Routine]) -> None:
        """注册单个 routine 类.注册即校验 passive_wire:is_passive 杂型当场 raise."""
        passive_wire(cls)
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
        return list(self._routines.keys())

    def deregister(self, name: str) -> Optional[Type[Routine]]:
        """移除已注册的 routine 类.返回被移除的类(没找到返回 None)."""
        return self._routines.pop(name, None)

    def __repr__(self) -> str:
        return f"Routines({', '.join(sorted(self._routines))})"
