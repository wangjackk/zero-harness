/** RoutineHandle ---- 父侧拿到的"指向一次 submit 的子 routine"的本地句柄.

 handle.id = kernel 分配的 command id(string).
 notify 路径统一经 kernel 中转:子 routine 自己发 lifecycle.started/stopped 给
 kernel,kernel 把它中转回来,hub.onInbound 按 child_id 找到 handle 调
 notifyStarted / notifyDone,唤醒 handle.wait().
 子 routine 的 run 是 async generator(yield)时,handle 也是 async iterable.
 */
import { AsyncEvent, AsyncQueue, withTimeout } from './async'
import { StartError } from './errors'
import type { Msg } from './protocol'
import type { RunContext } from './ctx'

// body 迭代终结哨兵(distinguish from yielded null / result)
const BODY_DONE = Symbol('routine.bodyDone')

// start/stop/unsubmit 等 ack 默认超时(对齐 ctx ACK_TIMEOUT=30s): kernel 回执
// 丢失/路由遗漏时抛 TimeoutErr, 不永久挂起.
const ACK_TIMEOUT = 30_000

export class RoutineHandle {
  readonly id: string
  name: string | null
  /** submit 时 kernel 确定的占用 modules(created 回报带回).编排器据此算冲突. */
  modules: string[] | null
  result: any = undefined
  error: string | null = null
  /** lifecycle.stopped 的 wire reason(大写 enum 值).父侧可读分流. */
  reason: string | null = null
  /** 可选生命周期回调(外部直接赋值). */
  onStarted: ((h: RoutineHandle) => Promise<void>) | null = null
  onStopped: ((h: RoutineHandle) => Promise<void>) | null = null

  private _ctx: RunContext | null
  private _started = new AsyncEvent()
  private _done = new AsyncEvent()
  /** start/try_start/force_start/stop/unsubmit 的 ack future.hub.onInbound 收
   * lifecycle.started/stopped / routine.rejected 时按 child_id 找到本 handle,
   * 调 resolveAck/rejectAck 唤醒. */
  private _ack: DeferredLike | null = null
  private _bodyQueue = new AsyncQueue<any>()
  private _bodyDone = false

  constructor(childId: string, name: string, opts: { ctx?: RunContext; modules?: string[] | null } = {}) {
    this.id = childId
    this.name = name
    this.modules = opts.modules ?? null
    this._ctx = opts.ctx ?? null
  }

  toString(): string {
    const state = this._done.isSet ? (this.error ? 'error' : 'done')
      : this._started.isSet ? 'started' : 'pending'
    return `RoutineHandle(${this.name ?? '?'} id=${this.id} ${state})`
  }

  isStarted(): boolean { return this._started.isSet }
  isDone(): boolean { return this._done.isSet }

  // --- control(直接发 wire 给 kernel,ack future 内化在 handle 上) ---

  private async sendAndWait(send: () => Promise<void>): Promise<any> {
    if (this._ack && !this._ack.done) {
      throw new Error(`${this}: another start/stop in flight`)
    }
    const fut: DeferredLike = { done: false, resolve: null, reject: null }
    let resolve!: (v: any) => void
    let reject!: (e: any) => void
    const promise = new Promise<any>((res, rej) => { resolve = res; reject = rej })
    fut.resolve = v => { if (!fut.done) { fut.done = true; resolve(v) } }
    fut.reject = e => { if (!fut.done) { fut.done = true; reject(e) } }
    fut.done = false
    this._ack = fut
    try {
      await send()
      return await withTimeout(promise, ACK_TIMEOUT, 'handle ack timeout')
    } finally {
      if (this._ack === fut) this._ack = null
    }
  }

  /** hub.onInbound 收到成功回执(lifecycle.started/stopped)时调.幂等. */
  _resolveAck(value: any): void {
    const ack = this._ack
    if (ack?.resolve) ack.resolve(value)
  }

  /** hub.onInbound 收到 rejected(op=stop/unsubmit)时调.幂等. */
  _rejectAck(exc: Error): void {
    const ack = this._ack
    if (ack?.reject) ack.reject(exc)
  }

  /** 已 done 的 handle 不接受控制命令: kernel 侧 node 已删, 回执(rejected)
   * 会被 hub 丢弃 ---- 只能靠 ack 超时兜底. 入口即报错. */
  private requireNotDone(op: string): void {
    if (this._done.isSet) {
      throw new Error(`${this}: already done (reason=${this.reason ?? 'null'}), cannot ${op}`)
    }
  }

  /** start/try_start/force_start 公共骨架.try_mode=true 失败返回 StartError
   * (null=成功,保留可重试);false 失败 raise StartError. */
  private async startCommon(op: string, send: () => Promise<void>, tryMode: boolean): Promise<StartError | null> {
    if (!this._ctx) throw new Error(`${this}: no ctx bound, cannot ${op}`)
    this.requireNotDone(op)
    this._ctx.requireParentStarted(`${op}ing child`)
    const err = await this.sendAndWait(send)
    if (!err) return null
    const se = new StartError(String(err))
    if (tryMode) return se
    throw se
  }

  /** 让 kernel start 子命令(占模块+运行),失败时保留可重试.失败返回
   * StartError(null=成功),不抛. */
  async tryStart(): Promise<StartError | null> {
    return await this.startCommon('start',
      () => this._ctx!._io.sendRoutineStart({ childId: this.id, tryStart: true }), true)
  }

  /** 让 kernel start 子命令,全有或全无----失败时 kernel 清 node+订阅,本侧清
   * created instance(handle 失效不可重试).失败 raise StartError. */
  async start(): Promise<void> {
    await this.startCommon('start',
      () => this._ctx!._io.sendRoutineStart({ childId: this.id, tryStart: false }), false)
  }

  /** 抢占式 start:kernel 驱逐占住子 declared 模块的第三方(cascade stop,
   * reason='force' 透传)后 start.失败 raise StartError. */
  async forceStart(): Promise<void> {
    await this.startCommon('force_start',
      () => this._ctx!._io.sendRoutineForceStart({ childId: this.id }), false)
  }

  /** 让 kernel stop 子命令(级联).fire=false(默认)等 lifecycle.stopped 回执
   * 确认子已停;fire=true 只发 wire 不等 ack. */
  async stop(opts: { fire?: boolean } = {}): Promise<void> {
    if (!this._ctx) throw new Error(`${this}: no ctx bound, cannot stop`)
    this.requireNotDone('stop')
    if (opts.fire) {
      await this._ctx._io.sendRoutineStop({ childId: this.id })
      return
    }
    this._ctx.requireParentStarted('stopping child')
    await this.sendAndWait(() => this._ctx!._io.sendRoutineStop({ childId: this.id }))
  }

  /** 撤销提交:清 created 态子命令(未 start 的).跟 submit 对称.已 start 的
   * 调本方法会被 kernel 拒(rejected op=unsubmit)----该用 stop. */
  async unsubmit(opts: { fire?: boolean } = {}): Promise<void> {
    if (!this._ctx) throw new Error(`${this}: no ctx bound, cannot unsubmit`)
    this.requireNotDone('unsubmit')
    if (opts.fire) {
      await this._ctx._io.sendRoutineUnsubmit({ childId: this.id })
      return
    }
    await this.sendAndWait(() => this._ctx!._io.sendRoutineUnsubmit({ childId: this.id }))
  }

  // --- waits ---

  /** 等 lifecycle.started.失败兜底也会 resolve(不死锁). */
  async waitStarted(): Promise<void> { await this._started.wait() }

  /** 等 lifecycle.stopped.成功 → 返回 result;失败/异常停掉 → throw Error(error). */
  async wait(): Promise<any> {
    await this._done.wait()
    if (this.error) throw new Error(this.error)
    return this.result
  }

  // --- hub-layer hooks(reader 按 child_id 路由调用) ---

  private _fireHandler(handler: ((h: RoutineHandle) => Promise<void>) | null): void {
    if (!handler || !this._ctx) return
    this._ctx.spawn(handler(this))
  }

  notifyStarted(): void {
    this._started.set()
    this._fireHandler(this.onStarted)
  }

  notifyDone(result: any = undefined, error: string | null = null, reason: string | null = null): void {
    this.result = result
    this.error = error
    this.reason = reason
    // 兜底:错过 started 直接 done 时也唤醒 waitStarted
    this._started.set()
    this._done.set()
    // body 迭代也终结(若子 routine yield 后还没发 is_final 就 stopped)
    this._finishBody()
    this._fireHandler(this.onStopped)
  }

  // --- body upstream 迭代(子 routine start 是 async generator 时) ---

  [Symbol.asyncIterator](): RoutineHandle { return this }

  async next(): Promise<IteratorResult<any>> {
    const item = await this._bodyQueue.get()
    if (item === BODY_DONE) return { done: true, value: undefined }
    if (item instanceof Error) throw item
    return { done: false, value: item }
  }

  /** routine.yielded 投喂:子 yield 的项 / 收尾 / 异常. */
  _onYieldChunk(data: any = undefined, isFinal = false, error: string | null = null): void {
    if (this._bodyDone) return
    if (error !== null) {
      this._bodyDone = true
      this._bodyQueue.push(new Error(error))
      this._bodyQueue.push(BODY_DONE)
      return
    }
    if (isFinal) {
      this._bodyDone = true
      this._bodyQueue.push(BODY_DONE)
      return
    }
    this._bodyQueue.push(data)
  }

  /** 确保 body 迭代能终结(handle done 时调,避免 async for 永远挂). */
  _finishBody(): void {
    if (!this._bodyDone) {
      this._bodyDone = true
      this._bodyQueue.push(BODY_DONE)
    }
  }
}

interface DeferredLike {
  done: boolean
  resolve: ((v: any) => void) | null
  reject: ((e: any) => void) | null
}
