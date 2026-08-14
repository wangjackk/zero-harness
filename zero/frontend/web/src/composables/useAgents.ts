import { ref, computed } from 'vue'

/**
 * Agent management state + HTTP calls (prime only).
 *
 * Backend (web_server.py) routes:
 *   GET  /agents                  -> {agents: AgentRow[]}
 *   POST /agents/create           -> {ok, agent_id?, session_id?, ...}
 *   POST /agents/resume           -> {ok, agent_id?, session_id?, ...}
 *   POST /agents/stop             -> {ok, agent_id?}
 *   POST /agents/delete           -> {ok, agent_id?} (删 DB 行 + messages; live 拒绝)
 *
 * One agent = one session: session_id = agent_id (backend auto-derives,
 * frontend never passes session_id). create = new agent (auto uuid);
 * resume = re-spawn a stopped agent_id (manager replays prior history
 * server-side from db keyed by agent_id).
 */

export interface AgentRow {
  agent_id: string
  kind: 'prime'
  session_id: string | null
  project_dir: string | null
  model: string | null
  reasoning_effort: string | null
  plan_mode: boolean
  status: 'live' | 'stopped' | string
  handle_id: string | null
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

export interface CreateAgentParams {
  kind: 'prime'
  project_dir?: string
  model?: string
  plan_mode?: boolean
  extra_instructions?: string
  max_turns?: number
  enabled_tools?: string[]
  disabled_tools?: string[]
  preload_skills?: string[]
  level1_skills?: string[]
}

/** resume 参数: agent_id 必传, 其他可选 (不传则后端从 DB 取 model 等). */
export interface ResumeAgentParams {
  kind: 'prime'
  agent_id: string
  model?: string
  project_dir?: string
  plan_mode?: boolean
  extra_instructions?: string
  max_turns?: number
  enabled_tools?: string[]
  disabled_tools?: string[]
  preload_skills?: string[]
  level1_skills?: string[]
}

export function useAgents(httpBase: string) {
  const agents = ref<AgentRow[]>([])
  const loading = ref(false)
  const creating = ref(false)

  async function refresh(): Promise<void> {
    loading.value = true
    try {
      const res = await fetch(`${httpBase}/agents`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      agents.value = ((data.agents as AgentRow[] | undefined) ?? []).slice()
    } catch {
      // manager not running yet or network error -- leave list as-is
    } finally {
      loading.value = false
    }
  }

  async function createAgent(params: CreateAgentParams): Promise<string | null> {
    creating.value = true
    try {
      const res = await fetch(`${httpBase}/agents/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (!data.ok) throw new Error(data.error || 'create failed')
      const id = data.agent_id as string
      void refresh()
      return id || null
    } finally {
      creating.value = false
    }
  }

  async function stopAgent(agent_id: string): Promise<boolean> {
    try {
      const res = await fetch(`${httpBase}/agents/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (!data.ok) throw new Error(data.error || 'stop failed')
      await refresh()
      return true
    } catch {
      return false
    }
  }

  /** 删除 agent: DB agents 行 + messages. live 拒绝 (前端应先 stop). */
  async function deleteAgent(agent_id: string): Promise<boolean> {
    try {
      const res = await fetch(`${httpBase}/agents/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (!data.ok) throw new Error(data.error || 'delete failed')
      await refresh()
      return true
    } catch {
      return false
    }
  }

  async function resumeAgent(params: ResumeAgentParams): Promise<AgentRow | null> {
    try {
      const res = await fetch(`${httpBase}/agents/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (!data.ok) throw new Error(data.error || 'resume failed')
      await refresh()
      const id = params.agent_id
      return agents.value.find(a => a.agent_id === id) ?? null
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

  return { agents, loading, creating, refresh, createAgent, resumeAgent, stopAgent, deleteAgent, projects, knownProjects }
}
