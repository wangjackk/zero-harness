"""RoutineHandle ---- 父侧拿到的"指向一次 submit 的子 routine"的本地句柄.

handle.id = kernel 分配的 command id(string).

notify 路径统一经 kernel 中转:子 routine 自己发 lifecycle.started/stopped 给
kernel,kernel 把它中转回来,hub.on_inbound 按 child_id 找到 handle 调
notify_started / notify_done,唤醒 handle.wait().
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from .errors import StartError

# body 迭代终结哨兵(distinguish from yielded None / result)
_BODY_DONE = object()

# start/stop/unsubmit 等 ack 默认超时(对齐 ctx.ACK_TIMEOUT): kernel 回执
# 丢失/路由遗漏时抛 TimeoutError, 不永久挂起.
_ACK_TIMEOUT = 30.0


class RoutineHandle:
    """父侧 submit handle.子 routine 的 run 是 async generator(yield)时,
    handle 也是 async iterable:``async for item in handle:`` 拿到每个 yield 项."""

    def __init__(self, child_id: str, name: str, ctx=None, modules=None):
        self.id: str = child_id
        self.name: Optional[str] = name
        # submit 时 kernel 确定的占用 modules(created 回报带回).编排器据此算
        # 冲突(``ctx.conflict(h1.modules, h2.modules)``).
        self.modules: Optional[list] = modules
        self.result: Any = None
        self.error: Optional[str] = None
        # lifecycle.stopped 的 wire reason(大写 enum 值).父侧可读分流
        # (force=被驱逐 / auto=自然结束 / stop=被停 / ...);wait() 不据此分流.
        self.reason: Optional[str] = None
        self._ctx = ctx
        self._started = asyncio.Event()
        self._done = asyncio.Event()
        # start/try_start/force_start/stop/unsubmit 的 ack future.
        # hub.on_inbound 收 lifecycle.started/stopped / routine.rejected 时按
        # child_id 经 _handles 表找到本 handle,调 _resolve_ack/_reject_ack 唤醒.
        self._ack: Optional[asyncio.Future] = None
        # 生命周期回调(async callable(handle) | None):父侧 push 通知.幂等.
        self.on_started_handler: Optional[Callable[['RoutineHandle'], Awaitable[None]]] = None
        self.on_stopped_handler: Optional[Callable[['RoutineHandle'], Awaitable[None]]] = None
        # body upstream 迭代队列 + 终结标志
        self._body_queue: asyncio.Queue = asyncio.Queue()
        self._body_done: bool = False

    def __repr__(self) -> str:
        if self._done.is_set():
            state = 'error' if self.error else 'done'
        elif self._started.is_set():
            state = 'started'
        else:
            state = 'pending'
        return f'RoutineHandle({self.name or "?"} id={self.id} {state})'

    def __await__(self):
        """``await handle`` 等价于 ``await handle.wait()``."""
        return self.wait().__await__()

    # --- state checks ---

    def is_started(self) -> bool:
        return self._started.is_set()

    def is_done(self) -> bool:
        return self._done.is_set()

    # --- control(直接发 wire 给 kernel,ack future 内化在 handle 上) ---

    async def _send_and_wait(self, send_coro, *, expects_err: bool) -> Any:
        """发 wire + 注册 ack + 等回执的公共模板."""
        if self._ack is not None and not self._ack.done():
            send_coro.close()
            raise RuntimeError(f'{self}: another start/stop in flight')
        fut = asyncio.get_running_loop().create_future()
        self._ack = fut
        try:
            await send_coro
            return await asyncio.wait_for(fut, timeout=_ACK_TIMEOUT)
        finally:
            if self._ack is fut:
                self._ack = None

    def _resolve_ack(self, value: Any) -> None:
        """hub.on_inbound 收到成功回执(lifecycle.started/stopped)时调.幂等."""
        if self._ack is not None and not self._ack.done():
            self._ack.set_result(value)

    def _reject_ack(self, exc: BaseException) -> None:
        """hub.on_inbound 收到 rejected(op=stop/unsubmit)时调.幂等."""
        if self._ack is not None and not self._ack.done():
            self._ack.set_exception(exc)

    async def _require_ctx(self, op: str) -> None:
        if self._ctx is None:
            raise RuntimeError(f'{self}: no ctx bound, cannot {op}')

    async def _require_not_done(self, op: str, pending_coro=None) -> None:
        """已 done 的 handle 不接受控制命令: kernel 侧 node 已删, 回执
        (rejected)会被 hub 丢弃 ---- 只能靠 ack 超时兜底. 入口即报错."""
        if self._done.is_set():
            if pending_coro is not None:
                pending_coro.close()   # eager 构造的发送协程: 关闭防泄漏告警
            raise RuntimeError(
                f'{self}: already done (reason={self.reason!r}), cannot {op}')

    async def _start_common(self, op: str, send_coro, *,
                            try_mode: bool) -> Optional[StartError]:
        """start/try_start/force_start 公共骨架.

        try_mode=True(try_start):失败返回 StartError(None=成功)--保留可重试.
        try_mode=False(start/force_start):失败 raise StartError.
        """
        await self._require_ctx(op)
        await self._require_not_done(op, send_coro)
        self._ctx._require_parent_started(f'{op}ing child')  # type: ignore[union-attr]
        err = await self._send_and_wait(send_coro, expects_err=True)
        if not err:
            return None
        se = StartError(err)
        if try_mode:
            return se
        raise se

    async def try_start(self) -> Optional[StartError]:
        """让 kernel start 子命令(占模块+运行),失败时保留可重试.

        失败(模块冲突/父未 started)时不清理 instance/node--保留可重试
        (占住者释放后第二次能成功).失败返回 StartError(None=成功),不抛.
        """
        return await self._start_common(
            'start',
            self._ctx._io.send_routine_start(  # type: ignore[union-attr]
                child_id=self.id, try_start=True,
            ),
            try_mode=True,
        )

    async def start(self) -> None:
        """让 kernel start 子命令(占模块+运行),全有或全无--失败时 kernel 清
        node+订阅,本侧清 created instance(handle 失效不可重试).

        失败 raise StartError.需 ctx 绑定 + 父 started.
        """
        await self._start_common(
            'start',
            self._ctx._io.send_routine_start(  # type: ignore[union-attr]
                child_id=self.id, try_start=False,
            ),
            try_mode=False,
        )

    async def force_start(self) -> None:
        """抢占式 start:kernel 驱逐占住子 declared 模块的第三方(cascade stop,
        reason='force' 透传)后 start.失败 raise StartError.
        """
        await self._start_common(
            'force_start',
            self._ctx._io.send_routine_force_start(  # type: ignore[union-attr]
                child_id=self.id,
            ),
            try_mode=False,
        )

    async def stop(self, *, fire: bool = False) -> None:
        """让 kernel stop 子命令(级联).

        ``fire=False``(默认):发 routine.stop 后等 lifecycle.stopped 回执确认子
        已停.``fire=True``:fire-and-forget,只发 wire 不等 ack.
        """
        await self._require_ctx('stop')
        await self._require_not_done('stop')
        if fire:
            await self._ctx._io.send_routine_stop(  # type: ignore[union-attr]
                child_id=self.id,
            )
            return
        self._ctx._require_parent_started('stopping child')  # type: ignore[union-attr]
        await self._send_and_wait(
            self._ctx._io.send_routine_stop(  # type: ignore[union-attr]
                child_id=self.id,
            ),
            expects_err=False,
        )

    async def unsubmit(self, *, fire: bool = False) -> None:
        """撤销提交:清 created 态子命令(未 start 的).跟 submit 对称.

        已 start 的 routine 调本方法会被 kernel 拒(rejected op=unsubmit)--
        该用 stop.不要求父 started(submit 也不要求).
        """
        await self._require_ctx('unsubmit')
        await self._require_not_done('unsubmit')
        if fire:
            await self._ctx._io.send_routine_unsubmit(  # type: ignore[union-attr]
                child_id=self.id,
            )
            return
        await self._send_and_wait(
            self._ctx._io.send_routine_unsubmit(  # type: ignore[union-attr]
                child_id=self.id,
            ),
            expects_err=False,
        )

    # --- waits ---

    async def wait_started(self) -> None:
        """等 lifecycle.started{child_id}.失败兜底也会 resolve(不死锁)."""
        await self._started.wait()

    async def wait(self) -> Any:
        """等 lifecycle.stopped{child_id}.

        成功 → 返回 result;失败/异常停掉 → raise RuntimeError(error).
        """
        await self._done.wait()
        if self.error:
            raise RuntimeError(self.error)
        return self.result

    # --- hub-layer hooks(reader 按 child_id 路由调用) ---

    def _fire_handler(self, handler) -> None:
        """fire-and-forget 调 async 回调:spawn(不阻塞 reader 协程)."""
        if handler is None or self._ctx is None:
            return
        self._ctx._spawn(handler(self))

    def notify_started(self) -> None:
        self._started.set()
        self._fire_handler(self.on_started_handler)

    def notify_done(self, result: Any = None, error: Optional[str] = None,
                    reason: Optional[str] = None) -> None:
        self.result = result
        self.error = error
        self.reason = reason
        # 兜底:错过 started 直接 done 时也唤醒 wait_started
        self._started.set()
        self._done.set()
        # body 迭代也终结(若子 routine yield 后还没发 is_final 就 stopped)
        self._finish_body()
        self._fire_handler(self.on_stopped_handler)

    # --- body upstream 迭代(子 routine start 是 async generator 时) ---

    def __aiter__(self) -> 'RoutineHandle':
        return self

    async def __anext__(self) -> Any:
        item = await self._body_queue.get()
        if item is _BODY_DONE:
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            raise item
        return item

    def _on_yield_chunk(self, data: Any = None, is_final: bool = False,
                        error: Optional[str] = None) -> None:
        """routine.yielded 投喂:子 yield 的项 / 收尾 / 异常."""
        if self._body_done:
            return
        if error is not None:
            self._body_done = True
            self._body_queue.put_nowait(RuntimeError(error))
            self._body_queue.put_nowait(_BODY_DONE)
            return
        if is_final:
            self._body_done = True
            self._body_queue.put_nowait(_BODY_DONE)
            return
        self._body_queue.put_nowait(data)

    def _finish_body(self) -> None:
        """确保 body 迭代能终结(handle done 时调,避免 async for 永远挂)."""
        if not self._body_done:
            self._body_done = True
            self._body_queue.put_nowait(_BODY_DONE)
