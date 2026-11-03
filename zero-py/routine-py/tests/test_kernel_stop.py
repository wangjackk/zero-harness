"""kernel.stop 的两条判据: 投影必须收干净, 跨进程不许越权.

运行: ``python tests/test_kernel_stop.py``

背景(为什么要有这个 case): ``kernel.stop()`` 只走 root 级联。顶层 create
(``parent_id=0``)的节点、父先走留下的节点都不在 ``root.children`` 里, 级联
永远停不到 —— 它们的 command body task 活到事件循环关闭, 退出时冒

    ERROR asyncio - Task was destroyed but it is pending!
    task: <Task pending name='cmd:asr-4' ...>

线上看到的就是这个形态(``cmd:board-3`` / ``cmd:asr-4``), 两个名字正是
"没被停到的节点", 不是"没来得及停的节点"。

钉住的语义:
1. root 子树之外的节点, ``kernel.stop()`` 后同样从 nodes 表消失;
2. 它们的 cmd task 必须结束(不留 pending task 给事件循环收尾);
3. ``stop()`` 幂等 ---- 关停半路被打断后重入不能炸;
4. **kernel.stop 按宿主分**: 同进程 hub(local conn)收到 lifecycle.stop 并等
   确认; 跨进程 hub 的实例归对方, kernel 只结算自己的投影, 不发 stop、不等
   确认(对端在断链时自清, 见 hub.handle_peer_down);
5. **正常显式 stop 不按宿主分**: 只要远程 owner 还活着, 就必须发
   lifecycle.stop 并等 STOPPED, 不能误用 kernel 关停的 DISCONNECTED 结算。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routine import kernel
from routine.kernel.grpc_server import GrpcKernelServer
from routine.kernel.manager import KernelConn, Route
from routine.logger import ColoredFormatter
from routine.protocol import LIFECYCLE_STOP, ROUTINE_STOP


async def _wait(pred, *, what: str, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f'timeout waiting for {what}')


async def _boot(k, conn: KernelConn, names) -> list:
    """在 conn 的 hub 名下起几个 running 节点, 返回[(pid, cmd_task)]."""
    k._conns[conn.id] = conn
    if conn.hub_id:
        k._hub_conns[conn.hub_id] = conn.id
    owner = conn.hub_id or conn.id
    ids = []
    for name in names:
        k.routes[name] = Route(owner=owner)
    under = k._on_create(k._root_id, names[0], {})
    top = k._on_create(0, names[1], {})       # 顶层: 父链不在 root 子树
    for pid in (under, top):
        k._start(pid)
        await k._on_lifecycle_created({'id': str(pid)}, conn.id)
        await k._on_lifecycle_started({'id': str(pid)}, conn.id)
    await _wait(lambda: all(k.nodes[p].cmd.state.value == 'running'
                            for p in (under, top)), what=f'{names} running')
    assert k.nodes[top].parent is None, '顶层节点不该有父'
    return [(under, k.nodes[under].cmd._task), (top, k.nodes[top].cmd._task)]


async def case_peer_hub() -> None:
    """kernel.stop 对跨进程 hub: 只结算投影, 不等对端确认。"""
    kref = await kernel.create({'root': {}})
    k = kref.kernel
    k.STOP_TIMEOUT = 0.2          # 万一真等了, 别让测试卡满 10s

    sent: list = []
    conn = KernelConn(id='sA', send=sent.append, hub_id='hubA')   # local=False
    ids = await _boot(k, conn, ('hold_root', 'hold_top'))
    top_task = dict(ids)[ids[-1][0]]

    await k.stop()

    assert not k.nodes, f'nodes 表该清空, 残留 {list(k.nodes)}'
    assert top_task.done(), '节点摘了但 cmd task 还活着 = 退出时的 pending task'
    stops = [m for m in sent if m.get('event') == LIFECYCLE_STOP]
    assert not stops, (
        '对独立 hub 的实例不该发 lifecycle.stop: 那是它的东西, '
        f'它自己在断链时收摊; 发了只会白等确认。实际发了 {stops}')
    await k.stop()               # 幂等
    print('peer hub shutdown: PASS')


async def case_peer_explicit_stop() -> None:
    """正常 handle.stop 对存活远程 owner 仍是请求-确认,不是断连结算。"""
    kref = await kernel.create({'root': {}})
    k = kref.kernel
    k.STOP_TIMEOUT = 0.2
    sent: list = []
    conn = KernelConn(id='sA', send=sent.append, hub_id='hubA')
    k._conns[conn.id] = conn
    k._hub_conns[conn.hub_id] = conn.id
    k.routes['hold'] = Route(owner='hubA')
    pid = k._on_create(0, 'hold', {})
    n = k.nodes[pid]
    k._start(pid)
    await k._on_lifecycle_created({'id': str(pid)}, conn.id)
    await k._on_lifecycle_started({'id': str(pid)}, conn.id)
    await _wait(lambda: n.cmd.state.value == 'running', what='remote hold running')

    await k.dispatch({'event': ROUTINE_STOP, 'child_id': str(pid)}, conn.id)
    await _wait(lambda: any(m.get('event') == LIFECYCLE_STOP for m in sent),
                what='lifecycle.stop sent for explicit remote stop')
    assert pid in k.nodes, '远程 owner 回报 stopped 前不该删除投影'
    assert not n.stopped_fut.done(), '远程 owner 回报前不该本地结算'

    await k._on_lifecycle_stopped(
        {'event': 'lifecycle.stopped', 'id': str(pid), 'reason': 'STOPPED'},
        conn.id)
    await _wait(lambda: pid not in k.nodes, what='remote stopped projection removed')
    assert n.stopped_fut.result()['reason'] == 'STOPPED', n.stopped_fut.result()
    assert not n.stopped_fut.result().get('error'), n.stopped_fut.result()
    await k.stop()
    print('peer explicit stop: PASS')


async def case_local_hub() -> None:
    """同进程 hub: 发 lifecycle.stop, 等 stopped 回报后才摘投影。"""
    kref = await kernel.create({'root': {}})
    k = kref.kernel
    k.STOP_TIMEOUT = 5.0

    sent: list = []
    conn = KernelConn(id='local', send=sent.append, hub_id='hubL', local=True)
    k._conns[conn.id] = conn
    k._hub_conns['hubL'] = conn.id
    k.routes['n1'] = Route(owner='hubL')
    pid = k._on_create(0, 'n1', {})     # 顶层: root 无子, 关停时 root 自己秒过
    k._start(pid)
    await k._on_lifecycle_created({'id': str(pid)}, 'local')
    await k._on_lifecycle_started({'id': str(pid)}, 'local')
    await _wait(lambda: k.nodes[pid].cmd.state.value == 'running',
                what='n1 running')
    task = k.nodes[pid].cmd._task

    stopper = asyncio.ensure_future(k.stop())
    await _wait(lambda: any(m.get('event') == LIFECYCLE_STOP for m in sent),
                what='lifecycle.stop sent to local hub')
    await asyncio.sleep(0.05)
    assert not stopper.done(), (
        'local 节点没回报就先摘了: 关停序列会与 hub 侧的 stop hook 赛跑')
    assert pid in k.nodes, f'#{pid} 回报前不该摘'

    await k._on_lifecycle_stopped(
        {'event': 'lifecycle.stopped', 'id': str(pid), 'reason': 'STOPPED'}, 'local')
    await asyncio.wait_for(stopper, timeout=5.0)
    assert not k.nodes, f'nodes 表该清空, 残留 {list(k.nodes)}'
    assert task.done(), 'cmd task 必须收尾'
    print('local hub: PASS')


class _HangingRequests:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()


class _StreamContext:
    async def write(self, _msg) -> None:
        pass

    def set_code(self, _code) -> None:
        pass

    def set_details(self, _details) -> None:
        pass


def case_logger_traceback() -> None:
    """自定义 formatter 必须保留 asyncio/业务异常的完整堆栈。"""
    try:
        raise ConnectionResetError(109, '管道已结束')
    except ConnectionResetError:
        record = logging.LogRecord(
            'asyncio', logging.ERROR, __file__, 1,
            'Exception in callback pipe', (), sys.exc_info())
    text = ColoredFormatter().format(record)
    assert 'Traceback (most recent call last)' in text
    assert 'ConnectionResetError: [Errno 109] 管道已结束' in text
    print('logger traceback: PASS')


def case_grpc_keepalive() -> None:
    """20s 空闲 ping 不应撞上服务端默认的 5min 接收门槛。"""
    with patch('routine.kernel.grpc_server.grpc.aio.server') as server_factory:
        GrpcKernelServer(None, '127.0.0.1:0')
    options = dict(server_factory.call_args.kwargs['options'])
    assert options['grpc.keepalive_permit_without_calls'] == 1
    assert options['grpc.http2.min_ping_interval_without_data_ms'] == 10_000
    assert options['grpc.http2.max_ping_strikes'] == 0
    print('grpc keepalive: PASS')


async def case_stream_handler_cancel() -> None:
    """gRPC 直接取消 handler 时, 连接内的四个任务也必须全部收尸。"""
    kref = await kernel.create({'root': {}})
    server = GrpcKernelServer(kref.kernel, '127.0.0.1:0')
    baseline = set(asyncio.all_tasks())
    handler = asyncio.create_task(server._on_stream(
        _HangingRequests(), _StreamContext()))
    await _wait(lambda: any(cid.startswith('s') for cid in kref.kernel._conns),
                what='stream conn registered')
    created = set(asyncio.all_tasks()) - baseline

    handler.cancel()
    try:
        await handler
    except asyncio.CancelledError:
        pass

    pending = [t for t in created if not t.done()]
    assert not pending, f'handler 取消后遗留 {len(pending)} 个连接任务: {pending}'
    assert not any(cid.startswith('s') for cid in kref.kernel._conns), (
        'handler 取消后 stream conn 必须注销')
    await kref.kernel.stop()
    print('stream handler cancel: PASS')


async def main() -> None:
    await case_peer_hub()
    await case_peer_explicit_stop()
    await case_local_hub()
    await case_stream_handler_cancel()
    case_grpc_keepalive()
    case_logger_traceback()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
