/** RunContext ---- 每次 ``run()`` invocation 的运行上下文(移植 z2/ctx.py).

 身份字段 id / name + lifecycle 出口(ackStart / requestStop)+ submit 能力
 (routine 调 routine,经 kernel 回环).

 submit 流程:发 routine.submit{req_id, name, kwargs, parent_id=self.id} 给
 kernel → kernel 建子命令(Create,不 Start)→ 回 routine.submitted{req_id,
 child_id, modules} → 拿到 child_id 建 RoutineHandle 注册到 runtime handle 表.

 handle.start()/stop() 发 routine.start/stop 给 kernel → kernel 走编排
 → lifecycle.start/stop 回 hub 跑子 routine → 子发 lifecycle.started/stopped
 给 kernel → kernel 中转回 hub → onInbound 按 child_id 路由到 handle 的
 notifyStarted/notifyDone(统一经 kernel,不本地抄近路).
 */
import { AsyncQueue, Deferred, withTimeout } from './async'
import { ReqTimeout, TimeoutErr } from './errors'
import { setupLogger } from './logger'
import {
  ENVELOPE_DATA, ENVELOPE_EVENT, ENVELOPE_REPLY_TO, ENVELOPE_REQ_ID,
  ENVELOPE_STREAM_ID, MESSAGE_REQ, MESSAGE_REQ_REPLY,
  MESSAGE_SEND, MESSAGE_STREAM_OPEN,
  type Msg, type Payload,
} from './protocol'
import { RoutineHandle } from './handle'
import { type Routine, type SubscribeHandler } from './routine'
import type { ServerRuntime } from './runtime'
import type { RoutineHub } from './hub'
import type { Transport } from './transport'
import { StreamCtx, StreamReader } from './stream'

let _reqCounter = 0
export function newReqId(): string { return `r${++_reqCounter}` }

/** invocation 生命周期相位:created(从未 start)→ started(运行中)→
 * stopped(start 后被 stop/驱逐).守卫据此区分"还没 start"(报错)与
 * "start 后被停"(no-op). */
export type Phase = 'created' | 'started' | 'stopped'

const _log = setupLogger('routine.req', 'warning')

/** @stream provider 侧控制对象:消费方 cancel 时置 cancelled,provider 在
 * 迭代间隙检查并退出(JS 无法强 cancel promise,靠协作标志). */
export interface ProviderStream {
  cancelled: boolean
  cancel(): void
}

export class RunContext {
  static ACK_TIMEOUT = 30.0  // submit/acquire/release 等 kernel ack 默认超时(秒)

  /** hub 引用(下划线约定私有,handle 层同模块簇内使用). */
  readonly _io: RoutineHub

  constructor(readonly id: string, readonly name: string,
              io: RoutineHub, private _routine: Routine,
              readonly runtime: ServerRuntime, private _transport: Transport) {
    this._io = io
  }

  // --- Hub 访问(业务侧入口) ---

  get hub(): RoutineHub { return this._io }

  // --- Lifecycle ack helpers ---

  /** 发 lifecycle.started 通知调度器进入 Started. */
  async ackStart(): Promise<void> {
    await this._io.sendLifecycleStarted(this.id)
  }

  /** 请求 hub 发起一次正规 stop 流程(routine 自杀). */
  async requestStop(): Promise<void> {
    await this._io.requestStop(this.id)
  }

  /** 父 routine 必须 started(ackStart 后)----否则抛错.handle 层 start/stop
   * 子 routine 前的守卫.submit/unsubmit 不查(created 即可). */
  requireParentStarted(action: string): void {
    if (this._routine._phase !== 'started') {
      throw new Error(`${this.name}: must start() before ${action} (parent routine not started)`)
    }
  }

  // --- routine 调 routine(submit) ---

  /** 提交一个子 routine,经 kernel 回环:建命令(created),created 回报后返回
   * handle.handle.modules = 子 routine onCreated() 返回的占用 modules. */
  async submit(name: string, kwargs: Payload | null = null): Promise<RoutineHandle> {
    const reqId = newReqId()
    const fut = new Deferred<{ childId: string; modules: string[] | null }>()
    this.runtime.registerSubmitFuture(reqId, fut)
    let r: { childId: string; modules: string[] | null }
    try {
      await this._io.sendRoutineSubmit({ reqId, parentId: this.id, name, kwargs: kwargs ?? {} })
      r = await withTimeout(fut.promise, RunContext.ACK_TIMEOUT * 1000, `submit ${name}`)
    } finally {
      this.runtime.popSubmitFuture(reqId)
    }
    const handle = new RoutineHandle(r.childId, name, { ctx: this, modules: r.modules })
    this._io.registerHandle(r.childId, handle)
    // 拿到 handle 时子一定已 created(kernel 等 created 回报才发 submitted):
    // created 路由表 + ctx 都已就绪----可直接 send / req / publish.
    return handle
  }

  /** 同步拿子 routine 结果:submit → start(priority) → wait 一步到位.
   * start 失败抛 StartError;子异常停止抛 Error;正常返回 result.
   * priority: start 优先级(档位约定见 routine.ts 头注).模块冲突时
   * incoming 严格大于全部第三方 holder 才驱逐(语义同 handle.start). */
  async call(name: string, kwargs: Payload | null = null, priority: number = 10): Promise<any> {
    this.requireStarted('call()')
    const kw: Payload = { ...(kwargs ?? {}) }
    const handle = await this.submit(name, kw)
    await handle.start(priority)
    return await handle.wait()
  }

  // --- 运行时占领/释放模块(跟类静态声明同一底层 tryAcquire/release) ---

  /** 运行时占领模块.撞车按本 routine 的 start 档位裁决:严格大于全部
   * 撞车 holder 才驱逐后占住,否则抛 AcquireError(try 语义自行 catch).
   * 只 start 期间可用. */
  async acquire(modules: string[]): Promise<void> {
    this.requireStarted('acquire')
    const reqId = newReqId()
    const fut = new Deferred<void>()
    this.runtime.registerAcquireFuture(reqId, fut)
    try {
      await this._io.sendRoutineAcquire({ reqId, id: this.id, modules })
      await withTimeout(fut.promise, RunContext.ACK_TIMEOUT * 1000, 'acquire')
    } finally {
      this.runtime.popAcquireFuture(reqId)
    }
  }

  /** 运行时释放指定模块(只这些,不全量).stop 时 kernel 自动全清.
   * 已 stop/被驱逐后再调为 no-op(kernel 已全量回收),打 warning 不抛错;
   * 从未 start 过才抛 Error. */
  async release(modules: string[]): Promise<void> {
    if (this._routine._phase !== 'started') {
      if (this._routine._phase === 'stopped') {
        _log.warning(`${this.name}: release(${modules}) after stopped ---- kernel already reclaimed all modules, no-op`)
        return
      }
      throw new Error(`${this.name}: release() before start() (routine never started)`)
    }
    const reqId = newReqId()
    const fut = new Deferred<void>()
    this.runtime.registerAcquireFuture(reqId, fut)
    try {
      await this._io.sendRoutineRelease({ reqId, id: this.id, modules })
      await withTimeout(fut.promise, RunContext.ACK_TIMEOUT * 1000, 'release')
    } finally {
      this.runtime.popAcquireFuture(reqId)
    }
  }

  /** 往 parent 模块下加载子模块 child(全局树动态增拓扑).只挂树不占用. */
  async loadModule(parent: string, child: string, name = ''): Promise<void> {
    this.requireStarted('load_module')
    const reqId = newReqId()
    const fut = new Deferred<void>()
    this.runtime.registerLoadFuture(reqId, fut)
    try {
      await this._io.sendRoutineLoadModule({ reqId, parentId: parent, childId: child, name })
      await withTimeout(fut.promise, RunContext.ACK_TIMEOUT * 1000, 'load_module')
    } finally {
      this.runtime.popLoadFuture(reqId)
    }
  }

  /** 卸载子模块 child(全局树动态删拓扑).child 有子/被占/不存在抛错. */
  async unloadModule(child: string): Promise<void> {
    this.requireStarted('unload_module')
    const reqId = newReqId()
    const fut = new Deferred<void>()
    this.runtime.registerUnloadFuture(reqId, fut)
    try {
      await this._io.sendRoutineUnloadModule({ reqId, childId: child })
      await withTimeout(fut.promise, RunContext.ACK_TIMEOUT * 1000, 'unload_module')
    } finally {
      this.runtime.popUnloadFuture(reqId)
    }
  }

  requireStarted(action: string): void {
    if (this._routine._phase !== 'started') {
      throw new Error(`${this.name}: must start() before ${action} (routine not started)`)
    }
  }

  // --- 模块拓扑:静态预测式 conflict(业务侧编排策略用) ---

  /** 两组 modules 是否冲突(cone 交集非空).纯本地计算,零 round-trip.
   * 树未缓存时抛错(不静默返回 false----那会让冲突对误并行). */
  conflict(modsA: string[], modsB: string[]): boolean {
    const tree = this.runtime.moduleTree
    if (!tree) {
      throw new Error(`${this.name}: module tree not cached yet (kernel has not pushed module.tree)`)
    }
    return tree.conflict(modsA, modsB)
  }

  // --- p2p 通信(req / streamReq 骑这条隧道,kernel dumb forward) ---

  /** 起一个后台 task(框架内部:@stream provider gen).委托 runtime task 池. */
  spawn<T>(p: Promise<T>): Promise<T> { return this._io.spawn(p) }

  /** (内部原语)发 message.* 给 target_id.envelope 全在 data 里,kernel 不解析. */
  async sendMessage(targetId: string, sendEvent: string, data: any = undefined): Promise<void> {
    await this._io.sendMessage({
      targetIds: [targetId], sendEvent, data: data ?? {}, sourceId: this.id,
    })
  }

  /** 对 target routine 发 request(经 kernel p2p 转发),等回执拿 result.
   * handler 抛异常 → ReqError;超时 → ReqTimeout. */
  async req(target: string, event: string, data: Payload | null = null,
            timeout = 30.0): Promise<any> {
    const reqId = newReqId()
    const fut = new Deferred<any>()
    this.runtime.registerReqFuture(reqId, fut)
    const envelope: Msg = {
      [ENVELOPE_REQ_ID]: reqId,
      [ENVELOPE_REPLY_TO]: this.id,
      [ENVELOPE_EVENT]: event,
      [ENVELOPE_DATA]: data ?? {},
    }
    try {
      await this.sendMessage(target, MESSAGE_REQ, envelope)
      const result = await withTimeout(fut.promise, timeout * 1000, `req ${event}`)
      return result
    } catch (e: any) {
      if (e instanceof TimeoutErr) {
        throw new ReqTimeout(`req ${JSON.stringify(event)} to ${target} timeout after ${timeout}s`)
      }
      throw e
    } finally {
      this.runtime.popReqFuture(reqId)
    }
  }

  async getRunningRoutines(): Promise<any[]> { return await this._transport.getRunningRoutines() }
  async getModuleTree(): Promise<any> { return await this._transport.getModuleTree() }
  async getRoutines(): Promise<any[]> { return await this._transport.getRoutines() }

  /** 对 target routine 发 stream request,返回 StreamCtx(for-await 消费). */
  async streamReq(target: string, event: string, data: Payload | null = null,
                  timeout = 30.0): Promise<StreamCtx> {
    const streamId = newReqId()
    const reader = new StreamReader(streamId, this, target)
    this.runtime.registerStreamReader(streamId, reader)
    const envelope: Msg = {
      [ENVELOPE_STREAM_ID]: streamId,
      [ENVELOPE_REPLY_TO]: this.id,
      [ENVELOPE_EVENT]: event,
      [ENVELOPE_DATA]: data ?? {},
    }
    await this.sendMessage(target, MESSAGE_STREAM_OPEN, envelope)
    return new StreamCtx(reader, timeout)
  }

  // --- lifecycle 观察流 ---

  /** 开启 kernel 生命周期事件观察(lifecycle 流).
   *
   *  返回事件 queue:先灌入当前 routine 树快照帧,再收增量帧
   *  (``{type: created/started/stopped/destroyed, id, parent_id, name, seq}``).
   *  消费端按 id 幂等 upsert/remove(id 不复用,乱序到达最终状态一致).
   *  实例停止时框架自动注销;也可显式 ``unwatchLifecycle()``.
   *  多 routine 同时观察共存,互不影响. */
  async watchLifecycle(): Promise<AsyncQueue<Msg>> {
    // 先注册 watcher 再发 enable:enable 之后 kernel 即开始推帧,
    // 晚注册会丢帧(丢的帧由快照按 id 幂等补齐).
    const q = new AsyncQueue<Msg>(256)
    this.runtime.registerLifecycleWatcher(this.id, q)
    const reqId = newReqId()
    const fut = new Deferred<Msg[]>()
    this.runtime.registerLifecycleFuture(reqId, fut)
    let frames: Msg[]
    try {
      await this._io.sendLifecycleWatch(reqId)
      frames = await withTimeout(fut.promise, RunContext.ACK_TIMEOUT * 1000, 'watch_lifecycle')
    } catch (e) {
      // 超时/发送失败:enable 未生效,注销刚注册的 watcher
      this.runtime.popLifecycleWatcher(this.id)
      throw e
    } finally {
      this.runtime.popLifecycleFuture(reqId)
    }
    for (const f of frames) {
      if (!q.tryPush(f)) {
        this.runtime.logger.warning(`lifecycle watcher ${this.id} queue full, snapshot truncated`)
      }
    }
    return q
  }

  /** 注销观察(最后一个观察者注销时自动关流). */
  async unwatchLifecycle(): Promise<void> {
    if (this.runtime.popLifecycleWatcher(this.id) !== undefined
        && this.runtime.lifecycleWatcherCount() === 0) {
      await this._io.sendLifecycleUnwatch()
    }
  }

  // --- pubsub(经 kernel 订阅表 fanout) ---

  /** 发一条 pubsub 消息到 (namespace, topic).kernel fanout 给所有订阅者. */
  async publish(topic: string, data: any = undefined, namespace = ''): Promise<void> {
    await this._io.sendPubsubPublish({ topic, data, sourceId: this.id, namespace })
  }

  /** (内部)发 pubsub.subscribe 注册到 kernel 订阅表. */
  async subscribeTopic(topic: string, namespace = ''): Promise<void> {
    await this._io.sendPubsubSubscribe({ id: this.id, topic, namespace })
  }

  /** 订阅 (namespace, topic):注册 handler 到本地表 + 发 pubsub.subscribe. */
  async subscribe(topic: string, handler: SubscribeHandler, namespace = ''): Promise<void> {
    this.runtime.registerSubscriber(this.id, namespace, topic, handler)
    await this.subscribeTopic(topic, namespace)
  }

  /** 退订 (namespace, topic):本地表删 handler + 发 pubsub.unsubscribe. */
  async unsubscribe(topic: string, namespace = ''): Promise<void> {
    const topics = this.runtime.popSubscriber(this.id)
    void topics
    await this._io.sendPubsubUnsubscribe({ id: this.id, topic, namespace })
  }

  namespace(ns: string): Namespace { return new Namespace(this, ns) }

  // --- yield(child 发,kernel dumb forward 给 parent) ---

  /** 发 routine.yield 给 kernel(child yield 一项 / 收尾 / 异常). */
  async sendYield(data: any = undefined,
                  opts: { isFinal?: boolean; error?: string } = {}): Promise<void> {
    await this._io.sendYield({
      id: this.id, data,
      isFinal: opts.isFinal ?? false,
      error: opts.error,
    })
  }

  // --- message.* 定向消息(push,调 target 的 onMessage) ---

  /** 给 target routine 发一条定向消息(message.send).target 需 created.
   * 派发是 spawn 并发----对端 onMessage 可能并发 fire,乱序到达,业务侧自带
   * id reorder. */
  async send(target: string, data: any = undefined): Promise<void> {
    await this.sendMessage(target, MESSAGE_SEND, data ?? {})
  }
}

/** namespaced pub/sub 助手. */
export class Namespace {
  constructor(private _ctx: RunContext, private _ns: string) {}

  async publish(topic: string, data: any = undefined): Promise<void> {
    await this._ctx.publish(topic, data, this._ns)
  }

  async subscribe(topic: string, handler: SubscribeHandler): Promise<void> {
    await this._ctx.subscribe(topic, handler, this._ns)
  }

  async unsubscribe(topic: string): Promise<void> {
    await this._ctx.unsubscribe(topic, this._ns)
  }
}
