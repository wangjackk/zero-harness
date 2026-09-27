/** PiAgentManager -- resident passive routine that spawns PiAgent children.
 *
 * 结构对齐 PrimeAgentManager: create/resume/stop/list/delete;
 * 差异: 不走 preset 体系, spawn 目标是 pi_agent (pi 引擎), 会话持久化
 * 由 pi SessionManager 接管 (workspace/pi-sessions/<agent_id>.jsonl).
 */
import { resolve } from 'node:path'
import { z } from 'zod'

import { Routine, request } from 'routine-ts'
import type { RoutineSource } from 'routine-ts'
import { AsyncEvent, sleep } from 'routine-ts/async'
import { setupLogger } from 'routine-ts/logger'

import { jsonSchema } from '../../toolSchema'
import { getStore } from '../_core/store'
import { getSharedModels, resolveModelRef } from '../_core/modelRuntime'

const log = setupLogger('pi.manager')

const AGENT_ROUTINE_NAME = 'pi/agent'
const MANAGER_NAME = 'pi/agent/manager'
const PRESET_TAG = 'pi'
const TYPES_HUB_NAME = 'agent_hub'

/** agent 类型 manifest: manager 启动时主动 register 到 agent_hub
 * (hub 纯被动, 不扫描 catalog), 前端经 agent_hub/types 查类型清单动态渲染创建表单. */
const PI_AGENT_MANIFEST = {
  kind: 'pi',
  label: 'Pi Agent',
  description: 'pi 引擎 coding agent: 会话循环/上下文压缩/多模型层由 pi AgentSession 托管',
  can_resume: true,
  uses_model: true,
  uses_skills: true,
  routines: {
    create: 'pi/agent/create',
    list: 'pi/agent/list',
    stop: 'pi/agent/stop',
    resume: 'pi/agent/resume',
    delete: 'pi/agent/delete',
  },
  create_fields: [
    { name: 'agent_id', label: 'Agent ID', type: 'text', required: false, placeholder: 'auto-generated' },
    { name: 'project_dir', label: '项目目录', type: 'text', required: false, placeholder: '空 = 全局 workspace', autocomplete: 'project_dir' },
    { name: 'extra_instructions', label: '附加指令', type: 'textarea', required: false, placeholder: '写入 AGENTS.md 的额外系统指令' },
  ],
}

interface LiveInfo {
  handle: import('routine-ts/handle').RoutineHandle
  projectDir: string | null
  sessionId: string
}

/** 协议参数清洗: 空串/空白 -> null (未显式指定). */
function cleanProtocol(value: any): string | null {
  const s = String(value ?? '').trim()
  return s || null
}

export class PiAgentManager extends Routine {
  static name = 'pi/agent/manager'

  /** resident passive manager: dynamically create/list/stop PiAgent children. */
  static isPassive = true
  static meta: Record<string, any> = {
    hidden: true,
    description: (
      'Resident manager for PiAgent coding agents (pi engine). Spawns/lists/stops '
      + 'PiAgent child instances on request. Conversation state persists via pi '
      + 'session files; each child runs concurrently, isolated by agent_id.'
    ),
  }

  private _stop = new AsyncEvent()
  private _manifestTask: Promise<void> = Promise.resolve()
  private _agents = new Map<string, LiveInfo>()
  private _stopping = new Set<string>()

  async onStarted(): Promise<void> {
    log.info('pi agents manager started')
  }

  async run(_kwargs: Record<string, any>): Promise<void> {
    // loop 挂引用 + stop 时 cancel: 实例被替换而 stop() 未被调用时,
    // 泄漏的旧 loop 会拿死 rid 覆盖 hub 注册表 (hub 侧也有一道拒绝防御).
    this._manifestTask = this._manifestLoop()
    await this._stop.wait()
    this._manifestTask.cancel?.()
  }

  /** 按名查 running routine 的 rid (单次; 竞态重试在调用方). */
  private async _findTypesHubRid(): Promise<string | null> {
    const routines = await this.getRunningRoutines()
    for (const r of routines) {
      if (String(r.name ?? '') === TYPES_HUB_NAME) {
        const rid = String(r.id ?? '').trim()
        if (rid) return rid
      }
    }
    return null
  }

  /** 周期 ping agent_hub (10s), 不通就重新 find + register.
   * agent_hub 实例重建 (kernel 重启 / 热载) 会换 rid 且内存注册表清零,
   * 一次性注册 (成功即止) 对此不感知 ---- 跨进程的 ts manager 尤甚.
   * 常态一次轻 ping 探活, 失联即回到 find+register 快速重试.
   * ping 通的同时对账 live agents: kernel 重启后子 agent 的 handle
   * 不一定 isDone (grpc 短断重连不 invalidate handle), 但 kernel 里
   * routine 已没了 ---- rid 不在 running 列表 = stale live, 清掉
   * (否则 resume 被 "already live" 拒, 发消息又 "not live", 死锁). */
  private async _manifestLoop(): Promise<void> {
    let lastRid: string | null = null
    while (!this._stop.isSet) {
      let interval = 10_000
      try {
        if (lastRid) {
          await this.req(lastRid, 'ping', {}, 2.0)
          await this._reconcileAgents()
        } else {
          const rid = await this._findTypesHubRid()
          if (rid) {
            await this.req(rid, 'register', PI_AGENT_MANIFEST, 5.0)
            lastRid = rid
            log.info(`agent manifest registered (kind: ${PI_AGENT_MANIFEST.kind})`)
          }
        }
      } catch (exc: any) {
        log.warning(`agent_hub ping/register retry: ${exc?.message ?? exc}`)
        lastRid = null
      }
      if (!lastRid) interval = 500
      await sleep(interval)
    }
  }

  async stop(): Promise<void> {
    // 注销 (尽力而为; hub 已停则静默放弃)
    void this._unregisterManifest()
    for (const [agentId, info] of [...this._agents]) {
      try {
        if (!info.handle.isDone()) await info.handle.stop({ fire: true })
      } catch (exc: any) {
        log.warning(`stop child ${agentId} on manager stop: ${exc?.message ?? exc}`)
      }
    }
    this._agents.clear()
    this._stop.set()
    this._manifestTask.cancel?.()
  }

  private async _unregisterManifest(): Promise<void> {
    try {
      const rid = await this._findTypesHubRid()
      if (rid) {
        await this.req(rid, 'unregister', { kind: PI_AGENT_MANIFEST.kind }, 3.0)
      }
    } catch {
      // 市场已停: 静默放弃
    }
  }

  /** live agents 对账: rid 不在 kernel running 列表的行是 stale live
   * (kernel 重启 / routine 被杀), 清出 live map ---- store 持久行不动,
   * 前端看到 stopped, resume 重新可用. */
  private async _reconcileAgents(): Promise<void> {
    if (this._agents.size === 0) return
    const running = await this.getRunningRoutines()
    const rids = new Set(running.map(r => String(r.id ?? '')))
    for (const [agentId, info] of [...this._agents]) {
      if (rids.has(String(info.handle.id))) continue
      log.warning(
        `agent ${agentId} rid ${info.handle.id} not running (kernel restart?); dropping stale live entry`,
      )
      void info.handle.stop({ fire: true }).catch(() => {})
      this._agents.delete(agentId)
    }
  }

  /** 活性感知取实例: handle 因 transport 断线作废的 stale
   * entry 视为不存在(立即清除). 否则 kernel 重启后
   * 出现 stale live + 新旧 rid 撞号串台. */
  private _live(agentId: string): LiveInfo | null {
    const info = this._agents.get(agentId)
    if (info === undefined) return null
    if (info.handle.isDone()) {
      log.warning(`agent ${agentId} handle dead (kernel restart?); clearing stale entry`)
      this._agents.delete(agentId)
      return null
    }
    return info
  }

  // ------------------------------------------------------------------
  // req handlers
  // ------------------------------------------------------------------

  @request('create_agent')
  async onCreate(_source: RoutineSource, data: any): Promise<any> {
    data = data ?? {}
    const store = this._storeFor()
    let agentId = String(data.agent_id || '').trim()
    if (agentId) {
      if (store.getAgent(agentId) !== null) {
        return { ok: false, error: `agent_id ${agentId} already exists; use resume instead` }
      }
    } else {
      agentId = this._nextAgentId(store)
    }
    if (this._live(agentId) !== null) {
      return { ok: false, error: `agent_id ${agentId} already live` }
    }
    const sessionId = crypto.randomUUID().replaceAll('-', '')
    const model = data.model || null  // 缺省交给 pi 解析
    const protocol = cleanProtocol(data.protocol)
    const result = await this._spawnChild(agentId, data, sessionId, false, model, protocol)
    // spawn 成功后才持久化: 预检失败 (未知 model / 未声明协议) 不留脏记录
    // (对齐 py manager 的修复). child 侧对 agents 表只 UPDATE 不 INSERT.
    if (result.ok) {
      try {
        store.registerAgent(agentId, { sessionId, model, protocol, preset: PRESET_TAG })
      } catch (exc: any) {
        log.warning(`persist agent ${agentId} failed: ${exc?.message ?? exc}`)
      }
    }
    return result
  }

  @request('resume_agent')
  async onResume(_source: RoutineSource, data: any): Promise<any> {
    data = data ?? {}
    const agentId = String(data.agent_id || '').trim()
    if (!agentId) return { ok: false, error: 'agent_id is required' }
    if (this._live(agentId) !== null) {
      return { ok: false, error: `agent_id ${agentId} already live` }
    }
    const store = this._storeFor()
    const agentRec = store.getAgent(agentId)
    if (agentRec === null) {
      return { ok: false, error: `agent_id ${agentId} not found` }
    }
    // model / protocol 优先级: 显式传参 > agent 记录持久化值; 都缺省时
    // model 交给 pi (resume 时 pi 还会从 session 文件自行恢复 model),
    // protocol 走矩阵推导.
    const model = data.model || agentRec.model || null
    const protocol = cleanProtocol(data.protocol) || agentRec.protocol || null
    const sessionId = String(agentRec.session_id || '') || crypto.randomUUID().replaceAll('-', '')
    return await this._spawnChild(agentId, data, sessionId, true, model, protocol)
  }

  private async _spawnChild(
    agentId: string, data: any, sessionId: string,
    isResume: boolean, model: string | null, protocol: string | null,
  ): Promise<any> {
    const projectDir = data.project_dir || data.project_dir_root_path || null
    const extraInstructions = data.extra_instructions || null
    // 初始 skill 预载: [{name, level: 'full' | 'summary'}], 非法项丢弃
    const skills = Array.isArray(data.skills)
      ? data.skills
          .filter((s: any) => s && typeof s.name === 'string' && s.name.trim())
          .map((s: any) => ({
            name: s.name.trim(),
            level: s.level === 'full' ? 'full' as const : 'summary' as const,
          }))
      : null

    const childKwargs: Record<string, any> = {
      agent_id: agentId,
      project_dir_root_path: projectDir,
      model,
      protocol,
      extra_instructions: extraInstructions,
      skills,
      session_id: sessionId,
    }
    // 预检 (对齐 py 的 spawn 前拦截): model + protocol 组合必须先在配置矩阵
    // 声明, 否则 spawn 直接返回错误而不是起一个坏 child. 共享缓存, 无
    // 额外网络开销.
    if (protocol) {
      try {
        const shared = await getSharedModels(this)
        const ref = model || shared.config.default
        if (!ref || !resolveModelRef(shared, ref, protocol)) {
          throw new Error(`model not found in neutral config: ${ref}`)
        }
      } catch (exc: any) {
        const action = isResume ? 'resume' : 'create'
        log.error(`${action} pi agent ${agentId} failed: ${exc?.message ?? exc}`)
        return { ok: false, error: String(exc?.message ?? exc) }
      }
    }
    let handle
    try {
      handle = await this.submit(AGENT_ROUTINE_NAME, childKwargs)
      await handle.start()
    } catch (exc: any) {
      log.error(`spawn pi agent ${agentId} failed: ${exc?.message ?? exc}`)
      return { ok: false, error: String(exc?.message ?? exc) }
    }

    this._agents.set(agentId, { handle, projectDir, sessionId })
    const action = isResume ? 'resumed' : 'created'
    log.info(
      `${action} pi agent: agent_id=${agentId} routine_id=${handle.id} `
      + `model=${model} project_dir=${projectDir}`,
    )
    return { ok: true, agent_id: agentId, routine_id: handle.id, session_id: sessionId }
  }

  @request('live_agents')
  async onLiveAgents(_source: RoutineSource, _data: any): Promise<any> {
    return {
      agents: [...this._agents.entries()]
        .filter(([, info]) => !info.handle.isDone())
        .map(([agentId, info]) => ({ agent_id: agentId, routine_id: info.handle.id })),
    }
  }

  @request('list_agents')
  async onList(_source: RoutineSource, data: any): Promise<any> {
    data = data ?? {}
    const store = this._storeFor()
    let rows = store.listAgents()

    const filterPd = data.project_dir
    if (filterPd) {
      const filterResolved = resolve(String(filterPd))
      rows = rows.filter(r => {
        const info = this._agents.get(r.agent_id)
        return info?.projectDir != null && resolve(info.projectDir) === filterResolved
      })
    }

    const items: Record<string, any>[] = []
    for (const row of rows) {
      const agentId = row.agent_id ?? ''
      // 归属声明: 共享 agents 库的行归属 tag (py prime 侧写 'prime'),
      // 只认自己的行, 不猜前缀.
      if (row.preset !== PRESET_TAG) continue
      const info = this._live(agentId)
      const live = info !== null
      const handle = info?.handle
      items.push({
        agent_id: agentId,
        preset: PRESET_TAG,
        model: row.model ?? null,
        protocol: row.protocol ?? null,
        title: row.title ?? null,
        created_at: row.created_at,
        updated_at: row.updated_at,
        session_id: info?.sessionId ?? null,
        project_dir: info?.projectDir ?? null,
        routine_id: handle ? handle.id : null,
        status: live ? 'live' : 'stopped',
        live,
        started: live,
        done: handle ? handle.isDone() : true,
      })
    }
    return { agents: items }
  }

  @request('stop_agent')
  async onStop(_source: RoutineSource, data: any): Promise<any> {
    data = data ?? {}
    const agentId = String(data.agent_id || '').trim()
    if (!agentId) return { ok: false, error: 'agent_id is required' }
    const info = this._live(agentId)
    if (info === null) {
      return { ok: false, error: `agent_id ${agentId} not live` }
    }
    if (this._stopping.has(agentId)) {
      return { ok: true, agent_id: agentId }
    }
    this._stopping.add(agentId)
    try {
      if (!info.handle.isDone()) await info.handle.stop()
      this._agents.delete(agentId)
      log.info(`stopped pi agent: agent_id=${agentId}`)
      return { ok: true, agent_id: agentId }
    } catch (exc: any) {
      log.warning(`stop pi agent ${agentId} failed: ${exc?.message ?? exc}`)
      return { ok: false, error: String(exc?.message ?? exc) }
    } finally {
      this._stopping.delete(agentId)
    }
  }

  @request('delete_agent')
  async onDelete(_source: RoutineSource, data: any): Promise<any> {
    data = data ?? {}
    const agentId = String(data.agent_id || '').trim()
    if (!agentId) return { ok: false, error: 'agent_id is required' }
    if (this._live(agentId) !== null) {
      return { ok: false, error: `agent_id ${agentId} is live; stop it first` }
    }
    const store = this._storeFor()
    if (store.getAgent(agentId) === null) {
      return { ok: false, error: `agent_id ${agentId} not found` }
    }
    try {
      store.deleteAgent(agentId)
    } catch (exc: any) {
      log.warning(`delete pi agent ${agentId} failed: ${exc?.message ?? exc}`)
      return { ok: false, error: String(exc?.message ?? exc) }
    }
    log.info(`deleted pi agent: agent_id=${agentId}`)
    return { ok: true, agent_id: agentId }
  }

  private _storeFor() {
    return getStore()
  }

  private _nextAgentId(store: ReturnType<typeof getStore>): string {
    const existing = new Set(store.listAgents().map(r => r.agent_id ?? ''))
    let n = 1
    while (existing.has(`pi_${n}`) || this._agents.has(`pi_${n}`)) n += 1
    return `pi_${n}`
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// entry routine (bridge -> manager)
// ──────────────────────────────────────────────────────────────────────────────

/** 按 name 查 running manager rid, 最多 ~5s (kernel 启动竞态兜底). */
async function findManagerRid(self: Routine): Promise<string | null> {
  for (let i = 0; i < 50; i++) {
    let routines: any[] = []
    try {
      routines = await self.ctx.getRunningRoutines()
    } catch (exc: any) {
      log.warning(`get_running failed (${exc?.message ?? exc}), retry`)
    }
    for (const r of routines) {
      if (String(r?.name ?? '') === MANAGER_NAME) {
        const rid = String(r?.id ?? '').trim()
        if (rid) return rid
      }
    }
    await sleep(100)
  }
  return null
}

const CreatePiAgentInput = z.object({
  agent_id: z.string().optional().describe('Agent id. Auto-generated when omitted.'),
  project_dir: z.string().optional().describe('Project root directory path.'),
  model: z.string().optional().describe('pi model ref, e.g. seed/glm-5.3.'),
  protocol: z.string().optional().describe(
    'LLM protocol override (agent policy), e.g. openai-completions. '
    + 'Must be declared in the provider apis matrix; omitted = derived from matrix.'),
  extra_instructions: z.string().optional().describe('Extra instructions (AGENTS.md).'),
})

export class CreatePiAgent extends Routine {
  static name = 'pi/agent/create'

  /** entry routine: spawn a PiAgent via the resident manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(CreatePiAgentInput),
    description: (
      'Entry routine that spawns a PiAgent (pi engine) via the resident '
      + 'PiAgentManager. Returns the new agent_id.'
    ),
  }

  private static _REQ_TIMEOUT = 10.0

  async run(kwargs: Record<string, any>): Promise<any> {
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot spawn agent`)
    }
    return await this.req(managerId, 'create_agent', kwargs ?? {}, CreatePiAgent._REQ_TIMEOUT)
  }
}

const StopPiAgentInput = z.object({
  agent_id: z.string().describe('Agent id to stop (e.g. pi_1).'),
})

export class StopPiAgent extends Routine {
  static name = 'pi/agent/stop'

  /** entry routine: stop a live PiAgent via the resident manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(StopPiAgentInput),
    description: (
      'Entry routine that stops a live PiAgent via the resident PiAgentManager. '
      + 'The agent record persists; resume to restart.'
    ),
  }

  private static _REQ_TIMEOUT = 10.0

  async run(kwargs: Record<string, any>): Promise<any> {
    const agentId = String(kwargs?.agent_id ?? '').trim()
    if (!agentId) throw new Error('agent_id is required')
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot stop agent`)
    }
    return await this.req(managerId, 'stop_agent', { agent_id: agentId }, StopPiAgent._REQ_TIMEOUT)
  }
}

const ResumePiAgentInput = z.object({
  agent_id: z.string().describe('Agent id to resume (e.g. pi_1).'),
  model: z.string().optional().describe('Optional model override.'),
  protocol: z.string().optional().describe(
    'Optional LLM protocol override; omitted = agent record value.'),
})

export class ResumePiAgent extends Routine {
  static name = 'pi/agent/resume'

  /** entry routine: resume a stopped PiAgent via the resident manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(ResumePiAgentInput),
    description: (
      'Entry routine that resumes a stopped PiAgent via the resident PiAgentManager. '
      + 'Rebuilds conversation from the pi session file.'
    ),
  }

  private static _REQ_TIMEOUT = 10.0

  async run(kwargs: Record<string, any>): Promise<any> {
    const agentId = String(kwargs?.agent_id ?? '').trim()
    if (!agentId) throw new Error('agent_id is required')
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot resume agent`)
    }
    return await this.req(managerId, 'resume_agent', kwargs ?? {}, ResumePiAgent._REQ_TIMEOUT)
  }
}

const ListPiAgentsInput = z.object({
  project_dir: z.string().optional().describe('Optional project dir filter.'),
})

export class ListPiAgents extends Routine {
  static name = 'pi/agent/list'

  /** entry routine: list all PiAgents (live + stopped) via the manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(ListPiAgentsInput),
    description: 'Entry routine that lists all PiAgents (live + stopped) via the manager.',
  }

  private static _REQ_TIMEOUT = 10.0

  async run(kwargs: Record<string, any>): Promise<any> {
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot list agents`)
    }
    return await this.req(managerId, 'list_agents', kwargs ?? {}, ListPiAgents._REQ_TIMEOUT)
  }
}

const DeletePiAgentInput = z.object({
  agent_id: z.string().describe('Agent id to delete (must be stopped).'),
})

export class DeletePiAgent extends Routine {
  static name = 'pi/agent/delete'

  /** entry routine: delete a stopped PiAgent record via the manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(DeletePiAgentInput),
    description: (
      'Entry routine that deletes a stopped PiAgent record via the resident '
      + 'PiAgentManager. Irreversible; live agents are rejected (stop first).'
    ),
  }

  private static _REQ_TIMEOUT = 10.0

  async run(kwargs: Record<string, any>): Promise<any> {
    const agentId = String(kwargs?.agent_id ?? '').trim()
    if (!agentId) throw new Error('agent_id is required')
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot delete agent`)
    }
    return await this.req(managerId, 'delete_agent', { agent_id: agentId }, DeletePiAgent._REQ_TIMEOUT)
  }
}
