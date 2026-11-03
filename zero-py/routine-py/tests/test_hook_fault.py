"""on_stopped 钩子异常不阻断收尾(lifecycle 硬承诺回归).

运行: ``python tests/test_hook_fault.py``

背景: runner 的 except 块里原先直接 ``await instance.on_stopped(...)``,
业务钩子一抛就跳过 ``_send_stopped`` + ``_cleanup`` ---- kernel 收不到
stopped、父侧 ``handle._done`` 永不置位、``await handle`` 活活挂到断连兜底。
修法: 全部收尾路径改走 ``_fire_on_stopped``(钩子异常只记日志).

覆盖三条收尾路径:
1. run() 抛异常(FAILED) ---- 钩子再抛, 父侧仍能拿到 RuntimeError
2. run() 正常返回(COMPLETED) ---- 钩子抛不影响 result
3. 父侧 stop(STOPPED) ---- 钩子抛不影响 stopped 回报与 reason
4. created 态 destroy(DESTROYED) ---- 钩子抛不破坏撤销语义
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from routine import Routines, TerminationReason, kernel
from routine.hub import RoutineHub
from routine.routine import Modules, Routine

_jobs: asyncio.Queue = asyncio.Queue()
_results: asyncio.Queue = asyncio.Queue()
_handles: dict = {}


class _FaultyStop(Routine):
    """按 mode 走不同收尾路径, on_stopped 一律抛 ---- 验证收尾不被吃掉."""

    name = 'faulty_stop'

    @classmethod
    def modules(cls, kwargs=None) -> Modules:
        return Modules([])

    async def on_created(self, rid: str, kwargs):
        self._mode = (kwargs or {}).get('mode', 'error')
        self._stop = asyncio.Event()

    async def run(self, kwargs):
        if self._mode == 'error':
            raise ValueError('boom in run')
        if self._mode == 'hold':
            await self._stop.wait()
            return {'stopped': True}
        return {'ok': True}

    async def stop(self):
        self._stop.set()

    async def on_stopped(
            self, reason: TerminationReason = TerminationReason.COMPLETED,
            result=None, by=None, detail: str = '') -> None:
        raise RuntimeError(f'hook exploded (reason={reason})')


class _Driver(Routine):
    """被动编排器: job → create/start/wait 或 stop, 结果进 _results."""

    name = 'driver'
    is_passive = True

    async def on_created(self, rid: str, kwargs):
        return None

    async def run(self, kwargs):
        while True:
            job = await _jobs.get()
            asyncio.create_task(self._do(job))

    async def _do(self, job):
        tag, op = job[0], job[1]
        if op == 'stop':
            h = _handles.pop(tag)
            await h.stop()
            return
        if op == 'destroy':
            h = await self.create('faulty_stop', {'mode': 'hold'})
            await h.destroy()
            result = await h
            _results.put_nowait({
                'tag': tag, 'phase': 'done', 'ok': True,
                'reason': h.reason, 'result': result,
            })
            return
        mode = job[2]
        try:
            h = await self.create('faulty_stop', {'mode': mode})
            await h.start(10)
        except Exception as exc:
            _results.put_nowait({'tag': tag, 'phase': 'start', 'ok': False,
                                 'err': type(exc).__name__, 'msg': str(exc)})
            return
        _handles[tag] = h
        _results.put_nowait({'tag': tag, 'phase': 'start', 'ok': True})
        try:
            result = await h
            _results.put_nowait({'tag': tag, 'phase': 'done', 'ok': True,
                                 'reason': h.reason, 'result': result})
        except Exception as exc:
            _results.put_nowait({'tag': tag, 'phase': 'done', 'ok': False,
                                 'reason': h.reason, 'err': type(exc).__name__,
                                 'msg': str(exc)})


async def _res(tag: str, phase: str) -> dict:
    r = await asyncio.wait_for(_results.get(), timeout=10)
    assert r['tag'] == tag and r['phase'] == phase, r
    return r


async def _case_error_branch() -> None:
    """run 抛异常 + 钩子抛: stopped 回报必须照发, 父侧收到 RuntimeError."""
    await _jobs.put(('e', 'start', 'error'))
    assert (await _res('e', 'start'))['ok']
    r = await _res('e', 'done')
    assert not r['ok'], r
    assert r['reason'] is TerminationReason.FAILED, r
    assert r['err'] == 'RuntimeError', r
    assert 'boom in run' in r['msg'], r
    print('case 1 ok: hook fault does not swallow stopped (reason=FAILED)')


async def _case_auto_branch() -> None:
    """run 正常返回 + 钩子抛: result 照常送达."""
    await _jobs.put(('a', 'start', 'ok'))
    assert (await _res('a', 'start'))['ok']
    r = await _res('a', 'done')
    assert r['ok'] and r['reason'] is TerminationReason.COMPLETED, r
    assert r['result'] == {'ok': True}, r
    print('case 2 ok: hook fault does not clobber result (reason=COMPLETED)')


async def _case_stop_branch() -> None:
    """父侧 stop + 钩子抛: graceful stop 收尾完整, reason=STOPPED."""
    await _jobs.put(('s', 'start', 'hold'))
    assert (await _res('s', 'start'))['ok']
    await _jobs.put(('s', 'stop'))
    r = await _res('s', 'done')
    assert r['ok'] and r['reason'] is TerminationReason.STOPPED, r
    assert r['result'] == {'stopped': True}, r
    print('case 3 ok: hook fault does not break graceful stop (reason=STOPPED)')


async def _case_destroy_branch() -> None:
    """created 态撤销 + 钩子抛:父子两侧统一为 DESTROYED."""
    await _jobs.put(('d', 'destroy'))
    r = await _res('d', 'done')
    assert r['ok'] and r['reason'] is TerminationReason.DESTROYED, r
    print('case 4 ok: destroy keeps DESTROYED through hook fault')


async def main() -> None:
    logging.getLogger('routine').setLevel(logging.CRITICAL)
    kref = await kernel.create({'root': {}})
    rs = Routines()
    rs.register(_Driver, _FaultyStop)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        await asyncio.sleep(0.5)
        for case in (_case_error_branch, _case_auto_branch, _case_stop_branch,
                     _case_destroy_branch):
            await case()
        print('ALL PASS')
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
