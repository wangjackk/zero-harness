/** StreamReader / StreamCtx ---- stream_req 的消费侧(移植 z2/_stream.py).

 消费方用法(JS 的 for-await 自动握手 + break/异常时自动取消,等价 py 的
 async with + async for)::

     const s = await ctx.streamReq(rid, 'count', {n: 5})
     for await (const chunk of s) ...

 帧流(全骑 p2p 隧道,kernel dumb forward):
 - 开流:消费方发 message.stream_open{__stream_id__, __reply_to__, event, data}
 - 数据:provider 每 yield 发 message.stream_data{__stream_id__, chunk}
 - 结束:provider 发 message.stream_data{__stream_id__, __eof__: done|error}
 - 取消:消费方迭代中断 → 发 message.stream_cancel{__stream_id__, __cancel__: true}
 */
import { AsyncEvent, AsyncQueue, withTimeout } from './async'
import { StreamCancelled, StreamError, StreamTimeout } from './errors'
import {
  ENVELOPE_CANCEL, ENVELOPE_STREAM_ID, MESSAGE_STREAM_CANCEL,
} from './protocol'
import type { RunContext } from './ctx'

type Frame =
  | { kind: 'chunk'; value: any }
  | { kind: 'eof'; value: string; error?: string }

export class StreamReader {
  readonly streamId: string
  private _ctx: RunContext
  private _targetId: string
  private _queue = new AsyncQueue<Frame>()
  /** 首帧事件:StreamCtx 开流握手等它(超时=provider 未响应). */
  readonly firstFrame = new AsyncEvent()
  private _done = false
  private _error: Error | null = null

  constructor(streamId: string, ctx: RunContext, targetId: string) {
    this.streamId = streamId
    this._ctx = ctx
    this._targetId = targetId
  }

  [Symbol.asyncIterator](): StreamReader { return this }

  async next(): Promise<IteratorResult<any>> {
    if (this._done && this._queue.length === 0) {
      if (this._error) throw this._error
      return { done: true, value: undefined }
    }
    const item = await this._queue.get()
    if (item.kind === 'chunk') return { done: false, value: item.value }
    // eof
    this._done = true
    if (item.value === 'error') {
      this._error = new StreamError(item.error ?? 'stream provider error')
      throw this._error
    }
    if (item.value === 'cancelled') {
      this._error = new StreamCancelled('stream cancelled')
      throw this._error
    }
    // done
    return { done: true, value: undefined }
  }

  /** 迭代中断(break/throw)时 for-await 自动调:发 stream_cancel. */
  async return?(value?: any): Promise<IteratorResult<any>> {
    await this.cancel()
    return { done: true, value: undefined }
  }

  /** 主动取消:发 message.stream_cancel 给 provider(best-effort). */
  async cancel(): Promise<void> {
    if (this._done) return
    this._done = true
    try {
      await this._ctx.sendMessage(this._targetId, MESSAGE_STREAM_CANCEL, {
        [ENVELOPE_STREAM_ID]: this.streamId, [ENVELOPE_CANCEL]: true,
      })
    } catch { /* best-effort */ }
  }

  // --- 投喂入口(hub.onInbound 按 __stream_id__ 路由调用) ---

  feedChunk(chunk: any): void {
    this._queue.push({ kind: 'chunk', value: chunk })
    this.firstFrame.set()
  }

  feedEof(eof: string, error?: string): void {
    this._queue.push({ kind: 'eof', value: eof, error })
    this.firstFrame.set()
  }
}

/** stream_req 的句柄:首个 next() 做开流握手(等首帧),中断时取消. */
export class StreamCtx {
  private _opened = false
  private _reader: StreamReader
  private _timeoutSec: number

  constructor(reader: StreamReader, timeoutSec: number) {
    this._reader = reader
    this._timeoutSec = timeoutSec
  }

  [Symbol.asyncIterator](): StreamCtx { return this }

  async next(): Promise<IteratorResult<any>> {
    if (!this._opened) {
      this._opened = true
      try {
        await withTimeout(this._reader.firstFrame.wait(),
          this._timeoutSec * 1000, `stream ${this._reader.streamId} open handshake`)
      } catch {
        throw new StreamTimeout(`stream ${this._reader.streamId} open handshake timeout`)
      }
    }
    return this._reader.next()
  }

  async return?(value?: any): Promise<IteratorResult<any>> {
    await this._reader.cancel()
    return { done: true, value: undefined }
  }
}
