/** GrpcClientTransport ---- routine hub 当 grpc client(dial-in 模型),带重连.
 * 移植 routine-py/routine/grpc_client.py; 与 GrpcKernelServer 对接(手写 Frame
 * 编解码, 不依赖 protobuf 生成代码):
 *
 *  - hub 主动 dial kernel 的 grpc server, 开一条 bidi Stream = 一个 peer.
 *  - 入站: recv loop 收 Frame → JSON.parse → inbound(peerId, msg).
 *  - 出站: sendEvent(payload) → out 队列 → send loop 写 Frame(有界 256 丢最老;
 *    write 抛错/背压超时视为半死流, destroy 强断促重连).
 *  - req: unary '/routine.RoutineService/Req' 直查 kernel(get_running_routines /
 *    get_routines / get_module_tree).
 *  - 重连: stream 断 → peerDown 清实例 → backoff(0.2s×1.5 上限 5s) → 重连 +
 *    Req 拉 module.tree + 重发 catalog.push(kernel 回来后自动恢复注册).
 *
 * 不实现 kernel→hub 的 Req 接收: dial-in 下方向矛盾(catalog 走 push,
 * module.tree 走 hub→kernel Req 拉).
 */
import * as grpc from '@grpc/grpc-js'
import { Deferred, sleep, withTimeout } from './async'
import { frameDecode, frameEncode } from './grpcServer'
import { setupLogger, type Logger } from './logger'
import { REQ_GET_MODULE_TREE, REQ_GET_ROUTINES, REQ_GET_RUNNING, type Msg } from './protocol'
import type { RoutineHub } from './hub'
import type { ModuleTree } from './moduleTree'
import { Transport } from './transport'

const log: Logger = setupLogger('routine.grpc_client', 'info')

const REQ_PATH = '/routine.RoutineService/Req'
const STREAM_PATH = '/routine.RoutineService/Stream'

/** wire 帧: JSON 文本(与 grpcServer 的 Frame 同构). */
type Frame = { payload: string }

function deserializeFrame(b: Buffer): Frame { return frameDecode(Buffer.from(b)) }

export class GrpcClientTransport extends Transport {
  /** dial-in 单 kernel: peer_id 固定常量. */
  static readonly PEER_ID = 'kernel'
  /** 重连退避: 初值 200ms, 每次 ×1.5, 上限 5s. */
  static readonly BACKOFF_INITIAL = 0.2
  static readonly BACKOFF_MAX = 5.0
  static readonly BACKOFF_FACTOR = 1.5
  /** Req unary 超时: 半开 kernel(进程存活但 handler 卡死)下不永久 hang. */
  static readonly REQ_TIMEOUT = 5.0
  /** 出站队列上限: 断连期间堆积到上限后丢最老(陈旧 wire 消息重连补发也无人接). */
  static readonly OUT_Q_MAX = 256
  /** 连接存活过此阈值才算健康, 重置 backoff; 连上即断保留退避防重连风暴. */
  static readonly STABLE_SECONDS = 2.0

  readonly address: string
  readonly peerId: string

  private _hub: RoutineHub | null = null
  private _client: grpc.Client | null = null
  private _call: grpc.ClientDuplexStream<Frame, Frame> | null = null
  private _runTask: Promise<void> | null = null
  private _sendTask: Promise<void> | null = null
  private _sendSignal: Deferred<void> | null = null
  private _stopped = false
  /** 首连/重连完成信号: 连上 resolve, 断开 re-arm 成新 Deferred. */
  private _ready: Deferred<void> | null = null
  private _outQ: Msg[] = []

  constructor(address: string, peerId: string = GrpcClientTransport.PEER_ID) {
    super()
    this.address = address
    this.peerId = peerId
  }

  /** 绑定 hub: 入站走 hub.dispatchInbound, 断线走 hub 清理. */
  attach(hub: RoutineHub): void {
    this._hub = hub
    this.setInbound(hub.dispatchInbound.bind(hub))
  }

  // --- Transport impl ---

  async start(): Promise<void> {
    // 非阻塞: 起 _run(含重连 loop)后返回, 首连成功 _ready resolve.
    if (this._runTask) return
    this._ready = new Deferred<void>()
    this._runTask = this._run().catch(e => { log.error(`run loop died: ${e?.stack ?? e}`) })
  }

  /** 等首连完成(超时抛 TimeoutError;单位秒,对齐 py). */
  async waitReady(timeout = GrpcClientTransport.BACKOFF_MAX): Promise<void> {
    if (!this._ready) throw new Error('transport not started')
    await withTimeout(this._ready.promise, timeout * 1000)
  }

  async stop(): Promise<void> {
    if (this._stopped) return
    this._stopped = true
    this._ready = null
    try { this._call?.destroy() } catch { /* already dead */ }
    this._call = null
    if (this._client) {
      const c = this._client
      this._client = null
      try { c.close() } catch { /* already closed */ }
    }
    this._sendSignal?.resolve()
    if (this._runTask) {
      await this._runTask
      this._runTask = null
    }
  }

  async sendEvent(payload: Msg, _peerId?: string): Promise<void> {
    // fire-and-forget: 有界队列, 满丢最老; stopped 后丢弃
    if (this._stopped) return
    if (this._outQ.length >= GrpcClientTransport.OUT_Q_MAX) {
      this._outQ.shift()
      log.warning('out_q saturated, dropping oldest send')
    }
    this._outQ.push(payload)
    this._sendSignal?.resolve()
  }

  async req(msg: Msg): Promise<Msg> {
    const client = this._client
    if (!client) throw new Error('grpc client not connected')
    const frame = { payload: JSON.stringify(msg) }
    return await withTimeout(new Promise<Msg>((resolve, reject) => {
      client.makeUnaryRequest<Frame, Frame>(
        REQ_PATH, frameEncode, deserializeFrame, frame,
        (err: grpc.ServiceError | null, resp?: Frame) => {
          if (err) reject(err)
          else resolve(JSON.parse(resp!.payload))
        })
    }), GrpcClientTransport.REQ_TIMEOUT * 1000)
  }

  async getRunningRoutines(): Promise<any[]> {
    try {
      const resp = await this.req({ event: REQ_GET_RUNNING })
      const routines = (resp as any).routines ?? []
      return Array.isArray(routines) ? routines : []
    } catch (e: any) {
      log.warning(`get_running_routines failed: ${e?.message ?? e}`)
      return []
    }
  }

  async getRoutines(): Promise<any[]> {
    try {
      const resp = await this.req({ event: REQ_GET_ROUTINES })
      const routines = (resp as any).routines ?? []
      return Array.isArray(routines) ? routines : []
    } catch (e: any) {
      log.warning(`get_routines failed: ${e?.message ?? e}`)
      return []
    }
  }

  async getModuleTree(): Promise<ModuleTree | null> {
    if (!this._hub) return null
    try {
      const resp = await this.req({ event: REQ_GET_MODULE_TREE })
      this._hub.cacheModuleTree(resp)
    } catch (e: any) {
      log.warning(`get_module_tree failed: ${e?.message ?? e}`)
    }
    return this._hub.runtime.moduleTree
  }

  // --- 重连 loop ---

  private async _run(): Promise<void> {
    const t = GrpcClientTransport
    let backoff = t.BACKOFF_INITIAL
    while (!this._stopped) {
      const connectedAt = Date.now()
      try {
        await this._connectOnce()
        await this._postConnect()
        await this._recvLoop(this._call!)
      } catch (e: any) {
        log.warning(`connect error: ${e?.message ?? e}`)
      } finally {
        // 断开: 清 call / 丢陈旧 outbound / re-arm ready
        await this._cleanupCall()
        this._ready = new Deferred<void>()
      }
      if (this._stopped) break
      await this._handleDisconnect()
      if (Date.now() - connectedAt >= t.STABLE_SECONDS * 1000) backoff = t.BACKOFF_INITIAL
      log.info(`reconnect in ${backoff.toFixed(1)}s...`)
      await sleep(backoff * 1000)
      backoff = Math.min(backoff * t.BACKOFF_FACTOR, t.BACKOFF_MAX)
    }
  }

  private async _connectOnce(): Promise<void> {
    const client = new grpc.Client(this.address, grpc.credentials.createInsecure())
    await new Promise<void>((resolve, reject) => {
      const deadline = new Date(Date.now() + GrpcClientTransport.BACKOFF_MAX * 1000)
      client.waitForReady(deadline, err => err ? reject(err) : resolve())
    })
    const call = client.makeBidiStreamRequest<Frame, Frame>(STREAM_PATH, frameEncode, deserializeFrame)
    this._client = client
    this._call = call
    this._ready?.resolve()
    log.info(`connected to kernel: ${this.address}`)
    // 旧 send loop(若还在 drain 等待)会因 call !== this._call 自行退出
    this._sendTask = this._sendLoop(call).catch(e => { log.warning(`send loop died: ${e?.stack ?? e}`) })
  }

  /** 连接成功后的初始化(初次 + 重连共用): 拉 tree + push catalog. */
  private async _postConnect(): Promise<void> {
    if (!this._hub) return
    await this.getModuleTree()
    await this._hub.sendCatalogPush()
  }

  /** 收 kernel 发来的事件. stream 断('end'/'error')即返回, 外层重连. */
  private _recvLoop(call: grpc.ClientDuplexStream<Frame, Frame>): Promise<void> {
    return new Promise<void>(resolve => {
      call.on('data', (frame: Frame) => {
        try {
          const msg: Msg = JSON.parse(frame.payload)
          // 串行投递(dispatchInbound 内部 spawn, 不堵 recv)
          void (async () => {
            if (!this._inbound) return
            try {
              await this._inbound(this.peerId, msg)
            } catch (e: any) {
              log.warning(`inbound dispatch error: ${e?.message ?? e}`)
            }
          })()
        } catch (e: any) {
          log.warning(`bad frame payload: ${e?.message ?? e}`)
        }
      })
      call.on('end', () => resolve())
      call.on('error', (err: Error) => {
        if (!this._stopped) log.warning(`stream error: ${err.message}`)
        resolve()
      })
      // destroy() 不带 error 时只有 'close'(无 'error'/'end'), 必须监听才不挂起
      call.on('close', () => resolve())
    })
  }

  /** 出站: 队列 → stream write. 半死流(发送方向死, recv 侧未察觉)必须
   * fail-fast: write 抛错或背压超过 REQ_TIMEOUT 都 destroy call 逼 recvLoop
   * 结束 → 外层重连(含 peer_down 清 handle). 不撕裂的话 out_q 堆到上限后
   * 开始静默丢消息, 连接永远半死. */
  private async _sendLoop(call: grpc.ClientDuplexStream<Frame, Frame>): Promise<void> {
    try {
      while (!this._stopped && call === this._call) {
        if (this._outQ.length === 0) {
          const sig = new Deferred<void>()
          this._sendSignal = sig
          if (this._outQ.length === 0 && !this._stopped) await sig.promise
          continue
        }
        const msg = this._outQ.shift()!
        const ok = call.write({ payload: JSON.stringify(msg) })
        if (!ok) {
          // 背压: 等 drain, 超时 = 发送方向已死, fail-fast
          let drained = false
          await Promise.race([
            new Promise<void>(r => call.once('drain', () => { drained = true; r() })),
            sleep(GrpcClientTransport.REQ_TIMEOUT * 1000),
          ])
          if (!drained) {
            log.warning(`send backpressure ${GrpcClientTransport.REQ_TIMEOUT}s; stream dead, forcing reconnect`)
            try { call.destroy() } catch { /* already dead */ }
            return
          }
        }
      }
    } catch (e: any) {
      log.warning(`send error: ${e?.message ?? e}`)
      // write 抛错 = 发送方向死; destroy 逼 recvLoop 收 close, 否则半死流
      // 无人察觉, out_q 满后静默丢消息
      try { call.destroy() } catch { /* already dead */ }
    }
  }

  /** stream 断开后通知 peerDown(清该 peer 的 running instance). */
  private async _handleDisconnect(): Promise<void> {
    if (this._peerDown) {
      try {
        await this._peerDown(this.peerId)
      } catch (e: any) {
        log.warning(`peer_down handler error: ${e?.message ?? e}`)
      }
    }
  }

  private async _cleanupCall(): Promise<void> {
    this._sendTask = null
    if (this._call) {
      const c = this._call
      this._call = null
      try { c.destroy() } catch { /* already dead */ }
    }
    // 丢弃陈旧 outbound(重连后补发也无人接)
    this._outQ = []
  }
}
