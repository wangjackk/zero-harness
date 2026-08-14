"""start/stop 子 routine 的 created 阶段守卫测试.

父 routine 未 started(_started=False,created 阶段)时,handle.start()/stop()
抛 RuntimeError----kernel 侧也会硬拦截(双保险).submit 不受守卫(created 即可).
"""
import unittest

from routine import Routine
from routine.ctx import RunContext
from routine.handle import RoutineHandle


class _StubRuntime:
    """最小 runtime:handle 自持 ack(_ack 字段),stub 不再介入 ack 表."""


class _StubIO:
    """记录发出的 routine.start/stop;持 stub runtime."""

    def __init__(self):
        self.sent = []
        self.runtime = _StubRuntime()

    async def send_routine_start(self, *, child_id, try_start=False, peer_id=None):
        self.sent.append(('start', child_id, try_start))

    async def send_routine_stop(self, *, child_id, peer_id=None):
        self.sent.append(('stop', child_id))

    async def send_routine_unsubmit(self, *, child_id, peer_id=None):
        self.sent.append(('unsubmit', child_id))


class _Concrete(Routine):
    async def run(self, kwargs):
        pass


class TestStartStopGuard(unittest.IsolatedAsyncioTestCase):

    async def test_start_rejected_when_not_started(self):
        """父 _started=False:handle.start() 抛 RuntimeError,不发 routine.start."""
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        handle = RoutineHandle('2', 'child', ctx=ctx)
        with self.assertRaises(RuntimeError):
            await handle.start()
        self.assertEqual(stub.sent, [], '未 started 不应发出 routine.start')

    async def test_stop_rejected_when_not_started(self):
        """父 _started=False:handle.stop() 抛 RuntimeError,不发 routine.stop."""
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        handle = RoutineHandle('2', 'child', ctx=ctx)
        with self.assertRaises(RuntimeError):
            await handle.stop()
        self.assertEqual(stub.sent, [], '未 started 不应发出 routine.stop')

    async def test_start_allowed_when_started(self):
        """父 _started=True:handle.start() 不抛(发 routine.start 后等 ack;
        这里 stub 不回 started,会挂----用 timeout 包裹验证至少没立即抛)."""
        import asyncio
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        instance._started = True
        handle = RoutineHandle('2', 'child', ctx=ctx)
        # start_child 会等 ack(lifecycle.started),stub 不回 → 超时
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(handle.start(), timeout=0.2)
        # 没抛 RuntimeError,且 routine.start 发出了(try_start=False:start 全有或全无)
        self.assertEqual(stub.sent, [('start', '2', False)])

    async def test_start_does_not_carry_kwargs(self):
        """handle.start() 不带 kwargs----start 用 submit 时存的 cmd.Kwargs,
        routine.start 事件本身不带 kwargs."""
        import asyncio
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        instance._started = True
        handle = RoutineHandle('2', 'child', ctx=ctx)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(handle.start(), timeout=0.2)
        # routine.start 事件只带 child_id,不带 kwargs(try_start=False)
        self.assertEqual(stub.sent, [('start', '2', False)])

    async def test_try_start_sends_try_flag(self):
        """handle.try_start() 发 routine.start 带 try=True(失败保留可重试)."""
        import asyncio
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        instance._started = True
        handle = RoutineHandle('2', 'child', ctx=ctx)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(handle.try_start(), timeout=0.2)
        # try_start=True:失败保留可重试
        self.assertEqual(stub.sent, [('start', '2', True)])

    async def test_unsubmit_sends_wire(self):
        """handle.unsubmit() 发 routine.unsubmit(不等父 started,created 即可)."""
        import asyncio
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        # 父未 started 也能 unsubmit(submit 也不要求父 started)
        handle = RoutineHandle('2', 'child', ctx=ctx)
        # unsubmit 等 stopped,stub 不回 → 超时(验证 wire 发出即可)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(handle.unsubmit(), timeout=0.2)
        self.assertEqual(stub.sent, [('unsubmit', '2')])


if __name__ == '__main__':
    unittest.main(verbosity=2)
