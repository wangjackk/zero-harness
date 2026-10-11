/** XmlAgent(TS) ---- reactive agent: sqlite 记忆 + pi-ai driver LLM + XML 工具调用.
 *
 * py 版 xml_agent/agent.py 的最小同构移植, 差异只在 LLM 层与工具执行层:
 * - **LLM 不自研**: 复用 _core/modelRuntime(pi-ai ModelRuntime.streamSimple).
 *   prompt caching 走 sessionId -> prompt_cache_key(pi-ai 无 previous_response_id
 *   增量链), 每轮全量发历史.
 * - **XML 解析内嵌**: py 边流边解析派发 act 子; TS 流完拿全文经 parser.ts
 *   解析, 工具按出现顺序串行 this.call(经 kernel 全局路由到 py/rs 侧 routine).
 * - **react loop**: LLM 输出 -> 解析 XML -> 执行工具 -> [feedback] 回投 -> 循环,
 *   无工具调用即回合结束.
 * - **打断**: epoch + AbortController(LLM 流 signal + 工具子 handle.stop).
 * - 记忆: xml/memory.ts(bun:sqlite messages + agents 表).
 * - 无 OV / 无 condenser / 无 speak / 无 wait(串行执行下无意义).
 */
import { readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { AsyncEvent, AsyncLock, AsyncQueue, sleep } from 'routine-ts/async'
import { Routine, request } from 'routine-ts'
import type { RoutineSource, RoutineHandle } from 'routine-ts'
import { setupLogger } from 'routine-ts/logger'

import { getSharedModels, resolveModelRef, type ModelLike, type SharedModels } from '../_core/modelRuntime'
import { registerWithBridge } from '../_core/bridge'
import { USER_SOURCE, wrapFrom, PRIORITY_HIGH, splitContent } from '../_core/messaging'
import { AGENT_ID_KEY } from '../_core/paths'
import { getMemory } from './memory'
import { parseXmlToolCalls } from './parser'

const log = setupLogger('xml.agent')

const _SYSTEM_PROMPT_FILE = join(dirname(fileURLToPath(import.meta.url)), 'system_prompt.md')

// agent.ts 在 zero-ts/app/routines/user/agents/xml/ 下, 6 级 .. = harness 根;
// 与 py uploads.py 的 UPLOADS_DIR 同一处 (web_server /files 路由服务).
const UPLOADS_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  '..', '..', '..', '..', '..', '..',
  'runtime', 'uploads',
)

const MIME_BY_EXT: Record<string, string> = {
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  gif: 'image/gif', webp: 'image/webp',
}

/** LLM 可主动调用的 routine 白名单: 进 system prompt(XML 示例形式). */
const PROMPT_ROUTINES = ['play_music', 'list_music', 'wait',
  'list_routines', 'routine_doc',
  'list_running_agents', 'send_message', 'read_image']

/** 合法 effort 档位; 'off' = 关 thinking(模型须声明 can_disable). */
const EFFORT_LEVELS = ['high', 'medium', 'low', 'off'] as const
type Effort = typeof EFFORT_LEVELS[number]

export interface XmlAgentParams {
  agent_id?: string | null
  session_id?: string | null
  model?: string | null
  /** LLM 协议覆盖: 必须已在 provider apis 矩阵声明; null = 矩阵推导. */
  protocol?: string | null
  /** 创建时 reasoning 覆盖: {effort: high|medium|low|none}. */
  reasoning?: { effort?: string } | null
}

/** react 打断哨兵(新输入 / interrupt). */
class Interrupted extends Error {
  constructor() { super('react interrupted') }
}

interface FeedbackItem {
  name: string
  result: string
  is_error: boolean
  input: Record<string, any> | null
  /** read_image 特判: result 携带的图片路径, 随 [feedback] 合并成一条
   * 多模态消息 (文本 + 图) 落库. */
  images?: string[]
}

/** pi Usage -> 前端 usage 载荷共用形状. */
interface UsageLite {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  max_context: number
  percent: number
}

const ZERO_USAGE = {
  input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
}

export class XmlAgent extends Routine {
  static name = 'xml/agent'

  static meta: Record<string, any> = {
    hidden: true,
    description: '精简版 reactive agent (TS): sqlite 记忆 + pi-ai driver LLM + XML 工具调用.',
  }

  static BRIDGE_NAME = 'web_server'

  // 状态
  _agentId = 'main'
  _sessionId = ''
  _modelRef: string | null = null
  _modelKey = ''          // usage 事件上报的 user-facing ref
  _protocol: string | null = null
  _model: ModelLike | null = null
  _shared: SharedModels | null = null
  _supportsImages = false   // 中立配置 image: true; 组装侧据此剥图
  _effort: Effort | null = null
  _lastUsage: Record<string, any> | null = null
  _staticPrompt = readFileSync(_SYSTEM_PROMPT_FILE, 'utf-8')

  // react / interrupt
  _epoch = 0
  _msgEpoch = 0
  _messageId = ''
  _stop = new AsyncEvent()
  _eventLock = new AsyncLock()
  _agentMsgLock = new AsyncLock()
  _reactTask: Promise<void> | null = null
  _abort: AbortController | null = null

  // TTS: 标签外裸文本流式喂 rs 侧 speak 子 (尽力而为, 失败降级纯文本).
  // 对齐 py 版 act/body_shell 语义: 段经队列 + pump 后台投递, mouth 模块被
  // 上一段占用时排队等待 (py 靠 shell 模块互斥排队); 播放属于 react 生命周期,
  // 收尾 drain 等播完, 新输入/打断全停.
  private _speakInTag = false
  private _speakHandle: RoutineHandle | null = null
  private _speakActives = new Set<RoutineHandle>()
  private _speakQueue = new AsyncQueue<{ text?: string; eof?: boolean; stop?: boolean }>()
  private _speakPumpTask: Promise<void> | null = null
  private _speakPumpStop = false

  // 就绪门闩: init 完成前 chat_message 等输入必须等待 (create 后立即 chat 的竞态)
  private _readyResolve!: () => void
  private _ready = new Promise<void>(r => { this._readyResolve = r })

  // ── 生命周期 ──

  async run(kwargs: Record<string, any>): Promise<void> {
    const params = kwargs as XmlAgentParams
    try {
      await this._init(params)
    } catch (exc: any) {
      log.error(`init failed: ${exc?.message ?? exc}`)
      throw exc
    } finally {
      this._readyResolve()
    }
    void this._registerWithBridge().catch(e => {
      log.error(`bridge register failed: ${e instanceof Error ? e.message : e}`)
    })
    await this._stop.wait()
  }

  async stop(): Promise<void> {
    await this._cancelReact('stop')
    this._stop.set()
  }

  private async _init(params: XmlAgentParams): Promise<void> {
    this._agentId = params.agent_id || crypto.randomUUID().replaceAll('-', '')
    this._sessionId = params.session_id || crypto.randomUUID().replaceAll('-', '')
    this._modelRef = params.model || null
    this._protocol = params.protocol || null

    // 模型解析: 显式 ref > 中立配置 default; 显式协议走变体键 (未声明即抛)
    const shared = await getSharedModels(this)
    this._shared = shared
    const ref = this._modelRef || shared.config.default
    if (!ref) throw new Error('no model available (models.yaml default missing)')
    const model = resolveModelRef(shared, ref, this._protocol ?? undefined)
    if (!model) throw new Error(`model not found in neutral config: ${ref}`)
    this._model = model
    this._modelKey = ref
    // 视觉能力 (中立配置 model 级 image: true): 组装侧据此决定是否发图 blocks.
    const slash = ref.indexOf('/')
    this._supportsImages = slash > 0
      && !!shared.config.providers[ref.slice(0, slash)]?.models?.[ref.slice(slash + 1)]?.image

    // reasoning effort: create 参数覆盖 > 中立配置模型声明; 均缺省 = null
    // (不传 reasoning, 走 provider 默认). 'none' = off(显式关).
    const effortRaw = params.reasoning?.effort ?? neutralEffort(shared, ref)
    this._effort = normalizeEffort(effortRaw)
    this._validateEffort()
    log.info(`xml agent init: agent=${this._agentId} session=${this._sessionId} `
      + `model=${ref} protocol=${this._protocol ?? '(derived)'} effort=${this._effort}`)

    // resume 恢复上一轮 usage (持久化在 messages.usage 列), 供前端圆环回填
    this._lastUsage = getMemory().getLastUsage(this._sessionId)
  }

  private _validateEffort(): void {
    const model = this._model as any
    if (this._effort === 'off' && model?.reasoning && !model.thinkingLevelMap?.off) {
      throw new Error(
        `model '${this._modelKey}' cannot disable thinking (no can_disable declared)`)
    }
  }

  // ── reactive / interrupt ──

  /** 输入通道的统一落库路径 (_onInput), 保证消息格式 (wrapFrom /
   * [图片] 占位) 单点维护. */
  private _storeIncoming(text: string, images: string[], fromAgent: string): string {
    let content = wrapFrom(text, fromAgent)
    if (images.length && !content) content = '[图片]'
    getMemory().addMessage('user', content, {
      agentId: this._agentId,
      messageId: crypto.randomUUID().replaceAll('-', ''),
      sessionId: this._sessionId,
      images: images.length ? images : null,
    })
    return content
  }

  private async _onInput(_source: RoutineSource, data: any): Promise<void> {
    await this._ready
    const text = String(data?.text ?? '')
    const images: string[] = Array.isArray(data?.images) ? data.images : []
    if (!text && !images.length) return
    const fromAgent = String(data?.from || USER_SOURCE)
    await this._eventLock.run(async () => {
      this._epoch += 1
      await this._cancelReact('user_input')
      // 新输入截断上一轮遗留的 TTS 播放 (react 可能已结束, 音频异步在播)
      await this._stopSpeak()
      const content = this._storeIncoming(text, images, fromAgent)
      log.info(`epoch -> ${this._epoch} (user_input): ${content.slice(0, 80)}`)
      if (fromAgent !== USER_SOURCE) {
        await this.emitIncomingMessage(content, fromAgent)
      }
      this._startReact(this._epoch)
    })
  }

  @request('interrupt')
  async onInterruptReq(source: RoutineSource, data: any): Promise<any> {
    void this._onInterrupt(source, data ?? {}).catch(() => {})
    return { ok: true, epoch: this._epoch }
  }

  private async _onInterrupt(_source: RoutineSource, _data: any): Promise<void> {
    await this._ready
    await this._eventLock.run(async () => {
      this._epoch += 1
      log.info(`epoch -> ${this._epoch} (interrupt)`)
      await this._cancelReact('interrupt')
    })
  }

  /** abort 在飞 react(LLM 流 + 工具子)并收敛等待. */
  private async _cancelReact(reason: string): Promise<void> {
    const task = this._reactTask
    this._reactTask = null
    this._abort?.abort()
    if (task === null) return
    try { await task } catch (exc: any) {
      log.warning(`react converge error (${reason}): ${exc?.message ?? exc}`)
    }
  }

  private _startReact(epoch: number): void {
    const task = this._runReact(epoch)
    this._reactTask = task
    task.catch(exc => {
      log.error(`react crashed (epoch=${epoch}): ${exc instanceof Error ? exc.stack : exc}`)
    })
  }

  /** epoch 内多轮迭代: 有 feedback 就再来一轮. */
  private async _runReact(epoch: number): Promise<void> {
    while (true) {
      const more = await this._reactOnce(epoch)
      if (!more) return
    }
  }

  // ── react: 一轮 inference + 工具执行 ──

  private async _reactOnce(epoch: number): Promise<boolean> {
    const messageId = crypto.randomUUID().replaceAll('-', '')
    this._messageId = messageId
    this._msgEpoch = epoch
    let textOutput = ''
    let feedbackItems: FeedbackItem[] = []
    const abort = new AbortController()
    this._abort = abort
    this._speakInTag = false
    this._speakPumpStop = false
    this._speakQueue = new AsyncQueue()
    this._speakPumpTask = this._speakPump()
    try {
      const { instructions, messages, sysView } = await this._buildContext()
      await this.emitSysPrompt(epoch, messageId, sysView)

      // LLM 流式 (pi-ai driver). epoch 漂移由 abort 触发流 error 事件.
      const stream = this._shared!.runtime.streamSimple(
        this._model as any,
        { systemPrompt: instructions || undefined, messages },
        {
          reasoning: (this._effort ?? undefined) as any,
          sessionId: this._sessionId,
          signal: abort.signal,
        },
      )
      let usage: Record<string, any> | null = null
      for await (const ev of stream) {
        this._checkEpoch(epoch)
        if (ev.type === 'text_delta' && ev.delta) {
          textOutput += ev.delta
          await this.emitOutput(ev.delta, false, epoch, messageId)
          this._feedSpeak(ev.delta)
        } else if (ev.type === 'thinking_delta' && ev.delta) {
          await this.emitOutput(ev.delta, false, epoch, messageId, true)
        } else if (ev.type === 'done') {
          usage = (ev.message as any)?.usage ?? null
          await this.emitUsage(usage, epoch, messageId)
        } else if (ev.type === 'error') {
          if (abort.signal.aborted) throw new Interrupted()
          throw new Error(
            `llm stream error: ${(ev.error as any)?.errorMessage ?? ev.reason}`)
        }
      }
      await this.emitOutput(textOutput, true, epoch, messageId)
      this._speakQueue.push({ eof: true })

      // XML 工具块解析 + 串行执行 (kernel 全局路由)
      const calls = parseXmlToolCalls(textOutput)
      const resultsRaw: Array<{ name: string; result: any; error: string | null; input: Record<string, any> | null }> = []
      for (const c of calls) {
        this._checkEpoch(epoch)
        const kwargs: Record<string, any> = { ...c.kwargs, [AGENT_ID_KEY]: this._agentId }
        try {
          const result = await this.callStoppable(c.name, kwargs, abort.signal)
          resultsRaw.push({ name: c.name, result, error: null, input: c.kwargs })
        } catch (exc: any) {
          if (abort.signal.aborted || this._epoch !== epoch) throw new Interrupted()
          resultsRaw.push({
            name: c.name, result: null,
            error: String(exc?.message ?? exc), input: c.kwargs,
          })
        }
      }
      feedbackItems = extractFeedbackItems(resultsRaw)

      // 正常完成入库 (feedback 供 resume 后前端渲染 tool 块)
      getMemory().addMessage('assistant', textOutput, {
        agentId: this._agentId, messageId, sessionId: this._sessionId,
        feedback: feedbackItems, usage,
      })
      // 对齐 py 版 await act.wait(): react 收尾等 TTS 播完 (播放属于 react
      // 生命周期); 新输入经 abort -> onAbort 停 speak -> drain 立即返回.
      await this._drainSpeak(abort.signal)
    } catch (exc: any) {
      if (exc instanceof Interrupted || abort.signal.aborted) {
        // 打断: 保住已输出的部分内容, 补发 final 收尾帧 (前端关流收光标)
        await this._stopSpeak()
        try {
          await this.emitOutput(textOutput, true, epoch, messageId, false, true)
        } catch (e: any) {
          log.warning(`emit final on interrupt failed: ${e?.message ?? e}`)
        }
        getMemory().addMessage('assistant', textOutput, {
          agentId: this._agentId, messageId, sessionId: this._sessionId,
          interrupted: true, feedback: feedbackItems,
        })
        return false
      }
      await this._stopSpeak()
      log.error(`react crashed (epoch=${epoch}): ${exc instanceof Error ? exc.stack : exc}`)
      return false
    } finally {
      if (this._abort === abort) this._abort = null
    }

    if (!feedbackItems.length) return false

    // [feedback] 回投 -> 再来一轮
    const feedbackMsg = buildFeedbackMessage(feedbackItems)
    // read_image 的图引用合并进 [feedback] 一条消息 (文本 + 图多模态).
    const feedbackImages = feedbackItems.flatMap(it => it.images ?? [])
    getMemory().addMessage('user', feedbackMsg, {
      agentId: this._agentId,
      messageId: crypto.randomUUID().replaceAll('-', ''),
      sessionId: this._sessionId,
      images: feedbackImages.length ? feedbackImages : null,
    })
    await this.emitFeedback(feedbackMsg, toEventResults(feedbackItems), epoch, messageId)
    return true
  }

  private _checkEpoch(epoch: number): void {
    if (this._epoch !== epoch) throw new Interrupted()
  }

  /** 工具桥的可停 call: submit → start → wait, abort 触发时 stop 子 routine. */
  async callStoppable(
    name: string, kwargs: Record<string, any>, signal?: AbortSignal,
  ): Promise<any> {
    if (!signal) return await this.call(name, kwargs)
    const handle = await this.submit(name, kwargs)
    await handle.start()
    const onAbort = () => { void handle.stop().catch(() => {}) }
    if (signal.aborted) onAbort()
    else signal.addEventListener('abort', onAbort, { once: true })
    try {
      return await handle.wait()
    } finally {
      signal.removeEventListener('abort', onAbort)
    }
  }

  // ── TTS: 标签外裸文本流式喂 rs 侧 speak 子 (尽力而为, 失败降级纯文本) ──
  // 对齐 py 版 act.on_body_text + body_shell 语义: 只念 XML 标签外的正文段;
  // 段经 AsyncQueue + 后台 pump 投递, mouth 被上一段占用时排队等待;
  // 播放属于 react 生命周期, 收尾 drain 等播完, 新输入/打断全停.

  /** LLM text delta -> 粗粒度标签状态机, 标签外文本/段界入队 (非阻塞). */
  private _feedSpeak(delta: string): void {
    let out = ''
    for (const ch of delta) {
      if (ch === '<') {
        if (!this._speakInTag) {
          this._speakInTag = true
          if (out) { this._speakQueue.push({ text: out }); out = '' }
          this._speakQueue.push({ eof: true })
        }
      } else if (ch === '>') {
        this._speakInTag = false
      } else if (!this._speakInTag) {
        out += ch
      }
    }
    if (out) this._speakQueue.push({ text: out })
  }

  /** pump: 队列消费 -> spawn speak 段 (mouth 忙则排队重试) / 流式 send / _eof. */
  private async _speakPump(): Promise<void> {
    while (true) {
      const item = await this._speakQueue.get()
      if (item.stop) return
      if (item.eof) {
        const handle = this._speakHandle
        this._speakHandle = null
        if (handle) {
          try { await this.send(handle.id, { _eof: true }) } catch (exc: any) {
            log.warning(`speak eof failed: ${exc?.message ?? exc}`)
          }
        }
        continue
      }
      const text = item.text ?? ''
      if (!text) continue
      // 确保段 handle: mouth 被上一段占用时等待重试 (py 靠 shell 模块排队)
      while (!this._speakHandle && !this._speakPumpStop) {
        try {
          const handle = await this.submit('speak')
          await handle.start()
          this._speakHandle = handle
          this._speakActives.add(handle)
          void handle.wait().catch(() => {}).then(() => this._speakActives.delete(handle))
        } catch (exc: any) {
          const msg = String(exc?.message ?? exc)
          if (msg.includes('blocked')) { await sleep(300); continue }
          log.warning(`speak degraded, text-only for this segment: ${msg}`)
          break
        }
      }
      const handle = this._speakHandle
      if (!handle) continue
      try {
        await this.send(handle.id, { text })
      } catch (exc: any) {
        log.warning(`speak send failed: ${exc?.message ?? exc}`)
        this._speakHandle = null
      }
    }
  }

  /** 新输入/打断/崩溃: 停 pump + 停掉所有还在播放的 speak 段 (截断音频). */
  private async _stopSpeak(): Promise<void> {
    this._speakPumpStop = true
    this._speakQueue.push({ stop: true })
    this._speakHandle = null
    const actives = [...this._speakActives]
    this._speakActives.clear()
    for (const handle of actives) {
      try {
        await handle.stop()
      } catch (exc: any) {
        log.warning(`speak stop failed: ${exc?.message ?? exc}`)
      }
    }
  }

  /** react 收尾: 等 pump 投递完 + 活跃段播完 (正常路径); abort 时全停立即返回. */
  private async _drainSpeak(signal: AbortSignal): Promise<void> {
    const onAbort = () => {
      this._speakPumpStop = true
      this._speakQueue.push({ stop: true })
      for (const h of [...this._speakActives]) void h.stop().catch(() => {})
    }
    if (signal.aborted) { onAbort(); return }
    signal.addEventListener('abort', onAbort, { once: true })
    try {
      if (this._speakPumpTask) {
        this._speakQueue.push({ stop: true })
        try { await this._speakPumpTask } catch { /* pump 异常不阻塞收尾 */ }
      }
      const actives = [...this._speakActives]
      for (const h of actives) {
        try { await h.wait() } catch { /* 停止/失败不阻塞收尾 */ }
      }
    } finally {
      signal.removeEventListener('abort', onAbort)
    }
  }

  // ── messages 组装 ──

  private async _buildContext(): Promise<{
    instructions: string
    messages: any[]
    sysView: any[]
  }> {
    let system = this._staticPrompt.replace('{AGENT_ID}', this._agentId)
    const routines = await this._renderRoutines()
    if (routines) system = system + '\n\n## 可用 routine\n\n' + routines

    const history = getMemory().loadHistory(this._sessionId)
    // 空 content 消息跳过 (打断时 LLM 未输出的空 assistant 对 API 无信息量)
    const usable = history.filter(m => m.content || m.images?.length)
    const messages: any[] = []
    for (const m of usable) {
      messages.push(await this._toPiMessage(m))
    }
    const sysView = [
      ...(system ? [{ role: 'system', content: system }] : []),
      ...history.map(m => ({ role: m.role, content: m.content })),
    ]
    return { instructions: system, messages, sysView }
  }

  /** history 消息 -> pi-ai Context 消息 (user 图文 blocks / assistant 合成).
   * 底层模型不支持读图 (_supportsImages=false) 时剥掉图, 只发文本. */
  private async _toPiMessage(m: { role: string; content: string; images: string[] | null }): Promise<any> {
    if (m.role === 'user' && m.images?.length && this._supportsImages) {
      const blocks: any[] = [{ type: 'text', text: m.content }]
      for (const p of m.images) {
        blocks.push(await toPiImage(p))
      }
      return { role: 'user', content: blocks, timestamp: Date.now() }
    }
    if (m.role === 'assistant') {
      return {
        role: 'assistant',
        content: [{ type: 'text', text: m.content }],
        api: (this._model as any)?.api, provider: (this._model as any)?.provider,
        model: this._model?.id, usage: ZERO_USAGE, stopReason: 'stop',
        timestamp: Date.now(),
      }
    }
    return { role: 'user', content: m.content, timestamp: Date.now() }
  }

  /** 渲染白名单 PROMPT_ROUTINES 的接口表 (kernel 全局 catalog). */
  private async _renderRoutines(): Promise<string> {
    let routines: any[] = []
    try {
      routines = await this.getRoutines()
    } catch (exc: any) {
      log.warning(`get routines failed: ${exc?.message ?? exc}`)
      return ''
    }
    const byName = new Map<string, any>()
    for (const r of routines) {
      const n = String(r?.name ?? '')
      if (n && !byName.has(n)) byName.set(n, r)
    }
    const blocks: string[] = []
    for (const name of PROMPT_ROUTINES) {
      const r = byName.get(name)
      if (r === undefined) {
        log.warning(`PROMPT_ROUTINES white-list routine not found: '${name}'`)
        continue
      }
      const meta = (r.meta ?? {}) as Record<string, any>
      const inputSchema = meta.input_schema
      if (inputSchema == null) {
        log.warning(`routine '${name}' has no input_schema, skip`)
        continue
      }
      const { xml, propInfos } = xmlFromSchema(name, inputSchema)
      const lines = [`### \`${name}\``, '', '```xml', xml, '```', '']
      const desc = meta.description || ''
      if (desc) lines.push(desc, '')
      if (propInfos.length) {
        lines.push('参数:')
        for (const p of propInfos) {
          const parts = [`- \`${p.name}\``]
          if (p.type) parts.push(`(${p.type})`)
          if (p.required) parts.push('必填')
          else if (p.default !== null) parts.push(`默认 ${p.default}`)
          if (p.desc) parts.push(`- ${p.desc}`)
          lines.push(parts.join(' '))
        }
        lines.push('')
      }
      blocks.push(lines.join('\n').trimEnd())
    }
    return blocks.join('\n\n---\n\n')
  }

  // ── request handlers ──

  @request('chat_message')
  async onChatMessage(source: RoutineSource, data: any): Promise<any> {
    let text: string
    let images: string[]
    try {
      [text, images] = splitContent(data?.content)
    } catch (exc: any) {
      return { ok: false, error: `invalid payload: ${exc?.message ?? exc}` }
    }
    if (!text && !images.length) return { ok: false, error: 'message is required' }
    const fromAgent = data?.from
    const rawPriority = data?.priority
    const priority = rawPriority !== undefined && rawPriority !== null
      ? Number(rawPriority) : PRIORITY_HIGH
    const payload: Record<string, any> = { text, from: fromAgent }
    if (images.length) payload.images = images
    let task: Promise<void>
    if (priority >= PRIORITY_HIGH) {
      task = this._onInput(source, payload)
    } else {
      task = this._deliverAgentMessage(source, payload)
    }
    task.catch(exc => {
      log.error(`deliver_agent_message crashed: ${exc instanceof Error ? exc.stack : exc}`)
    })
    return { ok: true, epoch: this._epoch }
  }

  /** 等当前 react 完成 (idle) 后再投递, 不打断在途轮次 (agent 消息排队串行). */
  private async _deliverAgentMessage(source: RoutineSource, data: any): Promise<void> {
    await this._agentMsgLock.run(async () => {
      while (this._reactTask !== null) {
        try { await this._reactTask } catch { /* ignore */ }
        await new Promise(r => setTimeout(r, 50))
      }
      await this._onInput(source, data)
    })
  }

  @request('get_history')
  async onGetHistory(_source: RoutineSource, _data: any): Promise<any> {
    return {
      ok: true,
      session_id: this._sessionId,
      messages: getMemory().loadMessages(this._sessionId),
      usage: this._usagePayload(this._lastUsage),
    }
  }

  @request('set_effort')
  async onSetEffort(_source: RoutineSource, data: any): Promise<any> {
    // 运行时语义(对齐 py): 缺省 / '' / 'none' = 显式关 thinking(模型不支持关时报错)
    const raw = data?.effort
    const wantOff = raw === undefined || raw === null
      || String(raw).trim() === '' || String(raw).trim() === 'none'
    let level: Effort = 'off'
    if (!wantOff) {
      try {
        level = normalizeEffort(raw) ?? 'off'
      } catch (exc: any) {
        return { ok: false, error: String(exc?.message ?? exc) }
      }
    }
    const prev = this._effort
    this._effort = level
    try {
      this._validateEffort()
    } catch (exc: any) {
      this._effort = prev
      return { ok: false, error: String(exc?.message ?? exc) }
    }
    return { ok: true, effort: this._effort === 'off' ? null : this._effort }
  }

  @request('agent_state')
  async onAgentState(_source: RoutineSource, _data: any): Promise<any> {
    return {
      agent_id: this._agentId,
      session_id: this._sessionId,
      project_root: null,
      skill_dir: null,
      skill_index_cache_dir: null,
    }
  }

  @request('run')
  async onRun(_source: RoutineSource, data: any): Promise<any> {
    /** HTTP 转发的 routine 调用. 注入 from_agent_id 后 call 目标 routine. */
    const target = String(data?.target ?? '')
    const kwargs = data?.kwargs ?? {}
    if (!target) return { ok: false, error: 'missing target routine name' }
    if (!kwargs || typeof kwargs !== 'object') {
      return { ok: false, error: 'kwargs must be an object' }
    }
    kwargs[AGENT_ID_KEY] = this._agentId
    try {
      const result = await this.call(target, kwargs)
      return { ok: true, result }
    } catch (exc: any) {
      return { ok: false, error: String(exc?.message ?? exc) }
    }
  }

  // ── emit (前端事件, 全局 + namespaced 双发) ──

  async emitOutput(
    text: string, isFinal: boolean, epoch: number, messageId: string,
    isThinking = false, interrupted = false,
  ): Promise<void> {
    if (!text && !isFinal) return
    const data = {
      text, is_final: isFinal,
      epoch, message_id: messageId, agent_id: this._agentId,
      is_thinking: isThinking, interrupted,
    }
    await this.publish('assistant_output', data)
    await this.publish('assistant_output', data, this._agentId)
  }

  async emitIncomingMessage(text: string, fromAgent: string): Promise<void> {
    const data = { text, from: fromAgent, agent_id: this._agentId }
    await this.publish('incoming_message', data)
    await this.publish('incoming_message', data, this._agentId)
  }

  async emitFeedback(
    content: string, results: any[], epoch: number, messageId: string,
  ): Promise<void> {
    const data = { content, results, epoch, message_id: messageId, agent_id: this._agentId }
    await this.publish('feedback', data)
    await this.publish('feedback', data, this._agentId)
  }

  async emitUsage(
    usage: Record<string, any> | null, epoch: number, messageId: string,
  ): Promise<void> {
    if (usage) this._lastUsage = { ...usage }
    const data = {
      ...this._usagePayload(usage),
      epoch, message_id: messageId,
    }
    await this.publish('usage', data)
    await this.publish('usage', data, this._agentId)
  }

  async emitSysPrompt(epoch: number, messageId: string, messages: any[]): Promise<void> {
    const data = { epoch, message_id: messageId, messages, agent_id: this._agentId }
    await this.publish('sys_prompt', data)
    await this.publish('sys_prompt', data, this._agentId)
  }

  /** 统一 usage 载荷 (实时推送与 get_history/session_changed 回填共用口径). */
  private _usagePayload(usage: Record<string, any> | null): UsageLite & Record<string, any> {
    const maxContext = this._model?.contextWindow ?? 0
    let inputTokens = 0
    let outputTokens = 0
    let totalTokens = 0
    let cacheRead = 0
    if (usage) {
      // pi Usage: input(未命中部分)/output/totalTokens(+cacheRead)
      inputTokens = Number(usage.input ?? 0)
      outputTokens = Number(usage.output ?? 0)
      totalTokens = Number(usage.totalTokens ?? usage.total ?? (inputTokens + outputTokens))
      cacheRead = Number(usage.cacheRead ?? 0)
      // 上下文占用应含命中缓存的 token
      inputTokens += cacheRead
    }
    const triggerTokens = maxContext * 0.5
    const percent = triggerTokens > 0
      ? Math.round((inputTokens / triggerTokens) * 1000) / 10 : 0.0
    return {
      input_tokens: inputTokens, output_tokens: outputTokens,
      total_tokens: totalTokens, max_context: maxContext, percent,
      cache_read: cacheRead,
      agent_id: this._agentId,
      model_key: this._modelKey,
      model_name: this._model?.name ?? this._model?.id ?? '',
      reasoning_effort: this._effort,
    }
  }

  // ── bridge 注册 ──

  async _registerWithBridge(): Promise<void> {
    await registerWithBridge({
      agentId: this._agentId,
      routineId: this.id ?? '',
      namePrefix: 'Xml-',
      bridgeName: (this.constructor as typeof Routine & { BRIDGE_NAME: string }).BRIDGE_NAME,
      ctx: this,
      stopEvent: this._stop,
      logger: log,
      onSuccess: async () => {
        // 前端 backfill: 历史 + 上一轮 usage 圆环
        const history = getMemory().loadHistory(this._sessionId)
        await this.publish('session_changed', {
          agent_id: this._agentId,
          session_id: this._sessionId,
          is_new: history.length === 0,
          messages: getMemory().loadMessages(this._sessionId),
          usage: this._lastUsage ? this._usagePayload(this._lastUsage) : null,
        })
      },
    })
  }
}

// ── helpers ──

/** 中立配置里该模型 ref 声明的默认 effort 档 (reasoning.effort). */
export function neutralEffort(shared: SharedModels, ref: string): string | null {
  const slash = ref.indexOf('/')
  if (slash <= 0) return null
  const entry = shared.config.providers[ref.slice(0, slash)]?.models?.[ref.slice(slash + 1)]
  return entry?.reasoning?.effort ?? null
}

/** 入参 effort -> 档位: 空 = null(未指定, 不传 reasoning); 'none' = off
 * (显式关); 非法值抛错(fail fast, 调用方决定报错口径). */
export function normalizeEffort(raw: any): Effort | null {
  const s = String(raw ?? '').trim()
  if (!s) return null
  if (s === 'none') return 'off'
  if (!(EFFORT_LEVELS as readonly string[]).includes(s)) {
    throw new Error(`invalid reasoning effort: ${raw}`)
  }
  return s as Effort
}

/** 从 JSON Schema 渲染 XML 调用示例 + 每参数详情. */
function xmlFromSchema(name: string, schema: any): {
  xml: string
  propInfos: Array<{ name: string; type: string; desc: string; default: string | null; required: boolean }>
} {
  const props = schema?.properties ?? {}
  const required = new Set<string>(schema?.required ?? [])
  const TYPE_DEFAULT: Record<string, string> = {
    string: 'x', integer: '0', number: '0', boolean: 'true',
  }
  const entries = Object.entries(props) as Array<[string, any]>
  if (!entries.length) return { xml: `<${name}/>`, propInfos: [] }

  const attrs: string[] = []
  const propInfos: Array<{ name: string; type: string; desc: string; default: string | null; required: boolean }> = []
  for (const [pname, pschema] of entries) {
    const ptype = resolveSchemaType(pschema ?? {})
    const pdesc = String(pschema?.description ?? '').trim()
    const hasDefault = pschema && 'default' in pschema
    const val = hasDefault ? fmtDefault(pschema.default) : (TYPE_DEFAULT[ptype] ?? 'x')
    attrs.push(`${pname}="${val}"`)
    propInfos.push({
      name: pname, type: ptype, desc: pdesc,
      default: hasDefault ? fmtDefault(pschema.default) : null,
      required: required.has(pname),
    })
  }
  return { xml: `<${name} ${attrs.join(' ')}/>`, propInfos }
}

function resolveSchemaType(ps: Record<string, any>): string {
  if (ps.type) return String(ps.type)
  for (const combo of ['anyOf', 'oneOf']) {
    for (const opt of ps[combo] ?? []) {
      if (opt && typeof opt === 'object' && opt.type !== 'null') {
        return String(opt.type ?? 'string')
      }
    }
  }
  return 'string'
}

function fmtDefault(v: any): string {
  if (typeof v === 'number' && Number.isInteger(v)) return String(v)
  if (v === null || v === undefined) return 'null'
  return String(v)
}

/** 工具执行结果 -> [feedback] 条目 (content 约定: 带 content 键的只回它的值).
 * read_image 特判: result 里的 images (本地路径列表) 提到条目上, 随 [feedback]
 * 合并成一条多模态消息 (文本 + 图) 落库; 模型不支持读图时组装侧剥掉. */
function extractFeedbackItems(resultsRaw: Array<{
  name: string; result: any; error: string | null; input: Record<string, any> | null
}>): FeedbackItem[] {
  const out: FeedbackItem[] = []
  for (const res of resultsRaw) {
    const name = res.name.trim()
    let result = res.result
    let isError = false
    if (result === null || result === undefined) {
      result = res.error
      isError = result !== null && result !== undefined
    }
    let images: string[] = []
    if (name === 'read_image' && result !== null && typeof result === 'object') {
      const raw = (result as any).images
      if (Array.isArray(raw)) {
        images = raw.map((p: unknown) => String(p)).filter(Boolean)
      }
    }
    if (result !== null && typeof result === 'object' && 'content' in (result as any)) {
      result = (result as any).content
    }
    if (!name || result === null || result === undefined) continue
    const item: FeedbackItem = {
      name,
      result: typeof result === 'object' ? JSON.stringify(result) : String(result),
      is_error: isError,
      input: res.input,
    }
    if (images.length) item.images = images
    out.push(item)
  }
  return out
}

function buildFeedbackMessage(items: FeedbackItem[]): string {
  const lines = ['[feedback]']
  for (const item of items) {
    const prefix = item.is_error ? 'ERROR ' : ''
    lines.push(`- ${item.name}: ${prefix}${item.result}`)
  }
  return lines.join('\n')
}

function toEventResults(items: FeedbackItem[]): any[] {
  const results: any[] = []
  for (const item of items) {
    const name = item.name.trim()
    const value = item.result.trim()
    if (!name || !value) continue
    const out: Record<string, any> = { name }
    if (item.input !== null) out.input = item.input
    if (item.is_error) out.error = { msg: value }
    else out.result = value
    results.push(out)
  }
  return results
}

/** 图片引用 ('/files/<name>' | 绝对路径 | data: URL) -> pi ImageContent. */
async function toPiImage(path: string): Promise<{ type: 'image'; data: string; mimeType: string }> {
  if (path.startsWith('data:')) {
    const m = /^data:([^;,]+);base64,(.+)$/s.exec(path)
    if (m === null) throw new Error(`invalid data url: ${path.slice(0, 32)}`)
    return { type: 'image', data: m[2], mimeType: m[1] }
  }
  let file = path
  if (path.startsWith('/files/')) {
    file = resolve(UPLOADS_DIR, path.slice('/files/'.length))
  }
  const buf = await readFile(file)
  const ext = file.split('.').pop()?.toLowerCase() ?? ''
  return { type: 'image', data: buf.toString('base64'), mimeType: MIME_BY_EXT[ext] ?? 'image/png' }
}
