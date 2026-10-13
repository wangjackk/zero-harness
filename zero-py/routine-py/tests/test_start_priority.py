"""start 优先级仲裁端到端单测(进程内 kernel 全链路, 零测试依赖).

运行: ``python tests/test_start_priority.py``

覆盖:
1. 常规冲突拒(默认档 10 双方) -- 旧 call 语义保持
2. 高优抢占: 20 驱逐默认档 10 holder, 旧例 reason=FORCE
3. 低档空间: 默认档 10 驱逐低档 5 holder
4. 平级不抢: 20 vs 20 -> StartError
5. 安全级不可被用户级抢: 100 holder vs 20 -> StartError
6. 无冲突时高优直通(不经驱逐路径)
7. try_start 永不抢占(钉 0 最低档, 礼貌语义)
8. passive 声明优先级: 拉起时按声明 priority 仲裁(声明处即调用点)
9. 运行时 acquire 按本 routine 的 start 档位仲裁: 20 驱逐默认档 holder
10. acquire 撞高档 holder(100) 拒, AcquireError, holder 无恙
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routine import Routines, StartError, kernel
from routine.errors import StartError as StartError2  # noqa: F401  (同源校验)
from routine.hub import RoutineHub
from routine.routine import Modules, PassiveConfig, Routine

assert StartError is StartError2

_jobs: asyncio.Queue = asyncio.Queue()
_results: asyncio.Queue = asyncio.Queue()
_acq_jobs: asyncio.Queue = asyncio.Queue()   # case 9/10: acquire 执行器的任务口
_hub: list = []   # main() 灌入 RoutineHub 引用(case 8 动态 register 用)


class _Driver(Routine):
    """被动自动拉起的编排器: _jobs 任务进, create+start(priority)+wait,
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
        tag, target, pri, *rest = job
        try_ = rest[0] if rest else False
        try:
            h = await self.create(target, {})
            if try_:
                r = await h.try_start()
                _results.put_nowait({'tag': tag, 'phase': 'start',
                                     'ok': r is None,
                                     'err': type(r).__name__ if r else None,
                                     'msg': str(r) if r else None})
                return
            await h.start(pri)
        except Exception as exc:
            _results.put_nowait({'tag': tag, 'phase': 'start', 'ok': False,
                                 'err': type(exc).__name__, 'msg': str(exc)})
            return
        assert h.priority == pri   # handle 镜像档位(kernel Node.priority 调用方视图)
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

    @classmethod
    def modules(cls, kwargs=None) -> Modules:
        return Modules(['body'])

    async def on_created(self, rid: str, kwargs):
        self._stop = asyncio.Event()

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


class _HoldP(_Hold):
    """声明 priority=20 的 passive ---- 拉起即驱逐默认档 holder."""

    name = 'hold_p'
    is_passive = PassiveConfig(kwargs={}, priority=20)


class _Acq(Routine):
    """声明 priority=20 的常驻 acquire 执行器(不占模块, 撞车全部来自
    self.acquire 本身): job(tag, op) 进, acquire/release(['body']) 结果出
    _results ---- case 结尾 release 归还 body, 不污染下一 case."""

    name = 'acq'
    is_passive = PassiveConfig(kwargs={}, priority=20)

    async def on_created(self, rid: str, kwargs):
        return None

    async def run(self, kwargs):
        while True:
            tag, op = await _acq_jobs.get()
            try:
                if op == 'acquire':
                    await self.acquire(['body'])
                else:
                    await self.release(['body'])
                _results.put_nowait({'tag': tag, 'phase': op, 'ok': True})
            except Exception as exc:
                _results.put_nowait({'tag': tag, 'phase': op, 'ok': False,
                                     'err': type(exc).__name__})


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
    await _jobs.put(('a', 'hold_a', 10))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', 10))
    r = await _res('b', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r
    print('case 1 ok: default(10) start conflict -> StartError')


async def _case_higher_priority_evicts() -> None:
    await _jobs.put(('a', 'hold_a', 10))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', 20))
    # 确定序: 驱逐(A 收尾)完成后 B 才重试 start ---- A 的 done 先到
    evicted = await _res('a', 'done')
    assert evicted['ok'] and evicted['reason'] == 'FORCE', evicted
    # result 是 run() 的返回(main task 返回值, _graceful_stop 语义)
    assert evicted['result'] == {'held': 'hold_a'}, evicted
    assert (await _res('b', 'start'))['ok']
    print('case 2 ok: 20 evicts 10 holder (reason=FORCE)')


async def _case_default_evicts_low_priority() -> None:
    await _jobs.put(('a', 'hold_a', 5))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', 10))
    evicted = await _res('a', 'done')
    assert evicted['ok'] and evicted['reason'] == 'FORCE', evicted
    assert (await _res('b', 'start'))['ok']
    print('case 3 ok: 10(default) evicts low-priority(5) holder')


async def _case_equal_priority_rejected() -> None:
    await _jobs.put(('a', 'hold_a', 20))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', 20))
    r = await _res('b', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r
    print('case 4 ok: equal priority does not preempt')


async def _case_safety_not_preemptable_by_user() -> None:
    await _jobs.put(('a', 'hold_a', 100))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('b', 'hold_b', 20))
    r = await _res('b', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r
    print('case 5 ok: 100 holder rejects 20 start')


async def _case_no_conflict_high_priority_plain() -> None:
    await _jobs.put(('b', 'hold_b', 20))
    r = await _res('b', 'start')
    assert r['ok'], r
    print('case 6 ok: no-conflict high-priority start goes straight through')


async def _case_try_start_never_preempts() -> None:
    await _jobs.put(('a', 'hold_a', 5))
    assert (await _res('a', 'start'))['ok']
    await _jobs.put(('c', 'hold_b', 0, True))   # try_start 撞低档 holder
    r = await _res('c', 'start')
    assert not r['ok'] and r['err'] == 'StartError', r   # 礼貌拒, 不驱逐
    print('case 7 ok: try_start never preempts (low-priority holder intact)')


async def _case_passive_declared_priority_evicts() -> None:
    await _jobs.put(('a', 'hold_a', 10))
    assert (await _res('a', 'start'))['ok']
    # 动态注册声明 priority=20 的 passive: kernel auto-start 驱逐默认档 holder
    await _hub[0].register_routine(_HoldP)
    evicted = await _res('a', 'done')
    assert evicted['ok'] and evicted['reason'] == 'FORCE', evicted
    print('case 8 ok: passive declared priority(20) evicts holder on auto-start')


async def _case_acquire_evicts_by_priority() -> None:
    await _jobs.put(('a', 'hold_a', 10))
    assert (await _res('a', 'start'))['ok']
    await _acq_jobs.put(('q', 'acquire'))
    evicted = await _res('a', 'done')   # stopped 帧先于 acquired ack(dispatch FIFO)
    assert evicted['ok'] and evicted['reason'] == 'FORCE', evicted
    r = await _res('q', 'acquire')
    assert r['ok'], r
    await _acq_jobs.put(('q', 'release'))
    assert (await _res('q', 'release'))['ok']   # 归还 body, 不污染下一 case
    print('case 9 ok: acquire(20) evicts default holder, gains body')


async def _case_acquire_rejected_by_higher() -> None:
    await _jobs.put(('a', 'hold_a', 100))
    assert (await _res('a', 'start'))['ok']
    await _acq_jobs.put(('q', 'acquire'))
    r = await _res('q', 'acquire')
    assert not r['ok'] and r['err'] == 'AcquireError', r   # 20 < 100: 不抢
    # holder 无恙: 再来一个平级 20 的 start 驱逐也拒
    await _jobs.put(('b', 'hold_b', 20))
    assert (await _res('b', 'start'))['ok'] is False
    print('case 10 ok: acquire(20) vs holder(100) rejected, holder intact')


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
    rs.register(_Driver, _HoldA, _HoldB, _Acq)
    hub = RoutineHub(rs)
    _hub.append(hub)
    conn = await hub.connect(kref)
    try:
        # driver 被动自动拉起需要一小段启动时间
        await asyncio.sleep(0.5)
        for case in (_case_normal_conflict_rejected,
                     _case_higher_priority_evicts,
                     _case_default_evicts_low_priority,
                     _case_equal_priority_rejected,
                     _case_safety_not_preemptable_by_user,
                     _case_no_conflict_high_priority_plain,
                     _case_try_start_never_preempts,
                     _case_passive_declared_priority_evicts,
                     _case_acquire_evicts_by_priority,
                     _case_acquire_rejected_by_higher):
            await case()
            await _cleanup_body(kref)
            await _drain_results()
        print('ALL PASS')
    finally:
        await conn.stop()


if __name__ == '__main__':
    asyncio.run(main())
