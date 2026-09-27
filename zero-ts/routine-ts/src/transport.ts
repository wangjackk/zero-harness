/** Transport ---- 传输层抽象:入站投递 + 出站发送 + 同步查询,传输无关.

 routine TS 版只实现 InProcessTransport(进程内内存直连);接口契约与 z2 对齐,
 将来加 grpc / ws 实现时上层(RoutineHub / RunContext / Kernel)零改动.
 */
import type { Msg } from './protocol'
import type { ModuleTree } from './moduleTree'

export type InboundHandler = (peerId: string, msg: Msg) => Promise<void>
export type PeerDownHandler = (peerId: string) => Promise<void>

export abstract class Transport {
  protected _inbound: InboundHandler | null = null
  protected _peerDown: PeerDownHandler | null = null

  setInbound(handler: InboundHandler): void { this._inbound = handler }
  setPeerDown(handler: PeerDownHandler): void { this._peerDown = handler }

  async start(): Promise<void> {}
  async stop(): Promise<void> {}

  /** 发事件给 peer(peer_id in-proc 下忽略,单 kernel).fire-and-forget. */
  abstract sendEvent(payload: Msg, peerId?: string): Promise<void>

  /** routine→kernel 同步查询(dict 进 dict 出). */
  abstract req(msg: Msg): Promise<Msg>

  abstract getRunningRoutines(): Promise<any[]>
  abstract getModuleTree(): Promise<ModuleTree | null>
  abstract getRoutines(): Promise<any[]>
}
