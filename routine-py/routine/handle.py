"""RoutineHandle ---- 父侧拿到的"指向一次 submit 的子 routine"的本地句柄.

精简版只保留 start/stop/wait + started/done 状态,
无 body upstream writer / extend_data / on_started-on_stopped 回调.

handle.id = kernel 分配的 command id(string),
child_id = 子 routine 的 id.python 不自己生成 id,submit 时经 kernel 回环拿到.

notify 路径统一经 kernel 中转:子 routine 自己发 lifecycle.started/stopped 给 kernel,
kernel 把它中转回来,server.on_inbound 按 child_id 找到 handle 调 notify_started /
notify_done,唤醒 handle.wait().不在 send 时本地抄近路.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from .errors import StartError


# body 迭代终结哨兵( distinguish from yielded None / result)
_BODY_DONE = object()


class RoutineHandle:
    """父侧 submit handle.id 是 kernel 分配的子 command id(string).

    若子 routine 的 start 是 async generator(yield),handle 也是 async iterable:
    ``async for item in handle:`` 拿到每个 yield 的项.yield 帧由 server.on_inbound
    收到 routine.yielded 时调 _on_yield_chunk 投喂.
    """

    def __init__(self, child_id: str, name: str, ctx=None, modules=None):
        self.id: str = child_id
        self.name: Optional[str] = name
        # submit 时 kernel 确定的占用 modules(static=catalog 缓存值,dynamic=按 kwargs
        # 现算值).编排器据此算冲突(``ctx.conflict(h1.modules, h2.modules)``).None =
        # 旧路径未带(submitted 回执没 modules 字段时,兼容).
        self.modules: Optional[list] = modules
        self.result: Any = None
        self.error: Optional[str] = None
        # lifecycle.stopped 的 wire reason(大写 enum 值,on_inbound 透传).父侧可读
        # 分流(force=被驱逐 / disconnect=infra 断连 / auto=自然结束 / stop=被停 / ...);
        # wait() 不据此分流(仍按 error raise),reason 仅作附加诊断信息.None=非
        # on_inbound 驱动(本地兜底构造).
        self.reason: Optional[str] = None
        self._ctx = ctx
        self._started = asyncio.Event()
        self._done = asyncio.Event()
        # start/try_start/force_start/stop/unsubmit 的 ack future.None=无在途操作.
        # server.on_inbound 收 lifecycle.started/stopped / routine.rejected 时按
        # child_id 经 _handles 表找到本 handle,调 _resolve_ack/_reject_ack 唤醒它.
        # 跟 _done 是两条独立通路:_done 等 lifecycle.stopped(handle.wait),
        # _ack 等单次操作的即时回执(start 成功/失败,stop 完成).
        # fire 模式(stop/unsubmit fire=True)不设 _ack--只发 wire,靠 _done 解除.
        self._ack: Optional[asyncio.Future] = None
        # 生命周期回调(async callable(handle) | None):父侧 push 通知.供 async-generator
        # 流式 yield 场景(Act:子 done 时 put queue,run async gen 从 queue 拉 yield 给父)--
        # 这种场景不能 await handle.wait() 阻塞整个 run.跟 routine 侧 on_started/on_stopped
        # (async override,子侧自身生命周期)是两个层面,故命名加 _handler 后缀区分.
        # async 是因为回调里可能要 await(send 个 wire / 等个子 / 触发编排动作)--回调签名
        # 统一 async,调用方(notify_*)fire-and-forget spawn,不阻塞 reader 协程.
        # 幂等:notify_started/notify_done 各靠 Event 只调一次.
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
        """发 wire + 注册 ack + 等回执的公共模板.

        ack future 存在本 handle 的 ``_ack`` 字段(不进 runtime 全局表),server
        按 child_id 经 ``_handles`` 表找到本 handle 后 ``_resolve_ack``/``_reject_ack``
        唤醒.ack 跟 ``_done`` 通路独立:_done 等 lifecycle.stopped(handle.wait),
        _ack 等本次操作回执(start 成功/失败,stop/unsubmit 完成).

        ``expects_err``:True=start/force_start(rejected 回 str error,调方包成
        StartError 返回);False=stop/unsubmit(rejected 抛异常,调方直接 await).
        """
        if self._ack is not None and not self._ack.done():
            # close the unused send_coro: it was created by the caller (e.g.
            # send_routine_stop(...)) and never awaited here -> Python would
            # warn "coroutine ... was never awaited". close() marks it done.
            send_coro.close()
            raise RuntimeError(f'{self}: another start/stop in flight')
        fut = asyncio.get_running_loop().create_future()
        self._ack = fut
        try:
            await send_coro
            return await fut
        finally:
            if self._ack is fut:
                self._ack = None

    def _resolve_ack(self, value: Any) -> None:
        """server.on_inbound 收到成功回执(lifecycle.started/stopped)时调.幂等."""
        if self._ack is not None and not self._ack.done():
            self._ack.set_result(value)

    def _reject_ack(self, exc: BaseException) -> None:
        """server.on_inbound 收到 rejected(op=stop/unsubmit)时调.幂等."""
        if self._ack is not None and not self._ack.done():
            self._ack.set_exception(exc)

    async def _require_ctx(self, op: str) -> None:
        if self._ctx is None:
            raise RuntimeError(f'{self}: no ctx bound, cannot {op}')

    async def _start_common(self, op: str, send_coro, *,
                            try_mode: bool) -> Optional[StartError]:
        """start/try_start/force_start 公共骨架.

        try_mode=True(try_start):失败返回 StartError(None=成功)--保留可重试,
        不打断调用方 run() 体.try_mode=False(start/force_start):失败 raise
        StartError--跟 ReqError 一致,调用方 try/except 或让异常上抛.
        """
        await self._require_ctx(op)
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

        失败(模块冲突/父未 started)时不清理 instance/node--保留是为了让
        Python 侧能再次调 try_start/start 重试(占住者释放后第二次能成功).
        真正清理走 stop()/级联 stop/断线 force_stop_peer.

        失败返回 StartError(None=成功),不抛--模块冲突是正常业务情况
        (占住者还没释放),不该打断调用方 run() 体.需 ctx 绑定 + 父 started.
        """
        return await self._start_common(
            'start',
            self._ctx._io.send_routine_start(  # type: ignore[union-attr]
                child_id=self.id, try_start=True, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
            ),
            try_mode=True,
        )

    async def start(self) -> None:
        """让 kernel start 子命令(占模块+运行),全有或全无--失败时 kernel
        清 node+订阅,本侧清 created instance(on_inbound 收到 try=false 的
        routine.rejected 时清).handle 失败后不可重试(已失效).

        失败 raise StartError(跟 try_start 的返回 Optional 区分)--与 ReqError
        等通信错误一致,调用方 try/except 或让异常上抛.需 ctx 绑定 + 父 started.
        """
        await self._start_common(
            'start',
            self._ctx._io.send_routine_start(  # type: ignore[union-attr]
                child_id=self.id, try_start=False, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
            ),
            try_mode=False,
        )

    async def force_start(self) -> None:
        """抢占式 start:让 kernel 驱逐占住子 declared 模块的第三方(cascade stop,
        reason='force' 透传)后 start.区别于 start/try_start--那俩冲突就放弃/等,
        force_start 主动打断占住者抢过来.

        失败 raise StartError(跟 start 一致).单轮驱逐不重试(竞态失败).
        永不驱逐祖先.需 ctx 绑定 + 父 started.
        """
        await self._start_common(
            'force_start',
            self._ctx._io.send_routine_force_start(  # type: ignore[union-attr]
                child_id=self.id, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
            ),
            try_mode=False,
        )
    async def stop(self, *, fire: bool = False) -> None:
        """让 kernel stop 子命令(级联).

        ``fire=False``(默认):发 routine.stop 后等 lifecycle.stopped 回执确认子
        已停--返回时子确定停完.需 ctx 绑定 + 父 started.

        ``fire=True``:fire-and-forget,只发 wire 不等 ack.供 ``Shell.interrupt``
        top-down 并发打断用--绕过 ack 等待,子的 lifecycle.stopped 到达后由
        ``handle.wait``(``await handle``)自然解除,跟 ack 通路独立.不等 ack 是
        因为 interrupt 要同时停多个子,fire 不阻塞让 interrupt 立即返回;调用方靠
        ``await handle`` 收 stopped.
        """
        await self._require_ctx('stop')
        if fire:
            await self._ctx._io.send_routine_stop(  # type: ignore[union-attr]
                child_id=self.id, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
            )
            return
        self._ctx._require_parent_started('stopping child')  # type: ignore[union-attr]
        await self._send_and_wait(
            self._ctx._io.send_routine_stop(  # type: ignore[union-attr]
                child_id=self.id, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
            ),
            expects_err=False,
        )

    async def unsubmit(self, *, fire: bool = False) -> None:
        """撤销提交:清 created 态子命令(未 start 的).跟 submit 对称.

        已 start 的 routine 调本方法会被 kernel 拒(rejected op=unsubmit)--
        该用 stop.不要求父 started(submit 也不要求).需 ctx 绑定.

        ``fire=True``:fire-and-forget,供 ``Shell.interrupt`` 撤未 start 命令
        (对标老版 Go ``Shell.Interrupt`` 撤未 start 命令).详见 ``stop``.
        """
        await self._require_ctx('unsubmit')
        if fire:
            await self._ctx._io.send_routine_unsubmit(  # type: ignore[union-attr]
                child_id=self.id, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
            )
            return
        await self._send_and_wait(
            self._ctx._io.send_routine_unsubmit(  # type: ignore[union-attr]
                child_id=self.id, peer_id=self._ctx.peer_id,  # type: ignore[union-attr]
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

    # --- service-layer hooks(server reader 按 child_id 路由调用) ---

    def _fire_handler(self, handler) -> None:
        """fire-and-forget 调 async 回调:spawn(不阻塞 reader 协程,回调里可 await
        send/等子).回调只支持 async;无 ctx(未绑定)无法 spawn,直接忽略--handle
        绑定 ctx(submit 时)后才可能被 notify,正常路径 _ctx 必非 None."""
        if handler is None or self._ctx is None:
            return
        self._ctx._spawn(handler(self))

    def notify_started(self) -> None:
        self._started.set()
        self._fire_handler(self.on_started_handler)

    def notify_done(self, result: Any = None, error: Optional[str] = None,
                    reason: Optional[str] = None) -> None:
        # reason:wire 透传的 lifecycle.stopped reason(AUTO/STOP/ERROR/CANCEL/FORCE/
        # DISCONNECT),父侧可据此分流(如 force=被驱逐,disconnect=infra 断连).
        # None=非 on_inbound 驱动(如本地兜底).wait() 仍按 error raise,不据此分流.
        self.result = result
        self.error = error
        self.reason = reason
        # 兜底:错过 started 直接 done 时也唤醒 wait_started
        self._started.set()
        self._done.set()
        # body 迭代也终结(若子 routine yield 后还没发 is_final 就 stopped)
        self._finish_body()
        # stopped 回调(幂等:notify_done 只调一次).供 Act 流式 yield 工具子结果用--
        # 子 done 时 put queue,父 act 的 run async gen 从 queue 拉 yield 给 agent.
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
