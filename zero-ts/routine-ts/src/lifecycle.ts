/** LifecycleManager ---- create / start / stop / destroy 四个生命周期入口
 * (移植 z2/lifecycle.py).

 routine TS 简化:单 hub in-proc,实例表直接按 rid 键(无 peer 维度).

 _stopFinalized + stopped 幂等守卫保证一条 invocation 只发一次 stopped:
 runner(正常完成/error)与 stopRunner(stop/超时)都调 sendStopped,先到者
 发,后到者 no-op.

 JS 无任务强 cancel:stop 超时后 main task 只能告警(JS routine 必须在
 stop() hook 里协作退出),stopped 照发不阻塞收尾.
 */
import { Deferred, isAsyncIterable, withTimeout } from './async'
import { TimeoutErr } from './errors'
import { ControlDoneReason, type Msg, type Payload } from './protocol'
import type { Routine, RoutineClass } from './routine'
import { RunContext } from './ctx'
import type { RoutineHub } from './hub'
import type { ServerRuntime } from './runtime'

/** onStopped reason 字符串 → wire enum 映射. */
const REASON_TO_ENUM: Record<string, string> = {
  auto: ControlDoneReason.AUTO,
  stop: ControlDoneReason.STOP,
  error: ControlDoneReason.ERROR,
  cancel: ControlDoneReason.CANCEL,
  force: ControlDoneReason.FORCE,
}

export class LifecycleManager {
  static STOP_TIMEOUT = 3.0

  constructor(private hub: RoutineHub, private runtime: ServerRuntime) {}

  async sendStopped(rid: string, opts: {
    reason: string; instance?: Routine | null
    result?: any; error?: string | null
  }): Promise<void> {
    const wireReason = REASON_TO_ENUM[opts.reason] ?? ControlDoneReason.UNKNOWN
    // 幂等守卫:runner 与 stopRunner 并发都调,靠 instance 上的 _stoppedSent
    // 标志保证只发一次.instance=null(routine 未找到,一次性发,无竞态)跳过守卫.
    if (opts.instance && !opts.instance._markStoppedSent()) return
    await this.hub.sendLifecycleStopped(rid, wireReason, opts.result, opts.error ?? null)
  }

  cleanup(rid: string): void {
    const inst = this.runtime.runningInstances.get(rid)
    if (inst) {
      this.runtime.runningInstances.delete(rid)
      inst.markNotStarted()
    }
    this.runtime.popCreated(rid)
    // mailbox 关停:停接收 + 消费者退出(在途回调随实例终结)
    this.runtime.closeMailbox(rid)
    // pubsub 本地 handler 表清掉(kernel 侧订阅在 lifecycle.stopped 时已自动退订)
    this.runtime.popSubscriber(rid)
    // lifecycle 观察者注销:最后一个观察者注销时关流(lifecycle.unwatch)
    if (this.runtime.popLifecycleWatcher(rid) !== undefined
        && this.runtime.lifecycleWatcherCount() === 0) {
      this.runtime.spawn(this.hub.sendLifecycleUnwatch())
    }
  }

  /** created 共用的实例化:resolve instance → 注册路由表 → 绑 ctx → 存
   * initKwargs → autoSubscribe. */
  private async instantiate(rid: string, name: string,
                            cls: RoutineClass, kwargs: Payload): Promise<{ instance: Routine; ctx: RunContext }> {
    const instance = this.runtime.resolveInstance(rid, cls)
    this.runtime.runningInstances.set(rid, instance)
    // restart/复用时清终态(runner 守卫查它)
    instance.clearStopFinalized()
    // message.* 路由表在 created 注册:created 后即可收 req/stream/send
    this.runtime.registerCreated(rid, instance)
    // mailbox 串行投递通道同步建立(重建先关旧, restart 语义)
    this.runtime.openMailbox(rid, instance)
    // ctx 绑到 created:onCreated() hook + 所有发送能力可用(instantiate 只
    // 发生在 connect 之后, transport 必非空)
    const ctx = new RunContext(rid, name, this.hub, instance, this.runtime, this.hub.transport!)
    instance.bindCtx(ctx)
    // initKwargs = submit 入参(routine 的唯一入参来源)
    instance.setInitKwargs(kwargs)
    // pubsub 订阅在 created:同步 await----created 回报前确保 kernel 订阅表
    // 已更新,避免 publish race.
    try {
      await instance._autoSubscribe()
    } catch (e: any) {
      this.runtime.logger.error(`${name}#${rid} auto-subscribe failed: ${e?.stack ?? e}`)
    }
    return { instance, ctx }
  }

  /** lifecycle.created(kernel→hub):实例化 + 绑 ctx + 注册路由表 +
   * autoSubscribe,然后发 lifecycle.created 回报(带 modules).
   *
   * 全部通信激活放在 created(而非 start)----created 后即可收发所有消息.
   * created 失败发 stopped(无实例)→ kernel 视为 created 失败. */
  async handleCreated(msg: Msg): Promise<void> {
    const rid = String(msg.id ?? '')
    const name = String(msg.name ?? '')
    const kwargs: Payload = { ...(msg.kwargs ?? {}) }
    const parentRid = String(msg.parent_id ?? '')

    const cls = this.runtime.routines.getRoutine(name)
    if (!cls) {
      this.runtime.logger.error(`routine not found: ${name}#${rid}`)
      await this.sendStopped(rid, { reason: 'error' })
      return
    }

    const { instance } = await this.instantiate(rid, name, cls, kwargs)
    if (parentRid && parentRid !== '0') instance._setParentRid(parentRid)

    // modules 声明走 cls.modules(kwargs) 静态方法(无实例可达,注册期/编排器
    // 静态冲突预测同源);onCreated() 纯轻量初始化,返回非空即协议违规.
    // 异常不阻断:按已算到的 modules 回报,让 start 阶段的 tryAcquire 兜底.
    let mods: string[] = []
    try {
      const declared = cls.modules(kwargs)
      if (Array.isArray(declared)) mods = declared.map(m => String(m))
      const result = await instance.onCreated(rid, kwargs)
      if (result !== undefined && result !== null) {
        throw new TypeError(
          `${name}.onCreated must return void; module declaration ` +
          'belongs to the modules() static method')
      }
    } catch (e: any) {
      this.runtime.logger.error(`${name}#${rid} created failed: ${e?.stack ?? e}`)
    }
    await this.hub.sendLifecycleCreated(rid, mods)
  }

  /** lifecycle.start(kernel→hub):跑 runner(ackStart → run → stopped). */
  async handleStart(msg: Msg): Promise<void> {
    const rid = String(msg.id ?? '')
    const name = String(msg.name ?? '')

    const instance = this.runtime.runningInstances.get(rid)
    if (!instance) {
      // instance 已被 stop/destroy 清理(级联 stop 与 start 竞态).记日志跳过.
      this.runtime.logger.warning(`${name}#${rid}: start skipped (instance already cleaned up by stop/destroy)`)
      return
    }
    const ctx = instance.ctx  // created 已绑;initKwargs 已在 created 存

    // run 入参:用 created 时存入 instance.initKwargs 的那份 submit kwargs
    const startKwargs = instance.initKwargs ?? {}

    const runner = async (): Promise<void> => {
      const done = new Deferred<void>()
      instance.setMainTask(done)
      try {
        if (instance.isStopFinalized()) return
        instance.resetForStart()
        let result: any
        try {
          await ctx.ackStart()
          instance.markStarted()  // 父已 started,可 start/stop 子 routine
          await instance.onStarted()  // started hook:run 之前调
          const runVal = instance.run(startKwargs)
          if (isAsyncIterable(runVal)) {
            // yield 模式:run 是 async generator,迭代每项发 routine.yield
            await this.runYield(ctx, runVal)
            result = undefined
          } else {
            result = await runVal
          }
        } catch (exc: any) {
          this.runtime.logger.error(`${name}#${rid} failed: ${exc?.stack ?? exc}`)
          await instance.onStopped('error', undefined, String(exc))
          await this.sendStopped(rid, { instance, reason: 'error', error: String(exc) })
          this.cleanup(rid)
          return
        }
        const reason = instance.isForceStop() ? 'force'
          : instance.isStopRequested() ? 'stop' : 'auto'
        await instance.onStopped(reason, result)
        await this.sendStopped(rid, { instance, reason, result })
        this.cleanup(rid)
      } finally {
        done.resolve()
      }
    }

    this.runtime.spawn(runner())
  }

  /** 迭代子 routine 的 async-gen run:每 yield 一项发 routine.yield,
   * 正常结束发 isFinal;抛异常发 isFinal+error 后 rethrow. */
  private async runYield(ctx: RunContext, iter: AsyncIterable<any>): Promise<void> {
    try {
      for await (const item of iter) {
        await ctx.sendYield(item)
      }
      await ctx.sendYield(undefined, { isFinal: true })
    } catch (exc: any) {
      await ctx.sendYield(undefined, { isFinal: true, error: String(exc) })
      throw exc
    }
  }

  /** lifecycle.destroy: kernel 销毁 created 态 routine(已 submit 未 start).
   * destroy 也是终止 ---- 走 stop hook(资源撤销的统一契约: created 态实例
   * 可能在 start 前已收 body 持有资源)后 onStopped + 回报 + cleanup.
   * 用于:unsubmit / start 失败清理 / 父终止级联清 created 子. */
  async handleDestroy(msg: Msg): Promise<void> {
    const rid = String(msg.id ?? '')
    const instance = this.runtime.runningInstances.get(rid)
    if (!instance) return
    instance._requestStop()
    let stopResult: any = undefined
    try {
      stopResult = await withTimeout(instance.stop(), LifecycleManager.STOP_TIMEOUT * 1000, 'stop()')
    } catch (e: any) {
      if (e instanceof TimeoutErr) {
        this.runtime.logger.warning(`${instance.name}#${rid} destroy: stop() timeout`)
      } else {
        this.runtime.logger.error(`${instance.name}#${rid} destroy: stop() failed: ${e?.stack ?? e}`)
      }
    }
    await instance.onStopped('stop', stopResult)
    await this.sendStopped(rid, { instance, reason: 'stop', result: stopResult })
    this.cleanup(rid)
  }

  /** graceful stop 核心: stop hook → 等/超时 → onStopped → cleanup.
   * notifyKernel=false 供 transport 断连本地销毁(kernel 世界已死,
   * stopped 回报无意义). */
  private async gracefulStop(rid: string, instance: Routine,
    force = false, by = '', notifyKernel = true): Promise<void> {
    try {
      if (!instance.isStopRequested()) instance._requestStop(force, by)
      let stopResult: any = undefined
      try {
        stopResult = await withTimeout(instance.stop(), LifecycleManager.STOP_TIMEOUT * 1000, 'stop()')
      } catch (e: any) {
        if (e instanceof TimeoutErr) {
          this.runtime.logger.warning(`${instance.name}#${rid} stop() timeout; fallback`)
        } else {
          this.runtime.logger.error(`${instance.name}#${rid} stop() failed: ${e?.stack ?? e}`)
        }
      }
      // stop() 只是 grace hook(set event 让 run 退出);返回后必须保证
      // main task 真正终止.先标 finalize 让 runner 的收尾幂等,再等 main
      // task done,超时告警(JS 无强 cancel,routine 必须协作退出).
      instance.finalizeStop()
      instance.markNotStarted()  // 已 stop,不能再 start/stop 子
      const task = instance._pendingMainTask()
      if (task !== null) {
        try {
          stopResult = await withTimeout(task, LifecycleManager.STOP_TIMEOUT * 1000, 'main task exit')
        } catch (e: any) {
          if (e instanceof TimeoutErr) {
            this.runtime.logger.warning(
              `${instance.name}#${rid} main task did not exit within ${LifecycleManager.STOP_TIMEOUT}s (JS 无法强制 cancel)`)
          } else {
            this.runtime.logger.error(`${instance.name}#${rid} main task await error: ${e?.stack ?? e}`)
          }
        }
      }
      const onReason = instance.isForceStop() ? 'force' : 'stop'
      const onDetail = instance.isForceStop() ? `evicted by ${instance.stopByRid()}` : ''
      await instance.onStopped(onReason, stopResult, onDetail)
      if (notifyKernel) {
        await this.sendStopped(rid, { instance, reason: onReason, result: stopResult })
      }
      this.cleanup(rid)
    } finally {
      instance.clearStopInProgress()
    }
  }

  /** lifecycle.stop(kernel→hub):graceful stop(stop hook → 等/超时 →
   * stopped 回报).带 reason=force 时是驱逐,透传到 onStopped. */
  async handleStop(msg: Msg): Promise<void> {
    const rid = String(msg.id ?? '')
    const instance = this.runtime.runningInstances.get(rid)
    if (!instance) {
      this.runtime.logger.warning(`lifecycle.stop #${rid}: no instance`)
      return
    }
    if (!instance.beginStop()) {
      this.runtime.logger.warning(
        `lifecycle.stop #${rid}: beginStop false ` +
        `(in_progress=${instance.isStopInProgress()}, finalized=${instance.isStopFinalized()})`)
      return
    }
    // force 驱逐:kernel 发 lifecycle.stop 带 reason="force" + by(evictor rid).
    const stopBy = String(msg.by ?? '')
    const force = String(msg.reason ?? '') === 'force'
    this.runtime.spawn(this.gracefulStop(rid, instance, force, stopBy))
  }

  /** transport 断连的本地全量销毁: 不区分 passive 一律停掉 ---- 老实例
   * 会以 stale rid 继续发 req, 污染注册表与对账; 重连后 catalog push
   * 重建, 拿新 rid. stopped 不回报 kernel(世界已死).
   *
   * 返回销毁 join ---- 调用方必须等它完成再进入重连期: 销毁是异步的
   * (stop hook + main task 各 STOP_TIMEOUT), 若销毁期与重建期重叠,
   * kernel 重启后 rid 从头分配会撞上销毁中的老 rid, 老 cleanup 误杀
   * 重建的新实例. join 有界(并行, 每实例 ~2×STOP_TIMEOUT). */
  async stopAllLocal(): Promise<void> {
    const ps: Promise<void>[] = []
    for (const [rid, instance] of [...this.runtime.runningInstances]) {
      if (!instance.beginStop()) continue
      // spawn 托管 unhandled; join 侧吞错(spawn wrapper 已记日志)
      ps.push(this.runtime.spawn(
        this.gracefulStop(rid, instance, false, '', false)).catch(() => {}))
    }
    await Promise.all(ps)
  }
}
