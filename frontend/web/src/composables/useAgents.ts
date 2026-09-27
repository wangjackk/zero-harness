import { ref, computed, unref, type Ref } from 'vue'

/**
 * Agent management state + HTTP calls (agent 类型无关, manifest 驱动).
 *
 * 可创建的 agent 类型来自 agent_hub/types routine (扫描 catalog 里的
 * agent_manifest —— 各类型 manager 自声明 kind/routines/create_fields);
 * 全部操作按 manifest 里各自的 routine name 走统一通道
 * POST /run_routine {name, kwargs}. 前端不硬编码任何 agent 类型.
 */

export interface AgentManifestField {
  name: string
  label: string
  type: 'text' | 'textarea'
  required?: boolean
  placeholder?: string
  /** text: 前端本地自动补全源 (project_dir = 已知项目历史). */
  autocomplete?: string
}

export interface AgentManifest {
  kind: string
  label: string
  description?: string
  can_resume?: boolean
  /** 声明后创建表单渲染平台统一 ModelPicker. */
  uses_model?: boolean
  /** 声明后创建表单渲染平台统一 SkillPicker (初始 skill 预载). */
  uses_skills?: boolean
  /** 声明后创建表单渲染平台统一 RoutinePicker (prompt_routines 白名单). */
  uses_routines?: boolean
  routines: {
    create: string
    list: string
    stop: string
    resume: string
    delete: string
  }
  create_fields: AgentManifestField[]
}

export interface AgentRow {
  agent_id: string
  kind: string
  session_id: string | null
  project_dir: string | null
  model: string | null
  status: 'live' | 'stopped' | string
  routine_id: string | null
  title: string | null
  created_at: string
  updated_at: string
  live: boolean
  started: boolean
  done: boolean
}

export interface ProjectGroup {
  project_dir: string
  agents: AgentRow[]
}

export type CreateAgentParams = Record<string, unknown>

/** 初始 skill 选择: level 体现渐进式披露等级
 * ('summary' 摘要级 / 'full' 全文预载, 各 agent manager 自行处理). */
export interface SkillPick { name: string; level: 'full' | 'summary' }

/** 统一 routine 调用: POST /run_routine {name, kwargs} -> {ok, result | error}.
 * 名字走 body (格式自由, 可含 '/'), 不再受 URL 路径段约束. */
async function runRoutine(httpBase: string, name: string, body: unknown): Promise<Record<string, any>> {
  const res = await fetch(`${httpBase}/run_routine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, kwargs: body ?? {} }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  if (!data.ok) throw new Error(data.error || `${name} failed`)
  return (data.result ?? {}) as Record<string, any>
}

/** 归一化 list 行: 统一补默认值成 AgentRow (kind 由聚合方打上). */
function normalizeRow(raw: Record<string, any>, kind: string): AgentRow {
  return {
    agent_id: String(raw.agent_id ?? ''),
    kind,
    session_id: raw.session_id ?? null,
    project_dir: raw.project_dir ?? null,
    model: raw.model ?? null,
    status: String(raw.status ?? 'stopped'),
    routine_id: raw.routine_id ?? null,
    title: raw.title ?? null,
    created_at: String(raw.created_at ?? ''),
    updated_at: String(raw.updated_at ?? ''),
    live: Boolean(raw.live),
    started: Boolean(raw.started ?? raw.live),
    done: Boolean(raw.done ?? !raw.live),
  }
}

export function useAgents(httpBase: string | Ref<string>) {
  // 每次调用时求值: 支持运行时切换后端 (App.vue 传 ref, localStorage 持久化)
  const base = (): string => unref(httpBase)
  const agents = ref<AgentRow[]>([])
  const agentTypes = ref<AgentManifest[]>([])
  const loading = ref(false)
  const creating = ref(false)

  /** agent_hub/types 拉可创建类型清单 (各类型 manager 自声明). */
  async function loadTypes(): Promise<void> {
    try {
      const res = await runRoutine(base(), 'agent_hub/types', {})
      agentTypes.value = (res.types ?? []) as AgentManifest[]
    } catch {
      agentTypes.value = []
    }
  }

  function manifestOf(kind: string): AgentManifest | undefined {
    return agentTypes.value.find(t => t.kind === kind)
  }

  /** 聚合 list: 遍历所有已注册类型, 各自 list 后合并 (行打 kind). */
  async function refresh(): Promise<void> {
    loading.value = true
    try {
      if (!agentTypes.value.length) await loadTypes()
      const rows: AgentRow[] = []
      for (const t of agentTypes.value) {
        try {
          const res = await runRoutine(base(), t.routines.list, {})
          for (const raw of (res.agents ?? []) as Record<string, any>[]) {
            rows.push(normalizeRow(raw, t.kind))
          }
        } catch {
          // 单类型 list 失败不阻塞其他类型 (该类型 manager 未起等)
        }
      }
      rows.sort((a, b) => (b.updated_at > a.updated_at ? 1 : b.updated_at < a.updated_at ? -1 : 0))
      agents.value = rows
    } finally {
      loading.value = false
    }
  }

  async function createAgent(kind: string, params: CreateAgentParams): Promise<string | null> {
    const t = manifestOf(kind)
    if (!t) throw new Error(`unknown agent kind: ${kind}`)
    creating.value = true
    try {
      const result = await runRoutine(base(), t.routines.create, params)
      if (!result.ok) throw new Error(result.error || 'create failed')
      const id = result.agent_id as string
      void refresh()
      return id || null
    } finally {
      creating.value = false
    }
  }

  async function stopAgent(a: AgentRow): Promise<boolean> {
    const t = manifestOf(a.kind)
    if (!t) return false
    try {
      const result = await runRoutine(base(), t.routines.stop, { agent_id: a.agent_id })
      if (!result.ok) throw new Error(result.error || 'stop failed')
      await refresh()
      return true
    } catch {
      return false
    }
  }

  /** 删除 agent: live 拒绝 (先 stop); 存储清理由各类型 manager 负责. */
  async function deleteAgent(a: AgentRow): Promise<boolean> {
    const t = manifestOf(a.kind)
    if (!t) return false
    try {
      const result = await runRoutine(base(), t.routines.delete, { agent_id: a.agent_id })
      if (!result.ok) throw new Error(result.error || 'delete failed')
      await refresh()
      return true
    } catch {
      return false
    }
  }

  async function resumeAgent(a: AgentRow): Promise<AgentRow | null> {
    const t = manifestOf(a.kind)
    if (!t) return null
    try {
      const result = await runRoutine(base(), t.routines.resume, {
        agent_id: a.agent_id,
        ...(a.model ? { model: a.model } : {}),
      })
      if (!result.ok) throw new Error(result.error || 'resume failed')
      await refresh()
      return agents.value.find(x => x.agent_id === a.agent_id) ?? null
    } catch {
      return null
    }
  }

  /** distinct project_dirs seen in history, newest-updated first. */
  const knownProjects = computed<string[]>(() => {
    const seen = new Map<string, string>() // project_dir -> latest updated_at
    for (const a of agents.value) {
      const dir = a.project_dir || '(unset)'
      const prev = seen.get(dir)
      if (!prev || a.updated_at > prev) seen.set(dir, a.updated_at)
    }
    return [...seen.entries()]
      .sort((a, b) => (b[1] > a[1] ? 1 : b[1] < a[1] ? -1 : 0))
      .map(([dir]) => dir)
  })

  /** agents grouped by project_dir, each group's agents newest-first. */
  const projects = computed<ProjectGroup[]>(() => {
    const groups = new Map<string, AgentRow[]>()
    for (const a of agents.value) {
      const dir = a.project_dir || '(unset)'
      if (!groups.has(dir)) groups.set(dir, [])
      groups.get(dir)!.push(a)
    }
    const out: ProjectGroup[] = []
    for (const dir of knownProjects.value) {
      const list = (groups.get(dir) ?? []).slice().sort((a, b) =>
        b.updated_at > a.updated_at ? 1 : b.updated_at < a.updated_at ? -1 : 0,
      )
      out.push({ project_dir: dir, agents: list })
    }
    return out
  })

  return {
    agents, agentTypes, loading, creating,
    loadTypes, refresh, createAgent, resumeAgent, stopAgent, deleteAgent,
    projects, knownProjects,
  }
}
