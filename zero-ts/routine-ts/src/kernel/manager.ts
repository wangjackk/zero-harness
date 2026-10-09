/** Kernel ---- 嵌入式编排内核(移植 Go kernel/shell:shell+handler+remote+passive+broker).

 与 hub 同进程同事件循环,经 InProcessLink 双向 FIFO 队列通信.职责:
 - routine 树编排: create(created 态)→ start(TryAcquire 占模块+运行)→
   stop(级联,父必须等子)→ 自然终止级联清理.
 - 模块冲突: 权威 Tree 的 cone 互斥(父子可共占,外人抢占被拒).
 - 通信 broker: message.* 按 target_id 定向转发;pubsub 订阅表 fanout;
   yield 中转回父.
 - catalog: 路由表(name → passive/meta)+ 运行时 register/reload/deregister.
 - passive 自动拉起(catalog push 后 Execute(root, name, kwargs)).

 与 Go 版差异:
 - bus/conn 层退化为 link 直连 + grpc peer conn.
 - 出站对齐 Go sendOut: 显式目标 conn_id(sendTo),路由决策在各
   调用点(回执发 dispatch source / lifecycle 命令发 node owner /
   module.tree 广播),无字段推断路由.
 */
import { Deferred, withTimeout } from '../async'
import { setupLogger } from '../logger'
import {
  CATALOG_DEREGISTER, CATALOG_DEREGISTER_CMD, CATALOG_DEREGISTER_CMD_ACK,
  CATALOG_DEREGISTERED, CATALOG_PUSH, CATALOG_PUSHED, CATALOG_REGISTER,
  CATALOG_REGISTERED, CATALOG_RELOAD, CATALOG_RELOADED,
  LIFECYCLE_EVENT, LIFECYCLE_UNWATCH, LIFECYCLE_WATCH, LIFECYCLE_SNAPSHOT,
  LIFECYCLE_CREATED, LIFECYCLE_DESTROY, LIFECYCLE_START, LIFECYCLE_STARTED,
  LIFECYCLE_STOP, LIFECYCLE_STOPPED, MESSAGE_REQ, MESSAGE_REQ_DELIVERED,
  MESSAGE_REQ_REPLY, MESSAGE_REQ_REPLY_DELIVERED, MESSAGE_DELIVERED,
  MESSAGE_SEND, MESSAGE_STREAM_CANCEL, MESSAGE_STREAM_CANCEL_DELIVERED,
  MESSAGE_STREAM_DATA, MESSAGE_STREAM_DATA_DELIVERED,
  MESSAGE_STREAM_OPEN, MESSAGE_STREAM_OPEN_DELIVERED, MODULE_TREE,
  PUBSUB_DELIVERED, PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE,
  REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING,
  ROUTINE_ACQUIRE, ROUTINE_ACQUIRED, ROUTINE_FORCE_ACQUIRE,
  ROUTINE_FORCE_RELEASE, ROUTINE_LOAD_MODULE,
  ROUTINE_MODULE_LOADED, ROUTINE_RELEASE, ROUTINE_RELEASED,
  ROUTINE_START, ROUTINE_STOP, ROUTINE_SUBMIT, ROUTINE_SUBMITTED,
  ROUTINE_UNLOAD_MODULE, ROUTINE_MODULE_UNLOADED, ROUTINE_UNSUBMIT,
  ROUTINE_REJECTED, ROUTINE_YIELD, ROUTINE_YIELDED,
  ControlDoneReason,
  type Msg, type Payload,
} from '../protocol'
import { Command, State } from './command'
import { ConflictError, Tree } from './tree'
import type { InProcessLink } from '../inproc'

const log = setupLogger('routine.kernel', 'warning')

export interface CreatedResult {
  modules: string[]
  error: string | null
}

export class Route {
  constructor(public isPassive = false, public passiveKwargs: Payload = {},
              public meta: Msg = {}, /** 提供该 routine 的 hub 身份(catalog.push 的 hub_id). */
              public owner = 'local') {}
}

/** kernel 出站连接: inproc link('local')或 grpc peer(dial-in).
 *  hubId: 该 conn 绑定的 hub 身份(catalog.push 登记; hub 侧自报的稳定 id).
 *  close: 拒绝连接时断开本 conn 的回调(仅 grpc peer 有). */
export interface KernelConn {
  readonly id: string
  send(msg: Msg): void
  hubId?: string
  close?(): void
}

export class Node {
  readonly createdFut: Deferred<CreatedResult>
  readonly startedFut: Deferred<void>
  readonly stoppedFut: Deferred<Msg>
  declared: string[] = []
  /** 该 routine 实例归属的 hub 身份(事件出站路由依据). */
  owner = 'local'
  children: Node[] = []
  createdDone = false
  createdSent = false
  stopping = false
  stopReason = ''
  stopBy = 0
  /** lifecycle 终止帧已发标志(一个 node 恰一帧 stopped/destroyed). */
  stopSent = false
  /** start 时的优先级(冲突驱逐裁决用). */
  priority = 0

  constructor(readonly cmd: Command, readonly parent: Node | null) {
    this.createdFut = new Deferred()
    this.startedFut = new Deferred()
    this.stoppedFut = new Deferred()
  }
}

type Handler = (msg: Msg, source: string) => Promise<void>

export class Kernel {
  static CREATED_TIMEOUT = 30.0
  static STOP_TIMEOUT = 10.0

  readonly tree: Tree
  readonly nodes = new Map<number, Node>()
  readonly routes = new Map<string, Route>()
  /** pubsub 订阅表: `${namespace}\u0000${topic}` → set[subscriber_rid] */
  private _pubsub = new Map<string, Set<string>>()
  private _passiveStarted = new Set<string>()
  private _link: InProcessLink | null = null
  /** 出站 conn 表: 'local'(inproc link) + grpc peer(dial-in). */
  private _conns = new Map<string, KernelConn>()
  /** hub 身份 → conn id(catalog.push 登记): owner 路由经此解析到 conn,
   *  hub 断线即摘除; 重复 hub_id 的新 conn 被拒绝连接(契约见 docs/03). */
  private _hubConns = new Map<string, string>()
  /** req_id → {name, holder, requester}: deregister 两跳流程的 pending 表
   *  (cmd.ack 不带 name ---- 对齐 Go pendingDeregisters). */
  private _pendingDeregisters = new Map<string, { name: string; holder: string; requester: string }>()
  /** 开启 lifecycle 观察的 conn 集合(框架可观察性原语,conn 级开关). */
  private _watchConns = new Set<string>()
  private _watchSeq = 0
  private _rootId = 0
  private _dispatch: Map<string, Handler>

  constructor(tree: Tree) {
    this.tree = tree
    this._dispatch = new Map<string, Handler>([
      [LIFECYCLE_CREATED, (m, s) => this.onLifecycleCreated(m, s)],
      [LIFECYCLE_STARTED, (m, s) => this.onLifecycleStarted(m, s)],
      [LIFECYCLE_STOPPED, (m, s) => this.onLifecycleStopped(m, s)],
      [PUBSUB_SUBSCRIBE, (m, s) => this.onPubsubSubscribe(m, s)],
      [PUBSUB_UNSUBSCRIBE, (m, s) => this.onPubsubUnsubscribe(m, s)],
      [PUBSUB_PUBLISH, (m, s) => this.onPubsubPublish(m, s)],
      [LIFECYCLE_WATCH, (m, s) => this.onLifecycleWatch(m, s)],
      [LIFECYCLE_UNWATCH, (m, s) => this.onLifecycleUnwatch(s)],
      [ROUTINE_YIELD, (m, s) => this.onYield(m, s)],
      [ROUTINE_SUBMIT, (m, s) => this.onRoutineSubmit(m, s)],
      [ROUTINE_START, (m, s) => this.onRoutineStart(m, s)],
      [ROUTINE_STOP, (m, s) => this.onRoutineStop(m, s)],
      [ROUTINE_UNSUBMIT, (m, s) => this.onRoutineUnsubmit(m, s)],
      [ROUTINE_ACQUIRE, (m, s) => this.onRoutineAcquire(m, s)],
      [ROUTINE_RELEASE, (m, s) => this.onRoutineRelease(m, s)],
      [ROUTINE_FORCE_ACQUIRE, (m, s) => this.onRoutineForceAcquire(m, s)],
      [ROUTINE_FORCE_RELEASE, (m, s) => this.onRoutineForceRelease(m, s)],
      [ROUTINE_LOAD_MODULE, (m, s) => this.onRoutineLoadModule(m, s)],
      [ROUTINE_UNLOAD_MODULE, (m, s) => this.onRoutineUnloadModule(m, s)],
      [MESSAGE_SEND, (m, s) => this.onMessage(m, s)],
      [MESSAGE_REQ, (m, s) => this.onMessage(m, s)],
      [MESSAGE_REQ_REPLY, (m, s) => this.onMessage(m, s)],
      [MESSAGE_STREAM_OPEN, (m, s) => this.onMessage(m, s)],
      [MESSAGE_STREAM_DATA, (m, s) => this.onMessage(m, s)],
      [MESSAGE_STREAM_CANCEL, (m, s) => this.onMessage(m, s)],
      [CATALOG_PUSH, (m, s) => this.onCatalogPush(m, s)],
      [CATALOG_REGISTER, (m, s) => this.onCatalogRegister(m, s)],
      [CATALOG_RELOAD, (m, s) => this.onCatalogReload(m, s)],
      [CATALOG_DEREGISTER, (m, s) => this.onCatalogDeregister(m, s)],
      [CATALOG_DEREGISTER_CMD_ACK, (m, s) => this.onCatalogDeregisterCmdAck(m, s)],
    ])
  }

  // --- 生命周期 ---

  bind(link: InProcessLink): void {
    this._link = link
    this._conns.set('local', { id: 'local', send: m => link.kernelToHub(m) })
  }

  /** 注册出站 conn(grpc peer dial-in 接入时调). */
  registerConn(conn: KernelConn): void { this._conns.set(conn.id, conn) }

  /** conn 断开: fail 该 owner 的节点 + 清路由(对齐 Go kernel failPending + UnloadRemote). */
  dropConn(connId: string): void {
    const conn = this._conns.get(connId)
    const hubId = conn?.hubId ?? ''
    this._conns.delete(connId)
    if (hubId && this._hubConns.get(hubId) === connId) this._hubConns.delete(hubId)
    this._watchConns.delete(connId)
    // 路由/节点 owner 是 hub 身份; 未 push 过 catalog 的违规路径退回 conn id.
    const owner = hubId || connId
    for (const [name, r] of [...this.routes]) {
      if (r.owner === owner) { this.routes.delete(name); this._passiveStarted.delete(name) }
    }
    for (const [rid, n] of [...this.nodes]) {
      if (n.owner !== owner) continue
      this.unsubscribeRid(String(rid))
      const err = `hub '${owner}' disconnected`
      n.createdFut.resolve({ modules: [], error: err })
      n.stoppedFut.resolve({ event: 'lifecycle.stopped', reason: err })
      // 不走 cmd.stop(): peer 已死,lifecycle.stop 无处可发(会错路由到别的 conn);
      // body 靠上面 resolved 的 future 自然退出,finally 里 release 模块.
      this.removeNode(rid)
      this.tree.release(rid)
    }
    this.pushModuleView()
    log.warning(`conn '${connId}' (hub '${owner}') dropped: routes failed, nodes cleaned`)
  }

  async start(): Promise<void> {
    const rootName = this.tree.rootId
    const cmd = new Command('root')
    cmd.attachBody(async () => { await cmd.kill.promise })
    cmd.parentId = 0
    this.tree.tryAcquire(cmd.id, [rootName], new Set())
    cmd.setModules([rootName])
    const n = new Node(cmd, null)
    this.nodes.set(cmd.id, n)
    this._rootId = cmd.id
    cmd.start()
    cmd.markRunning()
    log.info(`kernel ready (root=${this._rootId}, tree root=${rootName})`)
  }

  get rootId(): number { return this._rootId }

  async stop(): Promise<void> {
    await this.stopNode(this.nodes.get(this._rootId)!, false, 0)
  }

  // --- 出入站 ---

  /** 出站显式目标发送(对齐 Go sendOut: publish 到 conn 的出站通道).
   *  target 两种取值, 单点解析: hub 身份(Route/Node owner, 经 _hubConns
   *  找 conn)或直连 conn id(回执 source). conn 不存在/已断 = 无接收方,
   *  丢弃(fire-and-forget 语义). 路由决策在各调用点: 回执类发 source,
   *  lifecycle 命令发 node owner, module.tree 广播. */
  private sendTo(target: string, msg: Msg): void {
    const conn = this._conns.get(this._hubConns.get(target) ?? target)
    if (!conn) {
      log.debug(`send_to ${target}: conn gone, drop ${String(msg.event ?? '')}`)
      return
    }
    conn.send(msg)
  }

  /** conn 的 hub 身份; 未 push 过 catalog 的退回 conn id. */
  private hubOf(connId: string): string {
    for (const [hubId, cid] of this._hubConns) if (cid === connId) return hubId
    return connId
  }

  /** hub→kernel 事件总入口(inproc link 或 grpc peer 逐条调).
   *  source 传给每个 handler(对齐 Go dispatchEvent(connID, msg))----
   *  handler 内异步等待后回执仍发给正确的请求来源. */
  async dispatch(msg: Msg, source = 'local'): Promise<void> {
    const handler = this._dispatch.get(String(msg.event ?? ''))
    if (handler) await handler(msg, source)
    else log.debug(`dispatch table-miss: ${String(msg.event)}`)
  }

  /** 同步查询(transport.req 直达,零排队). */
  async handleReq(msg: Msg): Promise<Msg> {
    const event = String(msg.event ?? '')
    if (event === REQ_GET_RUNNING) return { routines: this.runningRoutines() }
    if (event === REQ_GET_MODULE_TREE) return { tree: this.tree.serialize() }
    if (event === REQ_GET_ROUTINES) {
      return {
        routines: [...this.routes.entries()].map(([name, r]) => ({
          name, hub_id: r.owner,
          is_passive: { flag: r.isPassive, kwargs: r.passiveKwargs },
          meta: r.meta,
        })),
      }
    }
    return {}
  }

  private runningRoutines(): Msg[] {
    return [...this.nodes.values()]
      .filter(n => n.cmd.state === State.STARTING || n.cmd.state === State.RUNNING)
      .map(n => ({ name: n.cmd.name, id: String(n.cmd.id) }))
  }

  // --- routine 树编排 ---

  private createNode(parentId: number, name: string, kwargs: Payload): Node {
    const parent = parentId ? this.nodes.get(parentId) ?? null : null
    if (parentId && !parent) throw new Error(`parent routine ${parentId} not found`)
    if (!this.routes.has(name)) throw new Error(`routine ${JSON.stringify(name)} not registered`)
    const cmd = new Command(name)
    cmd.parentId = parentId
    cmd.kwargs = kwargs ?? {}
    const n = new Node(cmd, parent)
    n.owner = this.routes.get(name)!.owner
    this.nodes.set(cmd.id, n)
    if (parent) parent.children.push(n)
    this.emitLifecycle('created', n)
    return n
  }

  /** 启动已 create 的 routine:TryAcquire(declared) + 起 body.冲突抛 ConflictError. */
  private startRoutine(rid: number): void {
    const n = this.requireNode(rid)
    if (n.declared.length) {
      const ancestors = this.ancestorIds(n)
      this.tree.tryAcquire(n.cmd.id, n.declared, ancestors)
    }
    n.cmd.setModules([...n.declared])
    n.cmd.attachBody(() => this.runRemote(n))
    n.cmd.onInterrupt = () => this.sendStop(n)
    n.cmd.start()
  }

  /** 处理 routine.submit:建 created 态命令返回 child_id.passive 拒手动. */
  private onSubmit(parentId: number, name: string, kwargs: Payload): number {
    const route = this.routes.get(name)
    if (route && route.isPassive) {
      throw new Error(`routine ${name} is passive (auto-started by kernel); manual submit is not allowed`)
    }
    const n = this.createNode(parentId, name, kwargs)
    log.info(`➡️ submit ${n.cmd}`)
    return n.cmd.id
  }

  private requireNode(rid: number): Node {
    const n = this.nodes.get(rid)
    if (!n) throw new Error(`routine ${rid} not found`)
    return n
  }

  private requireSelfRunning(rid: number, op: string): Node {
    const n = this.requireNode(rid)
    if (n.cmd.state !== State.STARTING && n.cmd.state !== State.RUNNING) {
      throw new Error(`cannot ${op}: routine ${n.cmd} not started (state=${n.cmd.state})`)
    }
    return n
  }

  private requireParentRunning(childId: number, op: string): Node {
    const n = this.requireNode(childId)
    if (!n.parent) return n  // 根 routine,由 kernel 直接驱动
    if (n.parent.cmd.state !== State.RUNNING) {
      throw new Error(`cannot ${op} routine ${n.cmd}: parent ${n.parent.cmd} not started (state=${n.parent.cmd.state})`)
    }
    return n
  }

  private ancestorIds(n: Node): Set<number> {
    const out = new Set<number>()
    let p = n.parent
    while (p) { out.add(p.cmd.id); p = p.parent }
    return out
  }

  private removeNode(rid: number): void {
    const n = this.nodes.get(rid)
    this.nodes.delete(rid)
    if (n) {
      // 兜底终止帧(conn 断开等 stop 路径未覆盖的删除;幂等)
      this.emitLifecycleStop('stopped', n)
      if (n.parent) {
        const i = n.parent.children.indexOf(n)
        if (i >= 0) n.parent.children.splice(i, 1)
      }
    }
  }

  // --- runRemote:routine body 状态机 ---

  private async runRemote(n: Node): Promise<void> {
    const cmd = n.cmd
    const rid = String(cmd.id)
    try {
      if (!n.createdSent) {
        this.sendCreated(n)
        let r: CreatedResult
        try {
          r = await withTimeout(n.createdFut.promise, Kernel.CREATED_TIMEOUT * 1000, `routine ${cmd} created`)
        } catch {
          log.error(`routine ${cmd} created timeout`)
          return
        }
        if (r.error) return
        n.declared = r.modules
      }
      // lifecycle.start(对齐 Go sendStart: 发给 node owner)
      this.sendTo(n.owner, { event: LIFECYCLE_START, id: rid, name: cmd.name })
      // 等 started(先到 stopped = start 失败)
      await Promise.race([n.startedFut.promise, n.stoppedFut.promise])
      if (n.stoppedFut.done) return
      cmd.markRunning()
      // 等自然结束;被打断时 onInterrupt 发 lifecycle.stop,stopped 回报 resolve 此处.
      await n.stoppedFut.promise
    } finally {
      this.tree.release(cmd.id)
    }
  }

  // --- stop 级联 ---

  private async stopNode(n: Node, cascade: boolean, forceBy: number): Promise<void> {
    if (cascade) {
      if (n.stopping) return
      n.stopping = true
      if (forceBy) { n.stopReason = 'force'; n.stopBy = forceBy }
    }
    for (const c of [...n.children]) await this.stopNode(c, true, forceBy)

    if (n.cmd.state === State.CREATED) {
      // created 态(已 submit 未 start):发 destroy 让 hub 销毁 instance
      // (先发再删: 发给 node owner, 对齐 Go sendDestroy)
      this.emitLifecycleStop('destroyed', n)
      this.unsubscribeRid(String(n.cmd.id))
      this.sendTo(n.owner, { event: LIFECYCLE_DESTROY, id: String(n.cmd.id) })
      this.removeNode(n.cmd.id)
      return
    }

    n.stopping = true
    if (n.stoppedFut.done) {
      // 已回报 stopped(自然终止路径):body 在收尾,等它退出即可,
      // 不要再发 lifecycle.stop.
      await n.cmd.wait()
    } else {
      await n.cmd.stop()  // 发 lifecycle.stop → 等 stopped → release
    }
    this.removeNode(n.cmd.id)
  }

  private async onTerminated(rid: number): Promise<void> {
    const n = this.nodes.get(rid)
    if (!n || n.stopping) return
    n.stopping = true
    log.info(`✅ ${n.cmd} stopped`)
    await this.stopNode(n, false, 0)
  }

  // --- pubsub 订阅表 ---

  private unsubscribeRid(rid: string): void {
    for (const subs of this._pubsub.values()) subs.delete(rid)
  }

  private pubsubKey(namespace: string, topic: string): string {
    return `${namespace}\u0000${topic}`
  }

  private onPubsubSubscribe(msg: Msg, source: string): Promise<void> {
    const key = this.pubsubKey(String(msg.namespace ?? ''), String(msg.topic ?? ''))
    let subs = this._pubsub.get(key)
    if (!subs) { subs = new Set(); this._pubsub.set(key, subs) }
    subs.add(String(msg.id ?? ''))
    return Promise.resolve()
  }

  private onPubsubUnsubscribe(msg: Msg, source: string): Promise<void> {
    const subs = this._pubsub.get(this.pubsubKey(String(msg.namespace ?? ''), String(msg.topic ?? '')))
    subs?.delete(String(msg.id ?? ''))
    return Promise.resolve()
  }

  private onPubsubPublish(msg: Msg, source: string): Promise<void> {
    const topic = String(msg.topic ?? '')
    const namespace = String(msg.namespace ?? '')
    const subs = this._pubsub.get(this.pubsubKey(namespace, topic))
    if (!subs) return Promise.resolve()
    // fanout 按 hub(node owner conn)分组: 每 conn 一条 delivered
    // (subscriber_ids 列表), wire 上 payload 只传一份; hub 侧按列表
    // 逐个路由本地 handler. 路由已由分组完成, 直发 conn.
    // node 不存在的残留 rid 归入发布方 conn(对齐 Go clientIDOfNode
    // 空串跳过的宽松版: 留在发布方本地可见).
    const groups = new Map<string, string[]>()
    for (const rid of subs) {
      const n = this.nodes.get(toInt(rid))
      const owner = n ? n.owner : source
      const list = groups.get(owner)
      if (list) list.push(rid)
      else groups.set(owner, [rid])
    }
    for (const [owner, rids] of groups) {
      const conn = this._conns.get(this._hubConns.get(owner) ?? owner)
      if (!conn) continue
      conn.send({
        event: PUBSUB_DELIVERED, subscriber_ids: rids,
        topic, namespace,
        data: msg.data, source: { id: String(msg.source_id ?? '') },
      })
    }
    return Promise.resolve()
  }

  // --- lifecycle 出站 ---

  private sendCreated(n: Node): void {
    const msg: Msg = { event: LIFECYCLE_CREATED, id: String(n.cmd.id), name: n.cmd.name }
    if (n.cmd.kwargs && Object.keys(n.cmd.kwargs).length) msg.kwargs = n.cmd.kwargs
    if (n.cmd.parentId) msg.parent_id = String(n.cmd.parentId)
    this.sendTo(n.owner, msg)
  }

  private sendStop(n: Node): void {
    const msg: Msg = { event: LIFECYCLE_STOP, id: String(n.cmd.id) }
    if (n.stopReason) {
      msg.reason = n.stopReason
      if (n.stopBy) msg.by = String(n.stopBy)
    }
    this.sendTo(n.owner, msg)
  }

  /** 把 lifecycle started/stopped / yielded 中转给父(唤醒父侧 handle).
   *  多 hub: 中转目标是父的 owner conn(父可能与子在不同 hub). */
  private relayToParent(childId: number, msg: Msg): void {
    const n = this.nodes.get(childId)
    const p = n?.parent
    if (!p) return
    this.sendTo(p.owner, msg)
  }

  // --- lifecycle 观察流(conn 级观察者)---
  //
  // routine 树状态变更(created/started/stopped/destroyed)旁路广播给显式
  // LIFECYCLE_WATCH 的 conn. 与业务 pubsub 分野: kernel 不感知消费者语义.
  // 单线程事件循环: emit 与状态变更天然同序, seq 单调递增.

  /** 发一帧 lifecycle 事件给所有开启观察的 conn.无观察者零开销. */
  private emitLifecycle(evType: string, n: Node, extra?: Msg): void {
    if (this._watchConns.size === 0) return
    this._watchSeq++
    const msg: Msg = {
      event: LIFECYCLE_EVENT, type: evType, id: String(n.cmd.id),
      parent_id: n.parent ? String(n.parent.cmd.id) : '0',
      name: n.cmd.name, seq: this._watchSeq,
    }
    if (extra) Object.assign(msg, extra)
    for (const cid of this._watchConns) {
      this._conns.get(cid)?.send({ ...msg })
    }
  }

  /** 终止帧(stopped/destroyed)幂等: 一个 node 恰一帧. */
  private emitLifecycleStop(evType: string, n: Node, extra?: Msg): void {
    if (n.stopSent) return
    n.stopSent = true
    this.emitLifecycle(evType, n, extra)
  }

  /** lifecycle.watch: 打开 conn 级观察开关(幂等)并回当前树快照. */
  private async onLifecycleWatch(msg: Msg, source: string): Promise<void> {
    const conn = this._conns.get(source)
    if (!conn) return
    this._watchConns.add(conn.id)
    this._watchSeq++
    const routines = [...this.nodes.values()].map(n => ({
      id: String(n.cmd.id),
      parent_id: n.parent ? String(n.parent.cmd.id) : '0',
      name: n.cmd.name,
      state: n.cmd.state,
    }))
    conn.send({
      event: LIFECYCLE_SNAPSHOT, req_id: msg.req_id,
      seq: this._watchSeq, routines,
    })
  }

  /** lifecycle.unwatch: 关闭 conn 级观察. */
  private async onLifecycleUnwatch(source: string): Promise<void> {
    this._watchConns.delete(source)
  }

  // --- dispatch 各事件 handler ---

  private async onLifecycleCreated(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id)
    const n = rid ? this.nodes.get(rid) : undefined
    if (!n) return
    const mods = (msg.modules ?? []).map((m: any) => String(m))
    n.createdDone = true
    n.createdFut.resolve({ modules: mods, error: null })
  }

  private async onLifecycleStarted(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id)
    const n = rid ? this.nodes.get(rid) : undefined
    if (!n) return
    this.emitLifecycle('started', n)
    n.startedFut.resolve(undefined)
    this.relayToParent(rid, msg)
  }

  private async onLifecycleStopped(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id)
    const n = rid ? this.nodes.get(rid) : undefined
    if (n) {
      // created 失败分流:created 还没回报过 → stopped 是 created 失败
      if (!n.createdDone) {
        n.createdDone = true
        n.createdFut.resolve({
          modules: [],
          error: `routine #${rid} stopped during created: reason=${msg.reason ?? ''}`,
        })
        this.emitLifecycleStop('stopped', n, { reason: msg.reason ?? '' })
        this.unsubscribeRid(String(rid))
        return
      }
      this.emitLifecycleStop('stopped', n, { reason: msg.reason ?? '' })
      n.stoppedFut.resolve(msg)
    }
    this.unsubscribeRid(String(rid))
    // relay 必须在 onTerminated 之前----terminated 会 remove node,
    // 之后 nodes.get(rid)=undefined 就永远中转不回父 handle(call() 死等).
    this.relayToParent(rid, msg)
    // fire-and-forget, 不堵 dispatch 队列: onTerminated 级联停子要等
    // child 的 stopped 消息, 而那消息正排在本队列里 ---- 内联 await 即
    // 自死锁(STOP_TIMEOUT 兜底解围, 表现为父自然完成后级联停子的尾延迟).
    this.onTerminated(rid)
      .catch(e => log.error(`terminated #${rid} failed: ${e?.stack ?? e}`))
  }

  private async onYield(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id)
    if (rid) this.relayToParent(rid, { ...msg, event: ROUTINE_YIELDED })
  }

  /** routine.submit:建 created 态 → 发 lifecycle.created → 等 created 回报
   *  → 回 routine.submitted{child_id, modules} 给 submit 发起方
   *  (source, 对齐 Go OnSubmitSubmitted(sourceClientID, ...)). */
  private async onRoutineSubmit(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const name = String(msg.name ?? '')
    const kwargs = msg.kwargs ?? {}
    const parentId = toInt(msg.parent_id) || 0
    let childId: number
    try {
      childId = this.onSubmit(parentId, name, kwargs)
    } catch (e: any) {
      this.sendTo(source, { event: ROUTINE_SUBMITTED, req_id: reqId, error: String(e?.message ?? e) })
      return
    }
    const n = this.nodes.get(childId)!
    n.cmd.kwargs = kwargs ?? {}
    n.createdSent = true
    this.sendCreated(n)

    const wait = async () => {
      let r: CreatedResult
      try {
        r = await withTimeout(n.createdFut.promise, Kernel.CREATED_TIMEOUT * 1000, `routine ${name} created`)
      } catch {
        this.sendTo(source, { event: ROUTINE_SUBMITTED, req_id: reqId, error: `routine ${name} created timeout` })
        return
      }
      if (r.error) {
        this.sendTo(source, { event: ROUTINE_SUBMITTED, req_id: reqId, error: r.error })
      } else {
        n.declared = r.modules
        this.sendTo(source, { event: ROUTINE_SUBMITTED, req_id: reqId, child_id: String(childId), modules: r.modules })
      }
    }
    wait().catch(e => log.error(`routine.submit wait failed: ${e?.stack ?? e}`))
  }

  private async onRoutineStart(msg: Msg, source: string): Promise<void> {
    const childId = toInt(msg.child_id) || 0
    const isTry = Boolean(msg.try)
    const priority = Math.max(0, toInt(msg.priority) || 0)
    try {
      const n = this.requireParentRunning(childId, 'start')
      n.priority = priority
      try {
        this.startRoutine(childId)
      } catch (e: any) {
        if (!(e instanceof ConflictError)) throw e
        // 优先级仲裁: incoming 严格大于全部第三方 holder 才驱逐他们后重试
        // start;平级/更低(含 priority=0 常规 start)不抢,原样拒(旧语义).
        if (priority <= 0) throw e
        // 驱逐要等 holder 的 stopped 回报(回经本 dispatch 队列),内联 await
        // 即自死锁 ---- 挪独立 task(对齐 force_acquire).
        this.evictAndStart(n, priority, e, childId, isTry, source)
          .catch(e2 => log.error(`priority start failed: ${e2?.stack ?? e2}`))
        return
      }
      log.info(`▶️ ${this.nodes.get(childId)!.cmd} ${isTry ? 'try_start' : 'start'}`)
    } catch (e: any) {
      if (e instanceof ConflictError) {
        this.rejectStart(source, childId, isTry, this.conflictMsg(e))
      } else {
        this.rejectStart(source, childId, isTry, String(e?.message ?? e))
      }
    }
  }

  /** routine.start 的驱逐分支(独立 task): 驱逐低优 holder 后重试 start,
   * 结果走同一套 reject 通道. */
  private async evictAndStart(n: Node, priority: number, ce: ConflictError,
                              childId: number, isTry: boolean, source: string): Promise<void> {
    try {
      await this.evictLowerHolders(n, priority, ce)
      this.startRoutine(childId)
      log.info(`▶️ ${this.nodes.get(childId)!.cmd} ${isTry ? 'try_start' : 'start'}`)
    } catch (e: any) {
      if (e instanceof ConflictError) {
        this.rejectStart(source, childId, isTry, this.conflictMsg(e))
      } else {
        if (!isTry) this.cleanupFailedStart(childId)
        this.rejectStart(source, childId, isTry, String(e?.message ?? e))
      }
    }
  }

  /** 优先级驱逐: cone 内第三方 holder 全部 priority 严格低于本 start 才逐个
   * cascade stop(reason='force')后返回;存在 >= 本优先级的 holder 时不驱逐
   * 任何节点, 直接抛原 ConflictError(平级不抢).优先级只裁第三方(排除
   * 自己/祖先);父子级联停走树权威, 不参与比较. */
  private async evictLowerHolders(n: Node, priority: number, ce: ConflictError): Promise<void> {
    const ancestors = this.ancestorIds(n)
    const holders = this.tree.evictableHolders([...n.declared], ancestors, n.cmd.id)
    for (const h of holders) {
      const hn = this.nodes.get(h)
      if (hn && hn.priority >= priority) throw ce   // 有 >= 平级/更高 holder: 不抢
    }
    log.info(`⚡ start ${n.cmd} (priority=${priority}) evicting ${holders.length} holder(s)`)
    for (const h of holders) {
      const hn = this.nodes.get(h)
      if (!hn) continue
      await this.stopNode(hn, true, n.cmd.id)
    }
  }

  private conflictMsg(ce: ConflictError): string {
    const holders = ce.holders.map(h => {
      const n = this.nodes.get(h)
      return n ? String(n.cmd) : String(h)
    }).join(', ')
    if (ce.requested === ce.blockedBy) {
      return `module ${JSON.stringify(ce.requested)} blocked by ${holders}`
    }
    return `module ${JSON.stringify(ce.requested)} blocked at ${JSON.stringify(ce.blockedBy)} by ${holders}`
  }

  /** rejected 发回请求来源(对齐 Go dispatchEvent 里 sendConn(connID, rej))----
   *  不能按 child_id 路由, 那会发给 routine owner 而非请求者. */
  private rejectStart(source: string, childId: number, isTry: boolean, error: string): void {
    if (!isTry) this.cleanupFailedStart(childId)
    const rej: Msg = { event: ROUTINE_REJECTED, op: 'start', child_id: String(childId), error }
    if (isTry) rej.try = true
    this.sendTo(source, rej)
  }

  private cleanupFailedStart(childId: number): void {
    this.unsubscribeRid(String(childId))
    this.removeNode(childId)
  }

  private async onRoutineStop(msg: Msg, source: string): Promise<void> {
    const childId = toInt(msg.child_id) || 0
    try {
      const n = this.requireParentRunning(childId, 'stop')
      if (n.stopping) return
      n.stopping = true
      log.info(`🛑 stop ${n.cmd}`)
      this.stopNode(n, false, 0).catch(e => log.error(`stop ${n.cmd} failed: ${e?.stack ?? e}`))
    } catch (e: any) {
      this.sendTo(source, { event: ROUTINE_REJECTED, op: 'stop', child_id: String(childId), error: String(e?.message ?? e) })
    }
  }

  private async onRoutineUnsubmit(msg: Msg, source: string): Promise<void> {
    const childId = toInt(msg.child_id) || 0
    try {
      const n = this.requireNode(childId)
      if (n.cmd.state !== State.CREATED) {
        throw new Error(`cannot unsubmit routine ${n.cmd}: already started (state=${n.cmd.state}), use stop instead`)
      }
      // 先发再删: destroy 发给 node owner(对齐 Go sendDestroy)
      this.unsubscribeRid(String(childId))
      this.sendTo(n.owner, { event: LIFECYCLE_DESTROY, id: String(childId) })
      // 主动 relay stopped 给父: node 即将删除, owner hub 的 destroy
      // 回报(stopped)到达时 node 已不存在, 无法中转 ---- 父 handle 的
      // unsubmit ack 会永久挂起. relay 须在 remove 之前(node 带 parent).
      this.relayToParent(childId, {
        event: LIFECYCLE_STOPPED, id: String(childId),
        reason: ControlDoneReason.UNSUBMITTED,
      })
      this.removeNode(childId)
      log.info(`↩️ unsubmit ${n.cmd}`)
    } catch (e: any) {
      this.sendTo(source, { event: ROUTINE_REJECTED, op: 'unsubmit', child_id: String(childId), error: String(e?.message ?? e) })
    }
  }

  private async onRoutineAcquire(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id) || 0
    const reqId = String(msg.req_id ?? '')
    const modules = (msg.modules ?? []).map((m: any) => String(m))
    const reply: Msg = { event: ROUTINE_ACQUIRED, req_id: reqId }
    try {
      const n = this.requireSelfRunning(rid, 'acquire')
      this.tree.tryAcquire(rid, modules, this.ancestorIds(n))
      n.cmd.addModules(modules)
      reply.ok = true
    } catch (e: any) {
      reply.ok = false
      reply.error = e instanceof ConflictError ? this.conflictMsg(e) : String(e?.message ?? e)
    }
    this.sendTo(source, reply)
  }

  private async onRoutineRelease(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id) || 0
    const reqId = String(msg.req_id ?? '')
    const modules = (msg.modules ?? []).map((m: any) => String(m))
    const reply: Msg = { event: ROUTINE_RELEASED, req_id: reqId }
    try {
      const n = this.requireSelfRunning(rid, 'release')
      this.tree.releaseModules(rid, modules)
      n.cmd.removeModules(modules)
      reply.ok = true
    } catch (e: any) {
      reply.ok = false
      reply.error = String(e?.message ?? e)
    }
    this.sendTo(source, reply)
  }

  private onRoutineForceAcquire(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id) || 0
    const reqId = String(msg.req_id ?? '')
    const modules = (msg.modules ?? []).map((m: any) => String(m))

    const evictAndAcquire = async () => {
      try {
        const n = this.requireSelfRunning(rid, 'force_acquire')
        await this.evictHolders(n, modules, rid)
        this.tree.tryAcquire(rid, modules, this.ancestorIds(n))
        n.cmd.addModules(modules)
        log.info(`⚡ force_acquire ${n.cmd} ← ${JSON.stringify(modules)}`)
        this.sendTo(source, { event: ROUTINE_ACQUIRED, req_id: reqId, ok: true })
      } catch (e: any) {
        const err = e instanceof ConflictError ? this.conflictMsg(e) : String(e?.message ?? e)
        this.sendTo(source, { event: ROUTINE_ACQUIRED, req_id: reqId, ok: false, error: err })
      }
    }
    evictAndAcquire().catch(e => log.error(`force_acquire failed: ${e?.stack ?? e}`))
    return Promise.resolve()
  }

  private onRoutineForceRelease(msg: Msg, source: string): Promise<void> {
    const rid = toInt(msg.id) || 0
    const reqId = String(msg.req_id ?? '')
    const modules = (msg.modules ?? []).map((m: any) => String(m))

    const evict = async () => {
      try {
        const n = this.requireSelfRunning(rid, 'force_release')
        const count = await this.evictHolders(n, modules, rid)
        log.info(`⚡ force_release ${n.cmd} ← ${JSON.stringify(modules)} (evicted ${count}, not acquired)`)
        this.sendTo(source, { event: ROUTINE_RELEASED, req_id: reqId, ok: true })
      } catch (e: any) {
        this.sendTo(source, { event: ROUTINE_RELEASED, req_id: reqId, ok: false, error: String(e?.message ?? e) })
      }
    }
    evict().catch(e => log.error(`force_release failed: ${e?.stack ?? e}`))
    return Promise.resolve()
  }

  /** 驱逐 cone 内第三方 holder(cascade stop,reason=force).返回驱逐数. */
  private async evictHolders(n: Node, modules: string[], rid: number): Promise<number> {
    const ancestors = this.ancestorIds(n)
    const holders = this.tree.evictableHolders(modules, ancestors, rid)
    for (const h of holders) {
      const hn = this.nodes.get(h)
      if (!hn) continue
      log.info(`🛑 stop ${hn.cmd} (force, evicting for ${n.cmd})`)
      await this.stopNode(hn, true, rid)
    }
    return holders.length
  }

  private onRoutineLoadModule(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const reply: Msg = { event: ROUTINE_MODULE_LOADED, req_id: reqId }
    let ok = true
    try {
      this.tree.loadModule(String(msg.parent_id ?? ''), String(msg.child_id ?? ''), String(msg.name ?? ''))
    } catch (e: any) {
      ok = false
      reply.error = String(e?.message ?? e)
    }
    reply.ok = ok
    this.sendTo(source, reply)
    if (ok) this.pushModuleView()
    return Promise.resolve()
  }

  private onRoutineUnloadModule(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const reply: Msg = { event: ROUTINE_MODULE_UNLOADED, req_id: reqId }
    let ok = true
    try {
      this.tree.unloadModule(String(msg.child_id ?? ''))
    } catch (e: any) {
      ok = false
      reply.error = String(e?.message ?? e)
    }
    reply.ok = ok
    this.sendTo(source, reply)
    if (ok) this.pushModuleView()
    return Promise.resolve()
  }

  /** 把当前模块树拓扑推给所有 conn(load/unload 后缓存刷新,
   *  对齐 Go pushModuleView 广播). */
  private pushModuleView(): void {
    const msg: Msg = { event: MODULE_TREE, tree: this.tree.serialize() }
    for (const conn of this._conns.values()) conn.send({ ...msg })
  }

  // --- message.* broker(dumb forward by target_id)---

  private async onMessage(msg: Msg, source: string): Promise<void> {
    const event = String(msg.event ?? '')
    const delivered: Record<string, string> = {
      [MESSAGE_SEND]: MESSAGE_DELIVERED,
      [MESSAGE_REQ]: MESSAGE_REQ_DELIVERED,
      [MESSAGE_REQ_REPLY]: MESSAGE_REQ_REPLY_DELIVERED,
      [MESSAGE_STREAM_OPEN]: MESSAGE_STREAM_OPEN_DELIVERED,
      [MESSAGE_STREAM_DATA]: MESSAGE_STREAM_DATA_DELIVERED,
      [MESSAGE_STREAM_CANCEL]: MESSAGE_STREAM_CANCEL_DELIVERED,
    }
    const target = delivered[event]
    if (!target) return
    // 按 target 查 node owner 定向转发(对齐 Go OnMessage 的
    // clientIDOfNode; target 不存在/stopped 即丢弃, 不回退请求者)
    for (const t of msg.target_ids ?? []) {
      const n = this.nodes.get(toInt(t))
      if (!n) continue
      this.sendTo(n.owner, {
        event: target, target_id: String(t),
        data: msg.data, source: { id: String(msg.source_id ?? '') },
      })
    }
  }

  // --- catalog ---

  /** hub 启动后主动 push catalog:登记 hub 身份 + 注册路由表 + 回执 + 推树 + 起 passive.
   *
   *  hub_id 是 hub 侧自报的进程级稳定身份(契约见 docs/03-registration.md):
   *  kernel 校验唯一性, 重复(已有别的 conn 用同一 hub_id)则拒绝连接;
   *  owner 记 hub_id ---- 身份跨重连稳定, list_routines 如实展示. */
  private async onCatalogPush(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const hubId = String(msg.hub_id ?? '')
    if (!hubId) {
      this.sendTo(source, { event: CATALOG_PUSHED, req_id: reqId, registered: [], skipped: [], error: 'hub_id is required' })
      return
    }
    const prev = this._hubConns.get(hubId)
    if (prev !== undefined && prev !== source) {
      // 重复身份: 回执错误 + 断开本 conn(拒绝连接).
      this.sendTo(source, { event: CATALOG_PUSHED, req_id: reqId, registered: [], skipped: [], error: `hub_id '${hubId}' already connected` })
      this._conns.get(source)?.close?.()
      log.warning(`peer ${source} rejected: hub_id '${hubId}' already owned by conn ${prev}`)
      return
    }
    // (重)登记: 首连, 或同 conn 重推(hot reload 后 re-push)
    this._hubConns.set(hubId, source)
    const conn = this._conns.get(source)
    if (conn) conn.hubId = hubId
    const registered: string[] = []
    const skipped: string[] = []
    for (const item of msg.routines ?? []) {
      const name = String(item?.name ?? '')
      if (!name || this.routes.has(name)) {
        if (name) skipped.push(name)
        continue
      }
      const ip = item.is_passive ?? {}
      this.routes.set(name, new Route(Boolean(ip.flag), ip.kwargs ?? {}, item.meta ?? {}, hubId))
      registered.push(name)
    }
    this.sendTo(source, { event: CATALOG_PUSHED, req_id: reqId, registered, skipped })
    this.pushModuleView()
    log.info(`catalog.pushed: hub '${hubId}' registered=${registered.length} skipped=${skipped.length}`)
    // passive 自动拉起
    const passives = [...this.routes.entries()].filter(([, r]) => r.isPassive)
    if (passives.length) {
      this.autoStartPassive(passives).catch(e => log.error(`auto-start passive failed: ${e?.stack ?? e}`))
    }
  }

  private async autoStartPassive(passives: [string, Route][]): Promise<void> {
    // 顺序安全由 hub 侧保证: catalog.registered 回执 handler 内同步本地
    // register, FIFO dispatch 使其 happens-before 这里的 lifecycle.created.
    for (const [name, route] of passives) {
      if (this._passiveStarted.has(name)) continue
      this._passiveStarted.add(name)
      try {
        const n = this.createNode(this._rootId, name, { ...route.passiveKwargs })
        this.sendCreated(n)
        n.createdSent = true
        let r: CreatedResult
        try {
          r = await withTimeout(n.createdFut.promise, Kernel.CREATED_TIMEOUT * 1000, `routine ${name} created`)
        } catch {
          throw new Error(`routine ${name} created timeout`)
        }
        if (r.error) throw new Error(r.error)
        n.declared = r.modules
        this.startRoutine(n.cmd.id)
      } catch (e: any) {
        this._passiveStarted.delete(name)
        this.removeNodeByName(name)
        log.error(`passive ${name} auto-start failed: ${e?.message ?? e}`)
        continue
      }
      log.info(`auto-start passive routine: ${name}`)
    }
  }

  private removeNodeByName(name: string): void {
    for (const rid of [...this.nodes.keys()]) {
      const n = this.nodes.get(rid)
      if (n && n.cmd.name === name) this.cleanupFailedStart(rid)
    }
  }

  private async onCatalogRegister(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const name = String(msg.name ?? '')
    const ip = msg.is_passive ?? {}
    if (!name || this.routes.has(name)) {
      this.sendTo(source, { event: CATALOG_REGISTERED, req_id: reqId, ok: false, error: `routine ${JSON.stringify(name)} already registered` })
      return
    }
    this.routes.set(name, new Route(Boolean(ip.flag), ip.kwargs ?? {}, msg.meta ?? {}, this.hubOf(source)))
    // 回执必须先于 auto-start 的 lifecycle.created 发出(对齐 Go
    // handleCatalogRegister:先 sendConn 再 go AutoStartPassive).
    this.sendTo(source, { event: CATALOG_REGISTERED, req_id: reqId, ok: true })
    if (ip.flag) {
      this.autoStartPassive([[name, this.routes.get(name)!]])
        .catch(e => log.error(`auto-start passive failed: ${e?.stack ?? e}`))
    }
  }

  private async onCatalogReload(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const name = String(msg.name ?? '')
    const ip = msg.is_passive ?? {}
    if (!name) {
      this.sendTo(source, { event: CATALOG_RELOADED, req_id: reqId, ok: false, error: 'name is required' })
      return
    }
    // 热替换语义(不区分 passive/动态): 覆盖路由 + 停掉该 name 所有运行中
    // 老实例(级联停子树). 老代码实例承载的闭包/连接在旧模块里, 留着跑完
    // 语义上已不是"这个 routine"; passive 额外等停完再 auto-start 新实例.
    this._passiveStarted.delete(name)
    this.routes.set(name, new Route(Boolean(ip.flag), ip.kwargs ?? {}, msg.meta ?? {}, this.hubOf(source)))
    // 回执先于 auto-start 的 created(同 register 语义).
    this.sendTo(source, { event: CATALOG_RELOADED, req_id: reqId, ok: true });
    // 先停老实例, 等它 stopped(节点移除)后再起新实例.
    // 必须串行 ---- 并发的话新节点 created 先进树, 老节点还在 running,
    // 树上短暂出现两个同名节点(lifecycle 观察流如实转发这个中间态).
    (async () => {
      await this.stopRunningByName(name)
      if (ip.flag) {
        await this.autoStartPassive([[name, this.routes.get(name)!]])
      }
    })().catch(e => log.error(`reload restart failed: ${e?.stack ?? e}`))
  }

  /** 两跳流程的 kernel 侧(对齐 Go handleCatalogDeregister):
   *  发 cmd 给持有者, 记 pending(req_id → name/holder/requester)----
   *  cmd.ack 不带 name, 删路由与回执请求者都靠 pending 查回. */
  private async onCatalogDeregister(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const name = String(msg.name ?? '')
    if (!this.routes.has(name)) {
      this.sendTo(source, { event: CATALOG_DEREGISTERED, req_id: reqId, ok: false, error: `routine ${JSON.stringify(name)} not in routes` })
      return
    }
    // 记 pending(req_id → name/holder)---- cmd.ack 不带 name, 删路由靠
    // pending 查回(对齐 Go pendingDeregisters).
    const holder = this.routes.get(name)!.owner
    if (reqId) this._pendingDeregisters.set(reqId, { name, holder, requester: source })
    // cmd 发给持有者(对齐 Go sendConn(holderConnID, cmd);
    // in-proc 单 hub:持有者就是请求者,直接发 cmd(它本地 dereg 后回 ack)
    this.sendTo(holder, { event: CATALOG_DEREGISTER_CMD, req_id: reqId, name })
  }

  private async onCatalogDeregisterCmdAck(msg: Msg, source: string): Promise<void> {
    const reqId = String(msg.req_id ?? '')
    const ok = Boolean(msg.ok)
    const pend = this._pendingDeregisters.get(reqId)
    if (!pend) {
      // 超时 / 重复 ack / 请求者已断开 → 丢弃
      log.warning(`catalog.deregister.cmd.ack req_id=${reqId} no pending`)
      return
    }
    this._pendingDeregisters.delete(reqId)
    // 校验 ack 来自持有者(防伪造)
    if (source !== pend.holder) {
      log.warning(`catalog.deregister.cmd.ack req_id=${reqId} from ${source}, expected holder ${pend.holder}`)
      return
    }
    if (ok) {
      this._passiveStarted.delete(pend.name)
      this.stopRunningByName(pend.name).catch(e => log.error(`deregister stop failed: ${e?.stack ?? e}`))
      this.routes.delete(pend.name)
      log.info(`➖ catalog.deregister ${pend.name} (holder=${pend.holder})`)
    }
    const reply: Msg = { event: CATALOG_DEREGISTERED, req_id: reqId, ok }
    if (!ok) reply.error = String(msg.error ?? 'deregister failed')
    this.sendTo(pend.requester, reply)
  }

  private async stopRunningByName(name: string): Promise<void> {
    for (const rid of [...this.nodes.keys()]) {
      const n = this.nodes.get(rid)
      if (n && n.cmd.name === name && !n.stopping) {
        n.stopping = true
        await this.stopNode(n, false, 0)
      }
    }
  }
}

function toInt(v: any): number {
  const n = Number(v)
  return Number.isFinite(n) && v !== null && v !== '' ? Math.trunc(n) : 0
}
