"""ctx.call = submit → start → wait 一步拿子 routine 结果.

验证组合语义:
  1. 成功:返回子的 result;
  2. start 失败:抛 StartError(不调 wait);
  3. 子异常停止:wait 抛 RuntimeError(透传给 call 调用方).

call 是 submit/start/wait 的语法糖,不引入新协议----用 stub ctx 直接验组合.
"""
import asyncio
import unittest
from typing import Any, Dict, Optional

from routine.ctx import RunContext
from routine.errors import StartError


class _StubHandle:
    """记录 start/wait 调用;可控返回 result / 抛异常."""

    def __init__(self, child_id: str, name: str,
                 start_err: Optional[StartError] = None,
                 wait_result: Any = None,
                 wait_error: Optional[Exception] = None):
        self.id = child_id
        self.name = name
        self._start_err = start_err
        self._wait_result = wait_result
        self._wait_error = wait_error
        self.start_called = False
        self.wait_called = False

    def __await__(self):
        # await handle 等价 await handle.wait()(对标真实 RoutineHandle.__await__)
        return self.wait().__await__()

    async def start(self) -> None:
        self.start_called = True
        if self._start_err is not None:
            raise self._start_err

    async def wait(self) -> Any:
        self.wait_called = True
        if self._wait_error is not None:
            raise self._wait_error
        return self._wait_result


class _StubCtx(RunContext):
    """重写 submit 拿 stub handle;其余走 RunContext."""

    def __init__(self, handle: _StubHandle):
        # RunContext 字段不实际用到(submit 被重写),给占位值.
        super().__init__(id='1', name='parent', peer_id='p',
                        io=_NoIO(), routine=_DummyRoutine(),
                        runtime=None, transport=None)
        self._stub_handle = handle

    async def submit(self, name, kwargs=None):
        return self._stub_handle


class _NoIO:
    """submit 被重写,IO 方法不会被调----给空壳避免 RunContext 初始化报错."""
    runtime = None


class _DummyRoutine:
    def __init__(self):
        self._started = True
        self._active_ctx = None


class TestRun(unittest.IsolatedAsyncioTestCase):

    async def test_call_returns_result_on_success(self):
        """submit → start(None)→ wait:返回子的 result."""
        handle = _StubHandle('2', 'child', wait_result={'ok': True})
        ctx = _StubCtx(handle)
        result = await ctx.call('child', {'x': 1})
        self.assertEqual(result, {'ok': True})
        self.assertTrue(handle.start_called)
        self.assertTrue(handle.wait_called)

    async def test_call_raises_start_error_on_start_failure(self):
        """start 返回 StartError → run 抛 StartError,不调 wait."""
        err = StartError('module "output" blocked by echo-3')
        handle = _StubHandle('2', 'child', start_err=err)
        ctx = _StubCtx(handle)
        with self.assertRaises(StartError):
            await ctx.call('child')
        self.assertTrue(handle.start_called)
        self.assertFalse(handle.wait_called, 'start 失败不应调 wait')

    async def test_call_propagates_wait_error(self):
        """子异常停止 → wait 抛 RuntimeError → run 透传."""
        handle = _StubHandle('2', 'child',
                             wait_error=RuntimeError('child crashed'))
        ctx = _StubCtx(handle)
        with self.assertRaises(RuntimeError, msg='child crashed'):
            await ctx.call('child')
        self.assertTrue(handle.wait_called)

    async def test_call_in_created_phase_raises(self):
        """父未 started(created 阶段)调 run → 抛 RuntimeError,不 submit(避免泄漏).

        run 含 start 子----父都没 started 不能 start 子.提前抛,不建子 instance/handle.
        """
        ctx = _StubCtx(_StubHandle('2', 'child'))
        ctx._routine._started = False
        with self.assertRaises(RuntimeError):
            await ctx.call('child')


if __name__ == '__main__':
    unittest.main(verbosity=2)
