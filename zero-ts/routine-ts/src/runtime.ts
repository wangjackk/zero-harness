/** ServerRuntime ---- hub 侧运行时状态:注册表,实例表,回执 future 表,task 池
 * (移植 z2/runtime.py). */
import { AsyncQueue, Deferred } from './async'
import { setupLogger, type Logger } from './logger'
import type { Msg } from './protocol'
import type { ModuleTree } from './moduleTree'
import { passiveWire, type Routine, type RoutineClass, type RoutineSource, type Routines, type SubscribeHandler } from './routine'
import type { RoutineHandle } from './handle'
import type { StreamReader } from './stream'
import type { ProviderStream } from './ctx'

/** mailbox 慢回调阈值(ms):超过打 warning,定位积压源头. */
const MAIL_SLOW_MS = 2000

/** mailbox 控制块:closed 后拒收;sentinel(null)唤醒消费者退出. */
interface MailboxCtl {
  closed: boolean
  q: AsyncQueue<[RoutineSource, any] | null>
}

export class ServerRuntime {
  readonly routines: Routines
  readonly modules: string[]
  /** 运行实例表:rid → Routine instance(单 hub,rid 全局唯一). */
  readonly runningInstances = new Map<string, Routine>()
  readonly logger: Logger
  /** 模块树拓扑缓存:kernel 经 module.tree 事件推过来(catalog push /
   * load/unload 后变更重推).未推送前为 null----ctx.conflict 读时若 null 抛清晰错. */
  moduleTree: ModuleTree | null = null
  /** created instance 按 rid(message.* 路由用;created 时注册,stopped 时 pop). */
  readonly createdByRid = new Map<string, Routine>()
  /** 实例 mailbox:rid → 串行投递队列(on_message 严格保序; 慢回调只积压
   * 本实例, 不堵 hub inbound 循环. req/pubsub 不走 mailbox ---- 独立
   * spawn 并行). */
  private _mailboxes = new Map<string, MailboxCtl>()

  private _tasks = new Set<Promise<any>>()
  private _submitFutures = new Map<string, Deferred<{ childId: string; modules: string[] | null }>>()
  private _acquireFutures = new Map<string, Deferred<void>>()
  private _loadFutures = new Map<string, Deferred<void>>()
  private _unloadFutures = new Map<string, Deferred<void>>()
  /** register/reload 回执条目携带 cls: 回执 handler(ok=true)内同步本地
   * register 后才 resolve fut ---- FIFO dispatch 保证该 register
   * happens-before kernel 随后发来的任何依赖该 name 的消息(如 passive
   * auto-start 的 lifecycle.created), 不依赖 microtask 调度顺序. */
  private _registerFutures = new Map<string, { fut: Deferred<void>; cls: RoutineClass }>()
  private _reloadFutures = new Map<string, { fut: Deferred<void>; cls: RoutineClass }>()
  private _deregisterFutures = new Map<string, Deferred<void>>()
  private _deregisterResults = new Map<string, RoutineClass | undefined>()
  private _handles = new Map<string, RoutineHandle>()
  private _reqFutures = new Map<string, Deferred<any>>()
  private _streamReaders = new Map<string, StreamReader>()
  private _providerStreams = new Map<string, ProviderStream>()
  /** rid → `${namespace}\u0000${topic}` → handler */
  private _subscribers = new Map<string, Map<string, SubscribeHandler>>()
  /** lifecycle 观察者:rid → 事件 queue(实例 cleanup 自动注销).
   *  fanout 非阻塞 tryPush(慢消费者丢帧+warn,不堵 dispatch). */
  private _lifecycleWatchers = new Map<string, AsyncQueue<Msg>>()
  /** lifecycle.watch 的快照回执 future:req_id → future. */
  private _lifecycleFutures = new Map<string, Deferred<Msg[]>>()

  constructor(routines: Routines, modules: string[] = []) {
    this.routines = routines
    this.modules = modules
    this.logger = setupLogger('routine.hub')
  }

  /** 起一个后台 task(JS promise eager,只做登记 + 异常兜底日志). */
  spawn<T>(p: Promise<T>): Promise<T> {
    this._tasks.add(p)
    void p
      .catch(e => { this.logger.error(`spawned task died: ${e?.stack ?? e}`) })
      .finally(() => { this._tasks.delete(p) })
    return p
  }

  printSummary(): void {
    try {
      const rs = this.routines.getRoutines()
      const enabled = rs.filter(r => (r as any).enable).length
      const passiveN = rs.filter(r => passiveWire(r).flag).length
      this.logger.info(`${rs.length} routines · ${enabled} enabled · ${passiveN} passive`)
    } catch {
      this.logger.info(String(this.routines))
    }
  }

  /** 同 rid 已有实例则复用(restart 语义),否则新建. */
  resolveInstance(rid: string, cls: RoutineClass): Routine {
    return this.runningInstances.get(rid) ?? new cls()
  }

  // --- submit future 表 ---

  registerSubmitFuture(reqId: string, fut: Deferred<{ childId: string; modules: string[] | null }>): void {
    this._submitFutures.set(reqId, fut)
  }

  popSubmitFuture(reqId: string): Deferred<{ childId: string; modules: string[] | null }> | undefined {
    const f = this._submitFutures.get(reqId)
    this._submitFutures.delete(reqId)
    return f
  }

  // --- acquire/release future 表 ---

  registerAcquireFuture(reqId: string, fut: Deferred<void>): void {
    this._acquireFutures.set(reqId, fut)
  }

  popAcquireFuture(reqId: string): Deferred<void> | undefined {
    const f = this._acquireFutures.get(reqId)
    this._acquireFutures.delete(reqId)
    return f
  }

  // --- load/unload future 表 ---

  registerLoadFuture(reqId: string, fut: Deferred<void>): void {
    this._loadFutures.set(reqId, fut)
  }

  popLoadFuture(reqId: string): Deferred<void> | undefined {
    const f = this._loadFutures.get(reqId)
    this._loadFutures.delete(reqId)
    return f
  }

  registerUnloadFuture(reqId: string, fut: Deferred<void>): void {
    this._unloadFutures.set(reqId, fut)
  }

  popUnloadFuture(reqId: string): Deferred<void> | undefined {
    const f = this._unloadFutures.get(reqId)
    this._unloadFutures.delete(reqId)
    return f
  }

  // --- catalog register/reload/deregister future 表 ---

  registerRegisterFuture(reqId: string, fut: Deferred<void>, cls: RoutineClass): void {
    this._registerFutures.set(reqId, { fut, cls })
  }

  popRegisterFuture(reqId: string): { fut: Deferred<void>; cls: RoutineClass } | undefined {
    const e = this._registerFutures.get(reqId)
    this._registerFutures.delete(reqId)
    return e
  }

  registerReloadFuture(reqId: string, fut: Deferred<void>, cls: RoutineClass): void {
    this._reloadFutures.set(reqId, { fut, cls })
  }

  popReloadFuture(reqId: string): { fut: Deferred<void>; cls: RoutineClass } | undefined {
    const e = this._reloadFutures.get(reqId)
    this._reloadFutures.delete(reqId)
    return e
  }

  registerDeregisterFuture(reqId: string, fut: Deferred<void>): void {
    this._deregisterFutures.set(reqId, fut)
  }

  popDeregisterFuture(reqId: string): Deferred<void> | undefined {
    const f = this._deregisterFutures.get(reqId)
    this._deregisterFutures.delete(reqId)
    return f
  }

  // --- catalog.deregister 两跳结果 ---

  setDeregisterResult(reqId: string, removed: RoutineClass | undefined): void {
    this._deregisterResults.set(reqId, removed)
  }

  popDeregisterResult(reqId: string): RoutineClass | undefined {
    const r = this._deregisterResults.get(reqId)
    this._deregisterResults.delete(reqId)
    return r
  }

  // --- handle 表 ---

  registerHandle(childId: string, handle: RoutineHandle): void {
    this._handles.set(childId, handle)
  }

  /** transport 断线: kernel 世界已死, 所有 handle 一次性作废
   * (调用方的 await 释放为错误; 实例销毁由 hub.handlePeerDown →
   * lifecycle.stopAllLocal 接管, 不留孤儿). */
  peerDownAll(reason: string): void {
    for (const [id, h] of [...this._handles]) {
      try {
        h.notifyDone(null, reason, 'ERROR')
      } catch { /* hook 异常不阻断其余清理 */ }
      this._handles.delete(id)
    }
  }

  popHandle(childId: string): RoutineHandle | undefined {
    const h = this._handles.get(childId)
    this._handles.delete(childId)
    return h
  }

  getHandle(childId: string): RoutineHandle | undefined {
    return this._handles.get(childId)
  }

  // --- created instance 按 rid ---

  registerCreated(rid: string, instance: Routine): void {
    this.createdByRid.set(rid, instance)
  }

  popCreated(rid: string): Routine | undefined {
    const i = this.createdByRid.get(rid)
    this.createdByRid.delete(rid)
    return i
  }

  getCreated(rid: string): Routine | undefined {
    return this.createdByRid.get(rid)
  }

  // --- 实例 mailbox(串行投递)---

  /** 实例 mailbox 建立 + 消费者起动(created 注册时调;重建先关旧的).
   * 消费者逐条调 onMessage, 同一实例严格串行 ---- 顺序语义由框架保
   * 证, 业务不再自建 reorder.慢回调只积压本实例. */
  openMailbox(rid: string, instance: Routine): void {
    this.closeMailbox(rid)
    const ctl: MailboxCtl = { closed: false, q: new AsyncQueue() }
    this._mailboxes.set(rid, ctl)
    void this.spawn(this._mailboxLoop(rid, instance, ctl))
  }

  /** 实例销毁时关 mailbox:停接收 + 唤醒消费者退出(JS promise 不可取消,
   * 在途回调让其自然跑完, 不再产生僵尸投递). */
  closeMailbox(rid: string): void {
    const ctl = this._mailboxes.get(rid)
    if (ctl === undefined) return
    this._mailboxes.delete(rid)
    ctl.closed = true
    ctl.q.push(null)
  }

  /** message 投递入实例 mailbox(调 onMessage).无 mailbox(实例未
   * created / 已销毁)静默丢弃, 对齐旧语义. */
  deliverMail(rid: string, source: RoutineSource, data: any): void {
    const ctl = this._mailboxes.get(rid)
    if (ctl !== undefined && !ctl.closed) ctl.q.push([source, data])
  }

  private async _mailboxLoop(rid: string, instance: Routine,
                             ctl: MailboxCtl): Promise<void> {
    while (!ctl.closed) {
      const item = await ctl.q.get()
      if (item === null) break
      const [source, data] = item
      const t0 = Date.now()
      try {
        await instance.onMessage(source, data)
      } catch (e: any) {
        this.logger.error(`${instance.name}#${rid} mailbox callback failed: ${e?.stack ?? e}`)
      }
      const dt = Date.now() - t0
      if (dt > MAIL_SLOW_MS) {
        this.logger.warning(`${instance.name}#${rid} slow mailbox callback: ${dt}ms (queue depth=${ctl.q.length})`)
      }
    }
  }

  // --- req 回执 future 表 ---

  registerReqFuture(reqId: string, fut: Deferred<any>): void {
    this._reqFutures.set(reqId, fut)
  }

  popReqFuture(reqId: string): Deferred<any> | undefined {
    const f = this._reqFutures.get(reqId)
    this._reqFutures.delete(reqId)
    return f
  }

  // --- streamreq reader 表 ---

  registerStreamReader(streamId: string, reader: StreamReader): void {
    this._streamReaders.set(streamId, reader)
  }

  popStreamReader(streamId: string): StreamReader | undefined {
    const r = this._streamReaders.get(streamId)
    this._streamReaders.delete(streamId)
    return r
  }

  getStreamReader(streamId: string): StreamReader | undefined {
    return this._streamReaders.get(streamId)
  }

  // --- streamreq provider 侧控制对象表(stream_id → cancel flag 入口)---

  registerProviderStream(streamId: string, entry: ProviderStream): void {
    this._providerStreams.set(streamId, entry)
  }

  popProviderStream(streamId: string): ProviderStream | undefined {
    const e = this._providerStreams.get(streamId)
    this._providerStreams.delete(streamId)
    return e
  }

  getProviderStream(streamId: string): ProviderStream | undefined {
    return this._providerStreams.get(streamId)
  }

  // --- pubsub 订阅者表 ---

  registerSubscriber(subscriberId: string, namespace: string,
                     topic: string, handler: SubscribeHandler): void {
    let topics = this._subscribers.get(subscriberId)
    if (!topics) { topics = new Map(); this._subscribers.set(subscriberId, topics) }
    topics.set(`${namespace}\u0000${topic}`, handler)
  }

  getSubscriberHandler(subscriberId: string, namespace: string,
                       topic: string): SubscribeHandler | undefined {
    return this._subscribers.get(subscriberId)?.get(`${namespace}\u0000${topic}`)
  }

  popSubscriber(subscriberId: string): Map<string, SubscribeHandler> | undefined {
    const s = this._subscribers.get(subscriberId)
    this._subscribers.delete(subscriberId)
    return s
  }

  // --- lifecycle 观察者表 ---

  /** 注册观察者(rid → queue).幂等:重复注册覆盖旧 queue. */
  registerLifecycleWatcher(rid: string, q: AsyncQueue<Msg>): void {
    this._lifecycleWatchers.set(rid, q)
  }

  /** 注销观察者.返回被注销的 queue(undefined = 未注册). */
  popLifecycleWatcher(rid: string): AsyncQueue<Msg> | undefined {
    const q = this._lifecycleWatchers.get(rid)
    this._lifecycleWatchers.delete(rid)
    return q
  }

  /** 当前观察者数(归零时调用方发 lifecycle.unwatch). */
  lifecycleWatcherCount(): number { return this._lifecycleWatchers.size }

  /** lifecycle 帧非阻塞投递给所有观察者.慢消费者(队列满)丢帧+warn
   *  ----观察者隔离,一个慢不拖别人,也不堵 dispatch. */
  fanoutLifecycle(msg: Msg): void {
    for (const [rid, q] of this._lifecycleWatchers) {
      if (!q.tryPush(msg)) {
        this.logger.warning(`lifecycle watcher ${rid} queue full, dropped frame (type=${msg.type})`)
      }
    }
  }

  // --- lifecycle 快照 future 表 ---

  registerLifecycleFuture(reqId: string, fut: Deferred<Msg[]>): void {
    this._lifecycleFutures.set(reqId, fut)
  }

  popLifecycleFuture(reqId: string): Deferred<Msg[]> | undefined {
    const f = this._lifecycleFutures.get(reqId)
    this._lifecycleFutures.delete(reqId)
    return f
  }
}
