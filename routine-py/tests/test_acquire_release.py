"""运行时占领/释放模块的守卫 + ack 流程测试.

- 未 started(_started=False)调 acquire/release 抛 RuntimeError,不发 wire.
- started 后发 routine.acquire/release 并等 acquired/released ack.
- acquired ack ok=false(ConflictError)→ 抛 RuntimeError.
"""
import asyncio
import unittest

from routine import Routine
from routine.ctx import RunContext
from routine.errors import AcquireError


class _StubRuntime:
    """持 acquire/release ack future 表."""

    def __init__(self):
        self.acks = {}

    def register_acquire_future(self, req_id, fut):
        self.acks[req_id] = fut

    def pop_acquire_future(self, req_id):
        return self.acks.pop(req_id, None)


class _StubIO:
    """记录发出的 routine.acquire/release;持 stub runtime."""

    def __init__(self):
        self.sent = []
        self.runtime = _StubRuntime()

    async def send_routine_acquire(self, *, req_id, id, modules, peer_id=None):
        self.sent.append(('acquire', req_id, id, list(modules)))

    async def send_routine_release(self, *, req_id, id, modules, peer_id=None):
        self.sent.append(('release', req_id, id, list(modules)))


class _Concrete(Routine):
    async def run(self, kwargs):
        pass


class TestAcquireReleaseGuard(unittest.IsolatedAsyncioTestCase):

    def _make_ctx(self, started: bool):
        instance = _Concrete()
        stub = _StubIO()
        ctx = RunContext(id='1', name='c', peer_id='p', io=stub, routine=instance, runtime=stub.runtime, transport=None)
        instance._active_ctx = ctx
        instance._started = started
        return instance, stub, ctx

    async def test_acquire_rejected_when_not_started(self):
        """_started=False:acquire() 抛 RuntimeError,不发 routine.acquire."""
        _instance, stub, ctx = self._make_ctx(started=False)
        with self.assertRaises(RuntimeError):
            await ctx.acquire(['body'])
        self.assertEqual(stub.sent, [], '未 started 不应发出 routine.acquire')

    async def test_release_rejected_when_not_started(self):
        """_started=False:release() 抛 RuntimeError,不发 routine.release."""
        _instance, stub, ctx = self._make_ctx(started=False)
        with self.assertRaises(RuntimeError):
            await ctx.release(['body'])
        self.assertEqual(stub.sent, [], '未 started 不应发出 routine.release')

    async def test_acquire_sends_wire_and_waits_ack(self):
        """_started=True:发 routine.acquire 并等 acquired ack 才返回."""
        _instance, stub, ctx = self._make_ctx(started=True)

        async def ack_after_send():
            # 等 wire 发出后 resolve future
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_acquire_future(req_id)
            self.assertIsNotNone(fut, 'acquire 应注册 future')
            fut.set_result(None)

        asyncio.create_task(ack_after_send())
        await ctx.acquire(['body', 'leg'])
        self.assertEqual(len(stub.sent), 1)
        self.assertEqual(stub.sent[0][0], 'acquire')
        self.assertEqual(stub.sent[0][3], ['body', 'leg'])

    async def test_acquire_conflict_raises(self):
        """acquired ack ok=false(ConflictError)→ acquire() 抛 RuntimeError."""
        _instance, stub, ctx = self._make_ctx(started=True)

        async def reject_after_send():
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_acquire_future(req_id)
            fut.set_exception(AcquireError('module "body" blocked'))

        asyncio.create_task(reject_after_send())
        with self.assertRaises(AcquireError, msg='module "body" blocked'):
            await ctx.acquire(['body'])

    async def test_release_sends_wire_and_waits_ack(self):
        """_started=True:发 routine.release 并等 released ack."""
        _instance, stub, ctx = self._make_ctx(started=True)

        async def ack_after_send():
            await asyncio.sleep(0.01)
            req_id = stub.sent[0][1]
            fut = stub.runtime.pop_acquire_future(req_id)
            fut.set_result(None)

        asyncio.create_task(ack_after_send())
        await ctx.release(['body'])
        self.assertEqual(stub.sent[0][0], 'release')


if __name__ == '__main__':
    unittest.main()
