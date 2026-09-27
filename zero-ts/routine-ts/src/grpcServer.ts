/** GrpcKernelServer ---- kernel 的 grpc server(dial-in 模型),移植 kernel/grpc/server.go.
 *
 * 外部 routine hub(zero/routine-py SDK 的 GrpcClientTransport)主动 dial 进来,
 * 每条 Stream = 一个 peer conn.入站 Frame → JSON.parse → kernel.dispatch(msg, peerId)
 * (per-peer FIFO worker 串行,对齐 inproc link 的逐条顺序投递);出站经
 * kernel.registerConn 注册的 KernelConn → stream.write.
 *
 * Req(unary): routine→kernel 查询(get_module_tree / get_running_routines /
 * get_routines),直达 kernel.handleReq,方向与 dial-in 对齐(catalog 走 push 不走 Req).
 *
 * wire: RoutineService { Req(Frame), Stream(stream Frame) },Frame.payload = 完整
 * 事件消息的 JSON 文本(与 kernel/grpc/routine.proto 一致).Frame / 入站 Msg 的
 * schema 用 zod 声明(z.infer 派生类型),入站边界统一 parse 校验,协议违规当场
 * 抛错暴露;protobuf varint 编解码手写(Frame 只有一个 string field,~15 行,
 * 不引入 protobufjs).
 *
 * 断线: stream 'end'/error → kernel.dropConn(peerId)(fail 该 owner 的节点 + 清路由).
 */
import * as grpc from '@grpc/grpc-js'
import { z } from 'zod'

import type { Msg } from './protocol'
import { setupLogger } from './logger'
import type { Kernel } from './kernel'
import type { KernelConn } from './kernel/manager'
import { AsyncQueue } from './async'

const log = setupLogger('routine.grpc', 'info')

// --- wire schema(zod 单一真理源,类型 z.infer 派生)---

/** Frame: grpc wire 唯一消息,payload = 完整事件消息的 JSON 文本. */
const FrameSchema = z.object({ payload: z.string() })
type Frame = z.infer<typeof FrameSchema>

/** 入站 Msg 最小形状:必须携带非空 event;其余字段透传(kernel dispatch 按 event 分支). */
const InboundMsgSchema = z.looseObject({ event: z.string().min(1) })
type InboundMsg = z.infer<typeof InboundMsgSchema>

/** 入站统一入口: payload JSON → Msg schema 校验,违规当场抛(调用方记日志跳帧). */
function parseInbound(payload: string): InboundMsg {
  return InboundMsgSchema.parse(JSON.parse(payload))
}

// --- Frame{payload=1: string} 手写 protobuf 编解码(二进制 wire 格式,非 schema;
//     GrpcClientTransport 复用同一套编解码)---

export function frameEncode(f: Frame): Buffer {
  const body = Buffer.from(f.payload, 'utf8')
  const varint: number[] = []
  let n = body.length
  while (n >= 0x80) { varint.push((n & 0x7f) | 0x80); n >>>= 7 }
  varint.push(n)
  return Buffer.concat([Buffer.from([0x0a]), Buffer.from(varint), body])
}

export function frameDecode(buf: Buffer): Frame {
  if (buf[0] !== 0x0a) throw new Error(`bad protobuf tag ${buf[0]}`)
  let len = 0, shift = 0, i = 1
  while (true) {
    const b = buf[i++]
    len |= (b & 0x7f) << shift
    if (!(b & 0x80)) break
    shift += 7
  }
  return FrameSchema.parse({ payload: buf.subarray(i, i + len).toString('utf8') })
}

/** grpc-js ServiceDefinition(手写,免 protoc 生成). */
const ROUTINE_SERVICE = {
  Req: {
    path: '/routine.RoutineService/Req',
    requestStream: false, responseStream: false,
    requestSerialize: frameEncode, responseSerialize: frameEncode,
    requestDeserialize: (b: Buffer) => frameDecode(Buffer.from(b)),
    responseDeserialize: (b: Buffer) => frameDecode(Buffer.from(b)),
  },
  Stream: {
    path: '/routine.RoutineService/Stream',
    requestStream: true, responseStream: true,
    requestSerialize: frameEncode, responseSerialize: frameEncode,
    requestDeserialize: (b: Buffer) => frameDecode(Buffer.from(b)),
    responseDeserialize: (b: Buffer) => frameDecode(Buffer.from(b)),
  },
} as const

/** dial-in peer conn id 自增,"s" 前缀(对齐 Go serverConnIDCounter). */
let peerCounter = 0

export class GrpcKernelServer {
  private _server = new grpc.Server()
  private _addr: string
  private _kernel: Kernel
  private _bound = false

  constructor(kernel: Kernel, addr: string) {
    this._kernel = kernel
    this._addr = addr
    this._server.addService(ROUTINE_SERVICE as any, {
      Req: (call: grpc.ServerUnaryCall<Frame, Frame>,
            callback: grpc.sendUnaryData<Frame>) => {
        this._onReq(call, callback)
      },
      Stream: (call: grpc.ServerDuplexStream<Frame, Frame>) => {
        this._onStream(call)
      },
    })
  }

  get address(): string { return this._addr }

  async start(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      this._server.bindAsync(this._addr, grpc.ServerCredentials.createInsecure(), (err, port) => {
        if (err) reject(new Error(`grpc bind ${this._addr}: ${err.message}`))
        else {
          this._addr = `127.0.0.1:${port}`
          log.info(`grpc kernel server on ${this._addr} (dial-in)`)
          resolve()
        }
      })
    })
    this._bound = true
  }

  async stop(): Promise<void> {
    if (!this._bound) return
    // tryShutdown 等 peer 主动关流;dial-in client 常驻重连不会自发结束,
    // 2s 超时后 forceShutdown 强断(对端走重连 loop 是正常生命周期).
    await new Promise<void>(resolve => {
      const timer = setTimeout(() => { this._server.forceShutdown(); resolve() }, 2000)
      this._server.tryShutdown(() => { clearTimeout(timer); resolve() })
    })
  }

  // --- Req(unary): routine→kernel 查询 ---

  private _onReq(call: grpc.ServerUnaryCall<Frame, Frame>,
                 callback: grpc.sendUnaryData<Frame>): void {
    let msg: Msg
    try {
      msg = parseInbound(call.request.payload)
    } catch (e: any) {
      callback(new Error(`bad frame payload: ${e?.message ?? e}`), null)
      return
    }
    this._kernel.handleReq(msg).then(reply => {
      callback(null, { payload: JSON.stringify(reply) })
    }, err => {
      callback(new Error(String(err?.message ?? err)), null)
    })
  }

  // --- Stream: 一条 Stream = 一个 peer conn ---

  private _onStream(call: grpc.ServerDuplexStream<{ payload: string }, { payload: string }>): void {
    const peerId = `s${++peerCounter}`

    // 出站队列: kernel.sendTo(peerId, msg) → conn.send 入队, writer loop
    // 逐条 stream.write(grpc-js 的 write 不可并发,单 writer 保序).
    const outQ = new AsyncQueue<Msg | null>()
    let dropped = false
    const drop = () => {
      if (dropped) return
      dropped = true
      outQ.push(null)  // 哨兵: writer 退出
      this._kernel.dropConn(peerId)
      log.info(`peer ${peerId} disconnected`)
    }

    // 入站: per-peer FIFO worker 串行 dispatch(对齐 inproc link kernelLoop).
    const inQ = new AsyncQueue<Msg | null>()
    void (async () => {
      while (true) {
        const msg = await inQ.get()
        if (msg === null) break
        try {
          await this._kernel.dispatch(msg, peerId)
        } catch (e: any) {
          log.error(`peer ${peerId} dispatch failed: ${e?.stack ?? e}`)
        }
      }
    })()

    // writer: 出站队列 → stream
    void (async () => {
      while (true) {
        const msg = await outQ.get()
        if (msg === null) break
        try {
          const ok = call.write({ payload: JSON.stringify(msg) })
          if (!ok) await new Promise<void>(r => call.once('drain', r))
        } catch (e: any) {
          log.warning(`peer ${peerId} send failed: ${e?.message ?? e}`)
          drop()
          break
        }
      }
    })()

    const conn: KernelConn = {
      id: peerId,
      send: (msg: Msg) => { if (!dropped) outQ.push(msg) },
    }
    this._kernel.registerConn(conn)
    log.info(`peer ${peerId} connected`)

    call.on('data', (frame: Frame) => {
      try {
        inQ.push(parseInbound(frame.payload))
      } catch (e: any) {
        // 坏帧/协议违规跳过(报错暴露),连接本身还活着(对齐 Go readLoop)
        log.warning(`peer ${peerId} bad frame: ${e?.message ?? e}`)
      }
    })
    call.on('end', () => { inQ.push(null); drop() })
    call.on('error', (e: Error) => { inQ.push(null); drop(); log.warning(`peer ${peerId} stream error: ${e.message}`) })
  }
}
