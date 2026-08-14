"""force_release / force_start / force_run 抢占式模块占用测试.

验证:
  1. force_release:发 routine.force_release + ack 成功返回 None / 失败抛 RuntimeError;
     created 阶段守卫(父未 started 抛).
  2. handle.force_start:发 routine.force_start + 成功返回 None / 失败返回 StartError;
     created 阶段守卫.
  3. force_run 组合:submit → force_start → wait;成功返回 result,
     force_start 失败抛 StartError,wait 错误透传,created 阶段抛.

wire 级用 stub IO(send 时 resolve ack future),组合级用 stub handle.
"""
import asyncio
import unittest
from typing import Any, Optional

from routine import Routine
from routine.ctx import RunContext
from routine.errors import AcquireError, ReleaseError, StartError
from routine.handle import RoutineHandle


# ---------------------------------------------------------------------------
# wire 级 stub:记录 send + 持 runtime future 表(send 时 resolve 模拟 kernel ack)
# ---------------------------------------------------------------------------

class _StubRuntime:
    def __init__(self):
        self.acquire_futures = {}
        # handle 表(child_id -> RoutineHandle):handle 自持 ack(_ack 字段),
        # server/stub 按 child_id 经本表找到 handle resolve.对标真实 runtime._handles.
        self._handles = {}

    def register_acquire_future(self, req_id, fut):
        self.acquire_futures[req_id] = fut

    def pop_acquire_future(self, req_id):
        return self.acquire_futures.pop(req_id, None)

    def register_handle(self, child_id, handle):
        self._handles[child_id] = handle

    def get_handle(self, child_id):
        return self._handles.get(child_id)


class _StubIO:
    """记录 send + 在 send 时 resolve 对应 future(模拟 kernel 回 ack)."""

    def __init__(self, *, force_release_ok=True, force_release_err=None,
                 force_acquire_ok=True, force_acquire_err=None,
                 force_start_err=None):
        self.sent = []
        self.runtime = _StubRuntime()
        self._force_release_ok = force_release_ok
        self._force_release_err = force_release_err
        self._force_acquire_ok = force_acquire_ok
        self._force_acquire_err = force_acquire_err
        self._force_start_err = force_start_err

    async def send_routine_force_release(self, *, req_id, id, modules, peer_id=None):
        self.sent.append(('force_release', req_id, id, list(modules)))
        fut = self.runtime.acquire_futures.get(req_id)
        if fut is not None and not fut.done():
            if self._force_release_ok:
                fut.set_result(None)
            else:
                fut.set_exception(ReleaseError(self._force_release_err))

    async def send_routine_force_acquire(self, *, req_id, id, modules, peer_id=None):
        self.sent.append(('force_acquire', req_id, id, list(modules)))
        fut = self.runtime.acquire_futures.get(req_id)
        if fut is not None and not fut.done():
            if self._force_acquire_ok:
                fut.set_result(None)
            else:
                fut.set_exception(AcquireError(self._force_acquire_err))

    async def send_routine_force_start(self, *, child_id, peer_id=None):
        self.sent.append(('force_start', child_id))
        handle = self.runtime.get_handle(child_id)
        if handle is not None:
            # None=成功(lifecycle.started);str=error(rejected op=force_start)
            handle._resolve_ack(self._force_start_err)


class _Concrete(Routine):
    async def run(self, kwargs):
        pass


def _make_ctx(stub):
    instance = _Concrete()
    ctx = RunContext(id='1', name='parent', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
    instance._active_ctx = ctx
    instance._started = True
    return instance, ctx


# ---------------------------------------------------------------------------
# force_release
# ---------------------------------------------------------------------------

class TestForceRelease(unittest.IsolatedAsyncioTestCase):

    async def test_sends_wire_and_returns_none_on_success(self):
        """force_release 发 routine.force_release{req_id,id,modules};ack ok → None."""
        stub = _StubIO(force_release_ok=True)
        _, ctx = _make_ctx(stub)
        result = await ctx.force_release(['body', 'leg'])
        self.assertIsNone(result)
        self.assertEqual(len(stub.sent), 1)
        ev, req_id, rid, modules = stub.sent[0]
        self.assertEqual(ev, 'force_release')
        self.assertEqual(rid, '1')
        self.assertEqual(modules, ['body', 'leg'])
        self.assertTrue(req_id)

    async def test_raises_on_failure(self):
        """ack ok=false(罕见--rid 未 started)-> 抛 ReleaseError.force_release 只驱逐不占,
        基本总成功(驱逐本身不失败)."""
        stub = _StubIO(force_release_ok=False,
                       force_release_err='routine not started')
        _, ctx = _make_ctx(stub)
        with self.assertRaises(ReleaseError) as cm:
            await ctx.force_release(['body'])
        self.assertIn('not started', str(cm.exception))
    async def test_created_phase_guard(self):
        """父未 started → 抛 RuntimeError,不发 force_release(避免泄漏)."""
        stub = _StubIO()
        instance, ctx = _make_ctx(stub)
        instance._started = False
        with self.assertRaises(RuntimeError):
            await ctx.force_release(['body'])
        self.assertEqual(stub.sent, [], '未 started 不应发出 force_release')


# ---------------------------------------------------------------------------
# force_acquire (驱逐+占住, 带驱逐的 acquire)
# ---------------------------------------------------------------------------

class TestForceAcquire(unittest.IsolatedAsyncioTestCase):

    async def test_sends_wire_and_returns_none_on_success(self):
        """force_acquire 发 routine.force_acquire{req_id,id,modules}; ack ok -> None."""
        stub = _StubIO(force_acquire_ok=True)
        _, ctx = _make_ctx(stub)
        result = await ctx.force_acquire(['body', 'leg'])
        self.assertIsNone(result)
        self.assertEqual(len(stub.sent), 1)
        ev, req_id, rid, modules = stub.sent[0]
        self.assertEqual(ev, 'force_acquire')
        self.assertEqual(rid, '1')
        self.assertEqual(modules, ['body', 'leg'])
        self.assertTrue(req_id)

    async def test_raises_on_conflict_after_eviction(self):
        """ack ok=false (驱逐后仍冲突--竞态, 被别人抢了) -> 抛 AcquireError."""
        stub = _StubIO(force_acquire_ok=False,
                       force_acquire_err='module "body" still held by dance-3 after eviction')
        _, ctx = _make_ctx(stub)
        with self.assertRaises(AcquireError) as cm:
            await ctx.force_acquire(['body'])
        self.assertIn('still held', str(cm.exception))

    async def test_created_phase_guard(self):
        """父未 started -> 抛 RuntimeError, 不发 force_acquire (避免泄漏)."""
        stub = _StubIO()
        instance, ctx = _make_ctx(stub)
        instance._started = False
        with self.assertRaises(RuntimeError):
            await ctx.force_acquire(['body'])
        self.assertEqual(stub.sent, [], '未 started 不应发出 force_acquire')


# ---------------------------------------------------------------------------
# handle.force_start
# ---------------------------------------------------------------------------

class TestForceStart(unittest.IsolatedAsyncioTestCase):

    async def test_sends_wire_and_returns_none_on_success(self):
        """handle.force_start 发 routine.force_start{child_id};started → None."""
        stub = _StubIO(force_start_err=None)
        _, ctx = _make_ctx(stub)
        handle = RoutineHandle('2', 'child', ctx=ctx)
        stub.runtime.register_handle('2', handle)
        await handle.force_start()  # 成功不抛
        self.assertEqual(stub.sent, [('force_start', '2')])

    async def test_returns_start_error_on_failure(self):
        """rejected op=force_start → 返回 StartError(不抛,跟 start 一致)."""
        stub = _StubIO(force_start_err='module "leg" still held by dance-3 after eviction')
        _, ctx = _make_ctx(stub)
        handle = RoutineHandle('2', 'child', ctx=ctx)
        stub.runtime.register_handle('2', handle)
        with self.assertRaises(StartError) as cm:
            await handle.force_start()
        self.assertIn('still held', str(cm.exception))

    async def test_created_phase_guard(self):
        """父未 started → 抛 RuntimeError,不发 force_start."""
        stub = _StubIO()
        instance, ctx = _make_ctx(stub)
        instance._started = False
        handle = RoutineHandle('2', 'child', ctx=ctx)
        with self.assertRaises(RuntimeError):
            await handle.force_start()
        self.assertEqual(stub.sent, [], '未 started 不应发出 force_start')


# ---------------------------------------------------------------------------
# force_run 组合(对标 test_run.py,区别:start → force_start)
# ---------------------------------------------------------------------------

class _StubHandle:
    """记录 force_start/wait 调用;可控返回."""

    def __init__(self, child_id='2', name='child',
                 force_start_err: Optional[StartError] = None,
                 wait_result: Any = None,
                 wait_error: Optional[Exception] = None):
        self.id = child_id
        self.name = name
        self._force_start_err = force_start_err
        self._wait_result = wait_result
        self._wait_error = wait_error
        self.force_start_called = False
        self.wait_called = False

    def __await__(self):
        return self.wait().__await__()

    async def force_start(self) -> None:
        self.force_start_called = True
        if self._force_start_err is not None:
            raise self._force_start_err

    async def wait(self) -> Any:
        self.wait_called = True
        if self._wait_error is not None:
            raise self._wait_error
        return self._wait_result


class _StubCtx(RunContext):
    """重写 submit 拿 stub handle."""

    def __init__(self, handle):
        super().__init__(id='1', name='parent', peer_id='p',
                        io=_NoIO(), routine=_DummyRoutine(),
                        runtime=None, transport=None)
        self._stub_handle = handle

    async def submit(self, name, kwargs=None):
        return self._stub_handle


class _NoIO:
    runtime = None


class _DummyRoutine:
    def __init__(self):
        self._started = True
        self._active_ctx = None


class TestForceRun(unittest.IsolatedAsyncioTestCase):

    async def test_returns_result_on_success(self):
        """submit → force_start(None)→ wait:返回子的 result."""
        handle = _StubHandle(wait_result={'ok': True})
        ctx = _StubCtx(handle)
        result = await ctx.force_call('child', {'x': 1})
        self.assertEqual(result, {'ok': True})
        self.assertTrue(handle.force_start_called)
        self.assertTrue(handle.wait_called)

    async def test_raises_start_error_on_force_start_failure(self):
        """force_start 返回 StartError → force_run 抛 StartError,不调 wait."""
        err = StartError('module "leg" still held by dance-3')
        handle = _StubHandle(force_start_err=err)
        ctx = _StubCtx(handle)
        with self.assertRaises(StartError):
            await ctx.force_call('child')
        self.assertTrue(handle.force_start_called)
        self.assertFalse(handle.wait_called, 'force_start 失败不应调 wait')

    async def test_propagates_wait_error(self):
        """子异常停止 → wait 抛 RuntimeError → force_run 透传."""
        handle = _StubHandle(wait_error=RuntimeError('child crashed'))
        ctx = _StubCtx(handle)
        with self.assertRaises(RuntimeError):
            await ctx.force_call('child')
        self.assertTrue(handle.wait_called)

    async def test_created_phase_raises(self):
        """父未 started 调 force_run → 抛 RuntimeError,不 submit(避免泄漏)."""
        ctx = _StubCtx(_StubHandle())
        ctx._routine._started = False
        with self.assertRaises(RuntimeError):
            await ctx.force_call('child')


if __name__ == '__main__':
    unittest.main(verbosity=2)
