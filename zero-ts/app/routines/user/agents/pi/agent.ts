/** PiAgent ---- pi 原版 agent 引擎的 routine 封装.
 *
 * 会话循环 / 上下文压缩 / 多模型层 / 重试 / steering 全部交给 pi
 * (@earendil-works/pi-coding-agent SDK 的 AgentSession); 本 routine 只做:
 *   1. 承载 AgentSession ---- 唯一工具 ipython, 经 call 走本仓 routine 生态
 *   2. pi 事件 -> zero 前端事件协议翻译 (assistant_output / feedback / usage)
 *   3. 前端与桥接请求 -> session API (prompt / abort / history)
 * 会话持久化用 pi 自己的 SessionManager (workspace/pi-sessions/*.jsonl),
 * agent stop 后 resume 由 pi 会话文件回放, 替代原 sqlite SessionStore.
 */
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { mkdir, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'
import { AsyncEvent, AsyncLock } from 'routine-ts/async'
import { Routine, request } from 'routine-ts'
import type { RoutineSource } from 'routine-ts'
import { setupLogger } from 'routine-ts/logger'

import {
  DefaultResourceLoader, SessionManager, SettingsManager, createAgentSession, getAgentDir,
  type AgentSession, type AgentSessionEvent, type CreateAgentSessionOptions, type Skill,
} from '@earendil-works/pi-coding-agent'

import { AGENT_ID_KEY } from '../_core/paths'
import { registerWithBridge } from '../_core/bridge'
import { getSharedModels, resolveModelRef, type ModelLike } from '../_core/modelRuntime'
import { USER_SOURCE, wrapFrom, PRIORITY_HIGH, splitContent } from '../_core/messaging'
import { makeIpthonTool, toPiImage } from './tools'
import { makeFilesApiExtension } from './filesApiExtension'
import { buildPrimeSystemPrompt } from './prompt'

const log = setupLogger('pi.agent')

// agent.ts 在 zero-ts/app/routines/user/agents/pi/ 下, 6 级 .. = harness 根;
// 与 py uploads.py 的 UPLOADS_DIR 同一处 (web_server /files 路由服务).
const UPLOADS_DIR = join(
  dirname(fileURLToPath(import.meta.url)),
  '..', '..', '..', '..', '..', '..',
  'runtime', 'uploads',
)

const MIME_EXT: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
}

// 用户共享 skill 库 (py skill/repo/* 的存储; routine-bridge 的拷贝源).
// 与 py registry.USER_SKILLS_DIR 同一约定: env ZERO_SKILLS_DIR 覆盖.
const USER_SKILLS_DIR = process.env.ZERO_SKILLS_DIR
  ? resolve(process.env.ZERO_SKILLS_DIR)
  : join(homedir(), '.zero', 'skills')

// 预注入基础清单: routine-bridge 是自举手册 (教 agent 调 routine / 用 skill
// 系统); create 参数 skills 可追加 (从共享库拷入 workspace).
const PRELOADED_SKILLS = ['routine-bridge']

/** skill 加载等级: full = 全文并入 system prompt; summary = 摘要级
 * (拷入 workspace, pi available_skills 块渐进而入). */
type SkillLevel = 'full' | 'summary'

interface SkillRequest { name: string; level?: SkillLevel }

/** full 级渲染: 与 py invoke_for_preload 三段式同构 (IMPORTANT 标记 +
 * 正文 + 目录), 保证跨引擎的预载语义一致. */
function renderPreloadedSkill(name: string, skillMd: string, skillDir: string): string {
  const m = /^---\s*\n(.*?)\n---\s*\n?/.exec(skillMd)
  const body = m ? skillMd.slice(m[0].length).trim() : skillMd.trim()
  return (
    `[IMPORTANT: The "${name}" skill is preloaded for this session. `
    + `Treat its instructions as active guidance for the duration of this `
    + `session unless the user overrides them.]\n\n`
    + `# Skill: ${name}\n\n${body}\n\n`
    + `[Skill directory: ${skillDir}]`
  )
}

export interface PiAgentParams {
  agent_id?: string | null
  model?: string | null
  /** LLM 协议覆盖 (agent 策略): 必须已在 provider apis 矩阵声明;
   *  null = 矩阵推导 (缺省策略). */
  protocol?: string | null
  extra_instructions?: string | null
  session_id?: string | null
  project_dir_root_path?: string | null
  /** 额外预载 skill (共享库名单 + 加载等级, 基础清单合并去重). */
  skills?: SkillRequest[] | null
}

/** message_update 携带的流事件形状子集. */
interface AssistantEventLite {
  type: string
  delta?: string
}

/** 前端 ChatMessage (与 _core/messages.ts 的 ChatMessage 对齐). */
interface ChatMessage {
  id: string
  role: string
  text: string
  results?: any[]
  final?: boolean
  images?: string[]
}

export class PiAgent extends Routine {
  static name = 'pi/agent'

  static meta: Record<string, any> = {
    hidden: true,
    description: (
      'pi (earendil-works) coding-agent engine hosting only the ipython tool. '
      + 'Agent loop, compaction, session persistence and multi-provider models '
      + 'are all handled by the pi AgentSession SDK.'
    ),
  }

  static BRIDGE_NAME = 'web_server'

  // 状态
  _agentId = 'main'
  _projectRoot: string | null = null
  _modelRef: string | null = null  // 显式传入才有; 缺省交给 pi 解析
  _protocol: string | null = null  // LLM 协议覆盖 (显式传入才有; null = 矩阵推导)
  _model: ModelLike | null = null
  _workspace: string | null = null

  // pi 引擎
  _session: AgentSession | null = null
  _sessionManager: SessionManager | null = null

  // epoch 中断 + 事件流
  _epoch = 0
  _msgEpoch = 0          // 当前 messageId 所属 epoch (打断后丢弃陈旧流式帧)
  _messageId = ''
  _stop = new AsyncEvent()
  _eventLock = new AsyncLock()
  _promptTask: Promise<void> | null = null

  // 就绪门闩: run() 的 _init 完成前, chat_message 等输入必须等待,
  // 否则 _session 未建 _onInput 会静默丢消息 (create 后立即 chat 的竞态)
  // 注意顺序: 无初始化器的字段声明会把值重置为 undefined, 必须放前面
  private _readyResolve!: () => void
  private _ready = new Promise<void>(r => { this._readyResolve = r })

  // 流内累计 (prime 前端契约: 同一 messageId 内累计文本)
  _textAcc = ''
  _thinkAcc = ''
  _toolArgs = new Map<string, any>()
  /** full 级 skill 全文块 (init 时收集, 构建 system prompt 时并入). */
  _fullSkillBlocks: string[] = []

  // ── 生命周期 ──

  async run(kwargs: Record<string, any>): Promise<void> {
    const params = kwargs as PiAgentParams
    try {
      await this._init(params)
    } catch (exc: any) {
      log.error(`init failed: ${exc?.message ?? exc}`)
      throw exc
    } finally {
      this._readyResolve()
    }
    // 推初始 usage 事件: 前端首屏立即显示模型名
    if (this._model) {
      await this.emitUsage({ usage: null, epoch: 0, messageId: '' })
    }
    // 历史/系统提示词立即下发: 前端 resume 渲染不等 bridge 注册
    // (注册只是 run_routine 寻址, 与 session 数据无依赖, 串行是纯延迟)
    try {
      await this._emitSessionHistory()
    } catch (exc: any) {
      log.warning(`emit session history failed: ${exc?.message ?? exc}`)
    }
    // fire-and-forget (对齐 py 侧 asyncio.create_task 语义): 注册循环
    // 自带 stopEvent 退出, 不需要框架 task 池托管
    void this._registerWithBridge().catch(e => {
      log.error(`bridge register failed: ${e instanceof Error ? e.message : e}`)
    })
    try {
      await this._stop.wait()
    } finally {
      if (this._promptTask) {
        await this._session?.abort().catch(() => {})
        try { await this._promptTask } catch { /* 收尾异常吞掉 */ }
      }
      this._session?.dispose()
    }
  }

  async stop(): Promise<void> {
    this._stop.set()
    await this._session?.abort().catch(() => {})
  }

  // ── 初始化 ──

  /** resource loader: 信任 workspace + 只暴露 workspace 级 skill.
   *
   * pi 的 skill 注入 system prompt 要求工具面含内置 read (渐进加载
   * SKILL.md 全文); 信任后它还会带入 user 级来源 (~/.agents/skills
   * 等 arkcli 全家桶), 对本 agent 是噪声 ---- 这里过滤掉, 只留
   * workspace/.pi/skills 下我们自己注入的 skill.
   */
  private _settings: SettingsManager | null = null

  private async _buildResourceLoader(): Promise<DefaultResourceLoader> {
    if (this._workspace === null) throw new Error('workspace not initialized')
    const ws = this._workspace
    const sm = SettingsManager.create(ws, undefined, { projectTrusted: true })
    this._settings = sm
    const wsSkillsRoot = join(ws, '.pi', 'skills')
    const loader = new (class extends DefaultResourceLoader {
      override getSkills(): { skills: Skill[]; diagnostics: ReturnType<DefaultResourceLoader['getSkills']>['diagnostics'] } {
        const r = super.getSkills()
        return { ...r, skills: r.skills.filter(s => s.baseDir.startsWith(wsSkillsRoot)) }
      }
    })({
      cwd: ws, agentDir: getAgentDir(), settingsManager: sm,
      // 基座提示词替换为 prime 风格 (IPython 持久控制环境);
      // pi SDK 仍自动附加 skills / AGENTS.md / cwd;
      // full 级预载 skill 全文直接并入 (摘要级走 available_skills 块)
      systemPrompt: buildPrimeSystemPrompt(this._agentId)
        + (this._fullSkillBlocks.length ? '\n\n' + this._fullSkillBlocks.join('\n\n') : ''),
      // 内联扩展: deepseek 内联图片 -> Files API file_id (上传一次, 轮次零重传)
      extensionFactories: [makeFilesApiExtension(join(ws, 'files-api-cache.json'))],
    })
    await loader.reload()
    return loader
  }

  private async _init(params: PiAgentParams): Promise<void> {
    this._agentId = params.agent_id || crypto.randomUUID()
    this._projectRoot = params.project_dir_root_path ?? null
    this._modelRef = params.model || null
    this._protocol = params.protocol || null

    // workspace: 有项目落 <project>/.agents/<agent_id>/, 无项目落全局
    this._workspace = params.project_dir_root_path
      ? join(params.project_dir_root_path, '.agents', this._agentId)
      : join(homedir(), '.zero', 'agent-workspaces', this._agentId)
    mkdirSync(this._workspace, { recursive: true })

    // skill: 预注入清单 (基础 + create 参数 skills) 从共享库拷 —— 库是唯一
    // 真源 (py kernel 的 run_routine 机制同库同名). 库里缺的跳过 (agent 无
    // 该手册可自愈: 库里补上后下次 init 生效); 其余 skill 全靠
    // skill/repo/list / skill/repo/pull 按需取. 等级: summary 只拷入
    // workspace (pi available_skills 摘要块渐进而入), full 另收集全文并入
    // system prompt. 显式传参覆盖基础清单的同名项.
    const skillsDst = join(this._workspace, '.pi', 'skills')
    const wanted = new Map<string, SkillLevel>()
    for (const name of PRELOADED_SKILLS) wanted.set(name, 'summary')
    for (const s of params.skills ?? []) {
      if (s && typeof s.name === 'string' && s.name.trim()) {
        wanted.set(s.name.trim(), s.level === 'full' ? 'full' : 'summary')
      }
    }
    for (const [name, level] of wanted) {
      const src = join(USER_SKILLS_DIR, name, 'SKILL.md')
      if (!existsSync(src)) {
        log.info(`preload skill missing in library, skipped: ${name}`)
        continue
      }
      mkdirSync(join(skillsDst, name), { recursive: true })
      copyFileSync(src, join(skillsDst, name, 'SKILL.md'))
      if (level === 'full') {
        this._fullSkillBlocks.push(
          renderPreloadedSkill(name, readFileSync(src, 'utf-8'), join(USER_SKILLS_DIR, name)),
        )
      }
    }

    // extra_instructions 以 pi 原生方式注入: workspace 的 AGENTS.md
    const extra = (params.extra_instructions ?? '').trim()
    if (extra) {
      writeFileSync(join(this._workspace, 'AGENTS.md'), extra + '\n', 'utf-8')
    }

    // 模型解析: 'provider/model' (model 部分是本地名) 经中立配置 (py
    // models hub req 'get') 解析; ref 缺省时用中立配置的 default ----
    // 两条路径都显式传 model 给 createAgentSession, 不再依赖 pi settings
    // 默认链. 共享缓存 runtime: 配置 hash 未变直接复用, 变化 (save/delete)
    // 才重建, 避免每次 spawn 拖垮 resume/new.
    const shared = await getSharedModels(this)
    // 显式协议时两条路径都带协议解析 (变体 provider 键); 未声明协议由
    // resolveModelRef 抛错 (fail fast, child 起不来即报).
    const proto = this._protocol ?? undefined
    let model: ModelLike | undefined
    if (this._modelRef) {
      model = resolveModelRef(shared, this._modelRef, proto)
      if (!model) {
        throw new Error(`model not found in neutral config: ${this._modelRef}`)
      }
      this._model = model
    } else if (shared.config.default) {
      model = resolveModelRef(shared, shared.config.default, proto)
      if (model) this._model = model
    }

    // 会话持久化: 每 agent 一个 jsonl 文件, resume 时 pi 回放
    const sessionDir = join(this._workspace, 'pi-sessions')
    const sessionManager = SessionManager.create(this._workspace, sessionDir)
    sessionManager.setSessionFile(join(sessionDir, this._agentId + '.jsonl'))
    this._sessionManager = sessionManager

    const { session, modelFallbackMessage } = await createAgentSession({
      cwd: this._workspace,
      model: model as CreateAgentSessionOptions['model'],
      modelRuntime: shared.runtime,
      // 工具面: 只有 ipython (prime 理念: 一切经 kernel; 图像经 display 捕获
      // 进 toolResult 的 image block); skill 渐进加载指定经 ipython 读
      // SKILL.md (pi 支持 skillFileReadTool 覆盖默认 read/bash 判定)
      tools: ['ipython'],
      skillFileReadTool: 'ipython',
      customTools: [
        makeIpthonTool({
          agentId: this._agentId,
          call: (name, kwargs, signal) => this.callStoppable(name, kwargs, signal),
        }),
      ],
      sessionManager,
      resourceLoader: await this._buildResourceLoader(),
    })
    this._session = session
    if (modelFallbackMessage) {
      log.warning(modelFallbackMessage)
    }
    if (!session.agent.state.model) {
      throw new Error(`no model available for pi agent (ref: ${this._modelRef})`)
    }
    this._model = session.agent.state.model as ModelLike
    this._session!.subscribe((event) => { void this._onPiEvent(event) })
  }

  // ── pi 事件 -> 前端事件翻译 ──

  private async _onPiEvent(event: AgentSessionEvent): Promise<void> {
    const session = this._session
    if (!session) return
    const epoch = this._epoch
      const messageId = this._messageId
      try {
        switch (event.type) {
          case 'message_start': {
            // 新 assistant 消息: 累计器清零 + 换 messageId (取 pi 消息 id).
            // 前端按 messageId 分条渲染 ---- turn 内每条消息独立成块,
            // 否则后续消息会拼进前一条且工具反馈后重复重画.
            this._textAcc = ''
            this._thinkAcc = ''
            const mid = (event.message as any)?.id
            this._messageId = typeof mid === 'string' && mid ? mid : crypto.randomUUID()
            break
          }
          case 'message_update': {
            // epoch 漂移 = 该消息已被打断, 陈旧流式帧不进管道 (否则多级缓冲
            // 积压的 delta 会在打断后继续涌向前端, 表现为"停不下来")
            if (this._epoch !== this._msgEpoch) break
            const ev = event.assistantMessageEvent as AssistantEventLite
          // 流式发增量(非累计全文): 长回复的 O(n²) 事件流量会打爆 out_q
          // 并拖慢 kernel 串行 dispatch, 前端 mergeStreamText 兼容两种,
          // final 帧仍发累计全文兜底.
          if (ev?.type === 'text_delta' && ev.delta) {
            this._textAcc += ev.delta
            await this.emitText(ev.delta, epoch, messageId, false, false)
          } else if (ev?.type === 'thinking_delta' && ev.delta) {
            this._thinkAcc += ev.delta
            await this.emitText(ev.delta, epoch, messageId, false, true)
          }
          break
        }
        case 'message_end': {
          // usage 快照 (每 assistant 消息一次)
          const usage = (event.message as any)?.usage
          if (usage) {
            await this.emitUsage({ usage, epoch, messageId })
          }
          break
        }
        case 'tool_execution_start':
          this._toolArgs.set(event.toolCallId, event.args)
          await this.emitFeedback('', [{
            name: event.toolName,
            input: event.args ?? null,
            result: null,
            rid: null,
            call_id: event.toolCallId,
            status: 'running',
          }], epoch, messageId)
          break
        case 'tool_execution_end': {
          const result = event.result as any
          const blocks = Array.isArray(result?.content) ? result.content : []
          const text = blocks.filter((c: any) => c?.type === 'text').map((c: any) => c.text).join('')
          // display 捕获的图像附件: 落盘 /files 路径, 前端 jupyter cell Out 渲染
          const imgBlocks = blocks.filter((b: any) => b?.type === 'image' && b?.data)
          let images: string[] | undefined
          if (imgBlocks.length) {
            try {
              images = await Promise.all(imgBlocks.map(
                (b: any) => this._persistImage(b.data, b.mimeType || 'image/png')))
            } catch (exc: any) {
              log.warning(`persist tool image failed: ${exc?.message ?? exc}`)
            }
          }
          await this.emitFeedback('', [{
            name: event.toolName,
            input: this._toolArgs.get(event.toolCallId) ?? null,
            result: text,
            rid: null,
            call_id: event.toolCallId,
            status: event.isError ? 'error' : 'done',
            images,
          }], epoch, messageId)
          this._toolArgs.delete(event.toolCallId)
          break
        }
        case 'agent_start':
          // turn 开始: 状态机 running (无文本不显示, 流式指示器负责常规展示)
          await this.emitStatus('running', '')
          break
        case 'compaction_start':
          // SDK 自动压缩开始: 一等状态事件 (前端状态条), 不再伪装工具项
          await this.emitStatus('compacting', `正在压缩上下文 (${event.reason})…`)
          break
        case 'compaction_end': {
          // 结束: 回到 running (压缩发生在 turn 中段), 结果短停留由前端清除
          const res = event.result as { tokensBefore?: number; estimatedTokensAfter?: number } | undefined
          const text = event.errorMessage
            ? `压缩失败: ${event.errorMessage}`
            : event.aborted
              ? '压缩已中止'
              : `上下文已压缩: ${res?.tokensBefore ?? '?'} -> ${res?.estimatedTokensAfter ?? '?'} tokens (est)`
          await this.emitStatus('running', text)
          break
        }
        case 'agent_settled':
          // 整轮结束: 收尾信号清除前端 streaming 状态
          await this.emitStatus('idle', '')
          await this.emitText(this._textAcc, epoch, messageId, true)
          break
        default:
          break
      }
    } catch (exc: any) {
      log.warning(`event dispatch failed: ${exc?.message ?? exc}`)
    }
  }

  // ── 事件处理 ──

  /** 工具桥的可停 call: submit → start → wait, abort 触发时 stop 子 routine.
   *  py 侧 ipython 的 stop hook 会 interrupt_kernel, 正在跑的 cell 在执行点
   *  抛 KeyboardInterrupt → run() 收敛 → handle.wait() 快速返回. */
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

  async _onInput(_source: RoutineSource, data: any): Promise<void> {
    const text = String(data?.text ?? '')
    const images: string[] = Array.isArray(data?.images) ? data.images : []
    if ((!text && !images.length)) return
    // init 完成前必须等: _session 未建时继续走会静默丢消息
    await this._ready
    if (!this._session) return
    const fromAgent = String(data?.from || USER_SOURCE)
    let content = wrapFrom(text, fromAgent)
    if (images.length && !content) content = '[image]'

    // 阶段1: 锁内 bump epoch + abort 在飞 prompt 并摘取 task.
    // 收敛等待必须放锁外: prompt settle 要经 _onPiEvent (同一把锁),
    // 锁内 await = 自死锁 ---- 打断/后续输入永久排队.
    let prev: Promise<void> | null = null
    await this._eventLock.run(async () => {
      this._epoch += 1
      if (this._promptTask) {
        log.info('input: abort in-flight prompt')
        await this._session!.abort().catch(() => {})
        prev = this._promptTask
        this._promptTask = null
      }
    })
    if (prev) {
      try { await prev } catch { /* 收尾异常吞掉 */ }
      log.info('input: in-flight prompt converged')
    }

    // 阶段2: 锁内注入新 prompt.
    await this._eventLock.run(async () => {
      if (fromAgent !== USER_SOURCE) {
        await this.emitIncomingMessage(content, fromAgent)
      }
      const epoch = this._epoch
      const messageId = crypto.randomUUID()
      this._messageId = messageId
      this._msgEpoch = epoch
      this._textAcc = ''
      this._thinkAcc = ''
      await this.emitSysPrompt(epoch, messageId, this.convToPromptMessages())
      try {
        const piImages = await Promise.all(images.map(toPiImage))
        const task = this._session!.prompt(
          content, piImages.length ? { images: piImages } : undefined)
        this._promptTask = task
        // run 结束即清引用: 否则 stale 的 _promptTask 会让 interrupt
        // 误报 in-flight 并对空闲 session 做无意义 abort (0ms done)
        task.then(
          () => { if (this._promptTask === task) this._promptTask = null },
          (exc: any) => {
            log.error(`prompt crashed: ${exc?.message ?? exc}`)
            if (this._promptTask === task) this._promptTask = null
          },
        )
      } catch (exc: any) {
        log.error(`resolve images failed: ${exc?.message ?? exc}`)
        this._promptTask = null
        return
      }
    })
  }

  async _onInterrupt(_source: RoutineSource, _data: any): Promise<void> {
    const t0 = Date.now()
    await this._eventLock.run(async () => {
      this._epoch += 1
      // lock_wait 大 → _eventLock 被占 (_onInput 等 prompt 收敛卡死, 死锁信号);
      // streaming=false 但 promptTask 在 → run 已结束, 前端仍在放积压帧
      const streaming = !!(this._session as any)?.isStreaming
      log.info(`interrupt: epoch=${this._epoch} lock_wait=${Date.now() - t0}ms `
        + `streaming=${streaming} prompt=${this._promptTask ? 'in-flight' : 'idle'}`)
      if (streaming || this._promptTask) {
        const t1 = Date.now()
        await this._session?.abort().catch(() => {})
        log.info(`interrupt: session.abort() done in ${Date.now() - t1}ms`)
      }
    })
  }

  // ── request handlers ──

  @request('agent_state')
  async onAgentState(_source: RoutineSource, _data: any): Promise<any> {
    return {
      agent_id: this._agentId,
      session_id: this._sessionManager?.getSessionId() ?? this._agentId,
      project_root: this._projectRoot,
      skill_dir: this._workspace ? join(this._workspace, '.pi', 'skills') : null,
      plan_mode: false,
      model: this._modelRef,
    }
  }

  @request('run')
  async onRun(_source: RoutineSource, data: any): Promise<any> {
    /** HTTP 转发的 routine 调用. 注入 fromAgentId 后 call 目标 routine. */
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

  @request('chat_message')
  async onChatMessage(source: RoutineSource, data: any): Promise<any> {
    /** 单向异步: 高优先级立即投递打断当前轮; 低优先级排队等 idle. */
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

  /** 等当前 prompt 完成 (idle) 后再投递, 不打断在飞轮次. */
  private async _deliverAgentMessage(source: RoutineSource, data: any): Promise<void> {
    while (this._promptTask) {
      try { await this._promptTask } catch { /* ignore */ }
      await new Promise(r => setTimeout(r, 50))
    }
    await this._onInput(source, data)
  }

  @request('get_history')
  async onGetHistory(_source: RoutineSource, _data: any): Promise<any> {
    const messages = await this._piMessagesToChat()
    return {
      ok: true,
      session_id: this._sessionManager?.getSessionId() ?? this._agentId,
      messages,
      usage: this._usagePayload(this._lastUsage()),
    }
  }

  @request('interrupt')
  async onInterruptReq(source: RoutineSource, data: any): Promise<any> {
    void this._onInterrupt(source, data ?? {}).catch(() => {})
    return { ok: true }
  }

  @request('set_effort')
  async onSetEffort(_source: RoutineSource, data: any): Promise<any> {
    let effort = data?.effort
    if (effort === 'none' || effort === '') effort = null
    const level = effort === 'high' || effort === 'medium' || effort === 'low'
      ? effort
      : 'off'
    try {
      this._session?.setThinkingLevel(level)
    } catch (err) {
      return { ok: false, error: String((err as Error).message ?? err) }
    }
    return { ok: true, effort: level === 'off' ? null : level }
  }

  // ── emit (前端事件发布, 与 ReactorAgent 协议一致) ──

  async emitText(
    text: string, epoch: number, messageId: string,
    isFinal = false, isThinking = false,
  ): Promise<void> {
    if (!text && !isFinal) return
    const data = {
      text, is_final: isFinal, is_thinking: isThinking,
      epoch, message_id: messageId, agent_id: this._agentId,
      ts: Date.now(),  // 源头时间戳: 排查管道积压 (probe 对比接收时刻)
    }
    await this.publish('assistant_output', data)
    await this.publish('assistant_output', data, this._agentId)
  }

  async emitFeedback(
    content: string, results: any[],
    epoch: number, messageId: string,
  ): Promise<void> {
    const data = {
      content, results,
      epoch, message_id: messageId, agent_id: this._agentId,
    }
    await this.publish('feedback', data)
    await this.publish('feedback', data, this._agentId)
  }

  /** 一等瞬态状态 (running/compacting/idle 状态机): 前端状态条, 不进消息流. */
  async emitStatus(
    phase: 'running' | 'compacting' | 'idle', text: string,
  ): Promise<void> {
    const data = { phase, text, agent_id: this._agentId, ts: Date.now() }
    await this.publish('agent_status', data)
    await this.publish('agent_status', data, this._agentId)
  }

  async emitSysPrompt(
    epoch: number, messageId: string, messages: any[],
  ): Promise<void> {
    const data = {
      epoch, message_id: messageId,
      messages, agent_id: this._agentId,
    }
    await this.publish('sys_prompt', data)
    await this.publish('sys_prompt', data, this._agentId)
  }

  /** pi agent-session 同款 effort 解析链 (settings 优先 -> session -> medium). */
  private _currentEffort(): string {
    return this._settings?.getDefaultThinkingLevel()
      ?? this._session?.thinkingLevel ?? 'medium'
  }

  /** 会话最后一条 assistant 的 usage (无则 null): resume 回填真实 token 数用. */
  private _lastUsage(): Record<string, any> | null {
    const msgs = this._piMessages()
    for (let i = msgs.length - 1; i >= 0; i--) {
      const usage = (msgs[i] as any)?.usage
      if (usage && typeof usage === 'object') return usage
    }
    return null
  }

  /** 统一 usage 载荷 (前端 applyUsage 形状). usage 为 null 时 token 数全 0,
   * 供 resume/重连回填 (session_changed / get_history) 复用 ---- 缺 agent_id
   * 的残缺载荷会被 applyUsage 直接丢弃, effort 下拉框因此不显示. */
  private _usagePayload(usage: Record<string, any> | null): Record<string, unknown> {
    const model = this._model
    let inputTokens = 0
    let outputTokens = 0
    let totalTokens = 0
    let cacheRead = 0
    let cacheWrite = 0
    const maxContext = model?.contextWindow ?? 0
    if (usage) {
      // pi Usage: input/output/total (+ cacheRead/cacheWrite)
      inputTokens = Number(usage.input ?? usage.input_tokens ?? usage.prompt_tokens ?? 0)
      outputTokens = Number(usage.output ?? usage.output_tokens ?? usage.completion_tokens ?? 0)
      totalTokens = Number(usage.total ?? usage.total_tokens ?? (inputTokens + outputTokens))
      cacheRead = Number(usage.cacheRead ?? usage.cache_read ?? 0)
      cacheWrite = Number(usage.cacheWrite ?? usage.cache_write ?? 0)
      // pi 的 input 只算未命中部分 (total = input + cacheRead + output);
      // 上下文占用 (圆环分子) 应含命中缓存的 token
      inputTokens += cacheRead
    }
    const triggerTokens = maxContext * 0.5
    const percent = triggerTokens > 0
      ? Math.round((inputTokens / triggerTokens) * 1000) / 10 : 0.0
    return {
      input_tokens: inputTokens, output_tokens: outputTokens,
      total_tokens: totalTokens, max_context: maxContext, percent,
      cache_read: cacheRead, cache_write: cacheWrite,
      agent_id: this._agentId,
      // model_key 用配置键 (user-facing ref); 显式协议时 model.provider 是
      // 变体键 (deepseek@openai-completions), 不进 UI 展示面
      model_key: this._modelRef || (model ? model.provider + '/' + model.id : ''),
      model_name: model?.name ?? model?.id ?? '',
      // 对齐 pi agent-session 的 effort 解析链(settings 优先):
      // 未显式设置时 session.thinkingLevel 为 undefined,
      // 不补默认会误显为 off.
      reasoning_effort: this._currentEffort(),
    }
  }

  async emitUsage(opts: {
    usage: Record<string, any> | null
    epoch: number
    messageId: string
  }): Promise<void> {
    const data = {
      ...this._usagePayload(opts.usage),
      epoch: opts.messageId ? opts.epoch : 0, message_id: opts.messageId,
    }
    await this.publish('usage', data)
    await this.publish('usage', data, this._agentId)
  }

  async emitIncomingMessage(text: string, fromAgent: string): Promise<void> {
    const data = { text, from: fromAgent, agent_id: this._agentId }
    await this.publish('incoming_message', data)
    await this.publish('incoming_message', data, this._agentId)
  }

  // ── 消息翻译 (pi AgentMessage -> 前端 ChatMessage) ──

  private _piMessages(): any[] {
    if (!this._sessionManager) return []
    try {
      return (this._sessionManager as any).buildSessionContext().messages ?? []
    } catch {
      return []
    }
  }

/** base64 图 -> 幂等落盘(内容 sha 命名) -> '/files/<name>' 路径引用.
   * 历史回填不再内联 data URL: 16 图会话一次刷新产生 6MB+ JSON,
   * 链路上多次序列化且 kernel read loop 的同步 json.loads 直接
   * 阻塞 event loop (前端 /models 等 coroutine 全部排队). */
  private async _persistImage(b64: string, mimeType: string): Promise<string> {
    const buf = Buffer.from(b64, 'base64')
    const sha = createHash('sha256').update(buf).digest('hex').slice(0, 16)
    const ext = MIME_EXT[mimeType.split(';')[0]] ?? 'png'
    const name = `${sha}.${ext}`
    const file = join(UPLOADS_DIR, name)
    if (!existsSync(file)) {
      await mkdir(UPLOADS_DIR, { recursive: true })
      await writeFile(file, buf)
    }
    return `/files/${name}`
  }

  async _piMessagesToChat(): Promise<ChatMessage[]> {
    const out: ChatMessage[] = []
    let seq = 0
    // 当前 assistant 的 toolCall 批次 + 已开的 tool 块
    let pending: { name: string; input: string; callId: string }[] = []
    let toolBlock: ChatMessage | null = null

    const newBlock = (): ChatMessage => {
      const block: ChatMessage = {
        id: 'tool-' + seq, role: 'tool', text: '', results: [], final: true,
      }
      seq += 1
      out.push(block)
      return block
    }

    for (const m of this._piMessages()) {
      if (m?.role === 'user') {
        pending = []
        toolBlock = null
        // pi 会话的 user content 可能是字符串或块数组
        // (text/image, 统一消息协议后图文同发写入为数组).
        const blocks = Array.isArray(m.content) ? m.content : []
        const text = typeof m.content === 'string' ? m.content
          : blocks
            .filter((b: any) => b?.type === 'text')
            .map((b: any) => b.text ?? '')
            .filter((t: string) => t)
            .join('\n')
        // image block (base64) -> 幂等落盘 -> '/files/<sha>.<ext>' 路径
        const images = await Promise.all(
          blocks
            .filter((b: any) => b?.type === 'image' && b?.data)
            .map((b: any) => this._persistImage(b.data, b.mimeType || 'image/png')),
        )
        if (text || images.length) {
          out.push({ id: 'hist-user-' + seq, role: 'user', text, final: true, images })
          seq += 1
        }
      } else if (m?.role === 'assistant') {
        pending = []
        toolBlock = null
        const blocks = Array.isArray(m.content) ? m.content : []
        const text = blocks
          .filter((b: any) => b?.type === 'text')
          .map((b: any) => b.text ?? '')
          .join('')
        if (text) {
          out.push({ id: 'hist-assistant-' + seq, role: 'assistant', text, final: true })
          seq += 1
        }
        const calls = blocks.filter((b: any) => b?.type === 'toolCall')
        if (calls.length) {
          pending = calls.map((b: any) => ({
            name: String(b.name ?? ''),
            // pi 会话 toolCall 块的参数字段是 arguments (对象), 而非 input
            input: JSON.stringify(b.arguments ?? {}),
            callId: String(b.id ?? ''),
          }))
        }
      } else if (m?.role === 'toolResult') {
        if (!pending.length) continue
        if (!toolBlock) toolBlock = newBlock()
        const blocks = Array.isArray(m.content) ? m.content : []
        const output = blocks
          .filter((b: any) => b?.type === 'text')
          .map((b: any) => b.text ?? '')
          .join('')
        // image block (display 捕获) -> 幂等落盘 -> /files 路径, 前端 Out 渲染
        const imgPaths = await Promise.all(
          blocks
            .filter((b: any) => b?.type === 'image' && b?.data)
            .map((b: any) => this._persistImage(b.data, b.mimeType || 'image/png')),
        )
        const slot = pending.find(p => p.callId === String(m.toolCallId ?? '')) ?? pending[0]
        toolBlock.results!.push({
          name: slot?.name ?? String(m.toolName ?? ''),
          input: safeJson(slot?.input),
          result: output,
          call_id: String(m.toolCallId ?? ''),
          status: m.isError ? 'error' : 'done',
          images: imgPaths.length ? imgPaths : undefined,
        })
      }
    }
    // 未回填的 pending (被打断的轮次): 无 output, 不展示
    return out
  }
  convToPromptMessages(): Record<string, any>[] {
    const msgs: Record<string, any>[] = []
    for (const m of this._piMessages()) {
      if (m?.role === 'user') {
        msgs.push({ role: 'user', content: typeof m.content === 'string' ? m.content : '' })
      } else if (m?.role === 'assistant') {
        const blocks = Array.isArray(m.content) ? m.content : []
        const text = blocks
          .filter((b: any) => b?.type === 'text')
          .map((b: any) => b.text ?? '')
          .join('')
        if (text) msgs.push({ role: 'assistant', content: text })
      }
    }
    return msgs
  }

  // ── bridge 注册 ──

  async _registerWithBridge(): Promise<void> {
    // onSuccess 已移除: session_changed 在 run() 里 _init 后立即下发,
    // 不再等注册完成 (两者无数据依赖, 串行只会拖慢前端 resume 渲染)
    await registerWithBridge({
      agentId: this._agentId,
      routineId: this.id ?? '',
      namePrefix: 'Pi-',
      bridgeName: (this.constructor as typeof Routine & { BRIDGE_NAME: string }).BRIDGE_NAME,
      ctx: this,
      stopEvent: this._stop,
      logger: log,
    })
  }

  private async _emitSessionHistory(): Promise<void> {
    const messages = await this._piMessagesToChat()
    await this.publish('session_changed', {
      agent_id: this._agentId,
      session_id: this._sessionManager?.getSessionId() ?? this._agentId,
      is_new: messages.length === 0,
      messages,
      usage: this._usagePayload(this._lastUsage()),
    })
    await this.emitSysPrompt(this._epoch, crypto.randomUUID(), this.convToPromptMessages())
  }
}

/** best-effort parse a tool arguments string into a dict; pass through on fail. */
function safeJson(raw: any): any {
  if (raw && typeof raw === 'object') return raw
  if (typeof raw !== 'string' || !raw.trim()) return {}
  try {
    const parsed = JSON.parse(raw)
    return (parsed && typeof parsed === 'object') ? parsed : {}
  } catch { return {} }
}





