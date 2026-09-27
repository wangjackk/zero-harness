/** XmlAgentManager(TS) -- resident passive routine that spawns XmlAgent children.
 *
 * py 版 xml_agent/manager.py 的最小同构移植, 结构对齐 PiAgentManager:
 * create/resume/stop/list/delete + agent_hub 注册 manifest.
 * 差异:
 * - 记忆独立 (xml/memory.ts, runtime/xml_agent_ts/), 不走共享 agents store,
 *   无 preset 归属过滤 (库里全是自己的行).
 * - 预检恒做 (pi 版仅显式 protocol 时): xml agent init 无 session 兜底,
 *   model+protocol+effort 组合必须先过矩阵, 否则 spawn 起一个必死的 child.
 * - agent_id 前缀 xts_ (py 版 xml_N 在同一 kernel 共存, 避免撞 bridge namespace).
 */
import { z } from 'zod'

import { Routine, request } from 'routine-ts'
import type { RoutineSource } from 'routine-ts'
import { AsyncEvent, sleep } from 'routine-ts/async'
import { setupLogger } from 'routine-ts/logger'

import { jsonSchema } from '../../toolSchema'
import { getSharedModels, resolveModelRef } from '../_core/modelRuntime'
import { getMemory } from './memory'
import { neutralEffort, normalizeEffort } from './agent'

const log = setupLogger('xml.manager')

const AGENT_ROUTINE_NAME = 'xml/agent'
const MANAGER_NAME = 'xml/agent/manager'
const TYPES_HUB_NAME = 'agent_hub'

/** agent 类型 manifest: manager 启动时主动 register 到 agent_hub
 * (hub 纯被动, 不扫描 catalog), 前端经 agent_hub/types 查类型清单动态渲染创建表单. */
const XML_AGENT_MANIFEST = {
  kind: 'xml_ts',
  label: 'XML Agent (TS)',
  description: 'XML 协议 agent (TS 版): pi-ai driver LLM + XML 工具调用 + sqlite 记忆, 事件/记忆体系自管理',
  can_resume: true,
  uses_model: true,
  routines: {
    create: 'xml/agent/create',
    list: 'xml/agent/list',
    stop: 'xml/agent/stop',
    resume: 'xml/agent/resume',
    delete: 'xml/agent/delete',
  },
  create_fields: [
    { name: 'agent_id', label: 'Agent ID', type: 'text', required: false, placeholder: 'auto-generated' },
    { name: 'model', label: '模型', type: 'text', required: false, placeholder: '中立配置 default' },
    { name: 'protocol', label: '协议', type: 'text', required: false, placeholder: '矩阵推导' },
  ],
}

interface LiveInfo {
  handle: import('routine-ts/handle').RoutineHandle
  sessionId: string
}

/** 协议参数清洗: 空串/空白 -> null (未显式指定). */
function cleanProtocol(value: any): string | null {
  const s = String(value ?? '').trim()
  return s || null
}

export class XmlAgentManager extends Routine {
  static name = 'xml/agent/manager'

  /** resident passive manager: dynamically create/list/stop XmlAgent children. */
  static isPassive = true
  static meta: Record<string, any> = {
    hidden: true,
    description: (
      'Resident manager for XmlAgent (TS). Spawns/lists/stops XmlAgent child '
      + 'instances on request. Conversation state persists via xml memory '
      + 'sqlite; each child runs concurrently, isolated by agent_id.'
    ),
  }

  private _stop = new AsyncEvent()
  private _agents = new Map<string, LiveInfo>()
  private _stopping = new Set<string>()

  async onStarted(): Promise<void> {
    log.info('xml agents manager (ts) started')
  }

  async run(_kwargs: Record<string, any>): Promise<void> {
    void this._registerManifest()
    await this._stop.wait()
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

  /** 向 agent_hub 注册 manifest (hub 纯被动, manager 主动注册).
   * 启动竞态 (hub 尚未就绪) 兜底重试 ~5s; 成功即止. */
  private async _registerManifest(): Promise<void> {
    for (let i = 0; i < 50; i++) {
      try {
        const rid = await this._findTypesHubRid()
        if (rid) {
          await this.req(rid, 'register', XML_AGENT_MANIFEST, 5.0)
          log.info(`agent manifest registered (kind: ${XML_AGENT_MANIFEST.kind})`)
          return
        }
      } catch (exc: any) {
        log.warning(`agent_hub register retry: ${exc?.message ?? exc}`)
      }
      await sleep(100)
    }
    log.error('agent_hub register failed: agent_hub unreachable')
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
  }

  private async _unregisterManifest(): Promise<void> {
    try {
      const rid = await this._findTypesHubRid()
      if (rid) {
        await this.req(rid, 'unregister', { kind: XML_AGENT_MANIFEST.kind }, 3.0)
      }
    } catch {
      // 市场已停: 静默放弃
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
    const memory = getMemory()
    let agentId = String(data.agent_id || '').trim()
    if (agentId) {
      if (memory.getAgent(agentId) !== null) {
        return { ok: false, error: `agent_id ${agentId} already exists; use resume instead` }
      }
    } else {
      agentId = this._nextAgentId()
    }
    if (this._live(agentId) !== null) {
      return { ok: false, error: `agent_id ${agentId} already live` }
    }
    const sessionId = crypto.randomUUID().replaceAll('-', '')
    const model = data.model || null
    const protocol = cleanProtocol(data.protocol)
    const result = await this._spawnChild(agentId, data, sessionId, false, model, protocol)
    // spawn 成功后才持久化: 预检失败 (未知 model / 未声明协议 / effort 违规)
    // 不留脏记录. child 侧对 agents 表只 UPDATE 不 INSERT.
    if (result.ok) {
      try {
        memory.registerAgent(agentId, { sessionId, model, protocol })
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
    const agentRec = getMemory().getAgent(agentId)
    if (agentRec === null) {
      return { ok: false, error: `agent_id ${agentId} not found` }
    }
    // model / protocol 优先级: 显式传参 > agent 记录持久化值;
    // model 再缺省交 agent 侧解析 (中立配置 default).
    const model = data.model || agentRec.model || null
    const protocol = cleanProtocol(data.protocol) || agentRec.protocol || null
    const sessionId = String(agentRec.session_id || '') || crypto.randomUUID().replaceAll('-', '')
    return await this._spawnChild(agentId, data, sessionId, true, model, protocol)
  }

  private async _spawnChild(
    agentId: string, data: any, sessionId: string,
    isResume: boolean, model: string | null, protocol: string | null,
  ): Promise<any> {
    const reasoning = (data && typeof data.reasoning === 'object') ? data.reasoning : null

    // 预检 (恒做, 对齐 py 的 spawn 前拦截): model+protocol 组合必须先在
    // 配置矩阵声明; effort 链 (显式 > 中立配置声明) 里的 off 档需要模型
    // 声明 can_disable. 共享缓存, 无额外网络开销.
    try {
      const shared = await getSharedModels(this)
      const ref = model || shared.config.default
      if (!ref) throw new Error('no model available (models.yaml default missing)')
      const resolved = resolveModelRef(shared, ref, protocol ?? undefined)
      if (!resolved) throw new Error(`model not found in neutral config: ${ref}`)
      const effort = normalizeEffort(reasoning?.effort ?? neutralEffort(shared, ref))
      if (effort === 'off' && (resolved as any).reasoning
        && !(resolved as any).thinkingLevelMap?.off) {
        throw new Error(
          `model '${ref}' cannot disable thinking (no can_disable declared)`)
      }
    } catch (exc: any) {
      const action = isResume ? 'resume' : 'create'
      log.error(`${action} xml agent ${agentId} failed: ${exc?.message ?? exc}`)
      return { ok: false, error: String(exc?.message ?? exc) }
    }

    const childKwargs: Record<string, any> = {
      agent_id: agentId,
      session_id: sessionId,
      model,
      protocol,
      reasoning,
    }
    let handle
    try {
      handle = await this.submit(AGENT_ROUTINE_NAME, childKwargs)
      await handle.start()
    } catch (exc: any) {
      log.error(`spawn xml agent ${agentId} failed: ${exc?.message ?? exc}`)
      return { ok: false, error: String(exc?.message ?? exc) }
    }

    this._agents.set(agentId, { handle, sessionId })
    const action = isResume ? 'resumed' : 'created'
    log.info(
      `${action} xml agent: agent_id=${agentId} routine_id=${handle.id} model=${model}`,
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
  async onList(_source: RoutineSource, _data: any): Promise<any> {
    const items: Record<string, any>[] = []
    for (const row of getMemory().listAgents()) {
      const agentId = row.agent_id ?? ''
      const info = this._live(agentId)
      const live = info !== null
      const handle = info?.handle
      items.push({
        agent_id: agentId,
        model: row.model ?? null,
        protocol: row.protocol ?? null,
        title: row.title ?? null,
        created_at: row.created_at,
        updated_at: row.updated_at,
        session_id: info?.sessionId ?? row.session_id ?? null,
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
      log.info(`stopped xml agent: agent_id=${agentId}`)
      return { ok: true, agent_id: agentId }
    } catch (exc: any) {
      log.warning(`stop xml agent ${agentId} failed: ${exc?.message ?? exc}`)
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
    const memory = getMemory()
    if (memory.getAgent(agentId) === null) {
      return { ok: false, error: `agent_id ${agentId} not found` }
    }
    try {
      memory.deleteAgent(agentId)
    } catch (exc: any) {
      log.warning(`delete xml agent ${agentId} failed: ${exc?.message ?? exc}`)
      return { ok: false, error: String(exc?.message ?? exc) }
    }
    log.info(`deleted xml agent: agent_id=${agentId}`)
    return { ok: true, agent_id: agentId }
  }

  private _nextAgentId(): string {
    // 前缀 xts_ 隔离 py 版 xml_N (共享 kernel 的 bridge namespace)
    const existing = new Set([
      ...getMemory().listAgents().map(r => r.agent_id ?? ''),
      ...this._agents.keys(),
    ])
    let n = 1
    while (existing.has(`xts_${n}`)) n += 1
    return `xts_${n}`
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

const CreateXmlAgentInput = z.object({
  agent_id: z.string().optional().describe('Agent id. Auto-generated (xts_N) when omitted.'),
  model: z.string().optional().describe('Model ref in neutral config, e.g. seed/glm-5.3.'),
  protocol: z.string().optional().describe(
    'LLM protocol override (agent policy), e.g. openai-completions. '
    + 'Must be declared in the provider apis matrix; omitted = derived from matrix.'),
  reasoning: z.object({ effort: z.string().optional() }).optional().describe(
    'Reasoning effort override: high | medium | low | none (none = disable thinking).'),
})

export class CreateXmlAgent extends Routine {
  static name = 'xml/agent/create'

  /** entry routine: spawn an XmlAgent via the resident manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(CreateXmlAgentInput),
    description: (
      'Entry routine that spawns an XmlAgent (TS) via the resident '
      + 'XmlAgentManager. Returns the new agent_id.'
    ),
  }

  private static _REQ_TIMEOUT = 10.0

  async run(kwargs: Record<string, any>): Promise<any> {
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot spawn agent`)
    }
    return await this.req(managerId, 'create_agent', kwargs ?? {}, CreateXmlAgent._REQ_TIMEOUT)
  }
}

const StopXmlAgentInput = z.object({
  agent_id: z.string().describe('Agent id to stop (e.g. xts_1).'),
})

export class StopXmlAgent extends Routine {
  static name = 'xml/agent/stop'

  /** entry routine: stop a live XmlAgent via the resident manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(StopXmlAgentInput),
    description: (
      'Entry routine that stops a live XmlAgent via the resident XmlAgentManager. '
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
    return await this.req(managerId, 'stop_agent', { agent_id: agentId }, StopXmlAgent._REQ_TIMEOUT)
  }
}

const ResumeXmlAgentInput = z.object({
  agent_id: z.string().describe('Agent id to resume (e.g. xts_1).'),
  model: z.string().optional().describe('Optional model override.'),
  protocol: z.string().optional().describe(
    'Optional LLM protocol override; omitted = agent record value.'),
})

export class ResumeXmlAgent extends Routine {
  static name = 'xml/agent/resume'

  /** entry routine: resume a stopped XmlAgent via the resident manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(ResumeXmlAgentInput),
    description: (
      'Entry routine that resumes a stopped XmlAgent via the resident XmlAgentManager. '
      + 'Rebuilds conversation from the xml memory sqlite.'
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
    return await this.req(managerId, 'resume_agent', kwargs ?? {}, ResumeXmlAgent._REQ_TIMEOUT)
  }
}

const ListXmlAgentsInput = z.object({})

export class ListXmlAgents extends Routine {
  static name = 'xml/agent/list'

  /** entry routine: list all XmlAgents (live + stopped) via the manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(ListXmlAgentsInput),
    description: 'Entry routine that lists all XmlAgents (live + stopped) via the manager.',
  }

  private static _REQ_TIMEOUT = 10.0

  async run(_kwargs: Record<string, any>): Promise<any> {
    const managerId = await findManagerRid(this)
    if (managerId === null) {
      throw new Error(`${MANAGER_NAME} manager not running; cannot list agents`)
    }
    return await this.req(managerId, 'list_agents', {}, ListXmlAgents._REQ_TIMEOUT)
  }
}

const DeleteXmlAgentInput = z.object({
  agent_id: z.string().describe('Agent id to delete (must be stopped).'),
})

export class DeleteXmlAgent extends Routine {
  static name = 'xml/agent/delete'

  /** entry routine: delete a stopped XmlAgent record via the manager. */

  static meta: Record<string, any> = {
    hidden: true,
    input_schema: jsonSchema(DeleteXmlAgentInput),
    description: (
      'Entry routine that deletes a stopped XmlAgent record via the resident '
      + 'XmlAgentManager. Irreversible; live agents are rejected (stop first).'
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
    return await this.req(managerId, 'delete_agent', { agent_id: agentId }, DeleteXmlAgent._REQ_TIMEOUT)
  }
}
