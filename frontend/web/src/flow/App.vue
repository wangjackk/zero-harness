<!-- Flow 独立前端 ---- dsh 概念: turn 边界 / followup / steer / inject 三档输入 /
     compaction summary / usage 压力条. 直连 FlowHub (默认 7790). -->
<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { renderMarkdown } from '../utils/markdown'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { confirmDialog } from '../composables/confirm'

/* ── 类型 ── */
interface ToolResult {
  call_id?: string
  name?: string
  input?: unknown
  result?: unknown
  status?: string
}
interface HistoryMsg { role?: string; text?: string; results?: ToolResult[] }
interface AgentInfo {
  agent_id: string; live?: boolean; title?: string
  project_dir?: string; created_at?: number
}
interface WsMsg { type: string; [k: string]: unknown }
interface Stats {
  turns?: number; steps?: number
  llm_ms?: number; tool_ms?: number
  ttft_ms?: number; ttft_steps?: number
  decode_ms?: number; decode_tokens?: number
  uncached_input?: number; cache_read?: number; cache_write?: number
  output_tokens?: number
}

interface MsgBlock {
  kind: 'msg'; role: string; text: string; think: string
  thinking: boolean; isSummary: boolean
}
interface ToolBlock { kind: 'tool'; results: ToolResult[]; open: boolean }
interface TurnBlock { kind: 'turn'; label: string }
type Block = MsgBlock | ToolBlock | TurnBlock

const MODE_HINT: Record<string, string> = {
  followup: '排队下一轮,不打断当前轮',
  steer: '下一个步骤边界生效,不打断在途请求',
  inject: '静默注入上下文,不唤醒循环',
}

/* hub 地址: ?hub=host:port 可覆盖 */
const HUB = new URLSearchParams(location.search).get('hub')
  || `${location.hostname || '127.0.0.1'}:7790`

/* ── 状态 ── */
const agents = ref<AgentInfo[]>([])
const cur = ref<string | null>(null)
const mode = ref('followup')
const connOn = ref(false)
const toastMsg = ref('')
const emptyText = ref('')
const inputText = ref('')
const effortSel = ref('')
const usage = reactive({ pct: 0, inTok: 0, max: 0 })
const stats = ref<Stats>({})
const msgsEl = ref<HTMLElement>()

/* hero (无会话): 居中输入栏, 发送即建会话 (dsh WorkspacePicker 流) */
const heroText = ref('')
const heroWs = ref(localStorage.getItem('flow.ws') || '')

/* 系统原生文件夹选择窗口 (hub → OS dialog, dsh native picker 语义) */
async function browse(): Promise<void> {
  try {
    const r = await request({ type: 'pick_dir_native' }, 600000)
    if (r.ok && r.path) {
      heroWs.value = String(r.path)
      localStorage.setItem('flow.ws', String(r.path))
    }
  } catch (e) {
    toast(String(e))
  }
}

const blocks = reactive<Block[]>([])
const streamMap = new Map<string, MsgBlock>()
const toolMap = new Map<string, ToolBlock>()
let ws: WebSocket | null = null
let toastTimer: ReturnType<typeof setTimeout> | undefined
let reqSeq = 0
const pendingReq = new Map<number, (m: WsMsg) => void>()

/* ── computed ── */
const curAgent = computed(() => agents.value.find(a => a.agent_id === cur.value))
const curState = computed(() =>
  curAgent.value?.live ? 'live' : '')
const curTitle = computed(() => agentTitle(curAgent.value))
const usageText = computed(() =>
  `${fmtK(usage.inTok)} / ${fmtK(usage.max)} tok · ${usage.pct.toFixed(0)}%`)

/* 会话显示名: title, 缺省回落创建时间 (前端不暴露 agent_id) */
function agentTitle(a?: AgentInfo): string {
  if (!a) return ''
  if (a.title) return a.title
  if (a.created_at) {
    const d = new Date(a.created_at * 1000)
    const p = (n: number) => String(n).padStart(2, '0')
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  }
  return '会话'
}

/* workspace 分组视图 (dsh workspace = 目录归属, 纯前端分组) */
const wsGroups = computed(() => {
  const m = new Map<string, AgentInfo[]>()
  for (const a of agents.value) {
    const key = a.project_dir || ''
    if (!m.has(key)) m.set(key, [])
    m.get(key)!.push(a)
  }
  return [...m.entries()].map(([dir, list]) => ({ dir, list }))
})

/* 统计栏 (dsh StatsLine 文案模板) */
function fmtDur(ms: number): string {
  if (ms <= 0) return ''
  const s = ms / 1000
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m${Math.round(s % 60)}s`
}

function fmtTok(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K'
  return String(n)
}

const statsLine = computed(() => {
  const st = stats.value
  const groups: string[] = []
  if ((st.steps || 0) > 0) {
    groups.push(`${st.turns} 轮 · ${st.steps} 步`)
    const dur: string[] = []
    if ((st.llm_ms || 0) > 0) dur.push(`LLM ${fmtDur(st.llm_ms!)}`)
    if ((st.tool_ms || 0) > 0) dur.push(`工具调用 ${fmtDur(st.tool_ms!)}`)
    if (dur.length) groups.push(dur.join(' · '))
    const spd: string[] = []
    if ((st.ttft_steps || 0) > 0 && (st.ttft_ms || 0) > 0)
      spd.push(`首 token 平均 ${fmtDur(st.ttft_ms! / st.ttft_steps!)}`)
    if ((st.decode_ms || 0) > 0)
      spd.push(`${(st.decode_tokens! / (st.decode_ms! / 1000)).toFixed(0)} tok/s`)
    if (spd.length) groups.push(spd.join(' · '))
  }
  const billed = (st.uncached_input || 0) + (st.cache_read || 0) + (st.cache_write || 0)
  if (billed > 0 || (st.output_tokens || 0) > 0) {
    if (billed > 0)
      groups.push(`缓存命中 ${Math.round((st.cache_read || 0) / billed * 100)}%`)
    groups.push(`输入 ${fmtTok(billed)} tok · 输出 ${fmtTok(st.output_tokens || 0)} tok`)
  }
  return groups.join('  |  ')
})

function toast(msg: string): void {
  toastMsg.value = msg
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastMsg.value = '' }, 3000)
}

function fmtK(n: number): string {
  n = n || 0
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n)
}

function fmtArgs(a: unknown): string {
  try { return JSON.stringify(a ?? {}) } catch { return String(a) }
}

function fmtRes(r: unknown): string {
  if (r == null) return '(null)'
  if (typeof r === 'string') return r
  try { return JSON.stringify(r, null, 1) } catch { return String(r) }
}

const toolNames = (b: ToolBlock) =>
  b.results.map(r => r.name).filter(Boolean).join(', ') || 'tool'
const toolDetail = (b: ToolBlock) =>
  b.results.map(r => `${r.name}(${fmtArgs(r.input)})\n→ ${fmtRes(r.result)}`).join('\n\n')

function scrollBottom(): void {
  nextTick(() => {
    const el = msgsEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

/* ── WS ── */
function connect(): void {
  const sock = new WebSocket(`ws://${HUB}/ws`)
  ws = sock
  sock.onopen = () => { connOn.value = true }
  sock.onclose = () => {
    connOn.value = false
    setTimeout(connect, 1500)
  }
  sock.onmessage = (e) => {
    let msg: WsMsg
    try { msg = JSON.parse(e.data) } catch { return }
    handle(msg)
  }
}

function send(obj: Record<string, unknown>): number {
  const id = ++reqSeq
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ ...obj, id }))
  return id
}

function request(obj: Record<string, unknown>, timeoutMs = 10000): Promise<WsMsg> {
  return new Promise((resolve, reject) => {
    const id = send(obj)
    pendingReq.set(id, resolve)
    setTimeout(() => {
      if (pendingReq.delete(id)) reject(new Error('请求超时'))
    }, timeoutMs)
  })
}

/* ── 事件处理 ── */
function handle(m: WsMsg): void {
  const id = Number(m.id)
  if (id && pendingReq.has(id)) {
    pendingReq.get(id)!(m)
    pendingReq.delete(id)
  }
  switch (m.type) {
    case 'agents':
      agents.value = (m.agents as AgentInfo[]) || []
      break
    case 'agents_changed': send({ type: 'list_agents' }); break
    case 'conversation_open': break
    case 'session_changed': onSession(m); break
    case 'history': onSession(m); break
    case 'assistant_output': onAssistant(m); break
    case 'feedback': onFeedback(m); break
    case 'usage': onUsage(m); break
    case 'stats': onStats(m); break
    case 'turn_event': onTurn(m); break
    case 'chat_result':
      if (m.ok === false) toast(String(m.error || 'send failed'))
      break
    case 'compact_result':
      toast(m.ok ? '已压缩' : String(m.error || 'compact 失败'))
      break
  }
}

function onSession(m: WsMsg): void {
  if (m.agent_id !== cur.value) return
  streamMap.clear()
  toolMap.clear()
  blocks.splice(0, blocks.length)
  stats.value = (m.stats as Stats) || {}
  const history = (m.messages as HistoryMsg[]) || []
  if (!history.length) {
    emptyText.value = '发送消息开始'
    return
  }
  emptyText.value = ''
  for (const h of history) {
    if (h.role === 'tool') {
      blocks.push({ kind: 'tool', results: h.results || [], open: false })
      continue
    }
    const isSum = h.role === 'user' && String(h.text || '').startsWith('Context summary:')
    blocks.push({
      kind: 'msg', role: h.role || 'user', text: String(h.text || ''),
      think: '', thinking: false, isSummary: isSum,
    })
  }
  scrollBottom()
}

/* 增量协议: text 恒为 delta, is_final 空文本 = 纯终止信号 */
function onAssistant(m: WsMsg): void {
  if (m.agent_id !== cur.value) return
  const txt = String(m.text || '')
  const final = Boolean(m.is_final)
  if (!txt && !final) return
  const mid = String(m.message_id ?? '')
  let st = streamMap.get(mid)
  if (!st) {
    if (!txt) return   // 终止信号但无流块: 无需收尾
    st = reactive({
      kind: 'msg' as const,
      role: 'assistant',
      text: '',
      think: '',
      thinking: Boolean(m.is_thinking),
      isSummary: false,
    })
    blocks.push(st)
    streamMap.set(mid, st)
    scrollBottom()
  }
  if (m.is_thinking) {
    st.think += txt
    st.thinking = true
  } else if (txt) {
    st.thinking = false
    st.text += txt
  }
  if (final) st.thinking = false
  scrollBottom()
}

function onFeedback(m: WsMsg): void {
  if (m.agent_id !== cur.value || !m.results) return
  const mid = String(m.message_id ?? '')
  let st = toolMap.get(mid)
  if (!st) {
    st = reactive({ kind: 'tool' as const, results: [] as ToolResult[], open: false })
    blocks.push(st)
    toolMap.set(mid, st)
  }
  for (const r of m.results as ToolResult[]) {
    const i = st.results.findIndex(x => x.call_id === r.call_id)
    if (i >= 0) st.results[i] = r; else st.results.push(r)
  }
  scrollBottom()
}

function onUsage(m: WsMsg): void {
  if (m.agent_id !== cur.value) return
  usage.pct = Math.min(100, Number(m.percent) || 0)
  usage.inTok = Number(m.input_tokens) || 0
  usage.max = Number(m.max_context) || 0
}

function onStats(m: WsMsg): void {
  if (m.agent_id !== cur.value) return
  stats.value = m as Stats
}

function onTurn(m: WsMsg): void {
  if (m.agent_id !== cur.value) return
  blocks.push({ kind: 'turn', label: `turn ${m.turn} · ${m.reason || ''}` })
  scrollBottom()
}

/* ── 交互 ── */
function newSession(): void {
  cur.value = ''
  streamMap.clear()
  toolMap.clear()
  blocks.splice(0, blocks.length)
  stats.value = {}
  usage.pct = 0
  usage.inTok = 0
  emptyText.value = ''
}

function selectAgent(a: AgentInfo): void {
  cur.value = a.agent_id
  streamMap.clear()
  toolMap.clear()
  blocks.splice(0, blocks.length)
  stats.value = {}
  usage.pct = 0
  usage.inTok = 0
  if (a.project_dir) {
    heroWs.value = a.project_dir
    localStorage.setItem('flow.ws', a.project_dir)
  }
  emptyText.value = ''
  send({ type: 'get_history', agent_id: a.agent_id })
}

async function delAgent(a: AgentInfo): Promise<void> {
  const ok = await confirmDialog({
    title: '删除会话',
    content: `删除会话「${agentTitle(a)}」?\n事件日志将一并删除, 不可恢复.`,
    positiveText: '删除', danger: true,
  })
  if (ok) send({ type: 'delete_agent', agent_id: a.agent_id })
}

function appendUser(text: string): void {
  blocks.push({ kind: 'msg', role: 'user', text, think: '', thinking: false, isSummary: false })
  scrollBottom()
}

function doSend(): void {
  const text = inputText.value.trim()
  if (!text || !cur.value) return
  appendUser(text)
  send({ type: 'chat_message', agent_id: cur.value, message: text, mode: mode.value })
  inputText.value = ''
}

/* hero 发送 = 建会话 + 首条消息 (dsh: 发首条消息即建 session; workspace 必选) */
async function heroSend(): Promise<void> {
  const text = heroText.value.trim()
  if (!text) return
  const wsDir = heroWs.value.trim()
  if (!wsDir) { toast('请先选择工作目录'); return }
  localStorage.setItem('flow.ws', wsDir)
  let r: WsMsg
  try {
    r = await request({ type: 'create_agent', project_dir: wsDir || undefined })
  } catch (e) {
    toast(String(e))
    return
  }
  if (!r.ok) { toast(String(r.error || 'create failed')); return }
  const aid = String(r.agent_id)
  cur.value = aid
  emptyText.value = ''
  appendUser(text)
  heroText.value = ''
  send({ type: 'chat_message', agent_id: aid, message: text, mode: 'followup' })
}

function interrupt(): void {
  if (cur.value) send({ type: 'interrupt', agent_id: cur.value })
}

function compact(): void {
  if (cur.value) send({ type: 'compact', agent_id: cur.value })
}

function setEffort(): void {
  if (cur.value) send({ type: 'set_effort', agent_id: cur.value, effort: effortSel.value || null })
}

onMounted(() => {
  connect()
})
</script>

<template>
  <div class="flow-root">
  <nav id="side">
    <h1><span>▸</span> Flow</h1>
    <button id="newchat" @click="newSession">＋ 新会话</button>
    <div id="agents">
      <div v-if="!wsGroups.length" class="side-empty">还没有会话<br />发送一条消息开始</div>
      <div v-for="g in wsGroups" :key="g.dir" class="wsgroup">
        <div class="wshead" :title="g.dir || '未设置工作目录'">
          <span class="wsname">{{ g.dir ? g.dir.split(/[\\/]/).filter(Boolean).pop() : '默认' }}</span>
          <span class="wscount">{{ g.list.length }}</span>
        </div>
        <div
          v-for="a in g.list" :key="a.agent_id"
          class="agent" :class="{ live: a.live, sel: a.agent_id === cur }"
          @click="selectAgent(a)"
        >
          <span class="dot" />
          <span class="nm" :title="agentTitle(a)">{{ agentTitle(a) }}</span>
          <span class="ops" @click.stop>
            <button class="del" :title="'删除会话'" @click="delAgent(a)">
              <svg viewBox="0 0 1024 1024" width="11" height="11" fill="currentColor">
                <path d="M944 192l-188.8 0 0-44.8c0-54.4-44.8-99.2-99.2-99.2l-294.4 0c-54.4 0-99.2 44.8-99.2 99.2l0 44.8-179.2 0c-12.8 0-25.6 9.6-25.6 25.6 0 12.8 9.6 25.6 25.6 25.6l60.8 0 0 0 0 633.6c0 54.4 44.8 99.2 99.2 99.2l550.4 0c54.4 0 99.2-44.8 99.2-99.2l0-630.4 0-3.2 51.2 0c12.8 0 25.6-9.6 25.6-25.6C966.4 204.8 960 192 944 192zM316.8 147.2c0-25.6 22.4-48 48-48l294.4 0c25.6 0 48 22.4 48 48l0 44.8-390.4 0L316.8 147.2zM841.6 867.2c0 28.8-16 57.6-41.6 57.6l-550.4 0c-25.6 0-54.4-19.2-54.4-44.8l3.2-636.8 643.2 0 0 0L841.6 867.2z" />
                <path d="M368 380.8c-16 0-25.6 9.6-25.6 25.6l0 342.4c0 16 12.8 25.6 25.6 25.6 12.8 0 25.6-9.6 25.6-25.6l0-342.4C390.4 393.6 384 380.8 368 380.8z" />
                <path d="M528 380.8c-16 0-25.6 9.6-25.6 25.6l0 342.4c0 16 12.8 25.6 25.6 25.6 12.8 0 25.6-9.6 25.6-25.6l0-342.4C550.4 393.6 544 380.8 528 380.8z" />
                <path d="M688 380.8c-16 0-25.6 9.6-25.6 25.6l0 342.4c0 16 12.8 25.6 25.6 25.6 12.8 0 25.6-9.6 25.6-25.6l0-342.4C710.4 393.6 704 380.8 688 380.8z" />
              </svg>
            </button>
          </span>
        </div>
      </div>
    </div>
  </nav>

  <main id="main">
    <template v-if="cur">
    <div id="topbar">
      <span class="aname">{{ curTitle }}</span>
      <span class="astate" :class="{ live: curAgent?.live }">{{ curState }}</span>
      <span v-if="curAgent?.project_dir" id="wsname" :title="curAgent.project_dir">
        {{ curAgent.project_dir }}
      </span>
      <div class="spacer" />
      <div id="usage-wrap">
        <div id="usage-bar" :class="{ hot: usage.pct >= 80 }" :style="{ width: usage.pct + '%' }" />
      </div>
      <span id="usage-txt">{{ usageText }}</span>
      <select id="effort" v-model="effortSel" @change="setEffort">
        <option value="">effort · 默认</option>
        <option value="high">high</option>
        <option value="medium">medium</option>
        <option value="low">low</option>
        <option value="none">off</option>
      </select>
      <span id="conn" :class="{ on: connOn }" :title="connOn ? '已连接' : '重连中'">
        {{ connOn ? '●' : '○' }}
      </span>
    </div>

    <div id="msgs" ref="msgsEl">
      <div v-if="emptyText" id="empty">{{ emptyText }}</div>
      <template v-for="(b, i) in blocks" :key="i">
        <div
          v-if="b.kind === 'msg'"
          class="msg"
          :class="[b.role === 'user' ? 'user' : 'assistant', { summary: b.isSummary }]"
        >
          <div class="role">{{ b.isSummary ? '◆ compaction summary' : b.role }}</div>
          <div v-if="b.thinking && b.think" class="thinkline" :title="b.think">
            <span class="tdot" />
            <span class="tw"><span class="t">{{ b.think }}</span></span>
          </div>
          <div v-if="b.text" class="body md" v-html="renderMarkdown(b.text, { breaks: true })" />
        </div>
        <div v-else-if="b.kind === 'tool'" class="toolblk" :class="{ open: b.open }">
          <div class="head" @click="b.open = !b.open">
            <span class="tname">{{ toolNames(b) }}</span>
            <span class="targs">{{ fmtArgs(b.results[0]?.input) }}</span>
            <span class="st" :class="b.results[0]?.status === 'running' ? 'run' : 'done'">
              {{ b.results[0]?.status === 'running' ? '⟳' : '✓' }}
            </span>
            <span v-if="b.results.length > 1" class="st">×{{ b.results.length }}</span>
          </div>
          <pre class="detail">{{ toolDetail(b) }}</pre>
        </div>
        <div v-else class="turnsep">{{ b.label }}</div>
      </template>
    </div>

    <div id="inputbar">
      <textarea
        id="inp" v-model="inputText"
        placeholder="Enter 发送 · Shift+Enter 换行"
        @keydown.enter.exact.prevent="doSend"
      />
      <div id="inputrow">
        <div class="modes">
          <button
            v-for="mn in ['followup', 'steer', 'inject']" :key="mn"
            class="ghost mode-btn" :class="{ sel: mode === mn }"
            @click="mode = mn"
          >{{ mn[0].toUpperCase() + mn.slice(1) }}</button>
          <span class="hint">{{ MODE_HINT[mode] }}</span>
        </div>
        <div class="spacer" />
        <button @click="interrupt">Interrupt</button>
        <button @click="compact">Compact</button>
        <button class="primary" @click="doSend">发送</button>
      </div>
      <div id="statsline" v-show="statsLine">{{ statsLine }}</div>
    </div>
    </template>

    <div v-else id="hero">
      <h2>Flow</h2>
      <p class="herosub">发送消息即创建会话</p>
      <div class="heropanel">
        <div class="herotop">
          <button class="wsbtn2" @click="browse" :title="heroWs || '选择工作目录'">
            <svg class="wsicon" viewBox="0 0 1024 1024" width="13" height="13">
              <path d="M853.333333 298.666667v512H170.666667V213.333333h221.866666l42.666667 42.666667H853.333333v42.666667zM256 341.333333v384h512V341.333333H256z m298.666667 256h170.666666v85.333334h-170.666666v-85.333334z" />
            </svg>
            <span class="wstext">{{ heroWs ? heroWs : '选择工作目录' }}</span>
          </button>
        </div>
        <textarea
          v-model="heroText"
          :disabled="!heroWs"
          :placeholder="heroWs ? '描述你的任务…' : ''"
          @keydown.enter.exact.prevent="heroSend"
        />
        <div class="herosend">
          <button class="primary" :disabled="!heroWs || !heroText.trim()" @click="heroSend">
            发送
          </button>
        </div>
      </div>
    </div>
  </main>

  <div id="toast" v-show="toastMsg">{{ toastMsg }}</div>
  <ConfirmDialog />
  </div>
</template>
