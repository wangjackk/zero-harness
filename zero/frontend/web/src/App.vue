<template>
  <NConfigProvider :theme="darkTheme" :theme-overrides="themeOverrides">
  <div class="app">
    <header class="header">
      <span class="logo">Zero</span>

      <!-- 连接状态 -->
      <NTag
        size="small"
        :bordered="false"
        :type="connected ? 'success' : 'error'"
        style="flex-shrink:0"
      >
        {{ connected ? '已连接' : '断开' }}
      </NTag>

      <div class="header-spacer" />

      <!-- Routine Runner -->
      <NButton
        size="small"
        :type="showRunner ? 'primary' : 'default'"
        :ghost="showRunner"
        quaternary
        title="Routine Runner"
        @click="showRunner = !showRunner"
      >⚡</NButton>
    </header>

    <Teleport to="body">
      <div v-show="showRunner" class="rr-overlay" @click.self="showRunner = false">
        <RoutineRunner class="rr-float" :routines="routines" @run="onRun" />
      </div>
    </Teleport>

    <!-- UI 请求弹窗 -->
    <Teleport to="body">
      <div v-if="currentUI" class="ui-overlay">
        <template v-if="currentUIComponent === 'selector'">
          <SelectorDialog
            v-bind="currentUIProps"
            @select="(v) => respondUI(currentUIId, v)"
            @cancel="() => cancelUI(currentUIId)"
          />
        </template>
        <TableDialog
          v-else-if="currentUIComponent === 'table'"
          v-bind="currentUIProps"
          @action="(payload) => respondUI(currentUIId, payload)"
          @cancel="() => cancelUI(currentUIId)"
        />
        <DateDialog
          v-else-if="currentUIComponent === 'date'"
          v-bind="currentUIProps"
          @select="(v) => respondUI(currentUIId, v)"
          @cancel="() => cancelUI(currentUIId)"
        />
        <!-- 未知组件兜底 -->
        <NCard v-else class="ui-unknown-card" :bordered="false">
          <NAlert type="warning" :show-icon="true">
            未知 UI 组件:{{ currentUIComponent }}
          </NAlert>
          <div style="text-align:right;margin-top:12px">
            <NButton size="small" @click="cancelUI(currentUIId)">关闭</NButton>
          </div>
        </NCard>
      </div>
    </Teleport>

    <div class="app-body">
      <!-- 左侧 sidebar -->
      <aside class="sidebar">
        <button class="sidebar-new" @click="viewMode = 'create'">+ New</button>
        <div class="sidebar-list">
          <div
            v-for="id in sidebarAgents"
            :key="id"
            class="sidebar-item"
            :class="{ 'sidebar-item-active': activeId === id && viewMode === 'chat' }"
            @click="selectAgent(id)"
          >
            <span class="dot" :class="dotClass(id)" />
            <NBadge
              class="name-badge"
              :value="unread[id] || 0"
              :show="!!unread[id]"
              color="#6366f1"
              :offset="[4, -2]"
            >
              <span class="name">{{ getAgentName(id) }}</span>
            </NBadge>
            <span
              v-if="stoppingIds.has(id)"
              class="sidebar-stopping"
              title="停止中..."
            >⟳</span>
            <span
              v-else-if="liveSet[id]"
              class="sidebar-close"
              title="stop + close"
              @click.stop="closeTab(id)"
            >✕</span>
            <span
              v-else
              class="sidebar-delete"
              title="删除 (不可恢复)"
              @click.stop="deleteAgent(id)"
            >🗑</span>
          </div>
          <div v-if="sidebarAgents.length === 0" class="sidebar-empty">
            点击 "+ New" 创建第一个 agent
          </div>
        </div>
      </aside>

      <!-- 右侧 main -->
      <main class="main">
        <!-- chat view: 渲染所有 panel, 仅显示 active (保留状态) -->
        <div v-show="viewMode === 'chat'" class="panels">
          <div v-if="!activeId" class="main-empty">
            <div class="main-empty-hint">点击左侧 "+ New" 创建第一个 agent</div>
          </div>
          <AgentPanel
            v-for="id in agentOrder"
            v-show="activeId === id"
            :key="id"
            :agent-id="id"
            :messages="panels[id] ?? []"
            :streaming="streaming[id]"
            :readonly="agentReadonly[id]"
            :stopped="!liveSet[id]"
            :usage="agentUsage[id]"
            @send="(text: string) => sendInput(text, id)"
            @interrupt="() => send({ type: 'interrupt', agent_id: id })"
            @set-effort="(effort: string) => setEffort(id, effort)"
          />
        </div>

        <!-- create view: 懒挂载 -->
        <div v-if="viewMode === 'create'" class="create-view">
          <header class="create-header">
            <h3>新建 Prime Agent</h3>
            <NButton size="small" @click="viewMode = 'chat'">取消</NButton>
          </header>
          <div class="create-body">
            <PrimeCreateForm
              :http-base="httpBase"
              :project-suggestions="projectSuggestions"
              :create-agent="createAgent"
              :creating="creating"
              @create="onChildCreate"
              @error="onChildError"
            />
          </div>
        </div>
      </main>
    </div>
  </div>
  </NConfigProvider>
</template>

<script setup lang="ts">
import { ref, reactive, watch, computed, onMounted, onUnmounted } from 'vue'
import { NConfigProvider, NButton, NBadge, NTag, NCard, NAlert, darkTheme } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import AgentPanel from './components/AgentPanel.vue'
import RoutineRunner from './components/RoutineRunner.vue'
import SelectorDialog from './components/SelectorDialog.vue'
import TableDialog from './components/TableDialog.vue'
import DateDialog from './components/DateDialog.vue'
import PrimeCreateForm from './components/PrimeCreateForm.vue'
import type { RoutineInfo } from './components/RoutineRunner.vue'
import { useWS } from './composables/useWS'
import { useUIRequests } from './composables/useUIRequests'
import type { UiRequest } from './composables/useUIRequests'
import { useAudioPlayer } from './composables/useAudioPlayer'
import { useAgents } from './composables/useAgents'
import type { CreateAgentParams, ResumeAgentParams, AgentRow } from './composables/useAgents'

interface PanelMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  text: string
  thinking?: string
  thinkingDone?: boolean
  final: boolean
  results?: ToolFeedback[]
  from?: string
}

interface ToolFeedback {
  name: string
  input?: Record<string, unknown>
  result?: unknown
  error?: { msg: string }
  rid?: string
  call_id?: string
  status?: 'running' | 'done' | 'error'
  startedAt?: number  // running 时记录的时间戳 (ms)
  duration?: number   // done 时记录的总耗时 (ms)
}

interface StreamingState {
  messageId: string
  entryId: string
  text: string
  thinking: string
}

interface UsageInfo {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  max_context: number
  percent: number
  model_key?: string
  model_name?: string
  reasoning_effort?: string | null
}

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#6366f1',
    primaryColorHover: '#818cf8',
    primaryColorPressed: '#4f46e5',
    primaryColorSuppl: '#6366f1',
    borderRadius: '8px',
    fontFamily: "'Segoe UI', system-ui, sans-serif",
  },
}

const WS_URL = `ws://${window.location.host}/ws`

const ws = useWS(WS_URL)
const { connected, on, send, request } = ws
useAudioPlayer(ws)

// HTTP base for /routines + /builtin_skills + /agents 端点拉可选项.
// 默认 7781, 跟 routines.yaml 里 web_server 条目的 kwargs.port 一致.
const httpBase = 'http://127.0.0.1:7781'

// useAgents: sidebar history + project suggestions
const { agents, refresh, createAgent, creating, resumeAgent, stopAgent, deleteAgent: _deleteAgent } = useAgents(httpBase)

// UI 请求队列(后端通过 ui_request 弹出组件,用户交互后发回 ui_response)
const { queue: uiQueue, receive: receiveUI, respond: respondUI, cancel: cancelUI, serverCancel } = useUIRequests(send)
on('ui_request', receiveUI)
on('ui_cancel', serverCancel)

// 队首请求(判别联合:按 component 收窄 props 类型)
const currentUI = computed<UiRequest | undefined>(() => uiQueue.value[0])
const currentUIProps = computed(() => currentUI.value?.props ?? {})
const currentUIId = computed(() => currentUI.value?.id ?? '')
const currentUIComponent = computed(() => currentUI.value?.component ?? '')

const routines = ref<RoutineInfo[]>([])

// routines 走 HTTP /routines:WS 不再有 routines 推送通道.
async function fetchRoutines() {
  try {
    const res = await fetch(`${httpBase}/routines`)
    if (!res.ok) return
    const data = await res.json()
    routines.value = (data.routines as RoutineInfo[]) ?? []
  } catch {
    // ignore
  }
}

// 连上后立即拉取 routine 列表 + 刷新 agents 历史
watch(connected, (v) => {
  if (v) {
    void fetchRoutines()
    refresh()
  }
})

// agents 列表变化时, 用 list 返回的 model + reasoning_effort 初始化 agentUsage.
// 前端刷新后 agentUsage 清空, 但 list_agents 已从 session_state snapshot 读出
// reasoning_effort, 故刷新后立即恢复显示, 无需等下一次 usage 事件.
// 已有的 agentUsage 不覆盖 (usage 事件带 token 数, 更准).
watch(agents, (list) => {
  for (const a of list) {
    if (agentUsage[a.agent_id]) continue
    const modelKey = a.model || ''
    const modelName = modelKey.includes('/') ? modelKey.split('/', 2)[1] : modelKey
    agentUsage[a.agent_id] = {
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      max_context: 0,
      percent: 0,
      model_key: modelKey,
      model_name: modelName,
      reasoning_effort: a.reasoning_effort === undefined ? undefined : a.reasoning_effort,
    }
  }
})

// 收到新 agent 注册通知 → 建 panel + 加 sidebar
on('conversation_open', (msg) => {
  const agent_id = msg.agent_id as string
  const name     = (msg.name as string | undefined) ?? agent_id
  if (!agent_id) return
  ensurePanel(agent_id)
  agentNames[agent_id] = name
  agentReadonly[agent_id] = !!(msg.readonly as boolean | undefined)
  liveSet[agent_id] = true
})

// session switch/resume -> backend pushes session_changed with panel history
on('session_changed', (msg) => {
  const agent_id = msg.agent_id as string
  const messages = (msg.messages as PanelMessage[]) ?? []
  // 历史消息的 thinking 早已结束,标记为 done 以避免重新显示 thinking 块
  for (const m of messages) {
    if (m.thinking) m.thinkingDone = true
  }
  ensurePanel(agent_id)
  panels[agent_id] = messages
  streaming[agent_id] = null
})


function sendInput(text: string, agentId: string) {
  if (!agentId) return
  ensurePanel(agentId)
  panels[agentId].push({ id: `user-${Date.now()}`, role: 'user', text, final: true })
  // user 与 agent 平等: 走 HTTP /agents/user/run/send_message, user 也是一种 agent.
  // send_message routine 内部 req 目标 agent 的 chat_message, 触发 react.
  fetch(`${httpBase}/agents/user/run/send_message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ to: agentId, message: text }),
  }).catch((err) => {
    console.error('send_message failed:', err)
  })
}

function setEffort(agentId: string, effort: string) {
  request({ type: 'set_effort', agent_id: agentId, effort }).catch(() => {})
}

const panels = reactive<Record<string, PanelMessage[]>>({})
// sidebar 只列用户创建的 agent, 无内置 main 项.
// activeId 为空时右侧 main 区显示空态 ("点击 + New 创建 agent").
const agentOrder = ref<string[]>([])
const agentNames = reactive<Record<string, string>>({})
const agentReadonly = reactive<Record<string, boolean>>({})
const liveSet = reactive<Record<string, boolean>>({})
const activeId = ref('')
const unread = reactive<Record<string, number>>({})
const streaming = reactive<Record<string, StreamingState | null>>({})
const showRunner = ref(false)
const agentUsage = reactive<Record<string, UsageInfo>>({})
let assistantEntrySeq = 0

// 视图模式: chat = 显示当前 agent 对话; create = 显示新建 agent 表单
const viewMode = ref<'chat' | 'create'>('chat')

// 项目历史 (从 useAgents 拉, 给 PrimeCreateForm 的 project_dir 自动补全)
const projectSuggestions = computed(() => {
  const out: string[] = []
  const seen = new Set<string>()
  for (const a of agents.value) {
    const dir = a.project_dir || ''
    if (dir && dir !== '(unset)' && !seen.has(dir)) {
      seen.add(dir)
      out.push(dir)
    }
  }
  return out
})

// sidebar agent 列表: 统一按 created_at 降序 (最新在前), live/stopped 状态变化不改变顺序.
const sidebarAgents = computed(() => {
  return agents.value
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
    .map(a => a.agent_id)
})

function getAgentName(id: string): string {
  const a = agents.value.find(x => x.agent_id === id)
  if (a?.title) return a.title
  if (agentNames[id]) return agentNames[id]
  if (a) return 'Prime-' + a.agent_id.slice(0, 8)
  return agentNames[id] || id
}

function dotClass(id: string): string {
  if (liveSet[id]) return 'dot-live'
  return 'dot-stopped'
}

function selectAgent(id: string) {
  ensurePanel(id)
  activeId.value = id
  viewMode.value = 'chat'
  unread[id] = 0
  // 历史 stopped agent 且 panel 空 -> 自动 resume (后端会推 session_changed 带历史消息)
  // 以 agents 列表的 live 字段为准 (后端权威), 不依赖本地 liveSet--后者在 WS 重连
  // 后可能残留旧值 (重启 zero 后 liveSet[id] 还是 true, 但后端已 stopped).
  const a = agents.value.find(x => x.agent_id === id)
  if (a && !a.live && panels[id].length === 0) {
    panels[id].push({
      id: `loading-${Date.now()}`,
      role: 'assistant',
      text: '正在恢复会话...',
      final: true,
    })
    void _resumeAgent(a)
  }
}

async function _resumeAgent(a: AgentRow) {
  const id = a.agent_id
  try {
    const params: ResumeAgentParams = {
      kind: a.kind,
      agent_id: id,
      project_dir: a.project_dir || undefined,
      model: a.model || undefined,
      plan_mode: a.plan_mode,
    }
    const row = await resumeAgent(params)
    if (!row) {
      throw new Error('resume 返回 null')
    }
    // session_changed 事件会覆盖 panels[id], loading 提示被替换;
    // 若 session_changed 没来 (resume 失败/超时), 兜底清掉 loading.
    if (panels[id] && panels[id].some(m => m.id.startsWith('loading-'))) {
      panels[id] = panels[id].filter(m => !m.id.startsWith('loading-'))
    }
  } catch (e) {
    if (panels[id]) {
      panels[id] = panels[id].filter(m => !m.id.startsWith('loading-'))
      panels[id].push({
        id: `err-${Date.now()}`,
        role: 'assistant',
        text: `恢复失败: ${(e as Error).message}`,
        final: true,
      })
    }
  }
}

// 子表单创建成功: 切到新 agent 的 chat view
function onChildCreate(_params: CreateAgentParams, newAgentId: string) {
  if (newAgentId) {
    ensurePanel(newAgentId)
    selectAgent(newAgentId)
  } else {
    viewMode.value = 'chat'
  }
}

function onChildError(msg: string) {
  // 子表单已自己显示 createError, 这里只是 hook 占位 (未来可加 toast)
  void msg
}

function mergeStreamText(current: string, incoming: string, isFinal: boolean) {
  if (isFinal) return incoming
  // 流式输出可能是累计文本或增量分片,两种都支持.
  return incoming.startsWith(current) ? incoming : current + incoming
}

function ensurePanel(agentId: string) {
  // user/world 是系统内部 agent, 不建 panel 不进 sidebar
  if (agentId === 'user' || agentId === 'world') return
  if (!panels[agentId]) {
    panels[agentId] = []
  }
  if (!agentOrder.value.includes(agentId)) {
    agentOrder.value.push(agentId)
    // 如果当前没有 active tab,自动聚焦第一个
    if (!agentOrder.value.includes(activeId.value)) {
      activeId.value = agentId
    }
  }
}

// close a tab: stop the live agent (HTTP /agents/stop), then drop local panel/tab/state.
// stopped agents just lose their tab. 之后 refresh useAgents, 让刚停的 agent 回到 sidebar 的历史区 (灰点).
const stoppingIds = ref<Set<string>>(new Set())
async function closeTab(agentId: string) {
  if (stoppingIds.value.has(agentId)) return  // 防重复点击
  if (liveSet[agentId]) {
    stoppingIds.value.add(agentId)
    try {
      await stopAgent(agentId)
    } finally {
      stoppingIds.value.delete(agentId)
      liveSet[agentId] = false
    }
  }
  delete panels[agentId]
  delete streaming[agentId]
  delete agentNames[agentId]
  delete agentReadonly[agentId]
  delete unread[agentId]
  delete liveSet[agentId]
  const idx = agentOrder.value.indexOf(agentId)
  if (idx >= 0) agentOrder.value.splice(idx, 1)
  // 不强制切到第一个, 让用户自己选. activeId 指向已关闭的 panel 时 AgentPanel v-show=false, sidebar 不显示 active 高亮.
  // 刷新 useAgents 历史, 让停掉的 agent 以 stopped 状态出现在 sidebar
  refresh()
}

// delete agent: DB 行 + messages 全删, 不可恢复. stopped 才允许.
async function deleteAgent(agentId: string) {
  const name = getAgentName(agentId)
  if (!window.confirm(`删除 agent "${name}"?\n将清除所有历史消息, 不可恢复.`)) return
  const ok = await _deleteAgent(agentId)
  if (!ok) return
  // 清本地状态 (跟 closeTab 一致, 但 DB 已删, refresh 后不会回到 sidebar)
  delete panels[agentId]
  delete streaming[agentId]
  delete agentNames[agentId]
  delete agentReadonly[agentId]
  delete unread[agentId]
  delete liveSet[agentId]
  const idx = agentOrder.value.indexOf(agentId)
  if (idx >= 0) agentOrder.value.splice(idx, 1)
  if (activeId.value === agentId) {
    activeId.value = agentOrder.value[0] ?? ''
  }
  refresh()
}

// 收到 assistant_output 流
on('assistant_output', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  const message_id = (msg.message_id as string | undefined) ?? `assistant-${msg.epoch ?? Date.now()}`
  const text = String(msg.text ?? '')
  const is_final = Boolean(msg.is_final)
  const is_thinking = (msg.is_thinking as boolean | undefined) ?? false
  ensurePanel(agent_id)

  const prev = streaming[agent_id]
  if (!prev || prev.messageId !== message_id) {
    const entryId = `${message_id}-${++assistantEntrySeq}`
    streaming[agent_id] = { messageId: message_id, entryId, text: '', thinking: '' }
    panels[agentId].push({ id: entryId, role: 'assistant', text: '', thinking: undefined, final: false })
  }

  const state = streaming[agent_id]!
  const entry = panels[agentId].find(m => m.id === state.entryId)

  if (is_thinking) {
    // thinking 增量不影响 text,单独累积
    state.thinking += text
    if (entry) entry.thinking = state.thinking
  } else if (entry) {
    // 第一个非 thinking 分片 → thinking 阶段结束,立即隐藏 thinking 块
    if (!entry.thinkingDone) entry.thinkingDone = true
    state.text = mergeStreamText(state.text, text, is_final)
    entry.text = state.text
  }

  // is_final 统一收尾:无论是否 thinking,标记结束 + 隐藏思考块.
  // 修复"LLM 只输出思考就 Completed / 思考中被中断"时思考块不消失的 bug.
  if (is_final && entry) {
    entry.thinkingDone = true
    entry.final = true
  }

  if (is_final) streaming[agent_id] = null

  if (agent_id !== activeId.value) {
    unread[agent_id] = (unread[agent_id] ?? 0) + (is_final ? 1 : 0)
  }
})

// 收到工具反馈
on('feedback', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  const results = normalizeToolFeedback(msg.results)
  const epoch = msg.epoch as number
  if (!results.length) return
  ensurePanel(agent_id)
  finishStreamingForTools(agent_id)
  for (const result of results) {
    upsertToolFeedback(agent_id, result, epoch)
  }
  if (agent_id !== activeId.value) {
    unread[agent_id] = (unread[agent_id] ?? 0) + 1
  }
})

// 收到入站 agent 消息 (send_message 投递, 实时渲染到接收方 panel)
on('incoming_message', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  const text = String(msg.text ?? '')
  const from_ = (msg.from as string | undefined) ?? ''
  ensurePanel(agent_id)
  panels[agent_id].push({
    id: `incoming-${Date.now()}`,
    role: 'user',
    text,
    final: true,
    from: from_ || undefined,
  })
  if (agent_id !== activeId.value) {
    unread[agent_id] = (unread[agent_id] ?? 0) + 1
  }
})

function finishStreamingForTools(agentId: string) {
  const state = streaming[agentId]
  if (!state) return
  const entry = panels[agentId].find(msg => msg.id === state.entryId)
  if (entry) {
    entry.final = true
    // 工具反馈到达 → 当前 streaming 结束,隐藏思考块
    entry.thinkingDone = true
  }
  streaming[agentId] = null
}

function normalizeToolFeedback(value: unknown): ToolFeedback[] {
  if (!Array.isArray(value)) return []
  return value.map((item, index) => {
    if (!item || typeof item !== 'object') {
      return { name: `tool_${index + 1}`, result: item, status: 'done' }
    }
    const raw = item as Record<string, unknown>
    const hasError = isRecord(raw.error) && typeof raw.error.msg === 'string'
    const status = raw.status === 'running' || raw.status === 'done' || raw.status === 'error'
      ? raw.status
      : (hasError ? 'error' : (raw.result === undefined ? 'running' : 'done'))
    return {
      name: String(raw.name || `tool_${index + 1}`),
      input: isRecord(raw.input) ? raw.input : undefined,
      result: raw.result,
      error: isRecord(raw.error) && typeof raw.error.msg === 'string'
        ? { msg: raw.error.msg }
        : undefined,
      rid: typeof raw.rid === 'string' ? raw.rid : undefined,
      call_id: typeof raw.call_id === 'string' ? raw.call_id : undefined,
      status,
    }
  })
}

function upsertToolFeedback(agentId: string, result: ToolFeedback, epoch: number) {
  // 优先用 call_id 匹配(call_id 在 LLM 返回时就有,running→done 全程不变).
  // rid 在 running 阶段为 None,只有 done 后才有,不能作主匹配键.
  const key = result.call_id || result.rid
  if (key) {
    const existing = panels[agentId].find(
      msg => msg.role === 'tool' && msg.results?.some(r => (r.call_id || r.rid) === key),
    )
    if (existing?.results?.[0]) {
      // 保留 startedAt (running 时记录的); done 时计算并锁定 duration
      const startedAt = existing.results[0].startedAt
      const isDone = result.status !== 'running'
      const duration = isDone && startedAt ? Date.now() - startedAt : undefined
      existing.results[0] = { ...result, startedAt, duration }
      existing.final = isDone
      return
    }
  }

  // running 状态: 记录开始时间
  if (result.status === 'running') {
    result.startedAt = Date.now()
  }

  panels[agentId].push({
    id: key ? `tool-${key}` : `fb-${epoch}-${Date.now()}-${panels[agentId].length}`,
    role: 'tool',
    text: '',
    results: [result],
    final: result.status !== 'running',
  })
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

// usage -- LLM 每次 Completed 后推送的 token 用量 + 上下文百分比
on('usage', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  agentUsage[agent_id] = {
    input_tokens: Number(msg.input_tokens) || 0,
    output_tokens: Number(msg.output_tokens) || 0,
    total_tokens: Number(msg.total_tokens) || 0,
    max_context: Number(msg.max_context) || 0,
    percent: Number(msg.percent) || 0,
    model_key: (msg.model_key as string | undefined) ?? '',
    model_name: (msg.model_name as string | undefined) ?? '',
    reasoning_effort: msg.reasoning_effort === undefined ? undefined : (msg.reasoning_effort as string | null),
  }
})

async function onRun(
  payload: { name: string, kwargs: Record<string, unknown> },
  onResult: (data: unknown[]) => void,
  onError: (msg: string) => void,
  onDone: () => void,
) {
  try {
    // 统一以 user 身份调用: POST /agents/user/run/{routine_name} body=kwargs.
    // 不设客户端超时:可能等用户交互(ui_request),固定超时会误杀.
    const res = await fetch(`${httpBase}/agents/user/run/${encodeURIComponent(payload.name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload.kwargs),
    })
    if (!res.ok) {
      onError(`HTTP ${res.status}`)
      return
    }
    const data = await res.json()
    if (!data.ok) {
      onError(data.error)
      return
    }
    onResult([{ name: payload.name, result: data.result }])
  } catch (e) {
    onError((e as Error).message)
  } finally {
    onDone()
  }
}

// 切换 tab 时清除未读
watch(activeId, (id) => {
  unread[id] = 0
})

// Esc 关闭浮窗 + 退出 create 视图
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    showRunner.value = false
    if (viewMode.value === 'create') viewMode.value = 'chat'
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: #0f1117;
  color: #e2e8f0;
  font-family: 'Segoe UI', system-ui, sans-serif;
  font-size: 14px;
  height: 100vh;
  overflow: hidden;
}

#app { height: 100vh; display: flex; flex-direction: column; }
</style>

<style scoped>
.app { display: flex; flex-direction: column; height: 100vh; }

.rr-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.ui-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.65);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.ui-unknown-card {
  width: 360px;
  background: #1e2130;
  border-radius: 10px !important;
}

.rr-float {
  width: 80vw;
  height: 70vh;
  min-width: 700px;
  min-height: 480px;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
  border: 1px solid #3d4270;
}

.app-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* ---- sidebar ---- */
.sidebar {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #0f1117;
  border-right: 1px solid #2d3148;
  overflow: hidden;  /* 禁止水平滚动 (NBadge offset / 长名字溢出不触发 x-scroll) */
}

.sidebar-new {
  margin: 8px 8px 4px;
  padding: 8px 12px;
  background: transparent;
  border: 1px solid #6366f1;
  color: #818cf8;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background .12s;
  flex-shrink: 0;
}
.sidebar-new:hover { background: #1e2235; }

.sidebar-list {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;  /* CSS 规范: overflow-y 非 visible 时 overflow-x 会隐式变 auto, 显式 hidden 避免水平滚动条 */
  padding: 4px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-empty {
  padding: 16px 12px;
  color: #64748b;
  font-size: 12px;
  text-align: center;
}

.main-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.main-empty-hint {
  color: #64748b;
  font-size: 14px;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 10px;
  border-radius: 6px;
  cursor: pointer;
  user-select: none;
  font-size: 13px;
  color: #9ca3af;
  border-left: 3px solid transparent;
  transition: background .12s, color .12s;
}
.sidebar-item:hover { background: #1a1d27; color: #e2e8f0; }
.sidebar-item-active {
  background: #1e2235;
  border-left-color: #6366f1;
  color: #c7d2fe;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot-live { background: #22c55e; box-shadow: 0 0 6px #22c55e88; }
.dot-stopped { background: #64748b; }

/* NBadge 包裹 .name 时, badge 本身在 flex 布局里没设 flex:1 / min-width:0,
   导致内部 .name 的 ellipsis 不生效 (名字撑开 badge -> 撑开 item -> 水平滚动).
   .name-badge 让 badge 跟原 .name 一样 flex 收缩 + overflow:hidden. */
.name-badge {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
}

.name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1;
}

.sidebar-close {
  flex-shrink: 0;
  font-size: 11px;
  color: #6b7280;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  line-height: 1;
}
.sidebar-close:hover { color: #f87171; background: #3a1d1d; }

.sidebar-stopping {
  flex-shrink: 0;
  font-size: 12px;
  color: #facc15;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  line-height: 1;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.sidebar-delete {
  flex-shrink: 0;
  font-size: 11px;
  color: #6b7280;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  line-height: 1;
  cursor: pointer;
}
.sidebar-delete:hover { color: #f87171; background: #3a1d1d; }

/* ---- main ---- */
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panels {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.create-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.create-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 24px;
  border-bottom: 1px solid #2d3148;
  flex-shrink: 0;
}
.create-header h3 {
  font-size: 14px;
  font-weight: 600;
  color: #cbd5e1;
}

.create-body {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  height: 44px;
  background: #1a1d27;
  border-bottom: 1px solid #2d3148;
  flex-shrink: 0;
}

.logo {
  font-weight: 700;
  font-size: 15px;
  color: #818cf8;
  letter-spacing: 1px;
  margin-right: 8px;
}

.header-spacer { flex: 1; }
</style>
