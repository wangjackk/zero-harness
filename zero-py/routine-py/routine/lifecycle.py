"""LifecycleManager ---- create / start / stop / destroy 四个生命周期入口.

routine 包简化:单 hub in-proc,实例表直接按 rid 键(无 peer 维度),无 force_stop_peer
(link 永不断).

_stop_finalized + _send_stopped 幂等守卫保证一条 invocation 只发一次 stopped:
runner(正常完成/error)与 stop_runner(stop/超时)都调 _send_stopped,先到者
发,后到者 no-op.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, Optional, Type

from .ctx import RunContext
from .protocol import ControlDoneReason
from .routine import Routine

# on_stopped reason 字符串 -> wire enum 映射.
_REASON_TO_ENUM: Dict[str, ControlDoneReason] = {
    'auto': ControlDoneReason.AUTO,
    'stop': ControlDoneReason.STOP,
    'destroyed': ControlDoneReason.DESTROYED,
    'error': ControlDoneReason.ERROR,
    'cancel': ControlDoneReason.CANCEL,
    'force': ControlDoneReason.FORCE,
}


class LifecycleManager:
    STOP_TIMEOUT = 3.0

    def __init__(self, hub, runtime):
        self.hub = hub
        self.runtime = runtime

    async def _send_stopped(self, rid: str, *, reason: str,
                            instance: Optional[Routine] = None,
                            result: Any = None,
                            error: Optional[str] = None) -> None:
        wire_reason = _REASON_TO_ENUM.get(reason, ControlDoneReason.UNKNOWN)
        # 幂等守卫:runner 与 stop_runner 并发都调,靠 instance 上的
        # _stopped_sent 标志保证只发一次.instance=None(routine 未找到,一次性
        # 发,无竞态)跳过守卫.
        if instance is not None and not instance._mark_stopped_sent():
            return
        await self.hub.send_lifecycle_stopped(
            id=rid, reason=wire_reason, result=result, error=error,
        )

    def _cleanup(self, rid: str) -> None:
        inst = self.runtime.running_instances.pop(rid, None)
        if inst is not None:
            inst.mark_not_started()
        self.runtime.pop_created(rid)
        # mailbox 关停:停接收 + 取消消费者(在途回调随实例终结)
        self.runtime.close_mailbox(rid)
        # pubsub 本地 handler 表清掉(kernel 侧订阅在 lifecycle.stopped 时已自动退订)
        self.runtime.pop_subscriber(rid)
        # lifecycle 观察者注销:最后一个观察者注销时关流(lifecycle.unwatch)
        if self.runtime.pop_lifecycle_watcher(rid) is not None \
                and self.runtime.lifecycle_watcher_count() == 0:
            self.runtime._spawn(self.hub.send_lifecycle_unwatch())

    async def _instantiate(self, rid: str, name: str,
                           cls: Type[Routine], kwargs: Dict[str, Any]):
        """created 共用的实例化:resolve instance → 注册路由表 → 绑 ctx → 存
        init_kwargs → auto_subscribe."""
        instance = self.runtime.resolve_instance(rid, cls)
        self.runtime.running_instances[rid] = instance
        # restart/复用时清终态(runner 守卫查它)
        instance.clear_stop_finalized()
        # message.* 路由表在 created 注册:created 后即可收 req/stream/send
        self.runtime.register_created(rid, instance)
        # mailbox 串行投递通道同步建立(重建先关旧, restart 语义)
        self.runtime.open_mailbox(rid, instance)
        # ctx 绑到 created:on_created() hook + 所有发送能力可用
        ctx = RunContext(
            id=rid, name=name, io=self.hub, routine=instance,
            runtime=self.runtime, transport=self.hub.transport,
        )
        instance.bind_ctx(ctx)
        # init_kwargs = create 入参(routine 的唯一入参来源)
        instance.set_init_kwargs(kwargs)
        # pubsub 订阅在 created:同步 await--created 回报前确保 kernel 订阅表
        # 已更新,避免 publish race.
        try:
            await instance._auto_subscribe()
        except Exception as exc:
            self.runtime.logger.exception(f'{name}#{rid} auto-subscribe failed: {exc}')
        return instance, ctx

    async def handle_created(self, msg: Dict[str, Any]) -> None:
        """lifecycle.created(kernel→hub):实例化 + 绑 ctx + 注册路由表 +
        auto_subscribe,然后发 lifecycle.created 回报(带 modules).

        全部通信激活放在 created(而非 start)----created 后即可收发所有消息.
        created 失败发 stopped(无实例)→ kernel 视为 created 失败.
        """
        rid = str(msg.get('id', ''))
        name = msg.get('name', '')
        kwargs = msg.get('kwargs') or {}
        parent_rid = str(msg.get('parent_id', '') or '')

        cls = self.runtime.routines.get_routine(name)
        if cls is None:
            self.runtime.logger.error(f'routine not found: {name}#{rid}')
            await self._send_stopped(rid, reason='error')
            return

        instance, ctx = await self._instantiate(rid, name, cls, kwargs)
        if parent_rid and parent_rid != '0':
            instance._set_parent_rid(parent_rid)

        # modules 声明走 cls.modules(kwargs) 类方法(无实例可达,注册期/编排器
        # 静态冲突预测同源);on_created() 纯轻量初始化,返回非 None 即协议违规.
        # 异常不阻断:按已算到的 modules 回报,让 start 阶段的 TryAcquire 兜底.
        mods: list = []
        try:
            mods = [str(m) for m in cls.modules(kwargs)]
            result = await instance.on_created(rid=rid, kwargs=kwargs)
            if result is not None:
                raise TypeError(
                    f'{name}.on_created must return None; module declaration '
                    'belongs to the modules() classmethod')
        except Exception as exc:
            self.runtime.logger.exception(f'{name}#{rid} created failed: {exc}')
        await self.hub.send_lifecycle_created(id=rid, modules=mods)

    async def handle_start(self, msg: Dict[str, Any]) -> None:
        """lifecycle.start(kernel→hub):跑 runner(ack_start → run → stopped)."""
        rid = str(msg.get('id', ''))
        name = msg.get('name', '')

        instance = self.runtime.running_instances.get(rid)
        if instance is None:
            # instance 已被 stop/destroy 清理(级联 stop 与 start 竞态).记日志跳过.
            self.runtime.logger.warning(
                f'{name}#{rid}: start skipped (instance already cleaned up by stop/destroy)')
            return
        ctx = instance.ctx  # created 已绑;init_kwargs 已在 created 存

        # run 入参:用 created 时存入 instance.init_kwargs 的那份 create kwargs
        start_kwargs = instance.init_kwargs or {}

        async def runner():
            if instance.is_stop_finalized():
                return
            instance.reset_for_start()
            instance._set_main_task(asyncio.current_task())
            result = None
            try:
                await ctx.ack_start()
                instance.mark_started()  # 父已 started,可 start/stop 子 routine
                await instance.on_started()  # started hook:run 之前调
                run_val = instance.run(start_kwargs)
                if inspect.isasyncgen(run_val):
                    # yield 模式:run 是 async generator,迭代每项发 routine.yield
                    result = await self._run_yield(ctx, run_val)
                else:
                    result = await run_val
            except asyncio.CancelledError:
                if instance.is_stop_finalized():
                    return
                await instance.on_stopped(reason='cancel')
                self._cleanup(rid)
                return
            except Exception as exc:
                self.runtime.logger.exception(f'{name}#{rid} failed: {exc}')
                await instance.on_stopped(reason='error', detail=str(exc))
                await self._send_stopped(
                    rid, instance=instance, reason='error', error=str(exc),
                )
                self._cleanup(rid)
                return
            reason = ('force' if instance.is_force_stop()
                      else 'stop' if instance.is_stop_requested() else 'auto')
            await instance.on_stopped(reason=reason, result=result)
            await self._send_stopped(
                rid, instance=instance, reason=reason, result=result,
            )
            self._cleanup(rid)

        self.runtime._spawn(runner())

    async def _run_yield(self, ctx, gen) -> None:
        """迭代子 routine 的 async-gen run:每 yield 一项发 routine.yield,
        正常结束发 is_final;抛异常发 is_final+error;CancelledError(stop)不发,
        靠后续 stopped 让 parent 的 yield 迭代终结.
        """
        try:
            async for item in gen:
                await ctx._send_yield(item, is_final=False)
            await ctx._send_yield(is_final=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await ctx._send_yield(is_final=True, error=str(exc))
            raise

    async def handle_destroy(self, msg: Dict[str, Any]) -> None:
        """lifecycle.destroy:kernel 销毁 created 态 routine(已 create 未 start).

        created 态销毁走独立 destroy hook(与 stop 分野: stop 一个从未
        start 的实例语义荒谬) ---- start 前实例可能已收 body 持有资源,
        destroy hook 负责撤销. 用于:destroy / start 失败清理 / 父终止
        级联清 created 子.
        """
        rid = str(msg.get('id', ''))
        instance = self.runtime.running_instances.get(rid)
        if instance is None:
            return
        instance._request_stop()
        stop_result = None
        try:
            stop_result = await asyncio.wait_for(
                instance.destroy(), timeout=self.STOP_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self.runtime.logger.warning(
                f'{instance.name}#{rid} destroy: destroy() timeout',
            )
        except Exception as exc:
            self.runtime.logger.exception(
                f'{instance.name}#{rid} destroy: destroy() failed: {exc}',
            )
        await instance.on_stopped(reason='destroyed', result=stop_result)
        await self._send_stopped(rid, instance=instance, reason='destroyed')
        self._cleanup(rid)

    async def _graceful_stop(self, rid: str, instance: Routine, *,
                             force: bool = False, by: str = '',
                             notify_kernel: bool = True) -> None:
        """graceful stop 核心: stop hook → 等/超时 cancel → on_stopped →
        cleanup. notify_kernel=False 供 transport 断连本地销毁(kernel 世界
        已死, stopped 回报无意义)."""
        self.runtime.logger.info(f'stop_runner #{rid} begin')
        try:
            if not instance.is_stop_requested():
                instance._request_stop(force=force, by=by)
            stop_result = None
            try:
                stop_result = await asyncio.wait_for(
                    instance.stop(), timeout=self.STOP_TIMEOUT,
                )
                self.runtime.logger.info(f'stop_runner #{rid}: stop() done')
            except asyncio.TimeoutError:
                self.runtime.logger.warning(
                    f'{instance.name}#{rid} stop() timeout; fallback cancel',
                )
            except Exception as exc:
                self.runtime.logger.exception(
                    f'{instance.name}#{rid} stop() failed: {exc}',
                )
            # stop() 只是 grace hook(set event 让 run 退出);返回后必须保证
            # main task 真正终止.先标 _stop_finalized 让 runner 的
            # CancelledError 分支跳过 cleanup(本函数接管),再等 main task
            # done,超时则 cancel.
            instance.finalize_stop()
            instance.mark_not_started()  # 已 stop,不能再 start/stop 子
            task = instance._pending_main_task()
            if task is not None:
                try:
                    stop_result = await asyncio.wait_for(
                        task, timeout=self.STOP_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    self.runtime.logger.warning(
                        f'{instance.name}#{rid} main task did not exit; cancel',
                    )
                    await instance._cancel_main_task(reason='stop')
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self.runtime.logger.exception(
                        f'{instance.name}#{rid} main task await error: {exc}',
                    )
            on_reason = 'force' if instance.is_force_stop() else 'stop'
            on_detail = (f'evicted by {instance.stop_by()}'
                         if instance.is_force_stop() else '')
            await instance.on_stopped(reason=on_reason, result=stop_result,
                                      detail=on_detail)
            if notify_kernel:
                await self._send_stopped(
                    rid, instance=instance, reason=on_reason,
                    result=stop_result,
                )
            self._cleanup(rid)
        finally:
            instance.clear_stop_in_progress()

    async def handle_stop(self, msg: Dict[str, Any]) -> None:
        """lifecycle.stop(kernel→hub):graceful stop(stop hook → 等/超时 cancel →
        stopped 回报).带 reason=force 时是驱逐,透传到 on_stopped."""
        rid = str(msg.get('id', ''))
        instance = self.runtime.running_instances.get(rid)
        if instance is None:
            # 良性竞态: 子自然终止的 stopped 回报在途(kernel 侧 stopped_fut 尚未
            # resolve, 级联停走了 cmd.stop() 分支), 而本侧实例已清. stopped 随后
            # 到达即正常收尾, 无挂死 ---- 降 debug 不占 WARNING 通道.
            self.runtime.logger.debug(f'lifecycle.stop #{rid}: no instance')
            return
        if not instance.begin_stop():
            self.runtime.logger.warning(
                f'lifecycle.stop #{rid}: begin_stop False '
                f'(in_progress={instance.is_stop_in_progress()}, '
                f'finalized={instance.is_stop_finalized()})')
            return
        # force 驱逐:kernel 发 lifecycle.stop 带 reason="force" + by(evictor rid).
        stop_reason = str(msg.get('reason', ''))
        stop_by = str(msg.get('by', ''))
        force = stop_reason == 'force'
        self.runtime._spawn(self._graceful_stop(
            rid, instance, force=force, by=stop_by))

    async def stop_all_local(self) -> None:
        """transport 断连的本地全量销毁: 不区分 passive 一律停掉 ---- 老实例
        会以 stale rid 继续发 req, 污染注册表与对账; 重连后 catalog push
        重建, 拿新 rid. stopped 不回报 kernel(世界已死).

        返回销毁 join ---- 调用方必须等它完成再进入重连期: 销毁是异步的
        (stop hook + main task 各 STOP_TIMEOUT), 若销毁期与重建期重叠,
        kernel 重启后 rid 从头分配会撞上销毁中的老 rid, 老 cleanup 误杀
        重建的新实例. join 有界(并行, 每实例 ~2×STOP_TIMEOUT)."""
        tasks = []
        for rid, instance in list(self.runtime.running_instances.items()):
            if not instance.begin_stop():
                continue
            tasks.append(asyncio.ensure_future(
                self._graceful_stop(rid, instance, notify_kernel=False)))
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                self.runtime.logger.error(
                    f'stop_all_local: instance stop failed: {result!r}')
