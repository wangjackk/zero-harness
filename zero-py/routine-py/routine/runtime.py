"""ServerRuntime ---- hub 侧运行时状态:注册表,实例表,回执 future 表,task 池."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Type

from .module_tree import ModuleTree
from .routine import Routine, Routines

# mailbox 慢回调阈值(ms):超过打 warning,定位积压源头
_MAIL_SLOW_MS = 2000.0


class ServerRuntime:
    def __init__(self, routines: Routines, modules: Optional[List[str]] = None):
        self.routines = routines
        self.modules: List[str] = modules or []
        # 运行实例表:rid → Routine instance(单 hub,rid 全局唯一).
        self.running_instances: Dict[str, Routine] = {}
        self._tasks: set = set()
        self.logger = logging.getLogger('routine.hub')
        # 模块树拓扑缓存:kernel 经 module.tree 事件推过来(catalog push /
        # load/unload 后变更重推).未推送前为 None----ctx.conflict 读时若 None
        # 抛清晰错.业务侧编排策略本地算 cone/conflict.
        self.module_tree: Optional[ModuleTree] = None
        # create 回执:req_id → future(等 routine.created 带 child_id 回来)
        self._create_futures: Dict[str, asyncio.Future] = {}
        # 运行时占领/释放回执:req_id → future(等 routine.acquired/released ack)
        self._acquire_futures: Dict[str, asyncio.Future] = {}
        # load/unload 子模块回执
        self._load_futures: Dict[str, asyncio.Future] = {}
        self._unload_futures: Dict[str, asyncio.Future] = {}
        # catalog.register/reload/deregister 回执
        self._register_futures: Dict[str, asyncio.Future] = {}
        self._reload_futures: Dict[str, asyncio.Future] = {}
        self._deregister_futures: Dict[str, asyncio.Future] = {}
        # catalog.deregister 两跳流程:持有者 cmd handler 存被移除的类
        self._deregister_results: Dict[str, Any] = {}
        # handle 表:child_id → RoutineHandle(on_inbound 按 child_id 路由 lifecycle)
        self._handles: Dict[str, Any] = {}
        # created instance 按 rid(message.* 路由用;created 时注册,stopped 时 pop)
        self.created_by_rid: Dict[str, Routine] = {}
        # 实例 mailbox:rid → 串行投递队列(on_message 严格保序; 慢回调只积压
        # 本实例, 不堵 hub inbound 循环. req/pubsub 不走 mailbox ---- 独立
        # 事务/离散通知, 立即 spawn 并行处理)
        self._mailboxes: Dict[str, asyncio.Queue] = {}
        self._mailbox_tasks: Dict[str, asyncio.Task] = {}
        # req 回执:req_id → future(等 p2p req_reply 回执)
        self._req_futures: Dict[str, asyncio.Future] = {}
        # streamreq:stream_id → StreamReader
        self._stream_readers: Dict[str, Any] = {}
        # streamreq provider 侧:stream_id → 正在跑的 async-gen task
        self._provider_streams: Dict[str, asyncio.Task] = {}
        # pubsub 订阅者:rid → {(namespace, topic): handler}
        self._subscribers: Dict[str, Dict[tuple, Any]] = {}
        # lifecycle 观察者:rid → 事件 queue(实例 cleanup 自动注销).
        # fanout 非阻塞 put_nowait(慢消费者丢帧+warn,不堵 dispatch).
        self._lifecycle_watchers: Dict[str, asyncio.Queue] = {}
        # lifecycle.watch 的快照回执 future:req_id → future
        self._lifecycle_futures: Dict[str, asyncio.Future] = {}

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        def _log_exc(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception() is not None:
                self.logger.error('spawned task died: %r', t.exception(),
                                  exc_info=t.exception())

        task.add_done_callback(_log_exc)
        return task

    def print_summary(self) -> None:
        """打印一行 routine 注册统计."""
        from .routine import passive_wire
        try:
            routines = list(self.routines.get_routines())
            enabled = sum(1 for r in routines if r.enable)
            passive_n = sum(1 for r in routines if passive_wire(r)['flag'])
            self.logger.info(
                f'{len(routines)} routines · {enabled} enabled · {passive_n} passive')
        except Exception:
            self.logger.info(repr(self.routines))

    def resolve_instance(self, rid: str, cls: Type[Routine]) -> Routine:
        """同 rid 已有实例则复用(restart 语义),否则新建."""
        inst = self.running_instances.get(rid)
        if inst is not None:
            return inst
        return cls()

    # --- create future 表 ---

    def register_create_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._create_futures[req_id] = fut

    def pop_create_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._create_futures.pop(req_id, None)

    # --- acquire/release future 表 ---

    def register_acquire_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._acquire_futures[req_id] = fut

    def pop_acquire_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._acquire_futures.pop(req_id, None)

    # --- load/unload future 表 ---

    def register_load_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._load_futures[req_id] = fut

    def pop_load_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._load_futures.pop(req_id, None)

    def register_unload_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._unload_futures[req_id] = fut

    def pop_unload_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._unload_futures.pop(req_id, None)

    # --- catalog register/reload/deregister future 表 ---

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

    # --- catalog.deregister 两跳结果 ---

    def set_deregister_result(self, req_id: str, removed: Any) -> None:
        self._deregister_results[req_id] = removed

    def pop_deregister_result(self, req_id: str) -> Any:
        return self._deregister_results.pop(req_id, None)

    # --- handle 表 ---

    def register_handle(self, child_id: str, handle) -> None:
        self._handles[child_id] = handle

    def pop_handle(self, child_id: str):
        return self._handles.pop(child_id, None)

    def get_handle(self, child_id: str):
        return self._handles.get(child_id)

    def peer_down_all(self, reason: str) -> None:
        """transport 断线: kernel 世界已死, 所有 handle 一次性作废
        (调用方的 await 释放为错误; 实例销毁由 hub.handle_peer_down →
        lifecycle.stop_all_local 接管, 不留孤儿)."""
        handles = list(self._handles.values())
        self._handles.clear()
        for h in handles:
            try:
                h.notify_done(None, reason, 'ERROR')
            except Exception:
                pass  # hook 异常不阻断其余清理

    # --- created instance 按 rid ---

    def register_created(self, rid: str, instance: Routine) -> None:
        self.created_by_rid[rid] = instance

    def pop_created(self, rid: str):
        return self.created_by_rid.pop(rid, None)

    def get_created(self, rid: str):
        return self.created_by_rid.get(rid)

    # --- 实例 mailbox(串行投递)---

    def open_mailbox(self, rid: str, instance: Routine) -> None:
        """实例 mailbox 建立 + 消费者起动(created 注册时调;重建先关旧的).

        消费者逐条调 on_message, 同一实例严格串行 ---- 顺序语义由框架保
        证, 业务不再自建 reorder.慢回调只积压本实例.
        """
        self.close_mailbox(rid)
        queue: asyncio.Queue = asyncio.Queue()
        self._mailboxes[rid] = queue
        self._mailbox_tasks[rid] = self._spawn(self._mailbox_loop(rid, instance, queue))

    def close_mailbox(self, rid: str) -> None:
        """实例销毁时关 mailbox:停接收 + 取消消费者(在途回调随实例终结,
        不再出现销毁后回调仍捏着实例的僵尸 task)."""
        self._mailboxes.pop(rid, None)
        task = self._mailbox_tasks.pop(rid, None)
        if task is not None and not task.done():
            task.cancel()

    def deliver_mail(self, rid: str, source: Any, data: Any) -> None:
        """message 投递入实例 mailbox(调 on_message).无 mailbox(实例未
        created / 已销毁)静默丢弃, 对齐旧语义."""
        queue = self._mailboxes.get(rid)
        if queue is not None:
            queue.put_nowait((source, data))

    async def _mailbox_loop(self, rid: str, instance: Routine,
                            queue: asyncio.Queue) -> None:
        while True:
            source, data = await queue.get()
            t0 = time.monotonic()
            try:
                await instance.on_message(source, data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.exception(
                    f'{instance.name}#{rid} mailbox callback failed: {exc}')
            dt_ms = (time.monotonic() - t0) * 1000
            if dt_ms > _MAIL_SLOW_MS:
                self.logger.warning(
                    f'{instance.name}#{rid} slow mailbox callback: {dt_ms:.0f}ms '
                    f'(queue depth={queue.qsize()})')

    # --- req 回执 future 表 ---

    def register_req_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._req_futures[req_id] = fut

    def pop_req_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._req_futures.pop(req_id, None)

    # --- streamreq reader 表 ---

    def register_stream_reader(self, stream_id: str, reader) -> None:
        self._stream_readers[stream_id] = reader

    def pop_stream_reader(self, stream_id: str):
        return self._stream_readers.pop(stream_id, None)

    def get_stream_reader(self, stream_id: str):
        return self._stream_readers.get(stream_id)

    # --- streamreq provider task 表 ---

    def register_provider_stream(self, stream_id: str, task: asyncio.Task) -> None:
        self._provider_streams[stream_id] = task

    def pop_provider_stream(self, stream_id: str):
        return self._provider_streams.pop(stream_id, None)

    def get_provider_stream(self, stream_id: str):
        return self._provider_streams.get(stream_id)

    # --- pubsub 订阅者表 ---

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

    # --- lifecycle 观察者表 ---

    def register_lifecycle_watcher(self, rid: str, q: asyncio.Queue) -> None:
        """注册观察者(rid → queue).幂等:重复注册覆盖旧 queue."""
        self._lifecycle_watchers[rid] = q

    def pop_lifecycle_watcher(self, rid: str) -> Optional[asyncio.Queue]:
        """注销观察者.返回被注销的 queue(None = 未注册)."""
        return self._lifecycle_watchers.pop(rid, None)

    def lifecycle_watcher_count(self) -> int:
        """当前观察者数(归零时调用方发 lifecycle.unwatch)."""
        return len(self._lifecycle_watchers)

    def fanout_lifecycle(self, msg: Dict[str, Any]) -> None:
        """lifecycle 帧非阻塞投递给所有观察者.慢消费者(队列满)丢帧+warn
        ----观察者隔离,一个慢不拖别人,也不堵 dispatch."""
        for rid, q in self._lifecycle_watchers.items():
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                self.logger.warning(
                    'lifecycle watcher %s queue full, dropped frame (type=%s)',
                    rid, msg.get('type'))

    # --- lifecycle 快照 future 表 ---

    def register_lifecycle_future(self, req_id: str, fut: asyncio.Future) -> None:
        self._lifecycle_futures[req_id] = fut

    def pop_lifecycle_future(self, req_id: str) -> Optional[asyncio.Future]:
        return self._lifecycle_futures.pop(req_id, None)
