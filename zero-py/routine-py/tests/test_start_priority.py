"""start 优先级仲裁端到端单测(进程内 kernel 全链路, 零测试依赖).

运行: ``python tests/test_start_priority.py``

覆盖:
1. 常规冲突拒(priority=0 双方) -- 旧 call 语义保持
2. 高优抢占: PRI_USER 驱逐 priority=0 holder, 旧例 reason=FORCE
3. 平级不抢: PRI_USER vs PRI_USER -> StartError
4. 安全级不可被用户级抢: PRI_SAFETY holder vs PRI_USER -> StartError
5. 无冲突时高优直通(不经驱逐路径)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routine import PRI_SAFETY, PRI_USER, Routines, StartError, kernel
from routine.errors import StartError as StartError2  # noqa: F401  (同源校验)
from routine.hub import RoutineHub
from routine.routine import Modules, Routine

assert StartError is StartError2

_jobs: asyncio.Queue = asyncio.Queue()
_results: asyncio.Queue = asyncio.Queue()


class _Driver(Routine):
    """被动自动拉起的编排器: _jobs 任务进, submit+start(priority)+wait,
    结果/异常进 _results.每个 job 独立 task ---- A 挂着的同时能起 B."""

    name = 'driver'
    is_passive = True

    async def on_created(self, rid: str, kwargs):
        return None   # 编排器不占模块

    async def run(self, kwargs):
        while True:
            job = await _jobs.get()
            asyncio.create_task(self._do(job))

    async def _do(self, job):
        tag, target, pri = job
        try:
            h = await self.submit(target, {})
            await h.start(pri)
        except Exception as exc:
            _results.put_nowait({'tag': tag, 'phase': 'start', 'ok': False,
                                 'err': type(exc).__name__, 'msg': str(exc)})
            return
        _results.put_nowait({'tag': tag, 'phase': 'start', 'ok': True})
        try:
            result = await h
            _results.put_nowait({'tag': tag, 'phase': 'done', 'ok': True,
                                 'reason': h.reason, 'result': result})
        except Exception as exc:
            _results.put_nowait({'tag': tag, 'phase': 'done', 'ok': False,
                                 'err': type(exc).__name__, 'msg': str(exc)})


class _Hold(Routine):
    """占 body 模块的阻塞例: run 挂到 stop, stop 返回标记结果."""

    _stop: asyncio.Event

    async def on_created(self, rid: str, kwargs):
        self._stop = asyncio.Event()
        return Modules(['body'])

    async def run(self, kwargs):
        await self._stop.wait()
        return {'held': self.name}

    async def stop(self):
        self._stop.set()
        return {'stopped': self.name}


class _HoldA(_Hold):
    name = 'hold_a'


class _HoldB(_Hold):
    name = 'hold_b'


async def _res(tag: str, phase: str) -> dict:
    """取下一条结果并校验 tag/phase.超时即败(死锁显性化)."""
    r = await asyncio.wait_for(_results.get(), timeout=10)
    assert r['tag'] == tag and r['phase'] == phase, r
    return r


async def _drain_results() -> None:
    """清场排空: 上一 case 被停实例的 done 残留不污染下一 case 的断言."""
    while not _results.empty():
        _results.get_nowait()


async def _case_normal_conflict_rejected() -> None:
    await _jobs.put(('a', 'hold_a', 0))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', 0))
    r = await _res('b', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r
    print('case 1 ok: normal start conflict -> StartError')


async def _case_higher_priority_evicts() -> None:
    await _jobs.put(('a', 'hold_a', 0))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', PRI_USER))
    # 确定序: 驱逐(A 收尾)完成后 B 才重试 start ---- A 的 done 先到
    evicted = await _res('a', 'done')
    assert evicted['ok'] and evicted['reason'] == 'FORCE', evicted
    # result 是 run() 的返回(main task 返回值, _graceful_stop 语义)
    assert evicted['result'] == {'held': 'hold_a'}, evicted
    assert (await _res('b', 'start'))['ok']
    print('case 2 ok: PRI_USER evicts priority=0 holder (reason=FORCE)')


async def _case_equal_priority_rejected() -> None:
    await _jobs.put(('a', 'hold_a', PRI_USER))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', PRI_USER))
    r = await _res('b', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r
    print('case 3 ok: equal priority does not preempt')


async def _case_safety_not_preemptable_by_user() -> None:
    await _jobs.put(('a', 'hold_a', PRI_SAFETY))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', PRI_USER))
    r = await _res('b', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r
    print('case 4 ok: PRI_SAFETY holder rejects PRI_USER start')


async def _case_no_conflict_high_priority_plain() -> None:
    await _jobs.put(('b', 'hold_b', PRI_USER))
    r = await _res('b', 'start')
    assert r['ok'], r
    print('case 5 ok: no-conflict high-priority start goes straight through')


async def _cleanup_body(kref) -> None:
    """清场: 停掉仍占 body 的 hold_* 实例(直接调 kernel 内部, 测试专用).
    按名字前缀精确圈定 ---- root/driver 绝不能碰(停 root 即拆树)."""
    for n in list(kref.kernel.nodes.values()):
        if n.cmd.name.startswith('hold_') \
                and n.cmd.state.value in ('running', 'starting'):
            await kref.kernel._stop_node(n, cascade=True, force_by=0)
    await asyncio.sleep(0.2)


async def main() -> None:
    kref = await kernel.create({'root': {}, 'body': {}})
    rs = Routines()
    rs.register(_Driver, _HoldA, _HoldB)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        # driver 被动自动拉起需要一小段启动时间
        await asyncio.sleep(0.5)
        for case in (_case_normal_conflict_rejected,
                     _case_higher_priority_evicts,
                     _case_equal_priority_rejected,
                     _case_safety_not_preemptable_by_user,
                     _case_no_conflict_high_priority_plain):
            await case()
            await _cleanup_body(kref)
            await _drain_results()
        print('ALL PASS')
    finally:
        await conn.stop()


if __name__ == '__main__':
    asyncio.run(main())
