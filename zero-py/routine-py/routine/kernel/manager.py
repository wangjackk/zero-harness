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
import time
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
    TerminationReason,
    LIFECYCLE_EVENT, LIFECYCLE_UNWATCH, LIFECYCLE_WATCH, LIFECYCLE_SNAPSHOT,
    LIFECYCLE_CREATED, LIFECYCLE_DESTROY, LIFECYCLE_START, LIFECYCLE_STARTED,
    LIFECYCLE_STOP, LIFECYCLE_STOPPED, MESSAGE_REQ, MESSAGE_REQ_DELIVERED,
    MESSAGE_REQ_REPLY, MESSAGE_REQ_REPLY_DELIVERED, MESSAGE_DELIVERED,
    MESSAGE_SEND, MESSAGE_STREAM_CANCEL, MESSAGE_STREAM_CANCEL_DELIVERED,
    MESSAGE_STREAM_DATA, MESSAGE_STREAM_DATA_DELIVERED,
    MESSAGE_STREAM_OPEN, MESSAGE_STREAM_OPEN_DELIVERED, MODULE_TREE,
    PUBSUB_DELIVERED, PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE,
    REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING,
    ROUTINE_ACQUIRE, ROUTINE_ACQUIRED,
    ROUTINE_LOAD_MODULE,
    ROUTINE_MODULE_LOADED, ROUTINE_RELEASE, ROUTINE_RELEASED,
    ROUTINE_START, ROUTINE_STOP, ROUTINE_CREATE, ROUTINE_CREATED,
    ROUTINE_UNLOAD_MODULE, ROUTINE_MODULE_UNLOADED, ROUTINE_DESTROY,
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
    passive_priority: int = 10  # passive 拉起优先级(声明处即调用点; 档位同 start)
    meta: Dict[str, Any] = field(default_factory=dict)
    # 静态声明的占用 modules(catalog 注册时 ``cls.modules()`` 无参默认; 运行时
    # acquire 的动态占用不在此, 注册期拿不到).get_routines 如实回显.
    modules: List[str] = field(default_factory=list)
    owner: str = 'local'  # 提供该 routine 的 hub 身份(catalog.push 的 hub_id)


def _passive_priority(ip: Dict[str, Any]) -> int:
    """is_passive wire 的 priority 字段解析: 缺省 10(常规默认档), 负数/非法钳 0."""
    raw = ip.get('priority')
    return 10 if raw is None else max(0, _to_int(raw))


@dataclass
class KernelConn:
    """kernel 出站连接: inproc link('local')或 grpc peer(dial-in).

    hub_id: 该 conn 绑定的 hub 身份(catalog.push 登记; hub 侧自报的稳定 id).
    close: 拒绝连接时断开本 conn 的回调(仅 grpc peer 有).
    local: 同进程 hub(inproc link). 这不是优化标记, 是**关停语义的分界**: 只有
        它能在关停时"请求 + 等确认"(通道是内存队列, 回报必达, 且全局树只在这
        一侧)。跨进程 peer 的实例归对方所有, 断链即通告, 对方自清(见 _stop_node)。
    """

    id: str
    send: Any  # Callable[[Dict[str, Any]], None]
    hub_id: str = ''
    close: Any = None  # Optional[Callable[[], None]]
    local: bool = False


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
        self.stop_reason: Optional[TerminationReason] = None
        self.stop_by = 0
        # lifecycle 终止帧(stopped/destroyed)已发----一个 node 恰一帧
        self.stop_sent = False


class Kernel:
    """嵌入式内核.hub 经 InProcessLink 连入,启动后接收 catalog push 注册路由."""

    CREATED_TIMEOUT = 30.0   # 等 created 回报超时
    STOP_TIMEOUT = 10.0      # stop 后等 lifecycle.stopped 超时(放弃防挂死)
    # kernel.stop() 语境下的同一份耐心。**必须大于 hub 侧的单节点预算**
    # (lifecycle.STOP_TIMEOUT=3s: stop hook 3s + main task 3s 兜底 cancel),
    # 否则框架自己的兜底动作会被判成"超时", 每次退出都刷一串假 WARNING,
    # 把真正停不动的那个淹掉。约定内的 routine(stop 里叫醒 run)是毫秒级,
    # 这个值只是兜底上限。
    SHUTDOWN_TIMEOUT = 3.5

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
        # 后台收尾任务(fire-and-forget)的强引用: event loop 只持弱引用,
        # 不存就会在 await 中途被 GC 掉 —— 收尾半途消失比不开始更糟.
        self._tasks: Set[asyncio.Task] = set()

    # --- 生命周期 ---

    def _spawn(self, coro) -> asyncio.Task:
        """起一个后台任务并持强引用到它结束."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def bind(self, link) -> None:
        """绑定 InProcessLink(link.start 前);link 包成 'local' conn."""
        self._link = link
        self._conns['local'] = KernelConn(
            id='local', send=lambda m: link.kernel_to_hub(m), local=True)

    def register_conn(self, conn: KernelConn) -> None:
        """注册出站 conn(grpc peer dial-in 接入时调)."""
        self._conns[conn.id] = conn

    def drop_conn(self, conn_id: str) -> None:
        """conn 断开: 摘该 owner 的路由, 名下节点交给停路径收尾.

        对齐 Go kernel failPending + UnloadRemote: peer 已死, lifecycle.stop
        无处可发, 由 ``_settle_local`` 按"对端已终止"本地结算(future 解掉,
        body 自然退出, finally 里还模块)。节点收尾走 ``_stop_node`` 而不是就地
        摘除, 这样"先子后父"对跨 hub 父子同样成立(见 ``_reap_dead``)。
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
        # 节点不在这里就地摘除:交出给停路径(_stop_node), 才是"先子后父"的唯一
        # 出处。死 owner 的节点由它本地结算; 它们的子若属于别的活着 hub(跨 hub
        # 父子), 会在同一趟递归里照常收到 stop —— 直接 _remove_node 会把那类
        # 子节点变成孤儿(仍在跑, 父链已断, 无人再能找到)。
        dead = [n for n in self.nodes.values() if n.owner == owner]
        if dead:
            self._spawn(self._reap_dead(dead))
        self._push_module_view()
        log.warning("conn '%s' (hub '%s') dropped: routes failed, "
                    "%d node(s) settling", conn_id, owner, len(dead))

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
        """关停内核:级联停 root 子树, 再清残余节点. 幂等(可重入).

        只走 root 级联是不够的: 顶层 create(parent_id=0)的节点、父先走留下
        的节点都不在 root.children 里, 级联永远停不到它们。它们的 command
        body task 会活到事件循环关闭, 退出时冒
        ``Task was destroyed but it is pending!``(名字形如 ``cmd:board-3``)。
        所以级联之后必须扫一遍 nodes 表, 把残余按同一停路径清掉。

        **等与不等按宿主分**: 同进程 hub(local conn)的节点走完整请求-确认
        (通道可靠, 且全局树只在这一侧, 先子后父只有这里能编排); 跨进程 hub
        的节点只结算 kernel 自己的投影, 由对端在断链时自清(见 _stop_node)。

        退出语境的耐心比稳态短(SHUTDOWN_TIMEOUT): 用户已经按下 Ctrl+C,
        再等满 10s 的 STOP_TIMEOUT 只让他以为卡死、再按一次(把级联劈断,
        剩下节点活到循环关闭)。不确认的节点照样拆 ---- 拆完即走。
        """
        self.STOP_TIMEOUT = min(self.STOP_TIMEOUT, self.SHUTDOWN_TIMEOUT)
        root = self.nodes.get(self._root_id)
        if root is not None:
            await self._stop_node(root, cascade=False, force_by=0,
                                  during_shutdown=True)
        left = list(self.nodes.values())
        if left:
            log.warning('kernel.stop: %d node(s) not under root, stopping: %s',
                        len(left), ', '.join(str(n.cmd) for n in left))
            for n in left:
                try:
                    await self._stop_node(n, cascade=True, force_by=0,
                                          during_shutdown=True)
                except Exception:
                    log.exception('kernel.stop: settle %s failed', n.cmd)

    # --- 出入站 ---

    def _conn_of_owner(self, owner: str) -> Optional[KernelConn]:
        """owner(hub 身份或直连 conn id)的活连接; None = 该宿主已失联.

        单点解析(对齐 Go sendOut 的 target 两种取值): hub 身份先经 _hub_conns
        映射到 conn id, 已是 conn id 的直接取. 断线后 _hub_conns/_conns 均摘除,
        所以"查不到"就是"没有了"——停命令无处可发, 只能本地结算.
        """
        return self._conns.get(self._hub_conns.get(owner, owner))

    def _send_to(self, target: str, msg: Dict[str, Any]) -> None:
        """出站显式目标发送(对齐 Go sendOut: publish 到 conn 的出站通道).

        target 两种取值, 单点解析: hub 身份(Route/Node owner, 经 _hub_conns
        找 conn)或直连 conn id(回执 source). conn 不存在/已断 = 无接收方,
        丢弃(fire-and-forget 语义). 路由决策在各调用点: 回执类发 source
        (dispatch 传入), lifecycle 命令发 node owner, module.tree 广播.
        """
        conn = self._conn_of_owner(target)
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
                 'is_passive': {'flag': r.is_passive, 'kwargs': r.passive_kwargs,
                                'priority': r.passive_priority},
                 'modules': list(r.modules), 'meta': r.meta}
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

    def _on_create(self, parent_id: int, name: str,
                   kwargs: Dict[str, Any]) -> int:
        """处理 routine.create:建 created 态命令返回 child_id.passive 拒手动."""
        route = self.routes.get(name)
        if route is not None and route.is_passive:
            raise PermissionError(
                f'routine {name} is passive (auto-started by kernel); '
                f'manual create is not allowed')
        n = self._create_node(parent_id, name, kwargs)
        log.info('➡️ create %s', n.cmd)
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
            # 不变量: 节点只能经停路径("先子后父")离开 nodes 表. 有活子还留
            # 在这儿 = 有人绕过了 _stop_node/_settle_local, 子会成孤儿(仍在
            # 跑, 但父链已断, 谁都找不到它们). 记一条 error 留线索.
            if n.children:
                log.error('node %s removed with %d live child(ren); '
                          'teardown bypassed the stop path', n.cmd,
                          len(n.children))
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

        create 路径(created_sent=True)跳过 created 阶段(已发过并 resolve 过).
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
                except asyncio.TimeoutError:
                    # 谁没回就是谁在把关停时间拖长: 退出时用户只看到"转了
                    # 十秒", 没有名字就只能猜.
                    log.warning('stop %s: no lifecycle.stopped in %.1fs, '
                                'giving up (host %r did not confirm)',
                                cmd, self.STOP_TIMEOUT, n.owner)
                except asyncio.CancelledError:
                    pass
                while task.cancelling() < pending_cancels:
                    task.cancel()
                raise
        finally:
            self.tree.release(cmd.id)

    # --- stop 级联(对齐 Go shell.stop)---

    async def _stop_node(self, n: Node, *, cascade: bool, force_by: int,
                         during_shutdown: bool = False) -> None:
        """停 n:先递归级联停子,再停自己("父必须等子").

        兄弟之间没有先后要求 ---- 并行停: 每个子都在等自己的 lifecycle.stopped
        回报, 串行等于把 N 份等待相加, 退出时用户看到的就是"按了 Ctrl+C 却
        还在转", 于是一路再按(把关停劈断)。父仍然等全部子停完才动。

        宿主已失联的节点(``_conn_of_owner`` 查不到)不再下发 stop —— 命令
        无处可发, 只能按"对端已自行终止"本地结算(``_settle_local``)。递归在
        判定之前: 它的子可能属于别的活着的 hub(跨 hub 父子), 那些要照常
        下发 stop, 否则就成孤儿。

        ``during_shutdown`` 只属于 ``Kernel.stop()`` 并递归传给子节点。存活
        远程 hub 只有在该语境才直接结算投影；正常显式 stop、reload、抢占
        都必须下发 lifecycle.stop 并等待 stopped，否则远端仍在跑而父侧却
        收到 DISCONNECTED。
        """
        if cascade:
            if n.stopping:
                return
            n.stopping = True
            if force_by:
                n.stop_reason = TerminationReason.PREEMPTED
                n.stop_by = force_by

        kids = list(n.children)
        if kids:
            results = await asyncio.gather(
                *(self._stop_node(c, cascade=True, force_by=force_by,
                                  during_shutdown=during_shutdown)
                  for c in kids),
                return_exceptions=True)
            for c, r in zip(kids, results):
                if isinstance(r, BaseException):
                    log.error('stop child %s failed: %r', c.cmd, r)

        conn = self._conn_of_owner(n.owner)
        if conn is None:
            log.info('🪦 settle %s (hub %r gone)', n.cmd, n.owner)
            self._settle_local(
                n, reason=TerminationReason.DISCONNECTED,
                error=(f"routine {n.cmd.name} host disconnected: "
                       f"hub '{n.owner}' down"))
            return

        if during_shutdown and not conn.local:
            # 仅 kernel 自身关停:跨进程 hub 的实例是它的, 不是 kernel 的:
            # 它自己有一套完整的自清路径(transport 断 → hub.handle_peer_down
            # → lifecycle.stop_all_local,
            # stop hook 与 main task 照跑)。kernel 在这里只结算自己的投影
            # ——nodes 表、body task、路由、在途调用, 全在 kernel 内存里, 只有
            # kernel 能收。
            # 不发 lifecycle.stop、不等确认: 通道一会儿就随 kernel 一起关掉,
            # 等来的只会是退出日志里的假超时(而真卡住的节点会被它淹没)。
            log.info('🤝 settle %s (hub %r 是独立进程, 断链后自行收摊)',
                     n.cmd, n.owner)
            self._settle_local(
                n, reason=TerminationReason.DISCONNECTED,
                error=(f"routine {n.cmd.name} host is a separate process: "
                       f"hub '{n.owner}' cleans up on peer down"))
            # 结算只解 future, 真要等 body 退出还得等一次(它收到 stopped 才收尾)。
            # 这不是"等确认", 等的是 kernel 自己的 task 结束 —— 不等就留着
            # pending task 到循环关闭, 正是本次要消灭的那行报错。
            t0 = time.monotonic()
            await n.cmd.wait()
            self._log_slow_stop(n, t0)
            return

        if n.cmd.state == State.CREATED:
            # created 态(已 create 未 start):发 destroy 让 hub 销毁 instance
            # (先发再删: 发给 node owner, 删后 owner 仍可查----但保持先发
            # 再删的顺序与 Go sendDestroy 一致)
            log.info('🗑️ %s destroyed (created, cascade=%s)', n.cmd, cascade)
            self._emit_lifecycle_stop(
                'destroyed', n, reason=TerminationReason.DESTROYED.value)
            self._unsubscribe_rid(str(n.cmd.id))
            self._send_to(n.owner, {'event': LIFECYCLE_DESTROY,
                                    'id': str(n.cmd.id)})
            # 主动 relay stopped 给父(对齐 destroy): node 即将删除, owner
            # hub 的 destroy 回报(stopped)到达时 node 已不存在, 无法中转
            # ---- 父侧 handle(entry await)会永久挂起. relay 须在 remove
            # 之前(node 带 parent).
            self._relay_to_parent(n.cmd.id, {
                'event': LIFECYCLE_STOPPED, 'id': str(n.cmd.id),
                'reason': TerminationReason.DESTROYED.value,
            })
            self._remove_node(n.cmd.id)
            return

        if cascade:
            tag = ' (force, cascade)' if force_by else ' (cascade)'
            log.info('🛑 stop %s%s', n.cmd, tag)
        n.stopping = True
        t0 = time.monotonic()
        if n.stopped_fut.done():
            # 已回报 stopped(自然终止路径):body 在收尾,等它退出即可,
            # 不要 cancel(否则多发一次 lifecycle.stop).
            await n.cmd.wait()
        else:
            await n.cmd.stop()  # cancel body → 发 lifecycle.stop → 等 stopped → release
        self._log_slow_stop(n, t0)
        self._remove_node(n.cmd.id)

    def _log_slow_stop(self, n: Node, t0: float) -> None:
        """停得慢的点名(≥0.5s). "关停完成 (4.0s)" 若没有名字, 只能靠猜
        —— 退出耗时几乎全是某一个 routine 的 stop hook 在等. 阈值取 0.5s:
        低于它的都是调度噪音, 常驻 routine 关停本该是毫秒级."""
        spent = time.monotonic() - t0
        if spent >= 0.5:
            log.info('⌛ %s 停用了 %.1fs (host %r)', n.cmd, spent, n.owner)

    def _settle_local(self, n: Node, *, reason: TerminationReason,
                      error: str) -> None:
        """宿主失联节点的本地结算:按"对端已终止"走完正常停止的状态迁移.

        与 ``_on_lifecycle_stopped`` 同一种收尾(解 created/stopped future、
        发终止帧、中转父、摘节点、还模块), 区别只有一点:不等对端回报 ——
        对端已经不在了, 等就是白等 STOP_TIMEOUT, 子节点也跟着全卡住.

        调用前提: ``n.children`` 已全部结算(``_stop_node`` 的先子后父保证),
        否则这里摘掉父链会把子变成孤儿.
        """
        rid = n.cmd.id
        by = n.owner if reason is TerminationReason.DISCONNECTED else None
        if not n.created_fut.done():
            n.created_fut.set_result(CreatedResult(error=error))
        if not n.stopped_fut.done():
            n.stopped_fut.set_result({
                'event': LIFECYCLE_STOPPED, 'reason': reason.value,
                'error': error, 'by': by})
        self._emit_lifecycle_stop('stopped', n, reason=reason.value, by=by)
        self._unsubscribe_rid(str(rid))   # 失败该节点名下全部在途调用
        if n.parent is not None:
            # 中转必须在 remove 之前:父侧 handle 据此收尾(父可能在别的 hub)。
            self._send_to(n.parent.owner, {
                'event': LIFECYCLE_STOPPED, 'id': str(rid),
                'reason': reason.value, 'error': error, 'by': by})
        self._remove_node(rid)
        self.tree.release(rid)

    async def _reap_dead(self, nodes: List[Node]) -> None:
        """owner 断线的收尾(``drop_conn`` 的异步尾): 逐个走停路径.

        ``drop_conn`` 是同步回调(grpc stream 收尾), 而停级联要 await 活着的
        子节点回报, 所以挪到这里。节点可能已被前面的级联带走, 逐个复查。
        """
        for n in nodes:
            if self.nodes.get(n.cmd.id) is None:
                continue
            try:
                await self._stop_node(n, cascade=True, force_by=0)
            except Exception:
                log.exception('reap %s failed', n.cmd)

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
        if n.stop_reason is not None:
            msg['reason'] = n.stop_reason.value
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
        self._spawn(self._on_terminated(rid))

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

    async def _on_routine_create(self, msg: Dict[str, Any],
                                 source: str) -> None:
        """routine.create:建 created 态 → 发 lifecycle.created → 等 created 回报
        → 回 routine.created{child_id, modules} 给 create 发起方
        (source, 对齐 ts kernel onRoutineCreate)."""
        req_id = str(msg.get('req_id', ''))
        name = str(msg.get('name', ''))
        kwargs = msg.get('kwargs') or {}
        parent_id = _to_int(msg.get('parent_id')) or 0
        try:
            child_id = self._on_create(parent_id, name, kwargs)
        except Exception as exc:
            self._send_to(source, {'event': ROUTINE_CREATED,
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
                self._send_to(source, {'event': ROUTINE_CREATED,
                                       'req_id': req_id,
                                       'error': f'routine {name} created timeout'})
                return
            if r.error:
                self._send_to(source, {'event': ROUTINE_CREATED,
                                       'req_id': req_id, 'error': r.error})
            else:
                n.declared = r.modules
                self._send_to(source, {'event': ROUTINE_CREATED,
                                       'req_id': req_id,
                                       'child_id': str(child_id),
                                       'modules': r.modules})

        asyncio.create_task(_wait())

    async def _on_routine_start(self, msg: Dict[str, Any],
                                source: str) -> None:
        child_id = _to_int(msg.get('child_id')) or 0
        try_ = bool(msg.get('try'))
        raw = msg.get('priority')
        # 字段缺省 -> 10(常规默认档); 显式负数/非法钳 0(最低档)
        priority = 10 if raw is None else max(0, _to_int(raw))
        try:
            n = self._require_parent_running(child_id, 'start')
            n.priority = priority
            try:
                self._start(child_id)
            except ConflictError as ce:
                # 优先级仲裁: incoming 严格大于全部第三方 holder 才驱逐他们
                # 后重试 start;平级/更低不抢,原样拒(与旧 call 语义一致).
                # priority=0 是最低档, 不存在比它更低的 holder, 短路拒绝.
                if priority <= 0:
                    raise
                # 驱逐要等 holder 的 stopped 回报(回经本 dispatch 队列),
                # 内联 await 即自死锁 ---- 挪独立 task(对齐 acquire 驱逐分支).
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
            await self._evict_lower_holders(n, list(n.declared), priority, ce)
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
        n = self.nodes.get(child_id)
        # created 态失败是**正常**路径(被拒/装配中途报错), 但一个已经
        # STARTING/RUNNING 的节点被这条清理路径摘掉, 说明有人绕过了停路径 ——
        # 那是一行日志都没有的消失, 事后只能看着"not found"猜它是谁停的.
        if n is not None and n.cmd.state not in (State.CREATED,):
            log.warning('node %s (state=%s) removed via cleanup path, '
                        'not a stop ---- 有活实例被绕过停路径摘掉了',
                        n.cmd, n.cmd.state.value)
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

    async def _on_routine_destroy(self, msg: Dict[str, Any],
                                   source: str) -> None:
        child_id = _to_int(msg.get('child_id')) or 0
        try:
            n = self._require_node(child_id)
            if n.cmd.state != State.CREATED:
                raise RuntimeError(
                    f'cannot destroy routine {n.cmd}: already started '
                    f'(state={n.cmd.state.value}), use stop instead')
            # 先发再删: destroy 发给 node owner(对齐 Go sendDestroy)
            self._unsubscribe_rid(str(child_id))
            self._send_to(n.owner, {'event': LIFECYCLE_DESTROY,
                                    'id': str(child_id)})
            # 主动 relay stopped 给父: node 即将删除, owner hub 的 destroy
            # 回报(stopped)到达时 node 已不存在, 无法中转 ---- 父 handle 的
            # destroy ack 会永久挂起. relay 须在 remove 之前(node 带 parent).
            self._relay_to_parent(child_id, {
                'event': LIFECYCLE_STOPPED, 'id': str(child_id),
                'reason': TerminationReason.DESTROYED.value,
            })
            self._remove_node(child_id)
            log.info('↩️ destroy %s', n.cmd)
        except Exception as exc:
            self._send_to(source, {'event': ROUTINE_REJECTED, 'op': 'destroy',
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
        except ConflictError as ce:
            # 撞车走优先级裁决: 本 routine 的 start 档位(Node.priority)
            # 严格大于全部撞车 holder 才驱逐后重试; 平级/更低原样拒
            # (try 语义由调用方 catch AcquireError 自行实现).
            n = self.nodes.get(rid)
            if n is None or n.priority <= 0:
                self._send_to(source, {'event': ROUTINE_ACQUIRED,
                                       'req_id': req_id, 'ok': False,
                                       'error': self._conflict_msg(ce)})
            else:
                # 驱逐要等 holder 的 stopped 回报(回经本 dispatch 队列),
                # 内联 await 即自死锁 ---- 挪独立 task.
                asyncio.create_task(self._evict_and_acquire(
                    n, modules, n.priority, ce, req_id, source))
            return
        except Exception as exc:
            ok, error = False, str(exc)
        reply: Dict[str, Any] = {'event': ROUTINE_ACQUIRED, 'req_id': req_id,
                                 'ok': ok}
        if error:
            reply['error'] = error
        self._send_to(source, reply)

    async def _evict_and_acquire(self, n: Node, modules: List[str],
                                 priority: int, ce: ConflictError,
                                 req_id: str, source: str) -> None:
        """routine.acquire 的驱逐分支(独立 task): 驱逐低优 holder 后重试,
        结果走同一套 acquired ack 通道."""
        try:
            await self._evict_lower_holders(n, modules, priority, ce)
            self.tree.try_acquire(n.cmd.id, modules, self._ancestor_ids(n))
            n.cmd.add_modules(modules)
            log.info('⚡ acquire %s ← %s (priority=%d evicted)',
                     n.cmd, modules, priority)
            self._send_to(source, {'event': ROUTINE_ACQUIRED,
                                   'req_id': req_id, 'ok': True})
        except Exception as exc:
            err = (self._conflict_msg(exc)
                   if isinstance(exc, ConflictError) else str(exc))
            self._send_to(source, {'event': ROUTINE_ACQUIRED,
                                   'req_id': req_id, 'ok': False,
                                   'error': err})

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

    async def _evict_lower_holders(self, n: Node, modules: List[str],
                                   priority: int, ce: ConflictError) -> None:
        """优先级驱逐: cone 内第三方 holder 全部 priority 严格低于本 routine
        才逐个 cascade stop(reason=PREEMPTED)后返回;存在 >= 本优先级的 holder
        时不驱逐任何节点, 直接抛原 ConflictError(平级不抢).

        modules 是要清场的模块集(start 裁决传 declared, acquire 传目标 modules).
        优先级只裁第三方(排除自己/祖先, 与 evictable_holders 一致);
        父子级联停走树权威, 不参与比较.
        """
        ancestors = self._ancestor_ids(n)
        holders = self.tree.evictable_holders(list(modules), ancestors,
                                               n.cmd.id)
        for h in holders:
            hn = self.nodes.get(h)
            if hn is not None and hn.priority >= priority:
                raise ce   # 有 >= 平级/更高 holder: 不抢
        log.info('⚡ %s (priority=%d) evicting %d holder(s) for %s',
                 n.cmd, priority, len(holders), modules)
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
                passive_priority=_passive_priority(ip),
                meta=item.get('meta') or {},
                modules=[str(m) for m in (item.get('modules') or [])],
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
                # 声明优先级仲裁: passive 无 call-site, 声明处即调用点.
                # auto_start 本就是独立 task, 内联 await 驱逐不堵 dispatch.
                n.priority = route.passive_priority
                try:
                    self._start(n.cmd.id)
                except ConflictError as ce:
                    if route.passive_priority <= 0:
                        raise
                    await self._evict_lower_holders(
                        n, list(n.declared), route.passive_priority, ce)
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
            passive_priority=_passive_priority(ip),
            meta=msg.get('meta') or {},
            modules=[str(m) for m in (msg.get('modules') or [])],
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
            passive_priority=_passive_priority(ip),
            meta=msg.get('meta') or {},
            modules=[str(m) for m in (msg.get('modules') or [])],
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
    ROUTINE_CREATE: Kernel._on_routine_create,
    ROUTINE_START: Kernel._on_routine_start,
    ROUTINE_STOP: Kernel._on_routine_stop,
    ROUTINE_DESTROY: Kernel._on_routine_destroy,
    ROUTINE_ACQUIRE: Kernel._on_routine_acquire,
    ROUTINE_RELEASE: Kernel._on_routine_release,
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
