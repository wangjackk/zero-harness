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

      <!-- 后端连接: host/port 分离表单 + 确认按钮 (localStorage 持久化) -->
      <NPopover v-model:show="showBackend" trigger="click" placement="bottom-start">
        <template #trigger>
          <NButton size="small" quaternary class="backend-btn" title="后端连接">
            {{ backendLabel }}
          </NButton>
        </template>
        <div class="backend-form">
          <div class="backend-row">
            <span class="backend-label">Host</span>
            <NInput
              v-model:value="backendHost"
              size="small"
              placeholder="127.0.0.1"
              @keydown.enter="confirmBackend"
            />
          </div>
          <div class="backend-row">
            <span class="backend-label">Port</span>
            <NInput
              v-model:value="backendPort"
              size="small"
              placeholder="7781"
              @keydown.enter="confirmBackend"
            />
          </div>
          <div v-if="backendError" class="backend-error">{{ backendError }}</div>
          <div class="backend-footer">
            <div class="backend-presets">
              <NButton size="tiny" @click="usePreset('127.0.0.1', '7781')">zero2</NButton>
              <NButton size="tiny" @click="usePreset('127.0.0.1', '7780')">zero-ts</NButton>
            </div>
            <NButton size="tiny" type="primary" @click="confirmBackend">连接</NButton>
          </div>
        </div>
      </NPopover>

      <div class="header-spacer" />

      <!-- Prompt 面板 -->
      <NButton
        size="small"
        :type="showPrompt ? 'primary' : 'default'"
        :ghost="showPrompt"
        quaternary
        title="Prompt 面板"
        @click="showPrompt = !showPrompt"
      ><Icon :comp="Notes" :size="15" /></NButton>

      <!-- Routine Runner -->
      <NButton
        size="small"
        :type="showRunner ? 'primary' : 'default'"
        :ghost="showRunner"
        quaternary
        title="Routine Runner"
        @click="showRunner = !showRunner"
      ><Icon :comp="Bolt" :size="15" /></NButton>

      <!-- Routine Tree -->
      <NButton
        size="small"
        :type="showTree ? 'primary' : 'default'"
        :ghost="showTree"
        quaternary
        title="Routine Tree"
        @click="showTree = !showTree"
      ><Icon :comp="Sitemap" :size="15" /></NButton>

      <!-- 模型管理 -->
      <NButton
        size="small"
        :type="showModels ? 'primary' : 'default'"
        :ghost="showModels"
        quaternary
        title="模型管理"
        @click="showModels = !showModels"
      ><Icon :comp="Settings" :size="15" /></NButton>

      <!-- 模块树编辑 -->
      <NButton
        size="small"
        :type="showModuleTree ? 'primary' : 'default'"
        :ghost="showModuleTree"
        quaternary
        title="模块树"
        @click="showModuleTree = !showModuleTree"
      ><Icon :comp="Components" :size="15" /></NButton>
    </header>

    <Teleport to="body">
      <div v-show="showRunner" class="rr-overlay" @click.self="showRunner = false">
        <RoutineRunner class="rr-float" :routines="routines" @run="onRun" @interrupt="onInterruptRun" />
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-show="showTree" class="rt-overlay" @click.self="showTree = false">
        <div class="rt-panel">
          <RoutineTree
            :nodes="routineTree"
            :events="lifecycleEvents"
            :selected="selectedRoutine"
            class="rt-side"
            @select="onTreeSelect"
            @back="selectedRoutine = null"
          />
          <RoutineTimeline :events="lifecycleEvents" :root-id="selectedRoutine" class="rt-main" />
        </div>
      </div>
    </Teleport>

    <!-- 模型管理浮窗 -->
    <Teleport to="body">
      <div v-show="showModels" class="mp-overlay" @click.self="showModels = false">
        <ModelsPanel class="mp-float" :http-base="httpBase" :active="showModels" />
      </div>
    </Teleport>

    <!-- 模块树编辑浮窗 -->
    <Teleport to="body">
      <div v-show="showModuleTree" class="mp-overlay" @click.self="showModuleTree = false">
        <ModuleTreeEditor class="mp-float" :http-base="httpBase" :active="showModuleTree" />
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

    <div class="app-body" ref="bodyEl">
      <!-- 左侧 sidebar -->
      <aside class="sidebar">
        <div class="sidebar-actions">
          <button class="sidebar-new" @click="viewMode = 'create'">
            <Icon :comp="CirclePlus" :size="14" />New
          </button>
        </div>
        <div class="sidebar-list">
          <template v-for="g in sidebarGroups" :key="g.label">
            <div class="sidebar-group-label">{{ g.label }}</div>
            <div
              v-for="a in g.list"
              :key="a.agent_id"
              class="sidebar-item"
              :class="{ 'sidebar-item-active': activeId === a.agent_id && viewMode === 'chat' }"
              @click="selectAgent(a.agent_id)"
            >
              <span class="dot" :class="dotClass(a.agent_id)" />
              <NBadge
                class="name-badge"
                :value="unread[a.agent_id] || 0"
                :show="!!unread[a.agent_id]"
                color="#6366f1"
                :offset="[4, -2]"
              >
                <span class="name">{{ getAgentName(a.agent_id) }}</span>
              </NBadge>
              <span
                v-if="stoppingIds.has(a.agent_id)"
                class="sidebar-stopping"
                title="停止中..."
              ><Icon :comp="Loader" :size="12" /></span>
              <span
                v-else-if="liveSet[a.agent_id]"
                class="sidebar-close"
                title="stop + close"
                @click.stop="closeTab(a.agent_id)"
              ><Icon :comp="X" :size="12" /></span>
              <span
                v-else
                class="sidebar-delete"
                title="删除 (不可恢复)"
                @click.stop="deleteAgent(a.agent_id)"
              ><Icon :comp="Trash" :size="12" /></span>
            </div>
          </template>
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
            :status="agentStatus[id]"
            :readonly="agentReadonly[id]"
            :stopped="!liveSet[id]"
            :usage="agentUsage[id]"
            :http-base="httpBase"
            @send="(text: string, images?: string[]) => sendInput(text, id, images)"
            @interrupt="() => send({ type: 'interrupt', agent_id: id })"
            @set-effort="(effort: string) => setEffort(id, effort)"
          />
        </div>

        <!-- create view: 懒挂载 -->
        <div v-if="viewMode === 'create'" class="create-view">
          <header class="create-header">
            <span class="create-title">新建 Agent</span>
            <NButton size="small" @click="viewMode = 'chat'">取消</NButton>
          </header>
          <div class="create-body">
            <AgentCreateForm
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

      <!-- 右侧 Prompt panel (可折叠, 可拖宽) -->
      <template v-if="showPrompt && activeId">
        <div class="divider" @mousedown="startDrag" />
        <PromptPanel
          :epoch="agentEpochs[activeId] ?? 0"
          :messages="agentPrompts[activeId] ?? []"
          class="prompt-sidebar"
          :style="{ width: sidebarWidth + 'px' }"
        />
      </template>
    </div>
  </div>
  <div id="toast" v-show="toastMsg">{{ toastMsg }}</div>
  <ConfirmDialog />
  </NConfigProvider>
</template>

<script setup lang="ts">
import { ref, reactive, watch, computed, onMounted, onUnmounted } from 'vue'
import { NConfigProvider, NButton, NBadge, NTag, NCard, NAlert, NPopover, NInput, darkTheme } from 'naive-ui'
import type { GlobalThemeOverrides } from 'naive-ui'
import AgentPanel from './components/AgentPanel.vue'
import RoutineRunner from './components/RoutineRunner.vue'
import RoutineTree from './components/RoutineTree.vue'
import RoutineTimeline from './components/RoutineTimeline.vue'
import type { RoutineNode } from './components/RoutineTree.vue'
import type { LifecycleEvent } from './components/RoutineTimeline.vue'
import PromptPanel from './components/PromptPanel.vue'
import SelectorDialog from './components/SelectorDialog.vue'
import TableDialog from './components/TableDialog.vue'
import DateDialog from './components/DateDialog.vue'
import Icon from './components/Icon.vue'
import { Notes, Bolt, Sitemap, Settings, CirclePlus, Loader, X, Trash, Components } from '@vicons/tabler'
import AgentCreateForm from './components/AgentCreateForm.vue'
import ModelsPanel from './components/ModelsPanel.vue'
import ModuleTreeEditor from './components/ModuleTreeEditor.vue'
import type { RoutineInfo } from './components/RoutineRunner.vue'
import { useWS } from './composables/useWS'
import { useUIRequests } from './composables/useUIRequests'
import type { UiRequest } from './composables/useUIRequests'
import { useAudioPlayer } from './composables/useAudioPlayer'
import { useAgents } from './composables/useAgents'
import type { CreateAgentParams, AgentRow } from './composables/useAgents'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { confirmDialog } from './composables/confirm'

interface PanelMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  text: string
  thinking?: string
  thinkingDone?: boolean
  final: boolean
  results?: ToolFeedback[]
  from?: string
  images?: string[]
  image?: { data: string; mime: string; caption?: string }
  emotion?: { instruction?: string, valence?: number, arousal?: number }
}

interface ToolFeedback {
  name: string
  input?: Record<string, unknown>
  result?: unknown
  error?: { msg: string }
  rid?: string
  call_id?: string
  status?: 'running' | 'done' | 'error' | 'interrupted'
  startedAt?: number  // running 时记录的时间戳 (ms)
  duration?: number   // done 时记录的总耗时 (ms)
  images?: string[]   // display 捕获图像的 /files 路径 (ipython Out 渲染)
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
  cache_read?: number
  cache_write?: number
  model_key?: string
  model_name?: string
  reasoning_effort?: string | null
}

interface PromptMessage {
  role: string
  content: string
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

// ---- 后端连接 (localStorage 持久化, 顶栏 host/port 表单 + 确认) ----
// zero2 与 zero-ts 是两套独立后端 (各自的 sqlite agents 库).
// 默认值按环境区分:
//   - 生产: 同源 location.origin (前端由 web_server 托管 dist, 天然指向
//     设备自身 ---- 远程浏览器访问 http://<设备IP>:7781 不会错连访问者本机)
//   - dev: 直指 vite proxy 的 target (127.0.0.1:7781), 显示与真实后端
//     一致且少走一跳 proxy (此前默认 origin 显示 5250 是前端自己, 误导)
const BACKEND_KEY = 'zero.backend'
const backend = ref(
  localStorage.getItem(BACKEND_KEY)
  || (import.meta.env.DEV ? 'http://127.0.0.1:7781' : window.location.origin),
)

function parseBackend(url: string): { host: string; port: string } {
  try {
    const u = new URL(url)
    return { host: u.hostname || '127.0.0.1', port: u.port || '80' }
  } catch {
    return { host: '127.0.0.1', port: '7781' }
  }
}

const backendLabel = computed(() => {
  const { host, port } = parseBackend(backend.value)
  return `${host}:${port}`
})

// 表单状态: 每次打开 popover 都重置为当前生效的后端 (丢弃未确认的编辑)
const showBackend = ref(false)
const backendError = ref('')
const _initBackend = parseBackend(backend.value)
const backendHost = ref(_initBackend.host)
const backendPort = ref(_initBackend.port)

watch(showBackend, (v) => {
  if (!v) return
  const { host, port } = parseBackend(backend.value)
  backendHost.value = host
  backendPort.value = port
  backendError.value = ''
})

// HTTP base: /run_routine 等端炵走它
const httpBase = computed(() => backend.value)
// WS 直连后端 (不走 vite 代理 ---- 代理目标写死 7781, 切后端必须直连;
// 后端 WS 不校验 Origin, CORS allow_origins=['*'] 已放开 HTTP)
const wsUrl = computed(() => backend.value.replace(/^http/, 'ws') + '/ws')

const ws = useWS(wsUrl)
const { connected, on, send, request, runRoutine, reconnect } = ws

// RoutineRunner 停止: fire-and-forget, run 的响应会因打断自行到达.
function onInterruptRun() {
  send({ type: 'interrupt_run', agent_id: 'user' })
}
useAudioPlayer(ws)

// useAgents: sidebar history + project suggestions
const {
  agents, refresh, createAgent, creating, resumeAgent, stopAgent, deleteAgent: _deleteAgent,
} = useAgents(httpBase)

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

// ---- routine 树实时状态 (lifecycle 观察流, web_server 直连 watch 广播) ----
const routineTree = reactive(new Map<string, RoutineNode>())
const lifecycleEvents = reactive<LifecycleEvent[]>([])
const showTree = ref(false)
// 点击树节点 → 时间轨只渲染该节点子树; 返回按钮回到上一级
const selectedRoutine = ref<string | null>(null)

function onTreeSelect(id: string) {
  selectedRoutine.value = id || null
}

function applyLifecycleFrame(t: unknown, id: unknown, pid: unknown, name: unknown) {
  const ev = String(t ?? '')
  const rid = String(id ?? '')
  if (!rid) return
  if (ev === 'created' || ev === 'started') {
    routineTree.set(rid, {
      id: rid,
      parent_id: String(pid ?? '0'),
      name: String(name ?? rid),
      state: ev === 'started' ? 'running' : 'created',
    })
  } else {
    routineTree.delete(rid) // stopped / destroyed
    // 选中节点已结束: 从 live 树移除, 自动弹回上一级
    if (selectedRoutine.value === rid) selectedRoutine.value = String(pid ?? '0') || null
  }
}

function pushEvent(t: unknown, id: unknown, pid: unknown, name: unknown, ts?: unknown) {
  const ev = String(t ?? '')
  if (!ev || !id) return
  lifecycleEvents.push({
    t: typeof ts === 'number' ? ts : Date.now(), ev,
    id: String(id), pid: String(pid ?? '0'), name: String(name ?? id),
  })
  // 历史上限: 防止长会话无限增长 (时间轨只渲染有限窗口)
  if (lifecycleEvents.length > 600) lifecycleEvents.splice(0, lifecycleEvents.length - 600)
}

on('lifecycle', (msg) => {
  applyLifecycleFrame(msg.event_type, msg.id, msg.parent_id, msg.name)
  pushEvent(msg.event_type, msg.id, msg.parent_id, msg.name)
})

// 连接/重连时的全量基线: 服务端缓存的事件历史(真实时间戳)先回放,
// 树快照再兜底(watch 之前启动的 routine 历史里没有, 用当前时刻补).
on('lifecycle_snapshot', (msg) => {
  routineTree.clear()
  lifecycleEvents.splice(0)
  const hist = (msg.events ?? []) as Array<Record<string, unknown>>
  const seen = new Set<string>()
  for (const e of hist) {
    pushEvent(e.type, e.id, e.parent_id, e.name, e.t)
    seen.add(String(e.id))
  }
  const list = (msg.routines ?? []) as Array<Record<string, unknown>>
  for (const r of list) {
    const rid = String(r.id)
    const ev = r.state === 'running' ? 'started' : 'created'
    applyLifecycleFrame(ev, rid, r.parent_id, r.name)
    if (!seen.has(rid)) pushEvent(ev, rid, r.parent_id, r.name)
  }
})

// routines 走 HTTP /run_routine (user_agent 通道):WS 不再有 routines 推送通道.
// wire 形状 is_passive 是 {flag} (py/ts kernel 一致), 前端展平成 boolean.
async function fetchRoutines() {
  try {
    const res = await fetch(`${httpBase.value}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'routine/list', kwargs: {} }),
    })
    if (!res.ok) return
    const data = await res.json()
    const raw = (data?.result?.routines ?? []) as Array<Record<string, any>>
    routines.value = raw.map(r => ({
      ...r,
      is_passive: Boolean((r.is_passive ?? {}).flag),
    })) as RoutineInfo[]
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

/** 应用新后端: 持久化 + 清运行时状态 + WS 立即重连 (connected watch 自动重拉). */
function applyBackend(next: string) {
  if (next === backend.value) return
  backend.value = next
  localStorage.setItem(BACKEND_KEY, next)
  // 后端相关运行时状态全清 (agents/messages 都是后端各自的库)
  for (const key of Object.keys(panels)) delete panels[key]
  for (const key of Object.keys(agentNames)) delete agentNames[key]
  for (const key of Object.keys(agentReadonly)) delete agentReadonly[key]
  for (const key of Object.keys(liveSet)) delete liveSet[key]
  for (const key of Object.keys(unread)) delete unread[key]
  for (const key of Object.keys(streaming)) delete streaming[key]
  for (const key of Object.keys(agentStatus)) delete agentStatus[key]
  for (const key of [...agentStatusTimers.keys()]) {
    clearTimeout(agentStatusTimers.get(key))
    agentStatusTimers.delete(key)
  }
  for (const key of [...streamBuf.keys()]) cancelStreamBuf(key)
  for (const key of Object.keys(agentUsage)) delete agentUsage[key]
  for (const key of Object.keys(agentEpochs)) delete agentEpochs[key]
  for (const key of Object.keys(agentPrompts)) delete agentPrompts[key]
  agentOrder.value = []
  activeId.value = ''
  routines.value = []
  agents.value = []
  reconnect()
}

/** 表单确认: host/port 校验后拼 URL 应用. */
function confirmBackend() {
  const host = backendHost.value.trim() || '127.0.0.1'
  const port = backendPort.value.trim()
  if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
    backendError.value = '端口必须是 1-65535 的数字'
    return
  }
  backendError.value = ''
  showBackend.value = false
  applyBackend(`http://${host}:${port}`)
}

/** 预设一键连接 (zero2 / zero-ts 快捷按钮, 填表 + 应用一步). */
function usePreset(host: string, port: string) {
  backendHost.value = host
  backendPort.value = port
  backendError.value = ''
  showBackend.value = false
  applyBackend(`http://${host}:${port}`)
}

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
  // 刷新回填: 重连时 get_history 捎带的上一轮 usage, 恢复圆环
  if (msg.usage) applyUsage(msg.usage as Record<string, any>)
})


function sendInput(text: string, agentId: string, images?: string[]) {
  if (!agentId) return
  ensurePanel(agentId)
  const msg: PanelMessage = { id: `user-${Date.now()}`, role: 'user', text, final: true }
  if (images?.length) msg.images = images
  panels[agentId].push(msg)
  // user 与 agent 平等: WS run_routine 调 send_message (user agent 身份).
  // send_message routine 内部 req 目标 agent 的 chat_message, 触发 react.
  // content 数组: text 与 image 平权 (统一消息协议).
  const content: Record<string, unknown>[] = []
  if (text) content.push({ type: 'text', text })
  for (const p of images ?? []) content.push({ type: 'image', path: p })
  runRoutine('send_message', { to: agentId, content }, 15000)
    .then((r: any) => {
      // result 双层: {ok, result: {ok, error|epoch}} ---- 内层才是 send_message 的返回
      if (r?.result?.ok === false) console.error('send_message failed:', r.result.error)
      else if (r && r.ok === false) console.error('send_message failed:', r.error)
    })
    .catch((err) => console.error('send_message failed:', err))
}

function setEffort(agentId: string, effort: string) {
  request({ type: 'set_effort', agent_id: agentId, effort }).then((m) => {
    if (m.ok === false) toast(`effort: ${m.error || 'failed'}`)
  }).catch(() => {})
}

// 轻量 toast (对齐 flow 前端): 操作失败等瞬时提示
const toastMsg = ref('')
let toastTimer: ReturnType<typeof setTimeout> | undefined
function toast(msg: string): void {
  toastMsg.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMsg.value = '' }, 3000)
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
// agent 瞬态状态条 (compaction 等): phase + 文本, idle 后短停留自动清除
const agentStatus = reactive<Record<string, { phase: string, text: string }>>({})
const agentStatusTimers = new Map<string, ReturnType<typeof setTimeout>>()
// 已收完 final 的 assistant message_id 集合: 同 id 的后续帧(打断补发的幂等
// final)直接丢弃, 不再当新消息渲染(否则同一段输出显示两条)
const finalizedMsgIds = new Set<string>()
const showRunner = ref(false)
// 模型管理浮窗 (provider/model 配置增删改, models/* routine 通道)
const showModels = ref(false)
// 模块树编辑浮窗 (tree.json 静态定义读写)
const showModuleTree = ref(false)
const agentUsage = reactive<Record<string, UsageInfo>>({})
// prompt 面板: 可折叠右侧栏, 按 agent 分 epoch + messages (sys_prompt 事件填充)
const showPrompt = ref(false)
const sidebarWidth = ref(750)
const bodyEl = ref<HTMLElement | null>(null)
const agentEpochs = reactive<Record<string, number>>({})
const agentPrompts = reactive<Record<string, PromptMessage[]>>({})

// 视图模式: chat = 显示当前 agent 对话; create = 显示新建 agent 表单
const viewMode = ref<'chat' | 'create'>('chat')

// create view: 由 AgentCreateForm 承载 (agent_hub/types 类型清单 + manifest 动态字段)

// 项目历史 (从 useAgents 拉, 给创建表单的 project_dir 自动补全)
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

// 日期分组 (本地时区): 今天 / 昨天 / 近 7 天 / 更早. 历史积累多时平铺一堵墙
// 挤在一起, 分组标题给视觉锚点. 空组不渲染.
const DAY_MS = 86_400_000
const sidebarGroups = computed(() => {
  const sorted = agents.value
    .slice()
    .sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0))
  const t0 = new Date().setHours(0, 0, 0, 0)
  const buckets = [
    { label: '今天', min: t0, list: [] as typeof sorted },
    { label: '昨天', min: t0 - DAY_MS, list: [] as typeof sorted },
    { label: '近 7 天', min: t0 - 6 * DAY_MS, list: [] as typeof sorted },
    { label: '更早', min: -Infinity, list: [] as typeof sorted },
  ]
  for (const a of sorted) {
    const t = Date.parse(a.created_at)
    for (const b of buckets) {
      if (t >= b.min) { b.list.push(a); break }
    }
  }
  return buckets.filter(b => b.list.length)
})

function getAgentName(id: string): string {
  const a = agents.value.find(x => x.agent_id === id)
  if (a?.title) return a.title
  if (agentNames[id]) return agentNames[id]
  if (a) return 'Pi-' + a.agent_id.slice(0, 8)
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
    const row = await resumeAgent(a)
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

// 子表单创建成功: 切到新 agent 的 chat view (kind 由表单按 manifest 路由, 这里不感知)
function onChildCreate(_kind: string, _params: CreateAgentParams, newAgentId: string) {
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

// close a tab: stop the live agent (POST /run_routine), then drop local panel/tab/state.
// stopped agents just lose their tab. 之后 refresh useAgents, 让刚停的 agent 回到 sidebar 的历史区 (灰点).
const stoppingIds = ref<Set<string>>(new Set())
async function closeTab(agentId: string) {
  if (stoppingIds.value.has(agentId)) return  // 防重复点击
  if (liveSet[agentId]) {
    stoppingIds.value.add(agentId)
    try {
      const row = agents.value.find(x => x.agent_id === agentId)
      if (row) await stopAgent(row)
    } finally {
      stoppingIds.value.delete(agentId)
      liveSet[agentId] = false
    }
  }
  delete panels[agentId]
  delete streaming[agentId]
  delete agentStatus[agentId]
  const st = agentStatusTimers.get(agentId)
  if (st) { clearTimeout(st); agentStatusTimers.delete(agentId) }
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
  const ok = await confirmDialog({
    title: '删除 agent',
    content: `删除 agent "${name}"?\n将清除所有历史消息, 不可恢复.`,
    positiveText: '删除', danger: true,
  })
  if (!ok) return
  const row = agents.value.find(x => x.agent_id === agentId)
  if (!row) return
  const deleted = await _deleteAgent(row)
  if (!deleted) return
  // 清本地状态 (跟 closeTab 一致, 但 DB 已删, refresh 后不会回到 sidebar)
  delete panels[agentId]
  delete streaming[agentId]
  delete agentStatus[agentId]
  const st = agentStatusTimers.get(agentId)
  if (st) { clearTimeout(st); agentStatusTimers.delete(agentId) }
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
// 合帧同步: 累计立即 (纯字符串 merge, 零渲染开销), 渲染挂 rAF 与显示刷新
// 同步 ---- 渲染积压时直接跳到最新状态, 不逐帧追赶播放 (打断后立即静止)
interface StreamBuf {
  messageId: string
  text: string
  thinking: string
  raf: number | null
}
const streamBuf = new Map<string, StreamBuf>()

// 本段情绪: emotion 事件先于首帧 assistant_output 到达 (agent submit 前 publish),
// 暂存于此, flushStream 建新 assistant entry 时盖章为该段的情绪标签
const pendingEmotion = new Map<string, PanelMessage['emotion']>()

function cancelStreamBuf(agentId: string) {
  const buf = streamBuf.get(agentId)
  if (!buf) return
  if (buf.raf !== null) cancelAnimationFrame(buf.raf)
  streamBuf.delete(agentId)
}

function flushStream(agentId: string, isFinal = false) {
  const buf = streamBuf.get(agentId)
  if (!buf) return
  if (buf.raf !== null) { cancelAnimationFrame(buf.raf); buf.raf = null }
  if (isFinal) streamBuf.delete(agentId)
  if (!buf.text && !buf.thinking && !isFinal) return
  ensurePanel(agentId)

  // entry id = messageId: 同一消息的重画幂等 upsert ---- 工具反馈清掉 streaming
  // 态后, 同 id 的后续帧更新既有 entry 而不是带全量累计文本重建新条目 (防重复).
  let entry = panels[agentId].find(m => m.id === buf.messageId)
  if (!entry) {
    entry = { id: buf.messageId, role: 'assistant', text: '', thinking: undefined, final: false,
              emotion: pendingEmotion.get(agentId) }
    panels[agentId].push(entry)
  }
  streaming[agentId] = { messageId: buf.messageId, entryId: buf.messageId, text: buf.text, thinking: buf.thinking }
  if (entry) {
    if (buf.thinking) entry.thinking = buf.thinking
    // 有正文 → thinking 阶段结束, 立即隐藏 thinking 块
    if (buf.text) {
      if (!entry.thinkingDone) entry.thinkingDone = true
      entry.text = buf.text
    }
    // is_final 统一收尾: 无论是否 thinking, 标记结束 + 隐藏思考块.
    // 修复"LLM 只输出思考就 Completed / 思考中被中断"时思考块不消失的 bug.
    if (isFinal) {
      entry.thinkingDone = true
      entry.final = true
    }
  }

  if (isFinal) {
    streaming[agentId] = null
    if (agentId !== activeId.value) {
      unread[agentId] = (unread[agentId] ?? 0) + 1
    }
  }
}

const __lagStats = { n: 0, sum: 0, max: 0, lastLog: 0 }
on('assistant_output', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  // 帧延迟追踪 (源头 ts vs 浏览器到达): 高延迟 = 渲染/WS 积压信号
  const ts = (msg as Record<string, unknown>).ts
  if (typeof ts === 'number') {
    const lag = Date.now() - ts
    __lagStats.n += 1
    __lagStats.sum += lag
    __lagStats.max = Math.max(__lagStats.max, lag)
    if (Date.now() - __lagStats.lastLog > 2000 && __lagStats.n > 1) {
      console.info(`[stream-lag] frames=${__lagStats.n} avg=${Math.round(__lagStats.sum / __lagStats.n)}ms max=${__lagStats.max}ms`)
      __lagStats.n = 0; __lagStats.sum = 0; __lagStats.max = 0
      __lagStats.lastLog = Date.now()
    }
  }
  const message_id = (msg.message_id as string | undefined) ?? `assistant-${msg.epoch ?? Date.now()}`
  const text = String(msg.text ?? '')
  const is_final = Boolean(msg.is_final)
  const is_thinking = (msg.is_thinking as boolean | undefined) ?? false
  // 幂等去重: 该 message_id 已收过 final, 后续帧(流后打断的补发 final)整体丢弃
  if (finalizedMsgIds.has(message_id)) return
  if (is_final) finalizedMsgIds.add(message_id)

  // 累计层: 纯字符串合并, 不触发渲染
  let buf = streamBuf.get(agent_id)
  if (buf && buf.messageId !== message_id) {
    flushStream(agent_id)   // 换消息: 旧缓冲立即冲刷, 文本不丢
    buf = undefined
  }
  if (!buf) {
    buf = { messageId: message_id, text: '', thinking: '', raf: null }
    streamBuf.set(agent_id, buf)
  }
  if (is_thinking) {
    buf.thinking = mergeStreamText(buf.thinking, text, false)
  } else {
    buf.text = mergeStreamText(buf.text, text, is_final)
  }
  if (is_final) {
    flushStream(agent_id, true)
  } else if (buf.raf === null) {
    buf.raf = requestAnimationFrame(() => {
      if (streamBuf.get(agent_id) === buf) flushStream(agent_id)
    })
  }
})

// 收到本段情绪 (TTS 指令 + 心境双轴), 暂存待首帧到达盖章
on('emotion', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  pendingEmotion.set(agent_id, {
    instruction: String(msg.instruction ?? ''),
    valence: msg.valence as number | undefined,
    arousal: msg.arousal as number | undefined,
  })
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

// agent 瞬态状态 (running/compacting/idle): compacting 持续显示,
// running 有文本时短停留 (如压缩结果), 无文本不显示, idle 清除
on('agent_status', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  const phase = String(msg.phase ?? '')
  const text = String(msg.text ?? '')
  const prevTimer = agentStatusTimers.get(agent_id)
  if (prevTimer) clearTimeout(prevTimer)
  if (phase === 'idle' || !text && phase !== 'compacting') {
    delete agentStatus[agent_id]
    return
  }
  agentStatus[agent_id] = { phase, text }
  if (phase !== 'compacting') {
    agentStatusTimers.set(agent_id, setTimeout(() => {
      agentStatusTimers.delete(agent_id)
      delete agentStatus[agent_id]
    }, 6000))
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

// 收到图片推送 (show_image routine → web_server 广播), 渲染图片气泡
on('show_image', (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  const data = String(msg.data ?? '')
  if (!agent_id || !data) return
  ensurePanel(agent_id)
  panels[agent_id].push({
    id: `image-${Date.now()}`,
    role: 'assistant',
    text: '',
    final: true,
    image: {
      data,
      mime: String(msg.mime || 'image/png'),
      caption: (msg.caption as string | undefined) || undefined,
    },
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
      || raw.status === 'interrupted'
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
      images: Array.isArray(raw.images) ? raw.images.map(String) : undefined,
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

// sys_prompt -- WS 只发轻量通知 (epoch + count), 全量经 /run_routine 拉取
const promptAppliedEpoch: Record<string, number> = {}
on('sys_prompt', async (msg) => {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  const epoch = msg.epoch as number | undefined
  if (epoch !== undefined) agentEpochs[agent_id] = epoch
  try {
    const res = await fetch(`${httpBase.value}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'sys_prompt', kwargs: { agent_id } }),
    })
    if (!res.ok) return
    const body = await res.json()
    const data = body?.result
    if (!body?.ok || !data) return
    // 乱序守卫: 并发拉取响应可能乱序, 只应用更新的 epoch
    const snapEpoch = Number(data.epoch) || 0
    if (snapEpoch < (promptAppliedEpoch[agent_id] ?? 0)) return
    promptAppliedEpoch[agent_id] = snapEpoch
    const msgs = data.messages as PromptMessage[] | undefined
    if (msgs?.length) agentPrompts[agent_id] = msgs
  } catch { /* 拉取失败保持旧快照 */ }
})

// prompt 面板拖宽 (divider col-resize)
function startDrag(e: MouseEvent) {
  e.preventDefault()
  const startX = e.clientX
  const startW = sidebarWidth.value
  document.body.style.userSelect = 'none'

  function onMove(ev: MouseEvent) {
    const dx = startX - ev.clientX
    const bodyW = bodyEl.value?.clientWidth ?? window.innerWidth
    sidebarWidth.value = Math.min(Math.max(startW + dx, 200), bodyW - 300)
  }
  function onUp() {
    document.body.style.userSelect = ''
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// usage -- LLM 每次 Completed 后推送的 token 用量 + 上下文百分比
// (实时事件与 session_changed 回填共用字段映射)
function applyUsage(msg: Record<string, any>): void {
  const agent_id = (msg.agent_id as string | undefined) ?? ''
  if (!agent_id) return
  agentUsage[agent_id] = {
    input_tokens: Number(msg.input_tokens) || 0,
    output_tokens: Number(msg.output_tokens) || 0,
    total_tokens: Number(msg.total_tokens) || 0,
    max_context: Number(msg.max_context) || 0,
    percent: Number(msg.percent) || 0,
    cache_read: Number(msg.cache_read) || 0,
    cache_write: Number(msg.cache_write) || 0,
    model_key: (msg.model_key as string | undefined) ?? '',
    model_name: (msg.model_name as string | undefined) ?? '',
    reasoning_effort: msg.reasoning_effort === undefined ? undefined : (msg.reasoning_effort as string | null),
  }
}

on('usage', applyUsage)

async function onRun(
  payload: { name: string, kwargs: Record<string, unknown> },
  onResult: (data: unknown[]) => void,
  onError: (msg: string) => void,
  onDone: () => void,
) {
  try {
    // 统一以 user agent 身份调用: WS run_routine {name, kwargs}.
    // 不设客户端超时:可能等用户交互(ui_request),固定超时会误杀.
    const data = await runRoutine(payload.name, payload.kwargs)
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
    showModels.value = false
    showModuleTree.value = false
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

/* 暗色滚动条 (对齐 flow 前端风格): 浏览器默认亮色在暗背景上突兀 */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #2d3148; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #4f5480; }
* { scrollbar-width: thin; scrollbar-color: #2d3148 transparent; }

#toast {
  position: fixed; bottom: 100px; left: 50%; transform: translateX(-50%);
  background: #1c2130; border: 1px solid #d96b6b; color: #e2e8f0;
  border-radius: 6px; padding: 6px 14px; font-size: 12px;
  z-index: 10; box-shadow: 0 4px 16px rgba(0, 0, 0, .4);
}
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

/* 模型管理浮窗 (同 Routine Runner 的居中 overlay) */
.mp-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.mp-float {
  width: 80vw;
  height: 70vh;
  min-width: 700px;
  min-height: 480px;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
  border: 1px solid #3d4270;
}

.rt-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.rt-panel {
  display: flex;
  align-items: stretch;
  width: 82vw;
  height: 70vh;
  min-width: 720px;
}

.app-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.prompt-sidebar {
  flex-shrink: 0;
  overflow: hidden;
}

.divider {
  width: 4px;
  flex-shrink: 0;
  background: #2d3148;
  cursor: col-resize;
  transition: background 0.15s;
}
.divider:hover { background: #6366f1; }

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

.sidebar-actions {
  display: flex;
  gap: 6px;
  margin: 8px 8px 4px;
  flex-shrink: 0;
}
.sidebar-new {
  flex: 1;
  padding: 8px 12px;
  background: transparent;
  border: 1px solid #6366f1;
  color: #818cf8;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background .12s;
  white-space: nowrap;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}
.sidebar-new:hover { background: #1e2235; }
.sidebar-new:disabled { opacity: .5; cursor: not-allowed; }

.sidebar-list {
  flex: 1;
  min-height: 0;  /* flex column 里允许收缩到可用高度以下, 滚动才生效 */
  overflow-y: auto;
  overflow-x: hidden;  /* CSS 规范: overflow-y 非 visible 时 overflow-x 会隐式变 auto, 显式 hidden 避免水平滚动条 */
  padding: 4px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-group-label {
  font-size: 10px;
  color: #64748b;
  letter-spacing: .5px;
  padding: 10px 10px 4px;
  flex-shrink: 0;
  user-select: none;
}
.sidebar-group-label:first-child { padding-top: 6px; }

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
  flex-shrink: 0;  /* column flex 默认 shrink=1: 超高时条目被压扁而不是溢出滚动 */
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
  position: relative;
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

.create-title {
  font-size: 13px;
  font-weight: 600;
  color: #cbd5e1;
  user-select: none;
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

/* 后端连接表单 (顶栏 popover): host/port 分离 + 确认按钮 */
.backend-btn {
  font-family: Consolas, monospace;
  color: #8b90a8;
  flex-shrink: 0;
}

.backend-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 240px;
}

.backend-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.backend-label {
  width: 34px;
  flex-shrink: 0;
  font-size: 12px;
  color: #8b90a8;
}

.backend-error {
  font-size: 12px;
  color: #e5484d;
}

.backend-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.backend-presets {
  display: flex;
  gap: 4px;
}
</style>
