/** InProcessTransport ---- 进程内内存直连(hub ↔ kernel 同事件循环).

 零序列化零跨进程:消息对象原样流转.双向各一条 FIFO 队列 + 单 worker,
 与 grpc stream 的"逐条顺序投递、处理方可 spawn"语义对齐:

 - hub → kernel: sendEvent 入队,kernel worker 逐条 ``await kernel.dispatch(msg)``
   (dispatch 内对阻塞操作 spawn 独立 task,不堵队列).
 - kernel → hub: ``kernel.sendTo('hub', msg)``(即 kernelToHub 入队),hub
   worker 逐条 ``await hub.dispatchInbound(peerId, msg)``(hub 侧自身 spawn handler).
 - 查询(req)不走队列:直接 ``await kernel.handleReq(msg)`` 同步返回,
   查询零排队、最低延迟.

 peer 语义:in-proc 下 hub 的 peer 恒为 KERNEL_PEER,kernel 的 conn 恒为
 HUB_CONN(单 hub;多 hub 是将来 grpc 版的事).

 JS 无 task cancel:stop() 用哨兵消息排空队列后 worker 自然退出.
 */
import { AsyncQueue } from './async'
import { setupLogger } from './logger'
import { ModuleTree } from './moduleTree'
import { REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING, type Msg } from './protocol'
import { Transport } from './transport'
import type { Kernel } from './kernel'

// hub 视角的 peer(kernel 的稳定身份);kernel 视角的 conn(hub 的稳定身份)
export const KERNEL_PEER = 'kernel'
export const HUB_CONN = 'hub'

const log = setupLogger('routine.link', 'warning')

/** stop() 投入队列的停机哨兵(排空在途消息后 worker 退出). */
const LINK_STOP = Symbol('routine.link.stop')

export class InProcessLink {
  /** hub 与 kernel 的进程内直连链路(双向 FIFO). */
  private _toKernel = new AsyncQueue<Msg | typeof LINK_STOP>()
  private _toHub = new AsyncQueue<Msg | typeof LINK_STOP>()
  private _kernel: Kernel | null = null
  private _hubInbound: ((peerId: string, msg: Msg) => Promise<void>) | null = null
  private _workers: Promise<void>[] = []
  private _started = false

  bind(kernel: Kernel, hubInbound: (peerId: string, msg: Msg) => Promise<void>): void {
    this._kernel = kernel
    this._hubInbound = hubInbound
  }

  async start(): Promise<void> {
    if (!this._kernel || !this._hubInbound) {
      throw new Error('link not bound (call bind before start)')
    }
    this._started = true
    this._workers.push(this.kernelLoop())
    this._workers.push(this.hubLoop())
  }

  async stop(): Promise<void> {
    this._started = false
    this._toKernel.push(LINK_STOP)
    this._toHub.push(LINK_STOP)
    await Promise.all(this._workers)
    this._workers = []
  }

  // --- 出站入队(非阻塞,FIFO 保序)---

  hubToKernel(msg: Msg): void {
    if (this._started) this._toKernel.push(msg)
  }

  kernelToHub(msg: Msg): void {
    if (this._started) this._toHub.push(msg)
  }

  // --- 查询直达(同步 await,不走队列)---

  async hubReq(msg: Msg): Promise<Msg> {
    if (!this._kernel) throw new Error('link not bound')
    return await this._kernel.handleReq(msg)
  }

  // --- workers ---

  private async kernelLoop(): Promise<void> {
    while (true) {
      const msg = await this._toKernel.get()
      if (msg === LINK_STOP) break
      try {
        await this._kernel!.dispatch(msg)
      } catch (e: any) {
        log.error(`kernel dispatch failed: ${e?.stack ?? e}`)
      }
    }
  }

  private async hubLoop(): Promise<void> {
    while (true) {
      const msg = await this._toHub.get()
      if (msg === LINK_STOP) break
      try {
        await this._hubInbound!(KERNEL_PEER, msg)
      } catch (e: any) {
        log.error(`hub inbound failed: ${e?.stack ?? e}`)
      }
    }
  }
}

/** hub 侧持有的 transport 实现:发事件入 kernel 队列,查询直达. */
export class InProcessTransport extends Transport {
  constructor(private _link: InProcessLink) { super() }

  async start(): Promise<void> {}  // worker 由 link.start 统一起
  async stop(): Promise<void> {}

  async sendEvent(payload: Msg): Promise<void> {
    this._link.hubToKernel(payload)
  }

  async req(msg: Msg): Promise<Msg> {
    return await this._link.hubReq(msg)
  }

  async getRunningRoutines(): Promise<any[]> {
    const reply = await this.req({ event: REQ_GET_RUNNING })
    return reply.routines ?? []
  }

  async getModuleTree(): Promise<ModuleTree | null> {
    const reply = await this.req({ event: REQ_GET_MODULE_TREE })
    return ModuleTree.fromDict(reply.tree)
  }

  async getRoutines(): Promise<any[]> {
    const reply = await this.req({ event: REQ_GET_ROUTINES })
    return reply.routines ?? []
  }
}
