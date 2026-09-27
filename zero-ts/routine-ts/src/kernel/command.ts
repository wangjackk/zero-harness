/** Command ---- kernel 的执行单元状态机(移植 z2/kernel/command.py).

 状态流转: created → starting(Start)→ running(MarkRunning)→ stopped.
 JS 无任务取消:stop() 通过 kill deferred(root body) + onInterrupt 钩子
 (routine body:发 lifecycle.stop)唤醒 body,超时放弃防挂死.
 模块由 body 退出时的 finally 释放.
 */
import { Deferred, withTimeout } from '../async'
import { setupLogger } from '../logger'
import type { Payload } from '../protocol'

const log = setupLogger('routine.kernel', 'warning')

let _nextId = 0

export enum State {
  CREATED = 'created',
  STARTING = 'starting',
  RUNNING = 'running',
  STOPPING = 'stopping',
  STOPPED = 'stopped',
}

export class Command {
  static STOP_TIMEOUT = 10.0  // 秒:stop 后等 body 退出超时(放弃防挂死)

  readonly id: number
  parentId = 0
  kwargs: Payload = {}
  modules: string[] = []
  /** body 被打断时调(routine body:发 lifecycle.stop).root 无此钩子. */
  onInterrupt: (() => void) | null = null
  /** body 等待的永久挂起点,stop() resolve 它(root body 用). */
  readonly kill = new Deferred<void>()

  private _state = State.CREATED
  private _body: (() => Promise<void>) | null = null
  private _task: Promise<void> | null = null

  constructor(public name: string, body?: () => Promise<void>) {
    this.id = ++_nextId
    if (body) this._body = body
  }

  toString(): string { return `${this.name}-${this.id}` }

  get state(): State { return this._state }

  start(): void {
    if (this._state !== State.CREATED) return
    if (!this._body) throw new Error(`command ${this} has no body attached`)
    this._state = State.STARTING
    this._task = this._body!().catch(e => {
      log.error(`command ${this} body crashed: ${e?.stack ?? e}`)
    })
  }

  markRunning(): void {
    if (this._state === State.STARTING) this._state = State.RUNNING
  }

  attachBody(body: () => Promise<void>): void {
    if (this._task !== null) throw new Error(`command ${this} already started`)
    this._body = body
  }

  setModules(modules: string[]): void { this.modules = [...modules] }

  addModules(modules: string[]): void {
    for (const m of modules) if (!this.modules.includes(m)) this.modules.push(m)
  }

  removeModules(modules: string[]): void {
    const drop = new Set(modules)
    this.modules = this.modules.filter(m => !drop.has(m))
  }

  /** 打断 body:唤醒 kill + 触发 onInterrupt(发 lifecycle.stop)+ 等 body 退出
   * (STOP_TIMEOUT 超时放弃).idempotent. */
  async stop(): Promise<void> {
    if (![State.STARTING, State.RUNNING, State.STOPPING].includes(this._state)) return
    this._state = State.STOPPING
    this.kill.resolve()
    try { this.onInterrupt?.() } catch (e) { log.error(`command ${this} onInterrupt failed: ${e}`) }
    if (this._task) {
      try { await withTimeout(this._task, Command.STOP_TIMEOUT * 1000, `command ${this} stop`) }
      catch (e) { log.warning(`command ${this} body did not exit within ${Command.STOP_TIMEOUT}s; giving up (modules may leak)`) }
    }
    this._state = State.STOPPED
  }

  /** 等 body 退出(自然完成或被打断). */
  async wait(): Promise<void> {
    if (this._task) await this._task
  }
}
