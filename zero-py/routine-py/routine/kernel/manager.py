"""Kernel ---- 嵌入式编排内核(移植 Go kernel/shell:shell+handler+remote+passive+broker).

与 hub 同进程同事件循环,经 InProcessLink 双向 FIFO 队列通信.职责:

  - routine 树编排: create(created 态)→ start(TryAcquire 占模块+运行)→
    stop(级联,父必须等子)→ 自然终止级联清理.
  - 模块冲突: 权威 Tree 的 cone 互斥(父子可共占,外人抢占被拒).
  - 通信 broker: message.* 按 target_id 定向转发;pubsub 订阅表 fanout;
    yield 中转回父.
  - catalog: 路由表(name → passive/meta)+ 运行时 register/reload/deregister.
  - passive 自动拉起(catalog push 后 Execute(root, name, kwargs)).

与 Go 版的差异(asyncio 化):
  - bus/conn 层退化为 link 直连 + grpc peer conn.
  - 出站对齐 Go sendOut: 显式目标 conn_id(_send_to),路由决策在各
    调用点(回执发 dispatch source / lifecycle 命令发 node owner /
    module.tree 广播),无字段推断路由.
  - future chan → asyncio.Future(dispatch resolve,幂等 if not done).
  - goroutine → asyncio.create_task(dispatch 内阻塞等待处 spawn).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .command import Command, State
from .tree import ConflictError, Tree
from ..protocol import (
    ENVELOPE_EOF, ENVELOPE_ERROR, ENVELOPE_OK, ENVELOPE_REQ_ID,
    ENVELOPE_STREAM_ID,
    CATALOG_DEREGISTER, CATALOG_DEREGISTER_CMD, CATALOG_DEREGISTER_CMD_ACK,
    CATALOG_DEREGISTERED, CATALOG_PUSH, CATALOG_PUSHED, CATALOG_REGISTER,
    CATALOG_REGISTERED, CATALOG_RELOAD, CATALOG_RELOADED,
    ControlDoneReason,
    LIFECYCLE_EVENT, LIFECYCLE_UNWATCH, LIFECYCLE_WATCH, LIFECYCLE_SNAPSHOT,
    LIFECYCLE_CREATED, LIFECYCLE_DESTROY, LIFECYCLE_START, LIFECYCLE_STARTED,
    LIFECYCLE_STOP, LIFECYCLE_STOPPED, MESSAGE_REQ, MESSAGE_REQ_DELIVERED,
    MESSAGE_REQ_REPLY, MESSAGE_REQ_REPLY_DELIVERED, MESSAGE_DELIVERED,
    MESSAGE_SEND, MESSAGE_STREAM_CANCEL, MESSAGE_STREAM_CANCEL_DELIVERED,
    MESSAGE_STREAM_DATA, MESSAGE_STREAM_DATA_DELIVERED,
    MESSAGE_STREAM_OPEN, MESSAGE_STREAM_OPEN_DELIVERED, MODULE_TREE,
    PUBSUB_DELIVERED, PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE,
    REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING,
    ROUTINE_ACQUIRE, ROUTINE_ACQUIRED, ROUTINE_FORCE_ACQUIRE,
    ROUTINE_FORCE_RELEASE, ROUTINE_LOAD_MODULE,
    ROUTINE_MODULE_LOADED, ROUTINE_RELEASE, ROUTINE_RELEASED,
    ROUTINE_START, ROUTINE_STOP, ROUTINE_SUBMIT, ROUTINE_SUBMITTED,
    ROUTINE_UNLOAD_MODULE, ROUTINE_MODULE_UNLOADED, ROUTINE_UNSUBMIT,
    ROUTINE_REJECTED, ROUTINE_YIELD, ROUTINE_YIELDED,
)

log = logging.getLogger('kernel.kernel')

# catalog 变更走独立 logger(不在 main.py 的降噪名单) ---- hub 拨入注册的
# 结果必须在 kernel 控制台可见,与 kernel.kernel 的 INFO 降噪无关.
cat_log = logging.getLogger('kernel.catalog')


@dataclass
class CreatedResult:
    modules: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class Route:
    is_passive: bool = False
    passive_kwargs: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    owner: str = 'local'  # 提供该 routine 的 hub 身份(catalog.push 的 hub_id)


@dataclass
class KernelConn:
    """kernel 出站连接: inproc link('local')或 grpc peer(dial-in).

    hub_id: 该 conn 绑定的 hub 身份(catalog.push 登记; hub 侧自报的稳定 id).
    close: 拒绝连接时断开本 conn 的回调(仅 grpc peer 有).
    """

    id: str
    send: Any  # Callable[[Dict[str, Any]], None]
    hub_id: str = ''
    close: Any = None  # Optional[Callable[[], None]]


@dataclass
class _PendingCall:
    """caller 对 target 的在途调用(message.req / message.stream_open).

    存 Kernel._pending_calls(key = kind + \\x00 + callID + \\x00 + targetID).
    target 停止 / 宿主 conn 断线 / target 不存在时,回复永不到来,kernel 据此合成
    失败帧给调用方 ---- @request/@stream 契约的错误反馈通道,与 provider 抛错
    同一种错误报告(caller 侧 req() raise ReqError / async for raise
    StreamError),不等超时.真实回执(req_reply / stream eof)回经 kernel 时摘除.
    """

    kind: str            # 'req' | 'stream'
    call_id: str         # envelope __req_id__ / __stream_id__(失败帧用)
    caller_conn: str     # 失败帧投递的 conn
    caller_rid: str      # 失败帧 target_id(调用方 routine id)
    target_rid: int      # 反向索引键(_fail_pending_calls 用)


class Node:
    """routine 树节点:Command + 父子链 + 声明模块 + lifecycle futures."""

    __slots__ = ('cmd', 'parent', 'children', 'declared', 'created_fut',
                 'started_fut', 'stopped_fut', 'created_done', 'created_sent',
                 'stopping', 'stop_reason', 'stop_by', 'owner', 'stop_sent',
                 'priority')

    def __init__(self, cmd: Command, parent: Optional['Node']):
        self.cmd = cmd
        self.parent = parent
        self.owner = 'local'  # 该 routine 实例归属的 hub 身份(事件出站路由依据)
        self.priority: int = 0  # start 时的优先级(冲突驱逐裁决用)
        self.children: List[Node] = []
        self.declared: List[str] = []
        loop = asyncio.get_running_loop()
        # cap1 future 语义:三源(回报/异常/关停)resolve 幂等,先到先得
        self.created_fut: asyncio.Future = loop.create_future()
        self.started_fut: asyncio.Future = loop.create_future()
        self.stopped_fut: asyncio.Future = loop.create_future()
        self.created_done = False
        self.created_sent = False
        self.stopping = False
        self.stop_reason = ''
        self.stop_by = 0
        # lifecycle 终止帧(stopped/destroyed)已发----一个 node 恰一帧
        self.stop_sent = False


class Kernel:
    """嵌入式内核.hub 经 InProcessLink 连入,启动后接收 catalog push 注册路由."""

    CREATED_TIMEOUT = 30.0   # 等 created 回报超时
    STOP_TIMEOUT = 10.0      # stop 后等 lifecycle.stopped 超时(放弃防挂死)

    def __init__(self, tree: Tree):
        self.tree = tree
        self.nodes: Dict[int, Node] = {}
        self.routes: Dict[str, Route] = {}
        # pubsub 订阅表: (namespace, topic) → set[subscriber_rid(str)]
        self._pubsub: Dict[tuple, Set[str]] = {}
        self._passive_started: Set[str] = set()
        self._link = None
        # 出站 conn 表: 'local'(inproc link) + grpc peer(dial-in)
        self._conns: Dict[str, KernelConn] = {}
        # hub 身份 → conn id(catalog.push 登记): owner 路由经此解析到 conn,
        # hub 断线即摘除; 重复 hub_id 的新 conn 被拒绝连接(契约见 docs/03).
        self._hub_conns: Dict[str, str] = {}
        # req_id → {name, holder, requester}: deregister 两跳流程的 pending 表
        # (cmd.ack 不带 name ---- 对齐 Go pendingDeregisters)
        self._pending_deregisters: Dict[str, Dict[str, str]] = {}
        # 在途调用跟踪(对齐 Go pendingCall): key = kind\x00callID\x00targetID.
        # target 死了(正常 stop / 宿主 conn 断线)回执永不到来,据此合成失败帧.
        self._pending_calls: Dict[str, _PendingCall] = {}
        self._calls_by_target: Dict[int, Set[str]] = {}
        # lifecycle 观察者: 开启观察流的 conn id 集合(conn 级开关)
        self._watch_conns: Set[str] = set()
        self._watch_seq: int = 0
        self._root_id = 0

    # --- 生命周期 ---

    def bind(self, link) -> None:
        """绑定 InProcessLink(link.start 前);link 包成 'local' conn."""
        self._link = link
        self._conns['local'] = KernelConn(
            id='local', send=lambda m: link.kernel_to_hub(m))

    def register_conn(self, conn: KernelConn) -> None:
        """注册出站 conn(grpc peer dial-in 接入时调)."""
        self._conns[conn.id] = conn

    def drop_conn(self, conn_id: str) -> None:
        """conn 断开: fail 该 owner 的节点 + 清路由.

        对齐 Go kernel failPending + UnloadRemote: peer 已死,lifecycle.stop
        无处可发,body 靠 resolved future 自然退出,finally 里 release 模块.
        """
        conn = self._conns.pop(conn_id, None)
        hub_id = conn.hub_id if conn is not None else ''
        if hub_id and self._hub_conns.get(hub_id) == conn_id:
            del self._hub_conns[hub_id]
        self._watch_conns.discard(conn_id)
        # 该 conn 上调用方名下的在途调用登记作废:失败帧无处投递,无人再等.
        self._pop_pending_calls_by_caller(conn_id)
        # 路由/节点 owner 是 hub 身份; 未 push 过 catalog 的违规路径退回 conn id.
        owner = hub_id or conn_id
        for name in [n for n, r in self.routes.items() if r.owner == owner]:
            del self.routes[name]
            self._passive_started.discard(name)
        for rid in list(self.nodes):
            n = self.nodes[rid]
            if n.owner != owner:
                continue
            self._unsubscribe_rid(str(rid))
            err = f"hub '{owner}' disconnected"
            if not n.created_fut.done():
                n.created_fut.set_result(CreatedResult(error=err))
            if not n.stopped_fut.done():
                n.stopped_fut.set_result(
                    {'event': 'lifecycle.stopped', 'reason': err})
            self._remove_node(rid)
            self.tree.release(rid)
            # 断线补报(对齐 Go UnloadRemote):父在其它 conn 时,合成
            # reason=DISCONNECT 的 stopped 帧发给父 conn ---- 唤醒父侧
            # handle.wait,与正常 stopped 同一种错误报告(否则父挂到超时).
            # 同 conn 的父不补(父也死了,发了也到不了;祖父会单独收帧).
            if n.parent is not None and n.parent.owner != owner:
                self._send_to(n.parent.owner, {
                    'event': LIFECYCLE_STOPPED, 'id': str(rid),
                    'reason': ControlDoneReason.DISCONNECT.value,
                    'error': (f'routine {n.cmd.name} host disconnected: '
                              f"hub '{owner}' down"),
                })
        self._push_module_view()
        log.warning("conn '%s' (hub '%s') dropped: routes failed, nodes cleaned",
                     conn_id, owner)

    async def start(self) -> None:
        """建 root(本地骨架,占根模块).catalog push 由 hub 启动后 push 进来."""
        root_name = self.tree.root_id
        cmd = Command('root', self._root_body())
        cmd.parent_id = 0
        # root 占根模块(无父,ancestors 空)
        self.tree.try_acquire(cmd.id, [root_name], set())
        cmd.set_modules([root_name])
        n = Node(cmd, None)
        self.nodes[cmd.id] = n
        self._root_id = cmd.id
        cmd.start()
        cmd.mark_running()
        log.info('kernel ready (root=%s, tree root=%s)', self._root_id, root_name)

    @property
    def root_id(self) -> int:
        return self._root_id

    async def _root_body(self) -> None:
        await asyncio.Event().wait()  # 永久挂起(kernel 关停时被 cancel)

    async def stop(self) -> None:
        """关停内核:级联停 root 子树."""
        await self._stop_node(self.nodes[self._root_id], cascade=False, force_by=0)

    # --- 出入站 ---

    def _send_to(self, target: str, msg: Dict[str, Any]) -> None:
        """出站显式目标发送(对齐 Go sendOut: publish 到 conn 的出站通道).

        target 两种取值, 单点解析: hub 身份(Route/Node owner, 经 _hub_conns
        找 conn)或直连 conn id(回执 source). conn 不存在/已断 = 无接收方,
        丢弃(fire-and-forget 语义). 路由决策在各调用点: 回执类发 source
        (dispatch 传入), lifecycle 命令发 node owner, module.tree 广播.
        """
        conn = self._conns.get(self._hub_conns.get(target, target))
        if conn is None:
            log.debug('send_to %s: conn gone, drop %r', target,
                      str(msg.get('event', '')))
            return
        conn.send(msg)

    async def dispatch(self, msg: Dict[str, Any],
                       source: str = 'local') -> None:
        """hub→kernel 事件总入口(link kernel worker 或 grpc peer 逐条调).

        source 传给每个 handler(对齐 Go dispatchEvent(connID, msg))----
        handler 内异步等待后回执仍发给正确的请求来源.
        """
        event = msg.get('event', '')
        handler = self._DISPATCH.get(event)
        if handler is not None:
            await handler(self, msg, source)
        else:
            log.debug('dispatch table-miss: %r', event)

    async def handle_req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """同步查询(transport.req 直达,零排队)."""
        event = msg.get('event', '')
        if event == REQ_GET_RUNNING:
            return {'routines': self._running_routines()}
        if event == REQ_GET_MODULE_TREE:
            return {'tree': self.tree.serialize()}
        if event == REQ_GET_ROUTINES:
            return {'routines': [
                {'name': name, 'hub_id': r.owner,
                 'is_passive': {'flag': r.is_passive, 'kwargs': r.passive_kwargs},
                 'meta': r.meta}
                for name, r in self.routes.items()]}
        return {}

    def _running_routines(self) -> List[Dict[str, str]]:
        return [{'name': n.cmd.name, 'id': str(n.cmd.id)}
                for n in self.nodes.values()
                if n.cmd.state in (State.STARTING, State.RUNNING)]

    # --- routine 树编排(对齐 Go shell.go)---

    def _create_node(self, parent_id: int, name: str,
                     kwargs: Dict[str, Any]) -> Node:
        parent = self.nodes.get(parent_id) if parent_id else None
        if parent_id and parent is None:
            raise ValueError(f'parent routine {parent_id} not found')
        route = self.routes.get(name)
        if route is None:
            raise KeyError(f'routine {name!r} not registered')
        cmd = Command(name, None)
        cmd.parent_id = parent_id
        cmd.kwargs = kwargs or {}
        n = Node(cmd, parent)
        n.owner = route.owner  # 实例归属提供方 conn(事件出站路由依据)
        self.nodes[cmd.id] = n
        if parent is not None:
            parent.children.append(n)
        self._emit_lifecycle('created', n)
        return n

    def _start(self, rid: int) -> None:
        """启动已 create 的 routine:TryAcquire(declared) + 起 body.
        冲突抛 ConflictError,错误信息带占住者 name-id."""
        n = self._require_node(rid)
        if n.declared:
            ancestors = self._ancestor_ids(n)
            self.tree.try_acquire(n.cmd.id, n.declared, ancestors)
        n.cmd.set_modules(list(n.declared))
        # body 延迟到此处注入(需要 n 闭包)
        n.cmd.attach_body(self._run_remote(n))
        n.cmd.start()

    def _on_submit(self, parent_id: int, name: str,
                   kwargs: Dict[str, Any]) -> int:
        """处理 routine.submit:建 created 态命令返回 child_id.passive 拒手动."""
        route = self.routes.get(name)
        if route is not None and route.is_passive:
            raise PermissionError(
                f'routine {name} is passive (auto-started by kernel); '
                f'manual submit is not allowed')
        n = self._create_node(parent_id, name, kwargs)
        log.info('➡️ submit %s', n.cmd)
        return n.cmd.id

    def _require_node(self, rid: int) -> Node:
        n = self.nodes.get(rid)
        if n is None:
            raise KeyError(f'routine {rid} not found')
        return n

    def _require_self_running(self, rid: int, op: str) -> Node:
        n = self._require_node(rid)
        if n.cmd.state not in (State.STARTING, State.RUNNING):
            raise RuntimeError(
                f'cannot {op}: routine {n.cmd} not started (state={n.cmd.state.value})')
        return n

    def _require_parent_running(self, child_id: int, op: str) -> Node:
        n = self._require_node(child_id)
        if n.parent is None:
            return n  # 根 routine,由 kernel 直接驱动
        if n.parent.cmd.state != State.RUNNING:
            raise RuntimeError(
                f'cannot {op} routine {n.cmd}: parent {n.parent.cmd} '
                f'not started (state={n.parent.cmd.state.value})')
        return n

    def _ancestor_ids(self, n: Node) -> Set[int]:
        out: Set[int] = set()
        p = n.parent
        while p is not None:
            out.add(p.cmd.id)
            p = p.parent
        return out

    def _remove_node(self, rid: int) -> None:
        n = self.nodes.pop(rid, None)
        if n is not None:
            # 兜底终止帧(conn 断开等 stop 路径未覆盖的删除;幂等)
            self._emit_lifecycle_stop('stopped', n)
            if n.parent is not None:
                try:
                    n.parent.children.remove(n)
                except ValueError:
                    pass

    # --- run_remote:routine body 状态机(对齐 Go remote.go)---

    async def _run_remote(self, n: Node) -> None:
        """驱动 hub 跑这条 routine:created → start → started → stopped.

        submit 路径(created_sent=True)跳过 created 阶段(已发过并 resolve 过).
        body 退出(自然/被打断)时全量 Release 该 routine 占的所有模块.
        """
        cmd = n.cmd
        rid = str(cmd.id)
        try:
            if not n.created_sent:
                self._send_created(n)
                try:
                    r = await asyncio.wait_for(
                        n.created_fut, timeout=self.CREATED_TIMEOUT)
                except asyncio.TimeoutError:
                    log.error('routine %s created timeout', cmd)
                    return
                if r.error:
                    return
                n.declared = r.modules
            # lifecycle.start(对齐 Go sendStart: 发给 node owner)
            self._send_to(n.owner, {'event': LIFECYCLE_START, 'id': rid,
                                    'name': cmd.name})
            # 等 started(先到 stopped = start 失败)
            done, _ = await asyncio.wait(
                {n.stopped_fut, n.started_fut},
                return_when=asyncio.FIRST_COMPLETED)
            if n.stopped_fut in done:
                return
            cmd.mark_running()
            # 等自然结束;被打断时发 lifecycle.stop 再等.
            # 注意必须 shield:Task.cancel() 会直接 cancel 掉 task 正 await 的
            # future 本身----裸 await stopped_fut 会让它被销毁,后续 stopped
            # 回报无处 resolve,node 被提前删,relay 丢失,父 handle 死等.
            try:
                await asyncio.shield(n.stopped_fut)
            except asyncio.CancelledError:
                self._send_stop(n)
                # 3.12+ wait_for 基于 timeouts.timeout:cancelling>0 时再进
                # timeout 上下文会立即重抛.先 uncancel 再等 stopped 回报.
                task = asyncio.current_task()
                pending_cancels = task.uncancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(n.stopped_fut), timeout=self.STOP_TIMEOUT)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                while task.cancelling() < pending_cancels:
                    task.cancel()
                raise
        finally:
            self.tree.release(cmd.id)

    # --- stop 级联(对齐 Go shell.stop)---

    async def _stop_node(self, n: Node, *, cascade: bool, force_by: int) -> None:
        """停 n:先递归级联停子,再停自己("父必须等子")."""
        if cascade:
            if n.stopping:
                return
            n.stopping = True
            if force_by:
                n.stop_reason = 'force'
                n.stop_by = force_by

        for c in list(n.children):
            await self._stop_node(c, cascade=True, force_by=force_by)

        if n.cmd.state == State.CREATED:
            # created 态(已 submit 未 start):发 destroy 让 hub 销毁 instance
            # (先发再删: 发给 node owner, 删后 owner 仍可查----但保持先发
            # 再删的顺序与 Go sendDestroy 一致)
            log.info('🗑️ %s destroyed (created, cascade=%s)', n.cmd, cascade)
            self._emit_lifecycle_stop('destroyed', n)
            self._unsubscribe_rid(str(n.cmd.id))
            self._send_to(n.owner, {'event': LIFECYCLE_DESTROY,
                                    'id': str(n.cmd.id)})
            self._remove_node(n.cmd.id)
            return

        if cascade:
            tag = ' (force, cascade)' if force_by else ' (cascade)'
            log.info('🛑 stop %s%s', n.cmd, tag)
        n.stopping = True
        if n.stopped_fut.done():
            # 已回报 stopped(自然终止路径):body 在收尾,等它退出即可,
            # 不要 cancel(否则多发一次 lifecycle.stop).
            await n.cmd.wait()
        else:
            await n.cmd.stop()  # cancel body → 发 lifecycle.stop → 等 stopped → release
        self._remove_node(n.cmd.id)

    async def _on_terminated(self, rid: int) -> None:
        """自然终止(lifecycle.stopped 且非显式 stop 接管):级联停子 + 清 node."""
        n = self.nodes.get(rid)
        if n is None or n.stopping:
            return
        n.stopping = True
        log.info('✅ %s stopped', n.cmd)
        await self._stop_node(n, cascade=False, force_by=0)

    # --- pubsub 订阅表 ---

    def _unsubscribe_rid(self, rid: str) -> None:
        for subs in self._pubsub.values():
            subs.discard(rid)
        # 该 routine 名下在途调用全部失败(对齐 Go OnRoutineStopped →
        # failPendingCalls):正常 stop 与 conn 断线卸载都经此,一处覆盖两条
        # 死路径,合成失败帧让 caller 立即抛错而不是挂到超时.
        rid_int = _to_int(rid)
        if rid_int:
            n = self.nodes.get(rid_int)
            name = n.cmd.name if n is not None else rid
            self._fail_pending_calls(rid_int, (
                f'routine {name} stopped before replying (call dropped)'))

    def _pubsub_subscribe(self, rid: str, topic: str, namespace: str) -> None:
        self._pubsub.setdefault((namespace or '', topic), set()).add(rid)

    def _pubsub_unsubscribe(self, rid: str, topic: str, namespace: str) -> None:
        subs = self._pubsub.get((namespace or '', topic))
        if subs is not None:
            subs.discard(rid)

    def _pubsub_publish(self, source: str, source_id: str, topic: str,
                        namespace: str, data: Any) -> None:
        # fanout 按 hub(node owner conn)分组: 每 conn 一条 delivered
        # (subscriber_ids 列表), wire 上 payload 只传一份; hub 侧按列表
        # 逐个路由本地 handler. 路由已由分组完成, 直发 conn.
        # node 不存在的残留 rid 归入发布方 conn(对齐 Go clientIDOfNode
        # 空串跳过的宽松版: 留在发布方本地可见).
        groups: Dict[str, List[str]] = {}
        for rid in list(self._pubsub.get((namespace or '', topic), ())):
            n = self.nodes.get(_to_int(rid))
            owner = n.owner if n is not None else source
            groups.setdefault(owner, []).append(rid)
        for owner, rids in groups.items():
            conn = self._conns.get(self._hub_conns.get(owner, owner))
            if conn is None:
                continue
            conn.send({
                'event': PUBSUB_DELIVERED, 'subscriber_ids': rids,
                'topic': topic, 'namespace': namespace or '',
                'data': data, 'source': {'id': source_id},
            })

    # --- lifecycle 出站 ---

    def _send_created(self, n: Node) -> None:
        """发 lifecycle.created 给 node owner(对齐 Go sendCreated)."""
        msg: Dict[str, Any] = {
            'event': LIFECYCLE_CREATED, 'id': str(n.cmd.id), 'name': n.cmd.name,
        }
        if n.cmd.kwargs:
            msg['kwargs'] = n.cmd.kwargs
        if n.cmd.parent_id:
            msg['parent_id'] = str(n.cmd.parent_id)
        self._send_to(n.owner, msg)

    def _send_stop(self, n: Node) -> None:
        """发 lifecycle.stop 给 node owner(对齐 Go sendStop)."""
        msg: Dict[str, Any] = {'event': LIFECYCLE_STOP, 'id': str(n.cmd.id)}
        if n.stop_reason:
            msg['reason'] = n.stop_reason
            if n.stop_by:
                msg['by'] = str(n.stop_by)
        self._send_to(n.owner, msg)

    def _relay_to_parent(self, child_id: int, msg: Dict[str, Any]) -> None:
        """把 lifecycle started/stopped / yielded 中转给父(唤醒父侧 handle).

        多 hub: 中转目标是父的 owner conn(父可能与子在不同 hub).
        """
        n = self.nodes.get(child_id)
        if n is not None and n.parent is not None:
            self._send_to(n.parent.owner, msg)

    # --- lifecycle 观察流(conn 级观察者)---
    #
    # routine 树状态变更(created/started/stopped/destroyed)旁路广播给显式
    # LIFECYCLE_WATCH 的 conn. 与业务 pubsub 分野: kernel 不感知消费者语义.
    # 单线程 asyncio: emit 与状态变更天然同序, seq 单调递增.

    def _emit_lifecycle(self, ev_type: str, n: Node, **extra: Any) -> None:
        """发一帧 lifecycle 事件给所有开启观察的 conn.无观察者零开销."""
        if not self._watch_conns:
            return
        self._watch_seq += 1
        msg: Dict[str, Any] = {
            'event': LIFECYCLE_EVENT, 'type': ev_type, 'id': str(n.cmd.id),
            'parent_id': str(n.parent.cmd.id) if n.parent else '0',
            'name': n.cmd.name, 'seq': self._watch_seq,
        }
        msg.update(extra)
        for cid in self._watch_conns:
            conn = self._conns.get(cid)
            if conn is not None:
                conn.send(dict(msg))

    def _emit_lifecycle_stop(self, ev_type: str, n: Node, **extra: Any) -> None:
        """终止帧(stopped/destroyed)幂等: 一个 node 恰一帧."""
        if n.stop_sent:
            return
        n.stop_sent = True
        self._emit_lifecycle(ev_type, n, **extra)

    async def _on_lifecycle_watch(self, msg: Dict[str, Any],
                                  source: str) -> None:
        """lifecycle.watch: 打开 conn 级观察开关(幂等)并回当前树快照."""
        conn = self._conns.get(source)
        if conn is None:
            return
        self._watch_conns.add(conn.id)
        self._watch_seq += 1
        routines = [{
            'id': str(n.cmd.id),
            'parent_id': str(n.parent.cmd.id) if n.parent else '0',
            'name': n.cmd.name,
            'state': n.cmd.state.value,
        } for n in self.nodes.values()]
        conn.send({'event': LIFECYCLE_SNAPSHOT, 'req_id': msg.get('req_id'),
                   'seq': self._watch_seq, 'routines': routines})

    async def _on_lifecycle_unwatch(self, msg: Dict[str, Any],
                                    source: str) -> None:
        """lifecycle.unwatch: 关闭 conn 级观察."""
        self._watch_conns.discard(source)

    # --- dispatch 各事件 handler ---

    async def _on_lifecycle_created(self, msg: Dict[str, Any],
                                    source: str) -> None:
        rid = _to_int(msg.get('id'))
        n = self.nodes.get(rid) if rid else None
        if n is None:
            return
        mods = [str(m) for m in (msg.get('modules') or [])]
        n.created_done = True
        if not n.created_fut.done():
            n.created_fut.set_result(CreatedResult(modules=mods))

    async def _on_lifecycle_started(self, msg: Dict[str, Any],
                                    source: str) -> None:
        rid = _to_int(msg.get('id'))
        n = self.nodes.get(rid) if rid else None
        if n is None:
            return
        self._emit_lifecycle('started', n)
        if not n.started_fut.done():
            n.started_fut.set_result(None)
        self._relay_to_parent(rid, msg)

    async def _on_lifecycle_stopped(self, msg: Dict[str, Any],
                                    source: str) -> None:
        rid = _to_int(msg.get('id'))
        n = self.nodes.get(rid) if rid else None
        log.info('📥 lifecycle.stopped #%s node=%s', rid, n is not None)
        if n is not None:
            # created 失败分流:created 还没回报过 → stopped 是 created 失败
            if not n.created_done:
                n.created_done = True
                if not n.created_fut.done():
                    n.created_fut.set_result(CreatedResult(
                        error=f'routine #{rid} stopped during created: '
                              f'reason={msg.get("reason", "")}'))
                self._emit_lifecycle_stop('stopped', n,
                                    reason=str(msg.get('reason', '')))
                self._unsubscribe_rid(str(rid))
                return
            self._emit_lifecycle_stop('stopped', n,
                                reason=str(msg.get('reason', '')))
            if not n.stopped_fut.done():
                n.stopped_fut.set_result(msg)
        self._unsubscribe_rid(str(rid))
        # relay 必须在 _on_terminated 之前----terminated 会 remove node,
        # 之后 nodes.get(rid)=None 就永远中转不回父 handle(call() 死等).
        self._relay_to_parent(rid, msg)
        # spawn 独立 task, 不堵 dispatch 队列: _on_terminated 级联停子要等
        # child 的 stopped 消息, 而那消息正排在本队列里 ---- 内联 await 即
        # 自死锁(STOP_TIMEOUT=10s 兜底解围, 表现为方向二 +10s 尾延迟).
        asyncio.create_task(self._on_terminated(rid))

    async def _on_pubsub_subscribe(self, msg: Dict[str, Any],
                                   source: str) -> None:
        self._pubsub_subscribe(str(msg.get('id', '')), str(msg.get('topic', '')),
                               str(msg.get('namespace', '')))

    async def _on_pubsub_unsubscribe(self, msg: Dict[str, Any],
                                     source: str) -> None:
        self._pubsub_unsubscribe(str(msg.get('id', '')), str(msg.get('topic', '')),
                                 str(msg.get('namespace', '')))

    async def _on_pubsub_publish(self, msg: Dict[str, Any],
                                 source: str) -> None:
        self._pubsub_publish(source, str(msg.get('source_id', '')),
                             str(msg.get('topic', '')),
                             str(msg.get('namespace', '')), msg.get('data'))

    async def _on_yield(self, msg: Dict[str, Any], source: str) -> None:
        rid = _to_int(msg.get('id'))
        if rid:
            self._relay_to_parent(rid, dict(msg, event=ROUTINE_YIELDED))

    async def _on_routine_submit(self, msg: Dict[str, Any],
                                 source: str) -> None:
        """routine.submit:建 created 态 → 发 lifecycle.created → 等 created 回报
        → 回 routine.submitted{child_id, modules} 给 submit 发起方
        (source, 对齐 Go OnSubmitSubmitted(sourceClientID, ...))."""
        req_id = str(msg.get('req_id', ''))
        name = str(msg.get('name', ''))
        kwargs = msg.get('kwargs') or {}
        parent_id = _to_int(msg.get('parent_id')) or 0
        try:
            child_id = self._on_submit(parent_id, name, kwargs)
        except Exception as exc:
            self._send_to(source, {'event': ROUTINE_SUBMITTED,
                                   'req_id': req_id, 'error': str(exc)})
            return
        n = self.nodes.get(child_id)
        n.cmd.kwargs = kwargs or {}
        n.created_sent = True
        self._send_created(n)

        async def _wait() -> None:
            try:
                r: CreatedResult = await asyncio.wait_for(
                    n.created_fut, timeout=self.CREATED_TIMEOUT)
            except asyncio.TimeoutError:
                self._send_to(source, {'event': ROUTINE_SUBMITTED,
                                       'req_id': req_id,
                                       'error': f'routine {name} created timeout'})
                return
            if r.error:
                self._send_to(source, {'event': ROUTINE_SUBMITTED,
                                       'req_id': req_id, 'error': r.error})
            else:
                n.declared = r.modules
                self._send_to(source, {'event': ROUTINE_SUBMITTED,
                                       'req_id': req_id,
                                       'child_id': str(child_id),
                                       'modules': r.modules})

        asyncio.create_task(_wait())

    async def _on_routine_start(self, msg: Dict[str, Any],
                                source: str) -> None:
        child_id = _to_int(msg.get('child_id')) or 0
        try_ = bool(msg.get('try'))
        priority = max(0, _to_int(msg.get('priority')) or 0)
        try:
            n = self._require_parent_running(child_id, 'start')
            n.priority = priority
            try:
                self._start(child_id)
            except ConflictError as ce:
                # 优先级仲裁: incoming 严格大于全部第三方 holder 才驱逐他们
                # 后重试 start;平级/更低(含 priority=0 的常规 start)不抢,
                # 原样拒(与旧 call 语义一致).
                if priority <= 0:
                    raise
                # 驱逐要等 holder 的 stopped 回报(回经本 dispatch 队列),
                # 内联 await 即自死锁 ---- 挪独立 task(对齐 force_acquire).
                asyncio.create_task(self._evict_and_start(
                    n, priority, ce, child_id, try_, source))
                return
            log.info('▶️ %s try_start' if try_ else '▶️ %s start', n.cmd)
        except ConflictError as ce:
            self._reject_start(source, child_id, try_, self._conflict_msg(ce))
        except Exception as exc:
            if not try_:
                self._cleanup_failed_start(child_id)
            self._reject_start(source, child_id, try_, str(exc))

    async def _evict_and_start(self, n: Node, priority: int, ce: ConflictError,
                               child_id: int, try_: bool, source: str) -> None:
        """routine.start 的驱逐分支(独立 task): 驱逐低优 holder 后重试 start,
        结果走同一套 reject 通道."""
        try:
            await self._evict_lower_holders(n, priority, ce)
            self._start(child_id)
            log.info('▶️ %s try_start' if try_ else '▶️ %s start', n.cmd)
        except ConflictError as exc:
            self._reject_start(source, child_id, try_, self._conflict_msg(exc))
        except Exception as exc:
            if not try_:
                self._cleanup_failed_start(child_id)
            self._reject_start(source, child_id, try_, str(exc))

    def _conflict_msg(self, ce: ConflictError) -> str:
        holders = ', '.join(
            str(self.nodes[h].cmd) if h in self.nodes else str(h)
            for h in ce.holders)
        if ce.requested == ce.blocked_by:
            return f'module {ce.requested!r} blocked by {holders}'
        return (f'module {ce.requested!r} blocked at {ce.blocked_by!r} '
                f'by {holders}')

    def _reject_start(self, source: str, child_id: int, try_: bool,
                      error: str) -> None:
        """rejected 发回请求来源(对齐 Go dispatchEvent 里 sendConn(connID, rej))---- 不能按 child_id 路由, 那会发给 routine owner 而非请求者."""
        if not try_:
            self._cleanup_failed_start(child_id)
        rej: Dict[str, Any] = {'event': ROUTINE_REJECTED, 'op': 'start',
                               'child_id': str(child_id), 'error': error}
        if try_:
            rej['try'] = True
        self._send_to(source, rej)

    def _cleanup_failed_start(self, child_id: int) -> None:
        rid = str(child_id)
        self._unsubscribe_rid(rid)
        self._remove_node(child_id)

    async def _on_routine_stop(self, msg: Dict[str, Any],
                               source: str) -> None:
        child_id = _to_int(msg.get('child_id')) or 0
        try:
            n = self._require_parent_running(child_id, 'stop')
            if n.stopping:
                return
            n.stopping = True
            log.info('🛑 stop %s', n.cmd)
            asyncio.create_task(self._stop_node(n, cascade=False, force_by=0))
        except Exception as exc:
            self._send_to(source, {'event': ROUTINE_REJECTED, 'op': 'stop',
                                   'child_id': str(child_id),
                                   'error': str(exc)})

    async def _on_routine_unsubmit(self, msg: Dict[str, Any],
                                   source: str) -> None:
        child_id = _to_int(msg.get('child_id')) or 0
        try:
            n = self._require_node(child_id)
            if n.cmd.state != State.CREATED:
                raise RuntimeError(
                    f'cannot unsubmit routine {n.cmd}: already started '
                    f'(state={n.cmd.state.value}), use stop instead')
            # 先发再删: destroy 发给 node owner(对齐 Go sendDestroy)
            self._unsubscribe_rid(str(child_id))
            self._send_to(n.owner, {'event': LIFECYCLE_DESTROY,
                                    'id': str(child_id)})
            # 主动 relay stopped 给父: node 即将删除, owner hub 的 destroy
            # 回报(stopped)到达时 node 已不存在, 无法中转 ---- 父 handle 的
            # unsubmit ack 会永久挂起. relay 须在 remove 之前(node 带 parent).
            self._relay_to_parent(child_id, {
                'event': LIFECYCLE_STOPPED, 'id': str(child_id),
                'reason': ControlDoneReason.UNSUBMITTED.value,
            })
            self._remove_node(child_id)
            log.info('↩️ unsubmit %s', n.cmd)
        except Exception as exc:
            self._send_to(source, {'event': ROUTINE_REJECTED, 'op': 'unsubmit',
                                   'child_id': str(child_id),
                                   'error': str(exc)})

    async def _on_routine_acquire(self, msg: Dict[str, Any],
                                  source: str) -> None:
        rid = _to_int(msg.get('id')) or 0
        req_id = str(msg.get('req_id', ''))
        modules = [str(m) for m in (msg.get('modules') or [])]
        try:
            n = self._require_self_running(rid, 'acquire')
            self.tree.try_acquire(rid, modules, self._ancestor_ids(n))
            n.cmd.add_modules(modules)
            ok, error = True, None
        except Exception as exc:
            ok, error = False, (
                self._conflict_msg(exc) if isinstance(exc, ConflictError) else str(exc))
        reply: Dict[str, Any] = {'event': ROUTINE_ACQUIRED, 'req_id': req_id,
                                 'ok': ok}
        if error:
            reply['error'] = error
        self._send_to(source, reply)

    async def _on_routine_release(self, msg: Dict[str, Any],
                                  source: str) -> None:
        rid = _to_int(msg.get('id')) or 0
        req_id = str(msg.get('req_id', ''))
        modules = [str(m) for m in (msg.get('modules') or [])]
        try:
            n = self._require_self_running(rid, 'release')
            self.tree.release_modules(rid, modules)
            n.cmd.remove_modules(modules)
            ok, error = True, None
        except Exception as exc:
            ok, error = False, str(exc)
        reply: Dict[str, Any] = {'event': ROUTINE_RELEASED, 'req_id': req_id,
                                 'ok': ok}
        if error:
            reply['error'] = error
        self._send_to(source, reply)

    async def _on_routine_force_acquire(self, msg: Dict[str, Any],
                                        source: str) -> None:
        rid = _to_int(msg.get('id')) or 0
        req_id = str(msg.get('req_id', ''))
        modules = [str(m) for m in (msg.get('modules') or [])]

        async def _evict_and_acquire() -> None:
            try:
                n = self._require_self_running(rid, 'force_acquire')
                await self._evict_holders(n, modules, rid)
                self.tree.try_acquire(rid, modules, self._ancestor_ids(n))
                n.cmd.add_modules(modules)
                log.info('⚡ force_acquire %s ← %s', n.cmd, modules)
                self._send_to(source, {'event': ROUTINE_ACQUIRED,
                                       'req_id': req_id, 'ok': True})
            except Exception as exc:
                err = (self._conflict_msg(exc)
                       if isinstance(exc, ConflictError) else str(exc))
                self._send_to(source, {'event': ROUTINE_ACQUIRED,
                                       'req_id': req_id, 'ok': False,
                                       'error': err})

        asyncio.create_task(_evict_and_acquire())

    async def _on_routine_force_release(self, msg: Dict[str, Any],
                                        source: str) -> None:
        rid = _to_int(msg.get('id')) or 0
        req_id = str(msg.get('req_id', ''))
        modules = [str(m) for m in (msg.get('modules') or [])]

        async def _evict() -> None:
            try:
                n = self._require_self_running(rid, 'force_release')
                count = await self._evict_holders(n, modules, rid)
                log.info('⚡ force_release %s ← %s (evicted %d, not acquired)',
                         n.cmd, modules, count)
                self._send_to(source, {'event': ROUTINE_RELEASED,
                                       'req_id': req_id, 'ok': True})
            except Exception as exc:
                self._send_to(source, {'event': ROUTINE_RELEASED,
                                       'req_id': req_id, 'ok': False,
                                       'error': str(exc)})

        asyncio.create_task(_evict())

    async def _evict_holders(self, n: Node, modules: List[str],
                             rid: int) -> int:
        """驱逐 cone 内第三方 holder(cascade stop,reason=force).返回驱逐数."""
        ancestors = self._ancestor_ids(n)
        holders = self.tree.evictable_holders(modules, ancestors, rid)
        for h in holders:
            hn = self.nodes.get(h)
            if hn is None:
                continue
            log.info('🛑 stop %s (force, evicting for %s)', hn.cmd, n.cmd)
            await self._stop_node(hn, cascade=True, force_by=rid)
        return len(holders)

    async def _evict_lower_holders(self, n: Node, priority: int,
                                   ce: ConflictError) -> None:
        """优先级驱逐: cone 内第三方 holder 全部 priority 严格低于本 start
        才逐个 cascade stop(reason='force')后返回;存在 >= 本优先级的 holder
        时不驱逐任何节点, 直接抛原 ConflictError(平级不抢).

        优先级只裁第三方(排除自己/祖先, 与 evictable_holders 一致);
        父子级联停走树权威, 不参与比较.
        """
        ancestors = self._ancestor_ids(n)
        holders = self.tree.evictable_holders(list(n.declared), ancestors,
                                              n.cmd.id)
        for h in holders:
            hn = self.nodes.get(h)
            if hn is not None and hn.priority >= priority:
                raise ce   # 有 >= 平级/更高 holder: 不抢
        log.info('⚡ start %s (priority=%d) evicting %d holder(s)',
                 n.cmd, priority, len(holders))
        for h in holders:
            hn = self.nodes.get(h)
            if hn is None:
                continue
            await self._stop_node(hn, cascade=True, force_by=n.cmd.id)

    async def _on_routine_load_module(self, msg: Dict[str, Any],
                                      source: str) -> None:
        req_id = str(msg.get('req_id', ''))
        try:
            self.tree.load_module(str(msg.get('parent_id', '')),
                                  str(msg.get('child_id', '')),
                                  str(msg.get('name', '')))
            ok, error = True, None
        except Exception as exc:
            ok, error = False, str(exc)
        reply: Dict[str, Any] = {'event': ROUTINE_MODULE_LOADED, 'req_id': req_id,
                                 'ok': ok}
        if error:
            reply['error'] = error
        self._send_to(source, reply)
        if ok:
            self._push_module_view()

    async def _on_routine_unload_module(self, msg: Dict[str, Any],
                                        source: str) -> None:
        req_id = str(msg.get('req_id', ''))
        try:
            self.tree.unload_module(str(msg.get('child_id', '')))
            ok, error = True, None
        except Exception as exc:
            ok, error = False, str(exc)
        reply: Dict[str, Any] = {'event': ROUTINE_MODULE_UNLOADED,
                                 'req_id': req_id, 'ok': ok}
        if error:
            reply['error'] = error
        self._send_to(source, reply)
        if ok:
            self._push_module_view()

    def _push_module_view(self) -> None:
        """把当前模块树拓扑推给所有 conn(对齐 Go pushModuleView 广播)."""
        msg: Dict[str, Any] = {'event': MODULE_TREE, 'tree': self.tree.serialize()}
        for conn in self._conns.values():
            conn.send(dict(msg))

    # --- message.* broker(dumb forward by target_id + 在途调用跟踪)---

    async def _on_message(self, msg: Dict[str, Any], source: str) -> None:
        event = msg.get('event', '')
        delivered = {
            MESSAGE_SEND: MESSAGE_DELIVERED,
            MESSAGE_REQ: MESSAGE_REQ_DELIVERED,
            MESSAGE_REQ_REPLY: MESSAGE_REQ_REPLY_DELIVERED,
            MESSAGE_STREAM_OPEN: MESSAGE_STREAM_OPEN_DELIVERED,
            MESSAGE_STREAM_DATA: MESSAGE_STREAM_DATA_DELIVERED,
            MESSAGE_STREAM_CANCEL: MESSAGE_STREAM_CANCEL_DELIVERED,
        }.get(event)
        if delivered is None:
            return
        data = msg.get('data')
        # 真实回执回经 kernel:摘除在途登记,闭环(失败帧不再补).
        if delivered == MESSAGE_REQ_REPLY_DELIVERED:
            self._pop_pending_calls('req', _envelope_call_id('req', data))
        elif delivered == MESSAGE_STREAM_DATA_DELIVERED and _envelope_eof(data):
            self._pop_pending_calls('stream', _envelope_call_id('stream', data))
        source_id = str(msg.get('source_id', ''))
        # 按 target 查 node owner 定向转发(对齐 Go OnMessage 的 clientIDOfNode).
        # 例外是在途调用跟踪(见 _PendingCall):target 不存在(stopped/断线
        # 卸载/非法 id)静默丢弃会让 caller 挂到超时,改为立即合成失败帧.
        for t in (msg.get('target_ids') or []):
            t = str(t)
            n = self.nodes.get(_to_int(t))
            if n is None:
                not_found = f'routine {t} not found (stopped or invalid target)'
                if delivered == MESSAGE_REQ_DELIVERED:
                    self._reply_call_error('req', source_id, data, not_found)
                elif delivered == MESSAGE_STREAM_OPEN_DELIVERED:
                    self._reply_call_error('stream', source_id, data, not_found)
                continue
            if delivered == MESSAGE_REQ_DELIVERED:
                self._add_pending_call('req', source_id, t, data)
            elif delivered == MESSAGE_STREAM_OPEN_DELIVERED:
                self._add_pending_call('stream', source_id, t, data)
            self._send_to(n.owner, {
                'event': delivered, 'target_id': t,
                'data': data, 'source': {'id': source_id},
            })

    # --- 在途调用跟踪(对齐 Go broker.go pendingCall 段)---

    def _conn_of_node(self, rid: str) -> Optional[str]:
        """按 routine id 查其归属 conn id(对齐 Go clientIDOfNode)."""
        n = self.nodes.get(_to_int(rid))
        return n.owner if n is not None else None

    def _add_pending_call(self, kind: str, source_id: str, target_id: str,
                          data: Any) -> None:
        """登记一条在途调用:target 死了回执永不到来,失败时据此合成失败帧."""
        call_id = _envelope_call_id(kind, data)
        if not call_id:
            return
        caller_conn = self._conn_of_node(source_id)
        if caller_conn is None:
            return  # 调用方自身已不在(断线竞态):无人等回执
        rid = _to_int(target_id)
        if not rid:
            return
        key = f'{kind}\x00{call_id}\x00{target_id}'
        self._pending_calls[key] = _PendingCall(
            kind=kind, call_id=call_id, caller_conn=caller_conn,
            caller_rid=source_id, target_rid=rid)
        self._calls_by_target.setdefault(rid, set()).add(key)

    def _pop_pending_calls(self, kind: str, call_id: str) -> None:
        """摘除该 kind + callID 的全部在途登记(真实回执已投递,闭环)."""
        if not call_id:
            return
        prefix = f'{kind}\x00{call_id}\x00'
        for key in [k for k in self._pending_calls if k.startswith(prefix)]:
            c = self._pending_calls.pop(key)
            s = self._calls_by_target.get(c.target_rid)
            if s is not None:
                s.discard(key)
                if not s:
                    del self._calls_by_target[c.target_rid]

    def _pop_pending_calls_by_caller(self, conn_id: str) -> None:
        """摘除指定 conn 上调用方名下的全部在途登记(caller 断线:失败帧
        无处投递,无人再等).drop_conn 用."""
        for key in [k for k, c in self._pending_calls.items()
                    if c.caller_conn == conn_id]:
            c = self._pending_calls.pop(key)
            s = self._calls_by_target.get(c.target_rid)
            if s is not None:
                s.discard(key)
                if not s:
                    del self._calls_by_target[c.target_rid]

    def _fail_pending_calls(self, rid: int, err_msg: str) -> None:
        """失败 target rid 名下所有在途调用:按 kind 合成失败帧发给各 caller ----
        与 provider 抛错同一种错误报告(req: caller 侧 req() raise ReqError;
        stream: async for raise StreamError).真实回执先到则登记已摘,不会双报;
        两者竞态时先到先得,后到的 caller 侧按 callID 配对自然丢弃."""
        s = self._calls_by_target.pop(rid, None)
        if not s:
            return
        dead = [self._pending_calls.pop(key) for key in s
                if key in self._pending_calls]
        for c in dead:
            if c.kind == 'req':
                frame = {
                    'event': MESSAGE_REQ_REPLY_DELIVERED,
                    'target_id': c.caller_rid,
                    'source': {'id': str(rid)},
                    'data': {ENVELOPE_REQ_ID: c.call_id,
                             ENVELOPE_OK: False, ENVELOPE_ERROR: err_msg},
                }
            else:
                frame = {
                    'event': MESSAGE_STREAM_DATA_DELIVERED,
                    'target_id': c.caller_rid,
                    'source': {'id': str(rid)},
                    'data': {ENVELOPE_STREAM_ID: c.call_id,
                             ENVELOPE_EOF: 'error', ENVELOPE_ERROR: err_msg},
                }
            self._send_to(c.caller_conn, frame)

    def _reply_call_error(self, kind: str, source_id: str, data: Any,
                          err_msg: str) -> None:
        """合成失败帧发给 sourceID(调用方 routine):target 不存在时用(转发即
        失败,不等 target 死).req → ok=false 回执;stream → eof=error 数据帧.
        找不到调用方 conn 则丢弃(无人等)."""
        call_id = _envelope_call_id(kind, data)
        if not call_id:
            return
        caller_conn = self._conn_of_node(source_id)
        if caller_conn is None:
            return
        if kind == 'req':
            frame = {
                'event': MESSAGE_REQ_REPLY_DELIVERED,
                'target_id': source_id,
                'data': {ENVELOPE_REQ_ID: call_id,
                         ENVELOPE_OK: False, ENVELOPE_ERROR: err_msg},
            }
        else:
            frame = {
                'event': MESSAGE_STREAM_DATA_DELIVERED,
                'target_id': source_id,
                'data': {ENVELOPE_STREAM_ID: call_id,
                         ENVELOPE_EOF: 'error', ENVELOPE_ERROR: err_msg},
            }
        self._send_to(caller_conn, frame)

    # --- catalog ---

    async def _on_catalog_push(self, msg: Dict[str, Any],
                               source: str) -> None:
        """hub 启动后主动 push catalog:登记 hub 身份 + 注册路由表 + 回执.

        hub_id 是 hub 侧自报的进程级稳定身份(契约见 docs/03-registration.md):
        kernel 校验唯一性, 重复(已有别的 conn 用同一 hub_id)则拒绝连接;
        owner 记 hub_id ---- 身份跨重连稳定, list_routines 如实展示.
        """
        req_id = str(msg.get('req_id', ''))
        hub_id = str(msg.get('hub_id', ''))
        if not hub_id:
            self._send_to(source, {'event': CATALOG_PUSHED, 'req_id': req_id,
                                   'registered': [], 'skipped': [],
                                   'error': 'hub_id is required'})
            return
        prev = self._hub_conns.get(hub_id)
        if prev is not None and prev != source:
            # 重复身份: 回执错误 + 断开本 conn(拒绝连接).
            self._send_to(source, {'event': CATALOG_PUSHED, 'req_id': req_id,
                                   'registered': [], 'skipped': [],
                                   'error': f'hub_id {hub_id!r} already connected'})
            conn = self._conns.get(source)
            if conn is not None and conn.close is not None:
                conn.close()
            log.warning("peer %s rejected: hub_id %r already owned by conn %s",
                        source, hub_id, prev)
            return
        # (重)登记: 首连, 或同 conn 重推(hot reload 后 re-push)
        self._hub_conns[hub_id] = source
        conn = self._conns.get(source)
        if conn is not None:
            conn.hub_id = hub_id
        registered: List[str] = []
        skipped: List[str] = []
        for item in (msg.get('routines') or []):
            name = str(item.get('name', ''))
            if not name or name in self.routes:
                if name:
                    skipped.append(name)
                continue
            ip = item.get('is_passive') or {}
            self.routes[name] = Route(
                is_passive=bool(ip.get('flag')),
                passive_kwargs=ip.get('kwargs') or {},
                meta=item.get('meta') or {},
                owner=hub_id)
            registered.append(name)
        self._send_to(source, {'event': CATALOG_PUSHED, 'req_id': req_id,
                               'registered': registered, 'skipped': skipped})
        self._push_module_view()
        cat_log.info('catalog.pushed from %s (hub_id=%s): registered=%s%s',
                     source, hub_id, registered or '[]',
                     f' skipped={skipped}' if skipped else '')
        # passive 自动拉起
        passives = [(name, r) for name, r in self.routes.items() if r.is_passive]
        if passives:
            asyncio.create_task(self._auto_start_passive(passives))

    async def _auto_start_passive(self, passives: List[tuple]) -> None:
        for name, route in passives:
            key = name  # 单 hub,client 维度退化
            if key in self._passive_started:
                continue
            self._passive_started.add(key)
            try:
                n = self._create_node(self._root_id, name, dict(route.passive_kwargs))
                self._send_created(n)
                n.created_sent = True
                try:
                    r: CreatedResult = await asyncio.wait_for(
                        n.created_fut, timeout=self.CREATED_TIMEOUT)
                except asyncio.TimeoutError:
                    raise TimeoutError(f'routine {name} created timeout')
                if r.error:
                    raise RuntimeError(r.error)
                n.declared = r.modules
                self._start(n.cmd.id)
            except Exception as exc:
                self._passive_started.discard(key)
                self._remove_node_by_name(name)
                log.error('passive %s auto-start failed: %s', name, exc)
                continue
            log.info('auto-start passive routine: %s', name)

    def _remove_node_by_name(self, name: str) -> None:
        for rid in list(self.nodes):
            n = self.nodes[rid]
            if n.cmd.name == name:
                self._cleanup_failed_start(rid)

    def _hub_of(self, conn_id: str) -> str:
        """conn 绑定的 hub 身份(catalog.push 登记).

        未 push 过 catalog 就 register/reload 的违规路径退回 conn id ----
        路由仍可用, 但身份不稳定(契约上 push 必须先行).
        """
        conn = self._conns.get(conn_id)
        return conn.hub_id if conn is not None and conn.hub_id else conn_id

    async def _on_catalog_register(self, msg: Dict[str, Any],
                                   source: str) -> None:
        req_id = str(msg.get('req_id', ''))
        name = str(msg.get('name', ''))
        ip = msg.get('is_passive') or {}
        if not name or name in self.routes:
            self._send_to(source, {'event': CATALOG_REGISTERED,
                                   'req_id': req_id, 'ok': False,
                                   'error': f'routine {name!r} already registered'})
            return
        self.routes[name] = Route(
            is_passive=bool(ip.get('flag')),
            passive_kwargs=ip.get('kwargs') or {},
            meta=msg.get('meta') or {},
            owner=self._hub_of(source))
        # 回执必须先于 auto-start 的 lifecycle.created 发出(对齐 Go
        # handleCatalogRegister:先 sendConn 再 go AutoStartPassive).
        self._send_to(source, {'event': CATALOG_REGISTERED, 'req_id': req_id,
                               'ok': True})
        if ip.get('flag'):
            asyncio.create_task(self._auto_start_passive(
                [(name, self.routes[name])]))

    async def _on_catalog_reload(self, msg: Dict[str, Any],
                                 source: str) -> None:
        req_id = str(msg.get('req_id', ''))
        name = str(msg.get('name', ''))
        ip = msg.get('is_passive') or {}
        if not name:
            self._send_to(source, {'event': CATALOG_RELOADED, 'req_id': req_id,
                                   'ok': False, 'error': 'name is required'})
            return
        # 热替换语义(不区分 passive/动态): 覆盖路由 + 停掉该 name 所有运行中
        # 老实例(级联停子树). 老代码实例承载的闭包/连接在旧模块里, 留着跑完
        # 语义上已不是"这个 routine"; passive 额外等停完再 auto-start 新实例.
        self._passive_started.discard(name)
        self.routes[name] = Route(
            is_passive=bool(ip.get('flag')),
            passive_kwargs=ip.get('kwargs') or {},
            meta=msg.get('meta') or {},
            owner=self._hub_of(source))
        # 回执先于 auto-start 的 created(同 register 语义).
        self._send_to(source, {'event': CATALOG_RELOADED, 'req_id': req_id,
                               'ok': True})
        # 先停老实例, 等它 stopped(节点移除)后再起新实例.
        # 必须串行 ---- 并发的话新节点 created 先进树, 老节点还在 running,
        # 树上短暂出现两个同名节点(lifecycle 观察流如实转发这个中间态).
        async def _restart() -> None:
            await self._stop_running_by_name(name)
            if ip.get('flag'):
                await self._auto_start_passive([(name, self.routes[name])])
        asyncio.create_task(_restart())

    async def _on_catalog_deregister(self, msg: Dict[str, Any],
                                     source: str) -> None:
        """两跳流程的 kernel 侧(对齐 Go handleCatalogDeregister):
        发 cmd 给持有者, 记 pending(req_id → name/holder/requester)----
        cmd.ack 不带 name, 删路由与回执请求者都靠 pending 查回.
        """
        req_id = str(msg.get('req_id', ''))
        name = str(msg.get('name', ''))
        if name not in self.routes:
            self._send_to(source, {'event': CATALOG_DEREGISTERED,
                                   'req_id': req_id, 'ok': False,
                                   'error': f'routine {name!r} not in routes'})
            return
        holder = self.routes[name].owner
        if req_id:
            self._pending_deregisters[req_id] = {'name': name,
                                                 'holder': holder,
                                                 'requester': source}
        # cmd 发给持有者(对齐 Go sendConn(holderConnID, cmd);
        # in-proc 单 hub 时持有者就是请求者自己)
        self._send_to(holder, {'event': CATALOG_DEREGISTER_CMD,
                               'req_id': req_id, 'name': name})

    async def _on_catalog_deregister_cmd_ack(self, msg: Dict[str, Any],
                                             source: str) -> None:
        req_id = str(msg.get('req_id', ''))
        ok = bool(msg.get('ok'))
        pend = self._pending_deregisters.pop(req_id, None)
        if pend is None:
            # 超时 / 重复 ack / 请求者已断开 → 丢弃
            log.warning('catalog.deregister.cmd.ack req_id=%s no pending',
                        req_id)
            return
        # 校验 ack 来自持有者(防伪造). source 是 conn id, holder 是 hub
        # 身份(hub_id) ---- 两个范畴, 比较前先把 source 归一到 hub 身份
        # (in-proc: conn 'local' → hub 'zero'; grpc: conn 's2' → hub 'zero-ts').
        if self._hub_of(source) != pend['holder']:
            log.warning('catalog.deregister.cmd.ack req_id=%s from %s, '
                        'expected holder %s', req_id, source,
                        pend['holder'])
            return
        name = pend['name']
        if ok:
            self._passive_started.discard(name)
            asyncio.create_task(self._stop_running_by_name(name))
            self.routes.pop(name, None)
            log.info('➖ catalog.deregister %s (holder=%s)', name,
                     pend['holder'])
        self._send_to(pend['requester'], {
            'event': CATALOG_DEREGISTERED, 'req_id': req_id, 'ok': ok,
            **({} if ok else {'error': str(msg.get('error', 'deregister failed'))})})

    async def _stop_running_by_name(self, name: str) -> None:
        for rid in list(self.nodes):
            n = self.nodes.get(rid)
            if n is not None and n.cmd.name == name and not n.stopping:
                n.stopping = True
                await self._stop_node(n, cascade=False, force_by=0)

    # --- dispatch 表 ---

    _DISPATCH = {}  # 类尾填充


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _envelope_call_id(kind: str, data: Any) -> str:
    """从 data envelope 里取该 kind 的调用 id(__req_id__ / __stream_id__).

    非 envelope / 缺失返回空串 ---- 不影响 forward,只是不跟踪.
    """
    env = data if isinstance(data, dict) else None
    if env is None:
        return ''
    key = ENVELOPE_REQ_ID if kind == 'req' else ENVELOPE_STREAM_ID
    v = env.get(key)
    return v if isinstance(v, str) else ''


def _envelope_eof(data: Any) -> str:
    """从 stream 的 data envelope 里取 __eof__(done/error/cancelled,
    空串 = 普通数据帧)."""
    env = data if isinstance(data, dict) else None
    if env is None:
        return ''
    v = env.get(ENVELOPE_EOF)
    return v if isinstance(v, str) else ''


# 填充 dispatch 表(event → handler)
Kernel._DISPATCH = {
    LIFECYCLE_CREATED: Kernel._on_lifecycle_created,
    LIFECYCLE_STARTED: Kernel._on_lifecycle_started,
    LIFECYCLE_STOPPED: Kernel._on_lifecycle_stopped,
    LIFECYCLE_WATCH: Kernel._on_lifecycle_watch,
    LIFECYCLE_UNWATCH: Kernel._on_lifecycle_unwatch,
    PUBSUB_SUBSCRIBE: Kernel._on_pubsub_subscribe,
    PUBSUB_UNSUBSCRIBE: Kernel._on_pubsub_unsubscribe,
    PUBSUB_PUBLISH: Kernel._on_pubsub_publish,
    ROUTINE_YIELD: Kernel._on_yield,
    ROUTINE_SUBMIT: Kernel._on_routine_submit,
    ROUTINE_START: Kernel._on_routine_start,
    ROUTINE_STOP: Kernel._on_routine_stop,
    ROUTINE_UNSUBMIT: Kernel._on_routine_unsubmit,
    ROUTINE_ACQUIRE: Kernel._on_routine_acquire,
    ROUTINE_RELEASE: Kernel._on_routine_release,
    ROUTINE_FORCE_ACQUIRE: Kernel._on_routine_force_acquire,
    ROUTINE_FORCE_RELEASE: Kernel._on_routine_force_release,
    ROUTINE_LOAD_MODULE: Kernel._on_routine_load_module,
    ROUTINE_UNLOAD_MODULE: Kernel._on_routine_unload_module,
    MESSAGE_SEND: Kernel._on_message,
    MESSAGE_REQ: Kernel._on_message,
    MESSAGE_REQ_REPLY: Kernel._on_message,
    MESSAGE_STREAM_OPEN: Kernel._on_message,
    MESSAGE_STREAM_DATA: Kernel._on_message,
    MESSAGE_STREAM_CANCEL: Kernel._on_message,
    CATALOG_PUSH: Kernel._on_catalog_push,
    CATALOG_REGISTER: Kernel._on_catalog_register,
    CATALOG_RELOAD: Kernel._on_catalog_reload,
    CATALOG_DEREGISTER: Kernel._on_catalog_deregister,
    CATALOG_DEREGISTER_CMD_ACK: Kernel._on_catalog_deregister_cmd_ack,
}
