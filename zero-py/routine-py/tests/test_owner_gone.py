"""宿主失联的节点收尾: "先子后父" 对跨 hub 父子同样成立.

运行: ``python tests/test_owner_gone.py``

背景(为什么要有这个 case): 停节点有两条语义, 只有一条原本是真停。

- 正常 stop / 自然终止 / 抢占: ``_stop_node`` 先递归停子, 再停自己;
- 宿主 conn 断开: 对端没了, lifecycle.stop 无处可发。旧实现就地
  ``_remove_node``, 只清"owner 是这个死 hub"的节点 ---- 挂在这些节点下、
  owner 是**别的活 hub** 的子节点一条消息都收不到, 继续跑, 而父链已断,
  谁都再找不到它们(孤儿)。跨 hub 父子在 code worker / 远程设备 hub 上
  是常态, 所以这条必须是结构性保证。

钉住的语义:
1. 父节点 owner 断线 → 父本地结算(reason=DISCONNECTED), 不等对端超时;
2. 父的子在别的活 hub 上 → 照常下发 stop, stop hook 跑到;
3. 两者都从 nodes 表消失, 不留孤儿。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routine import Routines, kernel
from routine.hub import RoutineHub
from routine.kernel.manager import KernelConn, Route
from routine.routine import Routine

STOPS: list = []


class _Child(Routine):
    """占住 body 的普通 routine: stop hook 打点, run 等 stop 才返回."""

    name = 'hold_child'

    async def on_created(self, rid: str, kwargs):
        self._evt = asyncio.Event()

    async def run(self, kwargs):
        await self._evt.wait()
        return {'child': 'done'}

    async def stop(self):
        STOPS.append('hold_child')
        self._evt.set()


async def _wait(pred, *, what: str, timeout: float = 8.0) -> None:
    """轮询到条件成立; 超时即败并把条件名带出来(死锁显性化)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f'timeout waiting for {what}')


async def main() -> None:
    kref = await kernel.create({'root': {}})
    k = kref.kernel
    rs = Routines()
    rs.register(_Child)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        # --- 假 hub A: 一个 conn + 一条路由, 冒充"代码宿主进程" ---
        sent: list = []
        k._conns['sA'] = KernelConn(id='sA', send=sent.append, hub_id='hubA')
        k._hub_conns['hubA'] = 'sA'
        k.routes['hold_parent'] = Route(owner='hubA')

        # 父: 建在假 hub A 上并跑起来(created/started 回报手工喂给 kernel)
        pid = k._on_create(0, 'hold_parent', {})
        parent = k.nodes[pid]
        k._start(pid)
        await k._on_lifecycle_created({'id': str(pid)}, 'sA')
        await k._on_lifecycle_started({'id': str(pid)}, 'sA')
        await _wait(lambda: parent.cmd.state.value == 'running',
                    what='parent running')

        # 子: 建在真 hub 上, parent_id 指向父 ---- 跨 hub 父子
        cid = k._on_create(pid, 'hold_child', {})
        child = k.nodes[cid]
        k._start(cid)
        await _wait(lambda: child.cmd.state.value == 'running',
                    what='child running')
        assert STOPS == [], STOPS

        # --- 拔掉 A: 父的宿主没了, 子还活得好好的在另一个 hub 上 ---
        k.drop_conn('sA')

        await _wait(lambda: pid not in k.nodes and cid not in k.nodes,
                    what='dead-owner subtree reaped')
        assert STOPS == ['hold_child'], STOPS
        assert parent.stopped_fut.result()['reason'] == 'DISCONNECTED', \
            parent.stopped_fut.result()
        assert parent.stopped_fut.result()['by'] == 'hubA', \
            parent.stopped_fut.result()
        assert child.stopped_fut.done(), 'child must get a stopped result'
        assert 'hold_parent' not in k.routes, 'dead hub route must be gone'
        assert k._conn_of_owner('hubA') is None
        print('case ok: owner gone -> parent settled, cross-hub child stopped, '
              'no orphan')

        # 留一点时间让后台收尾任务跑干净, 免得退出时冒 "task not retrieved"
        await asyncio.sleep(0.2)
        print('ALL PASS')
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
