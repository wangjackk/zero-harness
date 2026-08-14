"""LifecycleManager ---- create / start / stop 三个生命周期入口(精简版).

只做 create/start/stop(无 body consumer / shell cascade / available_routines /
push_parent / token_parent / pause / resume / heartbeat).

_stop_finalized + _send_stopped 幂等守卫保证一条 invocation 只发一次 stopped:
runner(正常完成/error)与 stop_runner(stop/超时)都调 _send_stopped,先到者
发,后到者 no-op.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, Optional, Type

from .ctx import RunContext
from .protocol import ControlDoneReason, LIFECYCLE_CREATED, LIFECYCLE_DESTROY
from .routine import Routine

# on_stopped reason 字符串 -> wire enum 映射.lifecycle 内部用语义字符串(跟
# instance.on_stopped(reason=...) 同源),_send_stopped 发 wire 时映射成 enum.
# 扩到 6 值后 wire 不再把 auto/force/disconnect 都压成 STOP -- 父侧可分流
# (force=被驱逐,disconnect=infra 断连,auto=自然结束).
_REASON_TO_ENUM: Dict[str, ControlDoneReason] = {
    'auto': ControlDoneReason.AUTO,
    'stop': ControlDoneReason.STOP,
    'error': ControlDoneReason.ERROR,
    'cancel': ControlDoneReason.CANCEL,
    'force': ControlDoneReason.FORCE,
    'disconnect': ControlDoneReason.DISCONNECT,
}


class LifecycleManager:
    STOP_TIMEOUT = 3.0

    def __init__(self, server, runtime):
        self.server = server
        self.runtime = runtime

    async def _send_stopped(self, prid: str, *, id: str, peer_id: str,
                            reason: str,
                            instance: Optional[Routine] = None,
                            result: Any = None,
                            error: Optional[str] = None) -> None:
        # reason 是 on_stopped 语义字符串(auto/stop/error/cancel/force/disconnect),
        # 映射成 wire enum(大写).与同处调的 instance.on_stopped(reason=...) 共用同一
        # 词汇,避免两套词表漂移(原 ControlDoneReason 3 值丢 cancel/force/disconnect).
        wire_reason = _REASON_TO_ENUM.get(reason, ControlDoneReason.UNKNOWN)
        # 幂等守卫:runner 与 stop_runner/force_stop 并发都会调,靠 instance 上的
        # _stopped_sent 标志保证只发一次(随 instance GC 自动回收,无全局 set 泄漏).
        # instance=None(handle_created reject:routine 未找到无实例,一次性发,无竞态)跳过守卫.
        if instance is not None and not instance._mark_stopped_sent():
            return
        await self.server.send_lifecycle_stopped(
            id=id, reason=wire_reason, result=result, error=error, peer_id=peer_id,
        )

    def _cleanup(self, prid: str) -> None:
        # 取 instance(cleanup 后还要 set _started=False / close inbox,所以先拿引用)
        inst = self.runtime.running_instances.pop(prid, None)
        if inst is not None:
            inst.mark_not_started()
        # rid 是 prid 末段(peer_id 含 ':',用 rsplit 从右取 rid)
        rid = prid.rsplit(':', 1)[-1]
        self.runtime.pop_created(rid)
        # pubsub 本地 handler 表清掉(kernel 侧订阅在 lifecycle.stopped 时已自动退订)
        self.runtime.pop_subscriber(rid)
        # stopped 幂等标志在 instance 上(_stopped_sent),随 instance pop 一起 GC,
        # 不用全局 set 永久堆积.

    async def _instantiate(self, peer_id: str, rid: str, name: str,
                           cls: Type[Routine], kwargs: Dict[str, Any]):
        """created / start-fallback 共用的实例化:resolve instance -> 注册路由表
        -> 绑 ctx -> 存 init_kwargs -> auto_subscribe.created 走本路径后额外调
        on_created 回报 modules;start fallback(created 未到的兜底)走本路径后
        直接进 runner.kwargs 在 created 是真实 submit 入参,fallback 兜底用 {}.

        所有 _ 前缀字段写入经 Routine 的 internal API 收口(bind_ctx /
        set_init_kwargs / clear_stop_finalized),lifecycle 不再直写 Routine 私有态.
        """
        prid = f'{peer_id}:{rid}'
        instance = self.runtime.resolve_instance(prid, cls)
        self.runtime.running_instances[prid] = instance
        # restart/复用时清终态(runner 守卫查它,不再清零).fresh 实例 __init__
        # 已置 False,此处对复用实例生效.
        instance.clear_stop_finalized()
        # message.* 路由表在 created 注册:created 后即可收 req/stream/send
        # (handler 表 __init__ 已建).
        self.runtime.register_created(rid, instance)
        # ctx 绑到 created:on_created() hook + 所有发送(req/publish/send/submit/...)可用.
        ctx = RunContext(
            id=rid, name=name, peer_id=peer_id, io=self.server, routine=instance,
        runtime=self.runtime, transport=self.server.transport,
        )
        instance.bind_ctx(ctx)
        # init_kwargs = submit 入参(routine 的唯一入参来源):created hook 用 +
        # run() 复用同一份(handle_start 从 init_kwargs 灌进 run()).
        instance.set_init_kwargs(kwargs)
        # pubsub 订阅在 created:同步 await--created 回报前确保 kernel 订阅表已更新,
        # 避免 publish race.@subscribe 装饰器 -> 注册本地 handler + 发 pubsub.subscribe.
        try:
            await instance._auto_subscribe()
        except Exception as exc:
            self.runtime.logger.exception(f'{name}#{rid} auto-subscribe failed: {exc}')
        return instance, ctx

    async def handle_created(self, peer_id: str, msg: Dict[str, Any]) -> None:
        """lifecycle.created(调度器→server):实例化 + 绑 ctx + 注册路由表 +
        建 inbox 队列 + auto_subscribe,然后发 lifecycle.created 回报.

        全部通信激活放在 created(而非 start)----created 后即可
        收发所有消息:req/stream(``get_created`` 命中,handler 表在 ``__init__`` 已建)
        + inbox(队列已建)+ pubsub(``_auto_subscribe`` 已发订阅给 kernel)+ 所有发送
        (ctx 已绑).**run 只负责跑 ``run()`` 体**,不再激活任何通信能力.

        ``_auto_subscribe`` 同步 await 在 created 回报前----保证 kernel 订阅表在 created
        时已更新,避免 publish race.created 失败发 stopped → kernel 自动退订 +
        ``_cleanup`` 清本地表.
        """
        rid = str(msg.get('id', ''))
        name = msg.get('name', '')
        kwargs = msg.get('kwargs') or {}
        parent_rid = str(msg.get('parent_id', '') or '')
        prid = f'{peer_id}:{rid}'

        cls = self.runtime.routines.get_routine(name)
        if cls is None:
            self.runtime.logger.error(f'routine not found: {name}#{rid}')
            await self._send_stopped(
                prid, id=rid, peer_id=peer_id, reason='error',
            )
            return

        instance, ctx = await self._instantiate(peer_id, rid, name, cls, kwargs)
        # 记录父 routine id(kernel 经 lifecycle.created 带来,0/空=无父 root).
        # routine 通过 self.parent_rid 反向 req 父(如 tool routine 获取 agent state).
        if parent_rid and parent_rid != '0':
            instance._set_parent_rid(parent_rid)

        # instance.on_created() 用户钩子(早于 start):返回声明的 modules(Modules 类型)
        # 或 None(不占模块).await 拿返回值再发 created 回报----modules 是 on_created()
        # 算的(单一真理源:实例级,static 返 Modules([...]),dynamic 按 kwargs 现算),
        # 经 created 回报回带 kernel,存进 node.declared(Start 的 TryAcquire 用)+ 经
        # submitted 回执带给父 handle(编排器算冲突).on_created() 应轻量(慢逻辑放
        # start)----它阻塞 created 回报.异常不阻断:created 失败按空 modules 回报,
        # 让 start 阶段的 TryAcquire 兜底(空 modules 不占不冲突,routine 仍能起).
        mods: list = []
        try:
            result = await instance.on_created(rid=rid, kwargs=kwargs)
            # None / 不返回 → [];Modules(list 子类)/list → 取元素转 str
            if result is not None and isinstance(result, list):
                mods = [str(m) for m in result]
        except Exception as exc:
            self.runtime.logger.exception(f'{name}#{rid} created failed: {exc}')
        await self.server.send_lifecycle_created(
            id=rid, modules=mods, peer_id=peer_id)

    async def handle_start(self, peer_id: str, msg: Dict[str, Any]) -> None:
        rid = str(msg.get('id', ''))
        name = msg.get('name', '')
        prid = f'{peer_id}:{rid}'

        cls = self.runtime.routines.get_routine(name)
        if cls is None:
            await self._send_stopped(
                prid, id=rid, peer_id=peer_id, reason='error',
            )
            return

        # 统一路径:kernel 必先发 lifecycle.created 再发 lifecycle.start(runRemote
        # 先 created 再 start,跟 submit 路径一致).instance 一定已在 created 阶段
        # 实例化 + 绑 ctx + register_created + auto_subscribe(inbox 惰性建在实例上).
        instance = self.runtime.running_instances.get(prid)
        if instance is None:
            # instance 已被 stop/destroy 清理(级联 stop 与 start 竞态:
            # kernel created 后发 start,同时父被 stop 级联发 stop,
            # Python 先处理 stop 清了 instance).start 到达时 instance 已不在,
            # stop 已完成 cleanup,不兜底重建——记日志跳过.
            self.runtime.logger.warning(
                f'{name}#{rid}: start skipped (instance already cleaned up by stop/destroy)'
            )
            return
        else:
            ctx = instance.ctx  # created 已绑;init_kwargs 已在 created 存

        # run 入参:用 created 时存入 instance.init_kwargs 的那份 submit kwargs
        # (submit kwargs 是 start 的唯一入参来源--created 和 start 共用同一份.
        # lifecycle.start 事件不再带 kwargs).created 已用同一份调过 on_created().
        start_kwargs = instance.init_kwargs or {}

        async def runner():
            if instance.is_stop_finalized():
                return
            instance.reset_for_start()
            instance._set_main_task(asyncio.current_task())
            # 通信能力在 created 已全部就绪(ctx + register_created + auto_subscribe
            # 都在 created).run 只负责跑 run() 体,不再激活任何通信.
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
                    # force_stop 接管完整 cleanup(disconnect 路径),runner 退出即可
                    return
                await instance.on_stopped(reason='cancel')
                self._cleanup(prid)
                return
            except Exception as exc:
                self.runtime.logger.exception(f'{name}#{rid} failed: {exc}')
                await instance.on_stopped(reason='error', detail=str(exc))
                await self._send_stopped(
                    prid, id=rid, peer_id=peer_id, instance=instance,
                    reason='error', error=str(exc),
                )
                self._cleanup(prid)
                return
            reason = 'stop' if instance.is_stop_requested() else 'auto'
            await instance.on_stopped(reason=reason, result=result)
            await self._send_stopped(
                prid, id=rid, peer_id=peer_id, instance=instance,
                    reason=reason, result=result,
            )
            self._cleanup(prid)

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

    async def handle_destroy(self, peer_id: str, msg: Dict[str, Any]) -> None:
        """lifecycle.destroy:调度器销毁 created 态 routine(已 submit 未 start).

        created instance 没 main task / 没 run() 体可停----直接 _cleanup 销毁 +
        发 lifecycle.stopped 回报.区别于 lifecycle.stop(打断 started 态的 runner):
        destroy 跳过 stop hook / 等 body 退出(created 没 body).

        用于:unsubmit / start 失败清理 / 父终止级联清 created 子.
        """
        rid = str(msg.get('id', ''))
        prid = f'{peer_id}:{rid}'
        instance = self.runtime.running_instances.get(prid)
        if instance is None:
            return
        await instance.on_stopped(reason='stop')
        await self._send_stopped(
            prid, id=rid, peer_id=peer_id, instance=instance,
            reason='stop',
        )
        self._cleanup(prid)

    async def handle_stop(self, peer_id: str, msg: Dict[str, Any]) -> None:
        rid = str(msg.get('id', ''))
        prid = f'{peer_id}:{rid}'
        instance = self.runtime.running_instances.get(prid)
        if instance is None:
            return
        if not instance.begin_stop():
            return
        # force 驱逐:kernel 发 lifecycle.stop 带 reason="force" + by(evictor rid).
        # 透传到 on_stopped 让被驱逐者走紧急退让分支(区别于 graceful stop 收尾).
        stop_reason = str(msg.get('reason', ''))
        stop_by = str(msg.get('by', ''))
        force = stop_reason == 'force'

        async def stop_runner():
            try:
                if not instance.is_stop_requested():
                    instance._request_stop()
                stop_result = None
                try:
                    stop_result = await asyncio.wait_for(
                        instance.stop(), timeout=self.STOP_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    self.runtime.logger.warning(
                        f'{instance.name}#{rid} stop() timeout; fallback cancel',
                    )
                except Exception as exc:
                    self.runtime.logger.exception(
                        f'{instance.name}#{rid} stop() failed: {exc}',
                    )
                # stop() 只是 grace hook(set event 让 start 退出);返回后必须保证
                # main task 真正终止.先标 _stop_finalized 让 runner 的 CancelledError
                # 分支跳过 cleanup(本函数接管),再等 main task done,超时则 cancel.
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
                on_reason = 'force' if force else 'stop'
                on_detail = f'evicted by {stop_by}' if (force and stop_by) else ''
                await instance.on_stopped(reason=on_reason, result=stop_result,
                                        detail=on_detail)
                await self._send_stopped(
                    prid, id=rid, peer_id=peer_id, instance=instance,
                    reason=on_reason, result=stop_result,
                )
                self._cleanup(prid)
            finally:
                instance.clear_stop_in_progress()

        self.runtime._spawn(stop_runner())

    async def force_stop_peer(self, peer_id: str) -> None:
        """peer 断连时强制清理该 peer 的所有 running instance.

        对标原版 stop_instance(disconnect):cancel_main_task → stop() best-effort
        (STOP_TIMEOUT)→ on_stopped(reason='disconnect')→ send_stopped(peer 已不在,
        队列已 pop,send 静默丢弃,保持幂等语义完整)→ _cleanup.

        设 _stop_finalized=True 让 runner 的 CancelledError 分支跳过重复 cleanup,
        force_stop 接管完整收尾.由 stream.py 的 Stream handler finally 触发.
        """
        prids = [prid for prid in list(self.runtime.running_instances)
                 if prid.startswith(f'{peer_id}:')]
        for prid in prids:
            await self._force_stop_one(prid)

    async def _force_stop_one(self, prid: str) -> None:
        inst = self.runtime.running_instances.get(prid)
        if inst is None:
            return
        if not inst.begin_stop():
            return
        inst.finalize_stop()  # force 路径立即标终态:runner CancelledError 跳过 cleanup
        inst.mark_not_started()
        peer_id, rid = prid.rsplit(':', 1)
        self.runtime.logger.info(f'⏹️ force stop {inst.name}#{rid} (peer {peer_id} disconnected)')
        try:
            await inst._cancel_main_task(reason='disconnect')
            try:
                await asyncio.wait_for(inst.stop(), timeout=self.STOP_TIMEOUT)
            except asyncio.TimeoutError:
                self.runtime.logger.warning(
                    f'{inst.name}#{rid} force stop timeout (disconnect); ignored'
                )
            except Exception as exc:
                self.runtime.logger.exception(
                    f'{inst.name}#{rid} force stop failed: {exc}'
                )
            await inst.on_stopped(reason='disconnect')
            await self._send_stopped(
                prid, id=rid, peer_id=peer_id, instance=inst,
                reason='disconnect',
            )
            self._cleanup(prid)
        finally:
            inst.clear_stop_in_progress()
