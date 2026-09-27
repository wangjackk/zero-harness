/** RoutineHub ---- routine 生命周期 + 跨 routine 通信(传输无关).
 * 移植 z2/hub.py.

 传输经 Transport 抽象(InProcessTransport / GrpcClientTransport)----本类不关心 wire:
 入站 transport → dispatchInbound 按 event 分发(lifecycle.start/stop/destroy/
 created → LifecycleManager;module.tree → 缓存;其余 → onInbound);出站
 send* helpers build payload 后调 transport.sendEvent.

 ``connect(kernelRef)`` 是组装入口(kernel/launch.ts):inproc 走 link 绑定 +
 catalog push;remote 走 grpc 拨入 + 重连 + catalog push.``launch()`` 是
 inproc 的便捷封装.
 */
import { Deferred, withTimeout } from './async'
import {
  AcquireError, DeregisterError, LoadModuleError, RegisterError, ReloadError,
  ReleaseError, SubmitError, UnloadModuleError,
} from './errors'
import type { Logger } from './logger'
import { LifecycleManager } from './lifecycle'
import { ModuleTree } from './moduleTree'
import type { Kernel } from './kernel'
import type { InProcessLink } from './inproc'
import {
  connect as connectKernel, create as createKernel,
  type Connection, type InprocKernel, type KernelRef,
} from './kernel/launch'
import {
  ROUTINE_YIELD, ROUTINE_YIELDED, CATALOG_PUSH, CATALOG_PUSHED,
  CATALOG_REGISTER, CATALOG_REGISTERED, CATALOG_RELOAD, CATALOG_RELOADED,
  CATALOG_DEREGISTER, CATALOG_DEREGISTER_CMD, CATALOG_DEREGISTER_CMD_ACK,
  CATALOG_DEREGISTERED, ControlDoneReason,
  LIFECYCLE_EVENT, ENVELOPE_CANCEL, ENVELOPE_CHUNK, ENVELOPE_DATA, LIFECYCLE_UNWATCH,
  LIFECYCLE_WATCH, ENVELOPE_EOF, ENVELOPE_ERROR, ENVELOPE_OK, ENVELOPE_REQ_ID,
  LIFECYCLE_SNAPSHOT, ENVELOPE_STREAM_ID,
  LIFECYCLE_CREATED, LIFECYCLE_DESTROY, LIFECYCLE_START,
  LIFECYCLE_STARTED, LIFECYCLE_STOP, LIFECYCLE_STOPPED, MODULE_TREE,
  MESSAGE_DELIVERED, MESSAGE_REQ_DELIVERED, MESSAGE_REQ_REPLY_DELIVERED,
  MESSAGE_STREAM_CANCEL_DELIVERED, MESSAGE_STREAM_DATA_DELIVERED,
  MESSAGE_STREAM_OPEN_DELIVERED, PUBSUB_DELIVERED, PUBSUB_PUBLISH,
  PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE, ROUTINE_ACQUIRE, ROUTINE_ACQUIRED,
  ROUTINE_FORCE_RELEASE, ROUTINE_FORCE_ACQUIRE, ROUTINE_FORCE_START,
  ROUTINE_LOAD_MODULE, ROUTINE_MODULE_LOADED, ROUTINE_MODULE_UNLOADED,
  ROUTINE_RELEASE, ROUTINE_RELEASED, ROUTINE_SUBMIT, ROUTINE_SUBMITTED,
  ROUTINE_REJECTED, ROUTINE_START, ROUTINE_STOP, ROUTINE_UNLOAD_MODULE,
  ROUTINE_UNSUBMIT,
  type Msg, type Payload,
} from './protocol'
import {
  makeSource, passiveWire, routineNameOf, Routines,
  type RoutineClass, type RoutineSource,
} from './routine'
import { ReqError } from './errors'
import { ServerRuntime } from './runtime'
import type { Transport } from './transport'
import type { RoutineHandle } from './handle'

// catalog req_id 计数器(前缀 'cat' 跟 ctx 的 'r' 区分)
let _catalogReqCounter = 0

function newCatalogReqId(): string { return `cat${++_catalogReqCounter}` }

type AckExcCtor = new (msg: string) => Error

export class RoutineHub {
  /** catalog.register/deregister 等 kernel ack 默认超时(对标 ctx.ACK_TIMEOUT). */
  static ACK_TIMEOUT = 30.0

  readonly runtime: ServerRuntime
  /** 出站 transport(connect() 时赋值;之前调用出站方法直接报错----早暴露). */
  transport: Transport | null
  readonly hubId: string
  readonly lifecycle: LifecycleManager
  private _logger: Logger
  /** onInbound 分发表:event → async handler(msg).未知 event table-miss → no-op. */
  private _inboundHandlers: Map<string, (msg: Msg) => Promise<void>>

  constructor(routines: Routines, transport: Transport | null = null,
              modules: string[] = [], hubId = 'local') {
    this.runtime = new ServerRuntime(routines, modules)
    this._logger = this.runtime.logger
    this.transport = transport
    this.hubId = hubId
    this.lifecycle = new LifecycleManager(this, this.runtime)
    this.runtime.printSummary()
    this._inboundHandlers = new Map<string, (msg: Msg) => Promise<void>>([
      [MESSAGE_DELIVERED, m => this.onMessageDelivered(m)],
      [ROUTINE_YIELDED, m => this.onYielded(m)],
      [PUBSUB_DELIVERED, m => this.onPubsubDelivered(m)],
      [MESSAGE_REQ_DELIVERED, m => this.onMessageReqDelivered(m)],
      [MESSAGE_REQ_REPLY_DELIVERED, m => this.onMessageReqReplyDelivered(m)],
      [MESSAGE_STREAM_OPEN_DELIVERED, m => this.onMessageStreamOpenDelivered(m)],
      [MESSAGE_STREAM_DATA_DELIVERED, m => this.onMessageStreamDataDelivered(m)],
      [MESSAGE_STREAM_CANCEL_DELIVERED, m => this.onMessageStreamCancelDelivered(m)],
      [LIFECYCLE_STARTED, m => this.onLifecycleStarted(m)],
      [LIFECYCLE_STOPPED, m => this.onLifecycleStopped(m)],
      [ROUTINE_REJECTED, m => this.onRoutineRejected(m)],
      [ROUTINE_SUBMITTED, m => this.onRoutineSubmitted(m)],
      [ROUTINE_ACQUIRED, m => this.onRoutineAcquired(m)],
      [ROUTINE_MODULE_LOADED, m => this.onRoutineModuleLoaded(m)],
      [ROUTINE_MODULE_UNLOADED, m => this.onRoutineModuleUnloaded(m)],
      [ROUTINE_RELEASED, m => this.onRoutineReleased(m)],
      [CATALOG_REGISTERED, m => this.onCatalogRegistered(m)],
      [CATALOG_RELOADED, m => this.onCatalogReloaded(m)],
      [CATALOG_DEREGISTER_CMD, m => this.onCatalogDeregisterCmd(m)],
      [CATALOG_DEREGISTERED, m => this.onCatalogDeregistered(m)],
      [CATALOG_PUSHED, m => this.onCatalogPushed(m)],
    ])
  }

  // --- 出站:build payload → transport.sendEvent ---

  private async sendEvent(payload: Msg): Promise<void> {
    if (!this.transport) throw new Error('hub not connected (call hub.connect first)')
    await this.transport.sendEvent(payload)
  }

  /** 组装 hub ↔ kernel(kernel/launch.ts 的实现体).inproc: link 绑定 +
   * catalog push;remote: grpc 拨入 + 重连 + catalog push. */
  async connect(kernelRef: KernelRef): Promise<Connection> {
    return await connectKernel(this, kernelRef)
  }

  /** transport 断线通知: dial 的 kernel 世界已死 ---- handle 全作废 +
   * 本地实例全量销毁 (不区分 passive: 老实例以 stale rid 继续发 req, 只会
   * 污染注册表; 重连后 catalog push 重建, 拿新 rid).
   * await 销毁完成才返回 ---- 调用方(transport 重连 loop)等它结束后再
   * 进入重连期, 避免销毁期与重建期重叠撞 rid. */
  async handlePeerDown(peerId: string): Promise<void> {
    this._logger.warning(
      `peer ${peerId} down; invalidating handles, destroying local instances`)
    this.runtime.peerDownAll(`transport peer down (${peerId})`)
    await this.lifecycle.stopAllLocal()
  }

  /** routine 自杀请求:本地直接走 handleStop(不经 kernel 绕圈). */
  async requestStop(id: string): Promise<void> {
    await this.lifecycle.handleStop({ id })
  }

  /** 只发 kernel;父 handle 的 notifyStarted 由 kernel 中转回
   * lifecycle.started 时在 onInbound 里做(统一经 kernel,不本地抄近路). */
  async sendLifecycleStarted(id: string): Promise<void> {
    await this.sendEvent({ event: LIFECYCLE_STARTED, id })
  }

  /** created 回报给 kernel:kernel 等 this 回报后才发 routine.submitted 给父,
   * 所以父 submit 拿到 id 时 instance 一定已 created.带 modules(onCreated
   * 钩子返回值)----kernel 存进 node.declared(start 的 tryAcquire 用)+ 经
   * submitted 回执带给父 handle. */
  async sendLifecycleCreated(id: string, modules: string[] | null = null): Promise<void> {
    const payload: Msg = { event: LIFECYCLE_CREATED, id }
    if (modules !== null) payload.modules = modules
    await this.sendEvent(payload)
  }

  async sendLifecycleStopped(id: string, reason: string = ControlDoneReason.UNKNOWN,
                             result: any = undefined, error: string | null = null): Promise<void> {
    const payload: Msg = { event: LIFECYCLE_STOPPED, id, reason }
    if (result !== undefined) payload.result = result
    if (error !== null) payload.error = error
    await this.sendEvent(payload)
  }

  // --- routine 调 routine 反向事件(hub→kernel) ---

  async sendRoutineSubmit(o: { reqId: string; parentId: string; name: string; kwargs: Payload }): Promise<void> {
    await this.sendEvent({
      event: ROUTINE_SUBMIT, req_id: o.reqId, parent_id: o.parentId,
      name: o.name, kwargs: o.kwargs,
    })
  }

  async sendRoutineStart(o: { childId: string; tryStart?: boolean }): Promise<void> {
    const payload: Msg = { event: ROUTINE_START, child_id: o.childId }
    if (o.tryStart) payload.try = true
    await this.sendEvent(payload)
  }

  async sendRoutineStop(o: { childId: string }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_STOP, child_id: o.childId })
  }

  async sendRoutineUnsubmit(o: { childId: string }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_UNSUBMIT, child_id: o.childId })
  }

  async sendRoutineAcquire(o: { reqId: string; id: string; modules: string[] }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_ACQUIRE, req_id: o.reqId, id: o.id, modules: o.modules })
  }

  async sendRoutineRelease(o: { reqId: string; id: string; modules: string[] }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_RELEASE, req_id: o.reqId, id: o.id, modules: o.modules })
  }

  async sendRoutineForceRelease(o: { reqId: string; id: string; modules: string[] }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_FORCE_RELEASE, req_id: o.reqId, id: o.id, modules: o.modules })
  }

  async sendRoutineForceAcquire(o: { reqId: string; id: string; modules: string[] }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_FORCE_ACQUIRE, req_id: o.reqId, id: o.id, modules: o.modules })
  }

  async sendRoutineForceStart(o: { childId: string }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_FORCE_START, child_id: o.childId })
  }

  async sendRoutineLoadModule(o: { reqId: string; parentId: string; childId: string; name?: string }): Promise<void> {
    await this.sendEvent({
      event: ROUTINE_LOAD_MODULE, req_id: o.reqId,
      parent_id: o.parentId, child_id: o.childId, name: o.name ?? '',
    })
  }

  async sendRoutineUnloadModule(o: { reqId: string; childId: string }): Promise<void> {
    await this.sendEvent({ event: ROUTINE_UNLOAD_MODULE, req_id: o.reqId, child_id: o.childId })
  }

  registerHandle(childId: string, handle: RoutineHandle): void {
    this.runtime.registerHandle(childId, handle)
  }

  spawn<T>(p: Promise<T>): Promise<T> { return this.runtime.spawn(p) }

  // --- 运行时 routine 注册/移除(对外 API,等 kernel 回执) ---

  /** 运行时注册 routine 类.kernel catalog.registered 回执 ok=true 时由回执
   * handler(onCatalogRegistered)同步本地 register 后 resolve fut ---- fut
   * 完成即"kernel 路由 + 本地注册"双侧就绪.同名冲突抛 RegisterError. */
  async registerRoutine(...routines: (RoutineClass | Routines)[]): Promise<void> {
    for (const cls of RoutineHub.flattenRegisterArgs(routines)) {
      const reqId = newCatalogReqId()
      const fut = new Deferred<void>()
      this.runtime.registerRegisterFuture(reqId, fut, cls)
      try {
        await this.sendCatalogRegister(cls, reqId)
        await withTimeout(fut.promise, RoutineHub.ACK_TIMEOUT * 1000, 'catalog.register')
      } finally {
        // 成功路径 handler 已 pop(无害);超时路径清理条目,迟到回执变 no-op.
        this.runtime.popRegisterFuture(reqId)
      }
    }
  }

  /** 运行时重载 routine 类(不区分归属,同名覆盖).等 kernel reloaded 回执. */
  async reloadRoutine(...routines: (RoutineClass | Routines)[]): Promise<void> {
    for (const cls of RoutineHub.flattenRegisterArgs(routines)) {
      const reqId = newCatalogReqId()
      const fut = new Deferred<void>()
      this.runtime.registerReloadFuture(reqId, fut, cls)
      try {
        await this.sendCatalogReload(cls, reqId)
        await withTimeout(fut.promise, RoutineHub.ACK_TIMEOUT * 1000, 'catalog.reload')
      } finally {
        this.runtime.popReloadFuture(reqId)
      }
    }
  }

  /** 运行时移除 routine 类.两跳流程(kernel 协调,in-proc 下请求者==持有者):
   * deregister → kernel 发 cmd → 本地 dereg → ack → kernel 删路由+停实例 →
   * deregistered 回执.返回被移除的类. */
  async deregisterRoutine(name: string): Promise<RoutineClass | undefined> {
    const reqId = newCatalogReqId()
    const fut = new Deferred<void>()
    this.runtime.registerDeregisterFuture(reqId, fut)
    try {
      await this.sendCatalogDeregister(name, reqId)
      await withTimeout(fut.promise, RoutineHub.ACK_TIMEOUT * 1000, 'catalog.deregister')
      return this.runtime.popDeregisterResult(reqId)
    } finally {
      this.runtime.popDeregisterFuture(reqId)
    }
  }

  static flattenRegisterArgs(routines: (RoutineClass | Routines)[]): RoutineClass[] {
    const out: RoutineClass[] = []
    for (const r of routines) {
      if (r instanceof Routines) out.push(...r.getRoutines())
      else out.push(r)
    }
    return out
  }

  async sendCatalogRegister(cls: RoutineClass, reqId = ''): Promise<void> {
    await this.sendEvent({
      event: CATALOG_REGISTER, name: routineNameOf(cls),
      is_passive: passiveWire(cls), meta: (cls as any).meta ?? {},
      req_id: reqId,
    })
  }

  async sendCatalogReload(cls: RoutineClass, reqId = ''): Promise<void> {
    await this.sendEvent({
      event: CATALOG_RELOAD, name: routineNameOf(cls),
      is_passive: passiveWire(cls), meta: (cls as any).meta ?? {},
      req_id: reqId,
    })
  }

  async sendCatalogDeregister(name: string, reqId = ''): Promise<void> {
    await this.sendEvent({ event: CATALOG_DEREGISTER, name, req_id: reqId })
  }

  async sendCatalogDeregisterCmdAck(o: { reqId: string; ok: boolean; error?: string }): Promise<void> {
    const payload: Msg = { event: CATALOG_DEREGISTER_CMD_ACK, req_id: o.reqId, ok: o.ok }
    if (o.error) payload.error = o.error
    await this.sendEvent(payload)
  }

  // --- p2p / pubsub / yield 通信(hub→kernel) ---

  /** 发 message.* 事件.envelope 全在 data 里,kernel 不解析只按 targetIds
   * 转发成对应的 *delivered. */
  async sendMessage(o: { targetIds: string[]; sendEvent: string; data: any; sourceId: string }): Promise<void> {
    await this.sendEvent({
      event: o.sendEvent, target_ids: o.targetIds,
      data: o.data, source_id: o.sourceId,
    })
  }

  async sendPubsubSubscribe(o: { id: string; topic: string; namespace?: string }): Promise<void> {
    await this.sendEvent({
      event: PUBSUB_SUBSCRIBE, id: o.id, topic: o.topic,
      namespace: o.namespace ?? '',
    })
  }

  async sendPubsubUnsubscribe(o: { id: string; topic: string; namespace?: string }): Promise<void> {
    await this.sendEvent({
      event: PUBSUB_UNSUBSCRIBE, id: o.id, topic: o.topic,
      namespace: o.namespace ?? '',
    })
  }

  async sendPubsubPublish(o: { topic: string; data: any; sourceId: string; namespace?: string }): Promise<void> {
    const payload: Msg = {
      event: PUBSUB_PUBLISH, topic: o.topic, source_id: o.sourceId,
      namespace: o.namespace ?? '',
    }
    if (o.data !== undefined) payload.data = o.data
    await this.sendEvent(payload)
  }

  async sendYield(o: { id: string; data?: any; isFinal: boolean; error?: string }): Promise<void> {
    const payload: Msg = { event: ROUTINE_YIELD, id: o.id, is_final: o.isFinal }
    if (o.data !== undefined) payload.data = o.data
    if (o.error !== undefined) payload.error = o.error
    await this.sendEvent(payload)
  }

  // --- lifecycle 观察流(hub→kernel) ---

  /** 发 lifecycle.watch(开启生命周期观察,幂等). */
  async sendLifecycleWatch(reqId: string): Promise<void> {
    await this.sendEvent({ event: LIFECYCLE_WATCH, req_id: reqId })
  }

  /** 发 lifecycle.unwatch(关闭观察,最后一个观察者注销时发). */
  async sendLifecycleUnwatch(): Promise<void> {
    await this.sendEvent({ event: LIFECYCLE_UNWATCH })
  }

  /** 启动后主动 push catalog 给 kernel(routines 路由 + passive + modules).
   *
   * fire-and-forget(不阻塞等回执)----回执 CATALOG_PUSHED 由
   * onCatalogPushed 收到后打印.kernel 注册完即推 module.tree + 起 passive. */
  async sendCatalogPush(): Promise<void> {
    const reqId = newCatalogReqId()
    const routines = this.buildRoutines()
    await this.sendEvent({
      event: CATALOG_PUSH, req_id: reqId, routines,
      modules: this.runtime.modules, hub_id: this.hubId,
    })
  }

  /** catalog.push 的 routines 载荷:name + is_passive wire + meta. */
  buildRoutines(): Msg[] {
    return this.runtime.routines.getRoutines().map(cls => ({
      name: routineNameOf(cls),
      is_passive: passiveWire(cls),
      meta: (cls as any).meta ?? {},
    }))
  }

  // --- 入站分发(link worker 调) ---

  /** link 收到一条 kernel→hub 消息 → 按 event 分发.
   *
   * lifecycle.start/stop/destroy/created(带 name) → LifecycleManager
   * (spawn 不阻塞 worker);module.tree → 同步缓存;其余 → onInbound. */
  async dispatchInbound(peerId: string, msg: Msg): Promise<void> {
    void peerId
    const event = String(msg.event ?? '')
    if (event === MODULE_TREE) {
      this.cacheModuleTree(msg)
      return
    }
    if (event === LIFECYCLE_EVENT) {
      // 框架事件帧:inline 非阻塞 fanout(只做 queue tryPush,不堵 worker).
      this.runtime.fanoutLifecycle(msg)
      return
    }
    if (event === LIFECYCLE_SNAPSHOT) {
      // enable 回执:resolve 请求 future(快照帧由请求者灌进自己的队列).
      this.onLifecycleSnapshot(msg)
      return
    }
    if (event === LIFECYCLE_START) this.runtime.spawn(this.lifecycle.handleStart(msg))
    else if (event === LIFECYCLE_STOP) this.runtime.spawn(this.lifecycle.handleStop(msg))
    else if (event === LIFECYCLE_DESTROY) this.runtime.spawn(this.lifecycle.handleDestroy(msg))
    else if (event === LIFECYCLE_CREATED && msg.name) this.runtime.spawn(this.lifecycle.handleCreated(msg))
    else this.runtime.spawn(this.onInbound(msg))
  }

  /** kernel 推来的模块树拓扑 → 重建本地缓存(ctx.conflict 用;
   * GrpcClientTransport 拉 module.tree 后也调). */
  cacheModuleTree(msg: Msg): void {
    try {
      this.runtime.moduleTree = ModuleTree.fromDict(msg.tree ?? {})
    } catch (e: any) {
      this._logger.warning(`module.tree cache failed: ${e?.message ?? e}`)
    }
  }

  /** lifecycle.snapshot 回执:快照 items 转成 lifecycle 事件帧格式 resolve 请求
   *  future(watchLifecycle 灌进请求者的观察队列). */
  private onLifecycleSnapshot(msg: Msg): void {
    const fut = this.runtime.popLifecycleFuture(String(msg.req_id ?? ''))
    if (!fut || fut.done) return
    const seq = msg.seq
    const frames: Msg[] = (msg.routines ?? []).map((it: any) => ({
      event: LIFECYCLE_EVENT,
      type: (it.state === 'running' || it.state === 'starting') ? 'started' : 'created',
      id: String(it.id ?? ''),
      parent_id: String(it.parent_id ?? '0'),
      name: it.name ?? '',
      seq,
    }))
    fut.resolve(frames)
  }

  // --- reader 路由(kernel→hub 回向事件) ---

  /** kernel→hub 回向事件.按 event 查分发表派发,未知 event → table-miss → no-op. */
  async onInbound(msg: Msg): Promise<void> {
    const handler = this._inboundHandlers.get(String(msg.event ?? ''))
    if (handler) await handler(msg)
  }

  private async onLifecycleStarted(msg: Msg): Promise<void> {
    const cid = String(msg.id ?? '')
    const handle = this.runtime.getHandle(cid)
    if (handle) {
      handle.notifyStarted()
      handle._resolveAck(undefined)
    }
  }

  private async onLifecycleStopped(msg: Msg): Promise<void> {
    const cid = String(msg.id ?? '')
    const handle = this.runtime.getHandle(cid)
    if (handle) {
      const reason = String(msg.reason ?? '')
      // error 优先用 stopped 带的;没带则按 reason 退回通用文本
      let error = msg.error ?? null
      if (!error && reason === ControlDoneReason.ERROR) {
        error = `routine stopped with reason=${reason}`
      }
      handle.notifyDone(msg.result, error, reason)
      // 先 resolve handle.ack(stop/unsubmit 成功回执)再 popHandle
      handle._resolveAck(undefined)
      this.runtime.popHandle(cid)
    }
    // instance 清理由各自路径负责:created 态走 handleDestroy(cleanup),
    // running 态走 runner 退出(cleanup).这里只管 handle.
  }

  private async onRoutineRejected(msg: Msg): Promise<void> {
    const cid = String(msg.child_id ?? '')
    const err = String(msg.error ?? 'rejected')
    const op = String(msg.op ?? '')
    const handle = this.runtime.getHandle(cid)
    if (op === 'start' || op === 'force_start') {
      // start/tryStart/forceStart 失败:resolve 错误串(调方包成 StartError).
      // start/forceStart(非 try)失败:kernel 已清 node,本侧清 created
      // instance(handle 失效不可重试).try 失败:保留可重试.
      if (handle) handle._resolveAck(err)
      const isTry = Boolean(msg.try)
      if (!isTry && cid) this.lifecycle.cleanup(cid)
    } else {
      // stop/unsubmit 失败:逻辑错误(已 start 调 unsubmit 等),抛异常
      handle?._rejectAck(new Error(err))
    }
  }

  private async onRoutineSubmitted(msg: Msg): Promise<void> {
    const fut = this.runtime.popSubmitFuture(String(msg.req_id ?? ''))
    if (!fut || fut.done) return
    if (msg.error) {
      fut.reject(new SubmitError(String(msg.error)))
      return
    }
    let modules = msg.modules
    if (!Array.isArray(modules)) modules = null
    fut.resolve({ childId: String(msg.child_id ?? ''), modules })
  }

  /** acquired/released 类共享模板:ok=false → 抛 excType;ok=true → resolve. */
  private resolveAckFuture(fut: Deferred<void> | undefined, msg: Msg,
                           excType: AckExcCtor, errDefault: string): void {
    if (!fut || fut.done) return
    if (msg.ok === false) {
      fut.reject(new excType(String(msg.error ?? errDefault)))
    } else {
      fut.resolve()
    }
  }

  private async onRoutineAcquired(msg: Msg): Promise<void> {
    this.resolveAckFuture(this.runtime.popAcquireFuture(String(msg.req_id ?? '')), msg, AcquireError, 'acquire failed')
  }

  private async onRoutineReleased(msg: Msg): Promise<void> {
    this.resolveAckFuture(this.runtime.popAcquireFuture(String(msg.req_id ?? '')), msg, ReleaseError, 'release failed')
  }

  private async onRoutineModuleLoaded(msg: Msg): Promise<void> {
    this.resolveAckFuture(this.runtime.popLoadFuture(String(msg.req_id ?? '')), msg, LoadModuleError, 'load_module failed')
  }

  private async onRoutineModuleUnloaded(msg: Msg): Promise<void> {
    this.resolveAckFuture(this.runtime.popUnloadFuture(String(msg.req_id ?? '')), msg, UnloadModuleError, 'unload_module failed')
  }

  private async onCatalogRegistered(msg: Msg): Promise<void> {
    const entry = this.runtime.popRegisterFuture(String(msg.req_id ?? ''))
    if (!entry) return
    // ok 时同步本地 register ---- 回执先于 kernel 后续消息(passive auto-start
    // 的 lifecycle.created)dispatch, register 在 handler 内完成后者才可见.
    if (msg.ok !== false) {
      try {
        this.runtime.routines.register(entry.cls)
      } catch (e: any) {
        entry.fut.reject(new RegisterError(String(e?.message ?? e)))
        return
      }
    }
    this.resolveAckFuture(entry.fut, msg, RegisterError, 'register rejected by kernel')
  }

  private async onCatalogReloaded(msg: Msg): Promise<void> {
    const entry = this.runtime.popReloadFuture(String(msg.req_id ?? ''))
    if (!entry) return
    if (msg.ok !== false) {
      try {
        // reload 覆盖语义(同名替换): register 的严格模式在这里必抛 RegisterError
        this.runtime.routines.reload(entry.cls)
      } catch (e: any) {
        entry.fut.reject(new ReloadError(String(e?.message ?? e)))
        return
      }
    }
    this.resolveAckFuture(entry.fut, msg, ReloadError, 'reload rejected by kernel')
  }

  private async onCatalogDeregistered(msg: Msg): Promise<void> {
    this.resolveAckFuture(this.runtime.popDeregisterFuture(String(msg.req_id ?? '')), msg, DeregisterError, 'deregister rejected by kernel')
  }

  private async onCatalogPushed(msg: Msg): Promise<void> {
    const error = msg.error ? String(msg.error) : ''
    if (error) {
      // kernel 拒绝连接(hub_id 重复 / 缺失): 重连无意义, 停止 transport.
      // stop() 会 await _runTask, 不能在本回调(_run 栈内)await, 独立执行.
      this._logger.error(`catalog.push rejected: ${error}`)
      void this.transport?.stop()
      return
    }
    const registered = msg.registered ?? []
    const skipped = msg.skipped ?? []
    const reqId = String(msg.req_id ?? '')
    if (skipped.length) {
      this._logger.warning(`registered=${registered.length}, skipped=${skipped.length} ${JSON.stringify(skipped)}`)
    } else {
      this._logger.info(`registered=${registered.length}, skipped=0`)
    }
  }

  /** catalog.deregister.cmd(持有者收到,kernel 发来):本地 dereg → 存结果 →
   * 发 cmd.ack 给 kernel.kernel 收到 ack 后删路由 + 停实例 + 回执请求者. */
  private async onCatalogDeregisterCmd(msg: Msg): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const name = String(msg.name ?? '')
    if (!name) {
      await this.sendCatalogDeregisterCmdAck({ reqId, ok: false, error: 'name is required' })
      return
    }
    const removed = this.runtime.routines.deregister(name)
    if (reqId) this.runtime.setDeregisterResult(reqId, removed)
    await this.sendCatalogDeregisterCmdAck({ reqId, ok: true })
    this._logger.info(`catalog.deregister.cmd: local dereg ${name} (removed=${removed ? routineNameOf(removed) : 'none'})`)
  }

  // --- message.* / pubsub / yield 入站 ---

  /** 从 delivered 消息的 source 字段构造 RoutineSource(补 name:source 在
   * 本 hub 则能查到 name). */
  private resolveSource(msg: Msg): RoutineSource {
    const src = msg.source ?? {}
    const sourceId = String(src.id ?? '')
    const srcInst = this.runtime.getCreated(sourceId)
    if (srcInst) return makeSource(sourceId, srcInst.name)
    return makeSource(sourceId)
  }

  /** message.delivered{target_id, data, source}:纯定向消息 → 实例
   * mailbox 串行投递. created 后即可收; 无 mailbox(未 created/已销毁)
   * 丢弃. */
  private async onMessageDelivered(msg: Msg): Promise<void> {
    const targetId = String(msg.target_id ?? '')
    if (!this.runtime.getCreated(targetId)) return
    const source = this.resolveSource(msg)
    this.runtime.deliverMail(targetId, source, msg.data)
  }

  /** message.req_delivered:req 到达 provider → 立即 spawn 处理.独立
   * 事务(req_id 配对回执), 相互并行 ---- HTTP 语义, 慢 handler 不堵
   * 同实例后续 req. */
  private async onMessageReqDelivered(msg: Msg): Promise<void> {
    const targetId = String(msg.target_id ?? '')
    const inst = this.runtime.getCreated(targetId)
    if (!inst) return
    const source = this.resolveSource(msg)
    this.runtime.spawn(inst._serveRequest(msg.data ?? {}, source))
  }

  /** message.req_reply_delivered:req 回执到达 caller,resolve req future. */
  private async onMessageReqReplyDelivered(msg: Msg): Promise<void> {
    const data = msg.data ?? {}
    const reqId = data[ENVELOPE_REQ_ID] ?? ''
    const fut = this.runtime.popReqFuture(String(reqId))
    if (!fut || fut.done) return
    if (data[ENVELOPE_OK]) {
      fut.resolve(data[ENVELOPE_DATA])
    } else {
      fut.reject(new ReqError(String(data[ENVELOPE_ERROR] ?? 'req failed')))
    }
  }

  /** message.stream_open_delivered:开流到达 provider,spawn provider task. */
  private async onMessageStreamOpenDelivered(msg: Msg): Promise<void> {
    const targetId = String(msg.target_id ?? '')
    const inst = this.runtime.getCreated(targetId)
    if (!inst) return
    const source = this.resolveSource(msg)
    await inst._serveStream(msg.data ?? {}, source)
  }

  /** message.stream_data_delivered:chunk / eof 到达 caller,喂 StreamReader. */
  private async onMessageStreamDataDelivered(msg: Msg): Promise<void> {
    const data = msg.data ?? {}
    const streamId = String(data[ENVELOPE_STREAM_ID] ?? '')
    const reader = this.runtime.getStreamReader(streamId)
    if (!reader) return
    if (data[ENVELOPE_EOF]) {
      reader.feedEof(String(data[ENVELOPE_EOF]), data[ENVELOPE_ERROR])
      this.runtime.popStreamReader(streamId)
    } else {
      reader.feedChunk(data[ENVELOPE_CHUNK])
    }
  }

  /** message.stream_cancel_delivered:caller 取消 stream,置 provider 取消标志. */
  private async onMessageStreamCancelDelivered(msg: Msg): Promise<void> {
    const data = msg.data ?? {}
    const streamId = String(data[ENVELOPE_STREAM_ID] ?? '')
    const entry = this.runtime.getProviderStream(streamId)
    entry?.cancel()
    this.runtime.popProviderStream(streamId)
  }

  /** kernel fanout 回来的 pubsub.delivered(已按 hub 分组, 一条含
   * subscriber_ids 列表).逐个 subscriber 按 (namespace, topic) 找本地
   * handler 调 handler(source, data). */
  private async onPubsubDelivered(msg: Msg): Promise<void> {
    const topic = String(msg.topic ?? '')
    const namespace = String(msg.namespace ?? '')
    const data = msg.data
    const src = msg.source ?? {}
    let source = makeSource(String(src.id ?? ''))
    const srcInst = this.runtime.getCreated(source.id)
    if (srcInst) source = makeSource(source.id, srcInst.name)
    for (const rid of (msg.subscriber_ids as string[]) ?? []) {
      const handler = this.runtime.getSubscriberHandler(String(rid), namespace, topic)
      if (!handler) continue
      // pubsub 是离散通知: 每帧每订阅者立即起 task 消费, 不进 mailbox
      // 串行 ---- 慢 handler 不堵 inbound 循环, 也不积压后续帧
      this.runtime.spawn(handler(source, data).catch(e => {
        this._logger.error(`subscriber ${rid} @subscribe(${JSON.stringify(topic)}) failed: ${e?.stack ?? e}`)
      }))
    }
  }

  /** kernel 中转回来的 routine.yielded{id, data, is_final, error}.
   *
   * 按 id(child_id)找父侧 submit 时注册的 handle,喂它的 yield 迭代队列. */
  private async onYielded(msg: Msg): Promise<void> {
    const childId = String(msg.id ?? '')
    const handle = this.runtime.getHandle(childId)
    if (!handle) return
    const isFinal = Boolean(msg.is_final)
    const error = msg.error ?? null
    const data = isFinal ? undefined : msg.data
    handle._onYieldChunk(data, isFinal, error)
  }
}

/** launch() 的产物:hub + kernel + link 持有体,stop() 级联关停. */
export class System {
  constructor(readonly hub: RoutineHub, readonly kernel: Kernel, readonly link: InProcessLink,
              private _conn: Connection) {}

  async stop(): Promise<void> {
    await this._conn.stop()
  }
}

/** 组装并启动 inproc kernel + RoutineHub(kernel.create + hub.connect 的便捷封装).
 *
 * treePayload 是模块树拓扑 ``{"root": id, "modules": {id: {"children": [...]}}}``
 * (缺省单 root 空树).启动顺序:kernel 建 root → link 起 workers → hub push
 * catalog → kernel 注册路由 + 推 module.tree + 自动拉起 passive.
 */
export async function launch(routines: Routines, treePayload: Msg | null = null,
                             opts: { modules?: string[]; hubId?: string } = {}): Promise<System> {
  const payload = treePayload ?? { root: 'root', modules: { root: {} } }
  const kernelRef = await createKernel(payload.modules ?? { root: {} }) as InprocKernel
  const hub = new RoutineHub(routines, null, opts.modules ?? [], opts.hubId ?? 'local')
  const conn = await hub.connect(kernelRef)
  return new System(hub, kernelRef.kernel, kernelRef.link, conn)
}
