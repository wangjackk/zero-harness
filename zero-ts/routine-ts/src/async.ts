/** 异步原语:Deferred / AsyncQueue / AsyncEvent / sleep / withTimeout. */
import { TimeoutErr } from './errors'

/** resolver 可外置的 Promise(原生 resolver 锁在 executor 里;多源 settle 的
 * 场景----回报 / outfail / 断连先到先得----需要把 resolve/reject 交出去). */
export class Deferred<T> {
  readonly promise: Promise<T>
  private readonly _resolvers = Promise.withResolvers<T>()
  private _settled = false

  constructor() {
    this.promise = this._resolvers.promise
    // 防 unhandled rejection(常被多方 race,败者不关心)
    this.promise.catch(() => {})
  }

  get done(): boolean { return this._settled }

  resolve(value: T | PromiseLike<T>): void {
    if (this._settled) return
    this._settled = true
    this._resolvers.resolve(value)
  }

  reject(reason: unknown): void {
    if (this._settled) return
    this._settled = true
    this._resolvers.reject(reason)
  }
}

/** 异步 FIFO:push 唤醒阻塞的 get,promise handoff 保序.
 * 默认无界;传 capacity 后可用 tryPush 做非阻塞投递(满则 false,不抛). */
export class AsyncQueue<T> {
  private _items: T[] = []
  private _waiters: ((v: T) => void)[] = []

  constructor(private _capacity = Infinity) {}

  push(item: T): void {
    const w = this._waiters.shift()
    if (w) w(item)
    else this._items.push(item)
  }

  /** 非阻塞投递:队列满时丢帧返回 false(观察者隔离,慢消费者不堵 fanout). */
  tryPush(item: T): boolean {
    if (this._items.length >= this._capacity) return false
    this.push(item)
    return true
  }

  async get(): Promise<T> {
    if (this._items.length) return this._items.shift()!
    return new Promise<T>(res => this._waiters.push(res))
  }

  get length(): number { return this._items.length }
}

/** 一次性门闩:set 后 wait 永远立即通过,可查询 isSet. */
export class AsyncEvent {
  private _flag = false
  private readonly _promise: Promise<void>
  private _resolve!: () => void

  constructor() {
    this._promise = new Promise<void>(res => { this._resolve = res })
  }

  get isSet(): boolean { return this._flag }

  set(): void {
    if (this._flag) return
    this._flag = true
    this._resolve()
  }

  wait(): Promise<void> { return this._promise }
}

export function sleep(ms: number): Promise<void> {
  return new Promise(res => setTimeout(res, ms))
}

/** 异步互斥锁:promise 链实现,同一时刻只放行一个持锁者. */
export class AsyncLock {
  private _chain: Promise<void> = Promise.resolve()

  /** 持锁执行 fn;并发调用按到达顺序串行. */
  run<T>(fn: () => Promise<T>): Promise<T> {
    const job = this._chain.then(fn)
    // 链上吞掉 fn 的异常,后续持锁者不被前一个失败卡死
    this._chain = job.then(() => {}, () => {})
    return job
  }
}

/** 超时抛 TimeoutErr(带 flag);原 promise 败选不产生 unhandled rejection
 * (Deferred 构造时已兜底). */
export async function withTimeout<T>(p: Promise<T>, ms: number, what = 'timeout'): Promise<T> {
  let timer: any
  const timeoutPromise = new Promise<never>((_, rej) => {
    timer = setTimeout(() => rej(new TimeoutErr(`${what}: timeout after ${ms}ms`)), ms)
  })
  try {
    return await Promise.race([p, timeoutPromise])
  } finally {
    clearTimeout(timer)
  }
}

/** async iterable 检测(for await 消费前的分型). */
export function isAsyncIterable(v: any): v is AsyncIterable<any> {
  return v != null && typeof v[Symbol.asyncIterator] === 'function'
}

export function fmtMs(ms: number): string { return ms.toFixed(1) }
