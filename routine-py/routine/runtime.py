"""ServerRuntime ---- runtime 状态:routine 注册表,运行实例表,peer 出站队列,task 池."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional, Type, Any

from .logger import setup_logger
from .module_tree import ModuleTree
from .routine import Routine, Routines


class ServerRuntime:
    def __init__(self, routines: Routines, modules: Optional[List[str]] = None):
        self.routines = routines
        self.modules: List[str] = modules or []
        self.running_instances: Dict[str, Routine] = {}
        self._tasks: set = set()
        self.logger = setup_logger('RoutineHub')
        # 模块树拓扑缓存:kernel 连接时通过 module.tree 事件推过来(静态 config,
        # 运行期不变 → 推一次永不 stale,reconnect 重推).未推送前为 None----
        # ctx.conflict 读时若 None 抛清晰错(实际不会撞:catalog 拉取窗口已推完,
        # 远早于 routine run()).业务侧编排策略(AutoSP 等)本地算 cone/conflict.
        self.module_tree: Optional[ModuleTree] = None
        # submit 回执:req_id → future(等 routine.submitted 带 child_id 回来)
        self._submit_futures: Dict[str, asyncio.Future] = {}
        # 运行时占领/释放回执:req_id → future(等 routine.acquired/released ack)
        self._acquire_futures: Dict[str, asyncio.Future] = {}
        # load/unload 子模块回执:req_id -> future(等 routine.module_loaded/unloaded ack)
        self._load_futures: Dict[str, asyncio.Future] = {}
        self._unload_futures: Dict[str, asyncio.Future] = {}
        # catalog.register/reload/deregister 回执:req_id -> future(等 catalog.registered/
        # reloaded/deregistered ack).py 等 kernel 校验+回执 ok=true 才本地 Routines 操作.
        self._register_futures: Dict[str, asyncio.Future] = {}
        self._reload_futures: Dict[str, asyncio.Future] = {}
        self._deregister_futures: Dict[str, asyncio.Future] = {}
        # catalog.deregister 两跳流程:持有者收到 cmd 后本地 dereg,把被移除的类存进
        # _deregister_results[req_id].请求者 == 持有者时,deregister_routine 从此表取
        # 被移除的类作为返回值;请求者 ≠ 持有者时此表不填(返回 None).
        self._deregister_results: Dict[str, Any] = {}
        # handle 表:child_id -> RoutineHandle(reader 按 child_id 路由 lifecycle / body).handle._ack 持有 start/stop/unsubmit 回执 future,server 收 lifecycle.started/stopped / routine.rejected 时经本表找 handle resolve--不再有独立 _child_acks 全局表(避免跟在途操作撞槽竞态).
        self._handles: Dict[str, object] = {}
        # 通信:按 rid 找 created instance(created 时注册).p2p.delivered 路由到
        # @request/@stream handler----created 后即可收 req/stream(handler 表在 __init__
        # 已建).stopped 时 pop.
        self.created_by_rid: Dict[str, Routine] = {}
        # req 回执:req_id → future(等 p2p __req_reply__ 回执)
        self._req_futures: Dict[str, asyncio.Future] = {}
        # streamreq:stream_id → StreamReader(等 p2p __stream_data__ 帧)
        self._stream_readers: Dict[str, object] = {}
        # streamreq provider 侧:stream_id → 正在跑的 async-gen task(消费方 cancel 时据此 cancel)
        self._provider_streams: Dict[str, asyncio.Task] = {}
        # pubsub 订阅者:subscriber_id(rid) → {(namespace, topic): handler}.
        # @subscribe 装饰器 / ctx.subscribe 在 created 时注册;kernel 在 routine
        # stopped 时自动退订.
        self._subscribers: Dict[str, Dict[tuple, Any]] = {}

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def print_summary(self) -> None:
        """打印一行 routine 注册统计.

        框架只陈述客观计数----不渲染名字表格,不读 description/modules/hidden 等
        业务字段,那些是业务侧的展示关心(业务层可遍历 routines 自行打印 banner).
        走 logger.info,格式跟 Go 侧 kernel/logger 一致.
        """
        try:
            routines = list(self.routines.get_routines())
            enabled = sum(1 for r in routines if r.enable)
            passive_n = sum(1 for r in routines if r.is_passive)
            self.logger.info(
                f'{len(routines)} routines · {enabled} enabled · {passive_n} passive'
            )
        except Exception:
            self.logger.info(repr(self.routines))

    def resolve_instance(self, prid: str, cls: Type[Routine]) -> Routine:
        """同 prid 已有实例则复用(restart 语义),否则新建."""
        inst = self.running_instances.get(prid)
        if inst is not None:
            return inst
        return cls()

    # --- submit future 表(ctx.submit 注册 / server.on_inbound 按 req_id 路由回执) ---

    def register_submit_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._submit_futures[req_id] = fut

    def pop_submit_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._submit_futures.pop(req_id, None)

    # --- 运行时占领/释放 future 表(ctx.acquire/release 注册 / server.on_inbound 按 req_id 路由回执) ---

    def register_acquire_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._acquire_futures[req_id] = fut

    def pop_acquire_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._acquire_futures.pop(req_id, None)

    # --- load/unload 子模块 future 表(ctx.load_module/unload_module 注册 / server.on_inbound 按 req_id 路由回执) ---

    def register_load_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._load_futures[req_id] = fut

    def pop_load_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._load_futures.pop(req_id, None)

    def register_unload_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._unload_futures[req_id] = fut

    def pop_unload_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._unload_futures.pop(req_id, None)

    # --- catalog.register/reload/deregister 回执 future 表(RoutineHub.register_routine /
    #      reload_routine / deregister_routine 注册 / server.on_inbound 按 req_id 路由回执) ---

    def register_register_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._register_futures[req_id] = fut

    def pop_register_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._register_futures.pop(req_id, None)

    def register_reload_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._reload_futures[req_id] = fut

    def pop_reload_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._reload_futures.pop(req_id, None)

    def register_deregister_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._deregister_futures[req_id] = fut

    def pop_deregister_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._deregister_futures.pop(req_id, None)

    # --- catalog.deregister 两跳流程:持有者 cmd handler 存被移除的类,
    #      请求者 == 持有者时 deregister_routine 取出作为返回值 ---

    def set_deregister_result(self, req_id: str, removed: Any) -> None:
        self._deregister_results[req_id] = removed

    def pop_deregister_result(self, req_id: str) -> Any:
        return self._deregister_results.pop(req_id, None)

    # --- handle 表(ctx.submit 注册 / server.on_inbound 按 child_id 路由 lifecycle) ---

    def register_handle(self, child_id: str, handle) -> None:
        self._handles[child_id] = handle

    def pop_handle(self, child_id: str):
        return self._handles.pop(child_id, None)

    def get_handle(self, child_id: str):
        return self._handles.get(child_id)

    # --- created instance 按 rid(p2p 路由用;created 时注册) ---

    def register_created(self, rid: str, instance: Routine) -> None:
        self.created_by_rid[rid] = instance

    def pop_created(self, rid: str):
        return self.created_by_rid.pop(rid, None)

    def get_created(self, rid: str):
        return self.created_by_rid.get(rid)

    # --- req 回执 future 表(ctx.req 注册 / on_inbound 按 __req_id__ 路由回执) ---

    def register_req_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._req_futures[req_id] = fut

    def pop_req_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._req_futures.pop(req_id, None)

    # --- streamreq reader 表(ctx.stream_req 注册 / on_inbound 按 __stream_id__ 路由帧) ---

    def register_stream_reader(self, stream_id: str, reader) -> None:
        self._stream_readers[stream_id] = reader

    def pop_stream_reader(self, stream_id: str):
        return self._stream_readers.pop(stream_id, None)

    def get_stream_reader(self, stream_id: str):
        return self._stream_readers.get(stream_id)

    # --- streamreq provider task 表(_serve_stream 注册 / on_inbound 收 cancel 时取消) ---

    def register_provider_stream(self, stream_id: str, task: asyncio.Task) -> None:
        self._provider_streams[stream_id] = task

    def pop_provider_stream(self, stream_id: str):
        return self._provider_streams.pop(stream_id, None)

    def get_provider_stream(self, stream_id: str):
        return self._provider_streams.get(stream_id)

    # --- pubsub 订阅者表(_auto_subscribe / ctx.subscribe 注册;
    #      on_inbound 按 subscriber_id + (namespace, topic) 找 handler) ---

    def register_subscriber(self, subscriber_id: str, namespace: str,
                            topic: str, handler) -> None:
        self._subscribers.setdefault(subscriber_id, {})[(namespace, topic)] = handler

    def get_subscriber_handler(self, subscriber_id: str, namespace: str, topic: str):
        topics = self._subscribers.get(subscriber_id)
        if topics is None:
            return None
        return topics.get((namespace, topic))

    def pop_subscriber(self, subscriber_id: str):
        return self._subscribers.pop(subscriber_id, None)
