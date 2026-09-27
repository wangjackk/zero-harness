/** Routine 基类 + Routines 注册表 + 通信装饰器(移植 z2/routine.py).

 生命周期核心:run / stop / onStopped / onCreated / name.
 通信(req/stream_req 骑 p2p 隧道,kernel dumb forward)经 @request/@stream
 装饰器 + RunContext.req/streamReq 暴露.
 */
import { setupLogger, type Logger } from './logger'
import {
  ENVELOPE_CHUNK, ENVELOPE_DATA, ENVELOPE_EOF, ENVELOPE_ERROR,
  ENVELOPE_EVENT, ENVELOPE_OK, ENVELOPE_REQ_ID, ENVELOPE_REPLY_TO,
  ENVELOPE_STREAM_ID, MESSAGE_REQ_REPLY, MESSAGE_STREAM_DATA,
  type Msg, type Payload,
} from './protocol'
import { fmtMs, type Deferred } from './async'
import { RegisterError } from './errors'
import type { Phase, RunContext, ProviderStream } from './ctx'

/** routine 声明占用的模块列表(on_created 返回值).null/undefined = 不占. */
export type Modules = string[] | null

/** 发送方 routine 引用(@request/@stream/on_message 的 source 参数). */
export interface RoutineSource {
  readonly id: string
  readonly name?: string
}

export function makeSource(id: string, name?: string): RoutineSource {
  return name !== undefined ? { id, name } : { id }
}

/** @request handler 签名. */
export type RequestHandler = (source: RoutineSource, data: any) => Promise<any>
/** @stream handler 签名(async generator). */
export type StreamHandler = (source: RoutineSource, data: any) => AsyncGenerator<any>
/** @subscribe handler 签名. */
export type SubscribeHandler = (source: RoutineSource, data: any) => Promise<void>

export type RoutineClass<T extends Routine = Routine> = new () => T

/** 装饰器标记(z2 是多个独立属性;ts 侧合并为单 symbol 单对象,每个装饰器
 * 闭包只做一次赋值——bun canary minifier 会把连续两条同对象赋值错误
 * 链化成 `topic(desc.value)`,单赋值形态绕开). */
interface MethodMeta { reqEvent?: string; streamEvent?: string; topic?: string; namespace?: string }
const META = Symbol('routine.methodMeta')

/** 装饰方法为 @request handler:收到 p2p topic=event 时调用,返回值作为
 * req_reply 的 data 回给 source.抛异常则回 ok=false. */
export function request(event: string): MethodDecorator {
  return (_target, _key, desc) => {
    const fn = desc!.value as any
    fn[META] = { ...(fn[META] as MethodMeta | undefined), reqEvent: event }
  }
}

/** 装饰方法(async generator)为 @stream handler:每 yield 一项发一个
 * stream_data 帧,结束发 __eof__:done. */
export function stream(event: string): MethodDecorator {
  return (_target, _key, desc) => {
    const fn = desc!.value as any
    fn[META] = { ...(fn[META] as MethodMeta | undefined), streamEvent: event }
  }
}

/** 装饰方法为 @subscribe handler:instance created 时自动订阅
 * (namespace, topic),收到匹配的 pubsub.delivered 时调用. */
export function subscribe(topic: string, namespace = ''): MethodDecorator {
  return (_target, _key, desc) => {
    const fn = desc!.value as any
    fn[META] = { ...(fn[META] as MethodMeta | undefined), topic, namespace }
  }
}

/** isPassive 带参形态: passive + auto-start 默认入参. */
export class PassiveConfig {
  constructor(readonly kwargs: Record<string, any>) {}
}

/** isPassive(boolean | PassiveConfig) 序列化成 wire 单字段 {flag, kwargs}.
 * 杂型注册即 TypeError(协议违规立即暴露,不留兼容分支). */
export function passiveWire(cls: RoutineClass): { flag: boolean; kwargs: Record<string, any> } {
  const ip: any = (cls as any).isPassive
  if (ip instanceof PassiveConfig) return { flag: true, kwargs: { ...ip.kwargs } }
  if (typeof ip === 'boolean') return { flag: ip, kwargs: {} }
  throw new TypeError(`${(cls as any).name}.isPassive only accepts boolean | PassiveConfig, got ${typeof ip}: ${JSON.stringify(ip)}`)
}

function snakeCase(s: string): string {
  return s.replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase()
}

const nameCache = new WeakMap<RoutineClass, string>()

/** routine 命令名:子类 `static name = 'xxx'` 显式声明(verbatim),
 * 否则从类名蛇形转换(PascalCase 含大写 → snake_case;全小写类名视为显式).
 * 对齐 py 版 name: ClassVar 覆盖语义. */
export function routineNameOf(cls: RoutineClass): string {
  let n = nameCache.get(cls)
  if (n) return n
  const raw = (cls as any).name as string
  n = /[A-Z]/.test(raw) ? snakeCase(raw) : raw
  nameCache.set(cls, n)
  return n
}

export abstract class Routine {
  static enable = true
  /** passive 声明 + auto-start 默认入参(kernel catalog push 后自动 Execute). */
  static isPassive: boolean | PassiveConfig = false
  /** 自由扩展元数据(类级别静态声明,框架不强制 schema). */
  static meta: Record<string, any> = {}

  _activeCtx: RunContext | null = null
  _mainDone: Deferred<void> | null = null
  _stopRequested = false
  /** force 驱逐标志 + 驱逐者 rid:lifecycle.stop(reason=force) 时置. */
  _stopForce = false
  _stopBy = ''
  _stopInProgress = false
  _stopFinalized = false
  /** stopped 回报幂等标志:runner 与 stop_runner 并发都调,靠它只发一次. */
  _stoppedSent = false
  /** 生命周期相位:created(从未 start)→ started(运行中)→ stopped(start 后
   * 被 stop/驱逐).ctx 守卫据此区分"还没 start"(报错)与"已停"(no-op). */
  _phase: Phase = 'created'
  /** submit 入参(routine 的唯一入参来源). */
  _initKwargs: Payload = {}
  _parentRid: string | null = null
  _logger: Logger

  _requestHandlers = new Map<string, RequestHandler>()
  _streamHandlers = new Map<string, StreamHandler>()
  /** key: `${namespace}\u0000${topic}` */
  _subscribeHandlers = new Map<string, SubscribeHandler>()

  constructor() {
    this._logger = setupLogger(`zt.routine.${this.name}`)
    // 扫描 @request / @stream / @subscribe 装饰的方法,建分发表.
    let proto = Object.getPrototypeOf(this)
    while (proto && proto !== Routine.prototype) {
      for (const key of Object.getOwnPropertyNames(proto)) {
        const desc = Object.getOwnPropertyDescriptor(proto, key)
        const fn = desc?.value
        if (typeof fn !== 'function') continue
        const meta: MethodMeta | undefined = (fn as any)[META]
        if (!meta) continue
        if (typeof meta.reqEvent === 'string' && !this._requestHandlers.has(meta.reqEvent)) {
          this._requestHandlers.set(meta.reqEvent, (this as any)[key].bind(this))
        }
        if (typeof meta.streamEvent === 'string' && !this._streamHandlers.has(meta.streamEvent)) {
          this._streamHandlers.set(meta.streamEvent, (this as any)[key].bind(this))
        }
        if (typeof meta.topic === 'string') {
          const k = `${meta.namespace ?? ''}\u0000${meta.topic}`
          if (!this._subscribeHandlers.has(k)) {
            this._subscribeHandlers.set(k, (this as any)[key].bind(this))
          }
        }
      }
      proto = Object.getPrototypeOf(proto)
    }
  }

  /** routine 命令名(类级,auto 蛇形或显式声明). */
  get name(): string { return routineNameOf(this.constructor as RoutineClass) }

  get id(): string | null {
    return this._activeCtx ? this._activeCtx.id : null
  }

  get ctx(): RunContext {
    if (!this._activeCtx) throw new Error(`${this.name}: no active context.`)
    return this._activeCtx
  }

  toString(): string { return this.name }

  // --- Lifecycle (to be overridden) ---

  /** routine 主体:跑到完成(return 结果)或 yield body(async generator). */
  abstract run(kwargs: Payload): Promise<any> | AsyncGenerator<any>

  /** 正规 stop 流程;routine 在此 set event 让 run 退出.可返回结果. */
  async stop(): Promise<any> {}

  /** 提交子 routine(经 kernel 回环).详见 ctx.submit. */
  async submit(routineName: string, kwargs?: Payload): Promise<import('./handle').RoutineHandle> {
    return await this.ctx.submit(routineName, kwargs)
  }

  /** 同步拿子 routine 结果(submit→start→wait 一步到位). */
  async call(routineName: string, kwargs?: Payload): Promise<any> {
    return await this.ctx.call(routineName, kwargs)
  }

  /** 抢占式拿子 routine 结果(submit→force_start→wait). */
  async forceCall(routineName: string, kwargs?: Payload): Promise<any> {
    return await this.ctx.forceCall(routineName, kwargs)
  }

  // --- 运行时模块占领/释放 ---

  async acquire(modules: string[]): Promise<void> { return await this.ctx.acquire(modules) }
  async release(modules: string[]): Promise<void> { return await this.ctx.release(modules) }
  async forceRelease(modules: string[]): Promise<void> { return await this.ctx.forceRelease(modules) }
  async forceAcquire(modules: string[]): Promise<void> { return await this.ctx.forceAcquire(modules) }
  async loadModule(parent: string, child: string, name = ''): Promise<void> {
    return await this.ctx.loadModule(parent, child, name)
  }
  async unloadModule(child: string): Promise<void> { return await this.ctx.unloadModule(child) }

  // --- p2p 通信 ---

  async req(target: string, event: string, data?: Payload, timeout = 30.0): Promise<any> {
    return await this.ctx.req(target, event, data, timeout)
  }

  async getRunningRoutines(): Promise<any[]> { return await this.ctx.getRunningRoutines() }
  async getModuleTree(): Promise<import('./moduleTree').ModuleTree> { return await this.ctx.getModuleTree() }
  async getRoutines(): Promise<any[]> { return await this.ctx.getRoutines() }

  async streamReq(target: string, event: string, data?: Payload, timeout = 30.0): Promise<import('./stream').StreamCtx> {
    return await this.ctx.streamReq(target, event, data, timeout)
  }

  // --- pubsub ---

  async publish(topic: string, data: any = undefined, namespace = ''): Promise<void> {
    await this.ctx.publish(topic, data, namespace)
  }

  async subscribe(topic: string, handler: SubscribeHandler, namespace = ''): Promise<void> {
    await this.ctx.subscribe(topic, handler, namespace)
  }

  async unsubscribe(topic: string, namespace = ''): Promise<void> {
    await this.ctx.unsubscribe(topic, namespace)
  }

  namespace(ns: string): import('./ctx').Namespace { return this.ctx.namespace(ns) }

  /** 发 routine.yield(yield 一项给 parent).run 是 async generator 时框架自动调. */
  async sendYieldData(data: any = undefined, opts: { isFinal?: boolean; error?: string } = {}): Promise<void> {
    await this.ctx.sendYield(data, opts)
  }

  // --- message.* 定向消息 ---

  async send(target: string, data: any = undefined): Promise<void> {
    await this.ctx.send(target, data)
  }

  /** 收到 message.delivered 的回调(基类空实现,子类 override).可能并发 fire. */
  async onMessage(source: RoutineSource, data: any): Promise<void> {}

  /** instance created 时调:对每个 @subscribe topic 发 pubsub.subscribe. */
  async _autoSubscribe(): Promise<void> {
    const rid = this.ctx.id
    for (const [key, handler] of this._subscribeHandlers) {
      const ns = key.slice(0, key.indexOf('\u0000'))
      const topic = key.slice(key.indexOf('\u0000') + 1)
      this.ctx.runtime.registerSubscriber(rid, ns, topic, handler)
      await this.ctx.subscribeTopic(topic, ns)
    }
  }

  /** on_created 钩子:routine 被创建时调一次(早于 start).返回声明占用的
   * modules(string[])或 null(不占).只做轻量初始化 + 算 modules. */
  async onCreated(rid: string, kwargs: Payload): Promise<Modules> { return null }

  /** started 钩子:lifecycle.started 已回报,run() 之前调. */
  async onStarted(): Promise<void> {}

  /** run 完成或退出后调用(lifecycle.stopped 发出前).
   * reason: 'auto' | 'error' | 'stop' | 'cancel' | 'force'. */
  async onStopped(reason: string = 'auto', result: any = undefined, detail: string = ''): Promise<void> {}

  // --- Stop control ---

  _requestStop(force = false, by = ''): boolean {
    if (force) this._stopForce = true
    if (by) this._stopBy = by
    if (this._stopRequested) return false
    this._stopRequested = true
    return true
  }

  setMainTask(done: Deferred<void>): void { this._mainDone = done }

  /** 返未完成 main task 的完成 promise(供 stop 流程 await),或 null. */
  _pendingMainTask(): Promise<void> | null {
    return this._mainDone && !this._mainDone.done ? this._mainDone.promise : null
  }

  // --- Internal lifecycle state ---

  resetForStart(): void {
    this._stopRequested = false
    this._stopForce = false
    this._stopBy = ''
    this._stopInProgress = false
    this._phase = 'created'
  }

  markStarted(): void { this._phase = 'started' }
  /** stop/驱逐后置 stopped.从未 started 的保持 created(release 守卫要区分
   * "还没 start"与"start 后被停"). */
  markNotStarted(): void { if (this._phase === 'started') this._phase = 'stopped' }

  beginStop(): boolean {
    if (this._stopInProgress || this._stopFinalized) return false
    this._stopInProgress = true
    return true
  }

  finalizeStop(): void { this._stopFinalized = true }
  clearStopFinalized(): void { this._stopFinalized = false }
  clearStopInProgress(): void { this._stopInProgress = false }

  /** stopped 回报幂等:首次调返 true(调用方应发 lifecycle.stopped). */
  _markStoppedSent(): boolean {
    if (this._stoppedSent) return false
    this._stoppedSent = true
    return true
  }

  isStopFinalized(): boolean { return this._stopFinalized }
  isStopInProgress(): boolean { return this._stopInProgress }
  isStopRequested(): boolean { return this._stopRequested }
  isForceStop(): boolean { return this._stopForce }
  /** force 驱逐者 rid(非 force 为空串). */
  stopByRid(): string { return this._stopBy }

  bindCtx(ctx: RunContext): void { this._activeCtx = ctx }
  setInitKwargs(kwargs: Payload): void { this._initKwargs = kwargs }

  get initKwargs(): Payload { return this._initKwargs }

  /** 父 routine id(无父=root 时为 null).routine 据此反向 req 父. */
  get parentRid(): string | null { return this._parentRid }

  _setParentRid(parentRid: string): void { this._parentRid = parentRid }

  // --- p2p 通信派发(hub.onInbound 按 target_id 找到 instance 后调用) ---

  async _serveRequest(envelope: Msg, source: RoutineSource): Promise<void> {
    const event = String(envelope[ENVELOPE_EVENT] ?? '')
    const handler = this._requestHandlers.get(event)
    const reqId = envelope[ENVELOPE_REQ_ID] ?? ''
    const replyTo = envelope[ENVELOPE_REPLY_TO] ?? ''
    const payload = envelope[ENVELOPE_DATA] ?? {}
    if (!handler) {
      this._logger.warning(`${this.name} @request(${JSON.stringify(event)}) no handler`)
      await this.ctx.sendMessage(replyTo, MESSAGE_REQ_REPLY, {
        [ENVELOPE_REQ_ID]: reqId, [ENVELOPE_OK]: false,
        [ENVELOPE_ERROR]: `no @request handler for ${JSON.stringify(event)}`,
      })
      return
    }
    try {
      const result = await handler(source, payload)
      await this.ctx.sendMessage(replyTo, MESSAGE_REQ_REPLY, {
        [ENVELOPE_REQ_ID]: reqId, [ENVELOPE_OK]: true, [ENVELOPE_DATA]: result,
      })
    } catch (exc: any) {
      this._logger.error(`${this.name} @request(${JSON.stringify(event)}) failed: ${exc?.stack ?? exc}`)
      await this.ctx.sendMessage(replyTo, MESSAGE_REQ_REPLY, {
        [ENVELOPE_REQ_ID]: reqId, [ENVELOPE_OK]: false,
        [ENVELOPE_ERROR]: String(exc?.message ?? exc),
      })
    }
  }

  async _serveStream(envelope: Msg, source: RoutineSource): Promise<void> {
    const event = String(envelope[ENVELOPE_EVENT] ?? '')
    const handler = this._streamHandlers.get(event)
    const streamId = envelope[ENVELOPE_STREAM_ID] ?? ''
    const replyTo = envelope[ENVELOPE_REPLY_TO] ?? ''
    const payload = envelope[ENVELOPE_DATA] ?? {}
    if (!handler) {
      this._logger.warning(`${this.name} @stream(${JSON.stringify(event)}) no handler`)
      return
    }
    const entry: ProviderStream = {
      cancelled: false,
      cancel() { this.cancelled = true },
    }
    this.ctx.runtime.registerProviderStream(streamId, entry)
    this.ctx.spawn(this._runStream(handler, source, payload, streamId, replyTo))
  }

  private async _runStream(handler: StreamHandler, source: RoutineSource,
                           payload: any, streamId: string, replyTo: string,
                           _entry?: ProviderStream): Promise<void> {
    const entry = this.ctx.runtime.getProviderStream(streamId)
    try {
      for await (const chunk of handler(source, payload)) {
        if (entry?.cancelled) break
        await this.ctx.sendMessage(replyTo, MESSAGE_STREAM_DATA, {
          [ENVELOPE_STREAM_ID]: streamId, [ENVELOPE_CHUNK]: chunk,
        })
      }
      if (entry?.cancelled) {
        // 消费方 cancel:best-effort 发一个 cancelled eof
        try {
          await this.ctx.sendMessage(replyTo, MESSAGE_STREAM_DATA, {
            [ENVELOPE_STREAM_ID]: streamId, [ENVELOPE_EOF]: 'cancelled',
          })
        } catch { /* best-effort */ }
      } else {
        await this.ctx.sendMessage(replyTo, MESSAGE_STREAM_DATA, {
          [ENVELOPE_STREAM_ID]: streamId, [ENVELOPE_EOF]: 'done',
        })
      }
    } catch (exc: any) {
      this._logger.error(`${this.name} @stream(${streamId}) error: ${exc?.stack ?? exc}`)
      await this.ctx.sendMessage(replyTo, MESSAGE_STREAM_DATA, {
        [ENVELOPE_STREAM_ID]: streamId, [ENVELOPE_EOF]: 'error',
        [ENVELOPE_ERROR]: String(exc?.message ?? exc),
      })
    } finally {
      this.ctx.runtime.popProviderStream(streamId)
    }
  }
}

/** routine 注册表:存 class(不存实例),created 时按需实例化. */
export class Routines {
  private _routines = new Map<string, RoutineClass>()
  private _logger = setupLogger('routine.routines')

  /** 注册 routine 类或 Routines 组.同名抛 RegisterError ---- 覆盖必须显式走
   * reload 语义(hub.reloadRoutine).同一 cls 重复 register 幂等通过.
   * enable=false 跳过. */
  register(...items: (RoutineClass | Routines)[]): void {
    for (const item of items) {
      if (item instanceof Routines) {
        for (const cls of item.getRoutines()) this._registerOne(cls)
      } else {
        if (!(item as any).enable) continue
        this._registerOne(item)
      }
    }
  }

  private _registerOne(cls: RoutineClass): void {
    passiveWire(cls)  // 注册即校验:isPassive 杂型当场 raise
    const name = routineNameOf(cls)
    const existing = this._routines.get(name)
    if (existing && existing !== cls) {
      throw new RegisterError(
        `Routines.register: name=${JSON.stringify(name)} already registered (${existing.name}); use reload to replace`)
    }
    this._routines.set(name, cls)
  }

  /** reload 覆盖语义: 同名直接替换, 不存在也 set(对齐 py register 的"同名覆盖").
   * hub.reloadRoutine 回执路径专用 ---- 与 register 的严格模式互补. */
  reload(cls: RoutineClass): void {
    passiveWire(cls)
    this._routines.set(routineNameOf(cls), cls)
  }

  getRoutine(name: string): RoutineClass | undefined { return this._routines.get(name) }
  getRoutines(): RoutineClass[] { return [...this._routines.values()] }
  getRoutineNames(): string[] { return [...this._routines.keys()] }
  deregister(name: string): RoutineClass | undefined {
    const removed = this._routines.get(name)
    this._routines.delete(name)
    return removed
  }

  toString(): string { return `Routines(${[...this._routines.keys()].sort().join(', ')})` }
}
