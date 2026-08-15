<template>
  <div class="panel">
    <NScrollbar ref="scrollbarRef" class="messages-scroll" @scroll="onScroll">
      <div class="messages">
        <template v-for="group in groupedMessages" :key="group.groupId">
          <!-- 单条消息: user / assistant -->
          <div
            v-if="group.type === 'single'"
            class="msg"
            :class="group.msg.role"
          >
            <!-- 用户消息 -->
            <template v-if="group.msg.role === 'user'">
              <div class="user-bubble">
                <span v-if="group.msg.from" class="from-agent-tag">{{ group.msg.from }}</span>
                {{ group.msg.text }}
              </div>
            </template>

            <!-- assistant 消息 -->
            <template v-else-if="group.msg.role === 'assistant'">
              <details
                v-if="group.msg.thinking && !group.msg.thinkingDone"
                class="thinking-block"
                open
              >
                <summary class="thinking-summary">
                  <span class="thinking-icon">💭</span>
                  <span>思考过程</span>
                  <span class="thinking-live">...</span>
                </summary>
                <div class="thinking-content">{{ group.msg.thinking }}</div>
              </details>
              <div v-if="group.msg.text" class="bubble">
                <div class="md-body" v-html="renderMarkdown(group.msg.text, { breaks: true })" />
                <span v-if="!group.msg.final" class="cursor">▌</span>
              </div>
              <div v-if="group.msg.image" class="image-bubble">
                <img
                  :src="`data:${group.msg.image.mime};base64,${group.msg.image.data}`"
                  :alt="group.msg.image.caption || ''"
                  class="show-image"
                >
                <div v-if="group.msg.image.caption" class="image-caption">{{ group.msg.image.caption }}</div>
              </div>
            </template>
          </div>

          <!-- 工具组: 连续的 tool messages 合并成一个块 -->
          <div v-else class="msg tool">
            <!-- 全 ipython 组: 无 tool-header, 每个 cell 独立折叠 -->
            <template v-if="isAllIpython(group.msgs)">
              <div class="jp-cell-list">
                <template v-for="msg in group.msgs" :key="msg.id">
                  <details
                    v-for="(r, i) in msg.results"
                    :key="`${msg.id}-${i}`"
                    class="jp-cell"
                    :class="cellStatusClass(r)"
                    :open="isCellOpen(group.msgs, r, group === groupedMessages[groupedMessages.length - 1])"
                  >
                    <summary class="jp-cell-summary">
                      <span class="jp-cell-indicator" :class="cellStatusClass(r)" />
                      <span class="jp-cell-code-preview">{{ codePreview(r.input?.code) }}</span>
                      <span v-if="cellDuration(r)" class="jp-cell-dur" :class="{ running: r.status === 'running' }">{{ cellDuration(r) }}</span>
                      <span v-if="r.status === 'running'" class="jp-cell-live">running</span>
                    </summary>
                    <div class="jp-cell-body">
                      <div class="jp-prompt-row">
                        <span class="jp-prompt">In:</span>
                      </div>
                      <pre class="jp-code language-python" v-html="highlightPython(String(r.input?.code ?? ''))"></pre>
                      <template v-if="r.error">
                        <div class="jp-prompt-row jp-prompt-row-out">
                          <span class="jp-prompt">Out:</span>
                        </div>
                        <pre class="jp-output jp-output-error">{{ r.error.msg }}</pre>
                      </template>
                      <template v-else-if="r.result != null && String(r.result).trim()">
                        <div class="jp-prompt-row jp-prompt-row-out">
                          <span class="jp-prompt">Out:</span>
                        </div>
                        <pre class="jp-output">{{ String(r.result) }}</pre>
                      </template>
                    </div>
                  </details>
                </template>
              </div>
            </template>
            <!-- 其他工具组: 原有折叠块 -->
            <details
              v-else
              class="tool-block"
              :class="toolGroupStatus(group.msgs)"
              :open="isToolGroupOpen(group)"
              @toggle="onToolGroupToggle(group, $event)"
            >
              <summary class="tool-header">
                <span class="tool-summary-text">{{ toolGroupSummary(group.msgs) }}</span>
                <span v-if="group.msgs.some(m => !m.final)" class="tool-header-live">running</span>
                <span
                  v-else
                  class="tool-status-badge"
                  :class="toolGroupStatus(group.msgs)"
                >{{ toolGroupStatus(group.msgs) === 'tool-block-error' ? 'error' : 'done' }}</span>
              </summary>
              <template v-for="msg in group.msgs" :key="msg.id">
                <div v-for="(r, i) in msg.results" :key="i" class="tool-item">
                  <!-- ipython: jupyter cell 风格渲染 -->
                  <template v-if="r.name === 'ipython'">
                    <div class="jp-cell">
                      <div class="jp-prompt-row">
                        <span class="jp-prompt">In:</span>
                        <NTag
                          size="small"
                          :type="toolStatusType(r)"
                          :bordered="false"
                          class="jp-status"
                        >
                          {{ r.error ? 'error' : (r.status ?? 'done') }}
                        </NTag>
                      </div>
                      <pre class="jp-code">{{ String(r.input?.code ?? '') }}</pre>
                      <template v-if="r.error">
                        <pre class="jp-output jp-output-error">{{ r.error.msg }}</pre>
                      </template>
                      <template v-else>
                        <template v-if="r.result != null && String(r.result).trim()">
                          <span class="jp-prompt jp-prompt-out">Out:</span>
                          <pre class="jp-output">{{ String(r.result) }}</pre>
                        </template>
                      </template>
                    </div>
                  </template>
                  <!-- 其他工具: 原有渲染 -->
                  <template v-else>
                    <div class="tool-row-main">
                      <span class="tool-name">{{ r.name }}</span>
                      <span v-if="toolInputSummary(r)" class="tool-input">
                        {{ toolInputSummary(r) }}
                      </span>
                      <NTag
                        size="small"
                        :type="toolStatusType(r)"
                        :bordered="false"
                      >
                        {{ r.error ? 'error' : (r.status ?? 'done') }}
                      </NTag>
                    </div>
                    <div
                      v-if="r.error || r.result != null"
                      class="tool-result"
                      :class="{ error: !!r.error }"
                    >
                      {{ r.error ? r.error.msg : toolResultSummary(r) }}
                    </div>
                    <details
                      v-if="r.input || r.result != null || r.error"
                      class="tool-details"
                    >
                      <summary>完整调试信息</summary>
                      <div v-if="r.input" class="tool-detail-section">
                        <div class="tool-detail-title">input</div>
                        <pre>{{ formatFullValue(r.input) }}</pre>
                      </div>
                      <div v-if="r.error" class="tool-detail-section">
                        <div class="tool-detail-title">error</div>
                        <pre>{{ r.error.msg }}</pre>
                      </div>
                      <div v-else-if="r.result != null" class="tool-detail-section">
                        <div class="tool-detail-title">result</div>
                        <pre>{{ formatFullValue(r.result) }}</pre>
                      </div>
                    </details>
                  </template>
                </div>
              </template>
            </details>
          </div>
        </template>

        <div v-if="messages.length === 0" class="empty">等待消息...</div>
      </div>
    </NScrollbar>

    <!-- 输入区(readonly 面板不显示) -->
    <div v-if="!props.readonly" class="input-bar">
      <template v-if="props.stopped">
        <div class="stopped-hint">
          <span class="stopped-dot" />
          <span>Agent stopped. Resume from the Agents panel to continue.</span>
        </div>
      </template>
      <template v-else>
        <NButton
          quaternary
          circle
          title="中断"
          @click="$emit('interrupt')"
        >
          <template #icon>⏹</template>
        </NButton>
        <NInput
          ref="inputRef"
          v-model:value="draft"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 5 }"
          placeholder="发送消息..."
          class="input"
          @keydown.enter.exact.prevent="submit"
        />
        <div v-if="modelDisplay" class="model-info">
          <NTag size="small" :bordered="false" class="model-tag">{{ modelDisplay }}</NTag>
          <NSelect
            v-if="props.usage?.reasoning_effort !== undefined"
            v-model:value="localEffort"
            :options="effortOptions"
            size="small"
            style="width: 90px"
            @update:value="onEffortChange"
          />
        </div>
        <div
          v-if="props.usage && props.usage.max_context > 0"
          class="ctx-ring"
          :class="contextBarClass"
          :title="contextBarText"
          :style="{ '--ctx-deg': Math.min(props.usage.percent, 100) * 3.6 + 'deg' }"
        >
          <span class="ctx-ring-text">{{ Math.round(props.usage.percent) }}</span>
        </div>
        <NButton
          type="primary"
          :disabled="!draft.trim()"
          @click="submit"
        >
          发送
        </NButton>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import { NScrollbar, NInput, NButton, NTag, NSelect } from 'naive-ui'
import { renderMarkdown } from '../utils/markdown'
import Prism from 'prismjs'
import 'prismjs/components/prism-python'
import 'prismjs/themes/prism-tomorrow.min.css'

interface ToolResult {
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

interface PanelMessage {
  id: string
  role: 'user' | 'assistant' | 'tool'
  text: string
  thinking?: string
  thinkingDone?: boolean
  final: boolean
  results?: ToolResult[]
  from?: string
  image?: { data: string; mime: string; caption?: string }
}

interface StreamingState {
  messageId: string
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

const props = defineProps<{
  agentId: string
  messages: PanelMessage[]
  isMain?: boolean
  readonly?: boolean
  stopped?: boolean
  streaming: StreamingState | null | undefined
  usage?: UsageInfo
}>()

const emit = defineEmits<{
  send: [text: string]
  interrupt: []
  'set-effort': [effort: string]
}>()

const draft = ref('')
const scrollbarRef = ref<InstanceType<typeof NScrollbar> | null>(null)
const inputRef = ref<InstanceType<typeof NInput> | null>(null)

// 用户手动向上滚时暂停自动滚到底, 滚回底部恢复
const stickToBottom = ref(true)
function onScroll(e: Event) {
  const el = e.target as HTMLElement
  if (!el) return
  const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  stickToBottom.value = distFromBottom < 40
}

// effort 本地状态: 从 usage 事件同步, 用户改时立即更新 + emit 给后端
const effortOptions = [
  { label: 'high', value: 'high' },
  { label: 'medium', value: 'medium' },
  { label: 'low', value: 'low' },
  { label: 'off', value: 'none' },
]
const localEffort = ref<string>('high')
watch(
  () => props.usage?.reasoning_effort,
  (v) => { if (v) localEffort.value = v; else if (v === null) localEffort.value = 'none' },
  { immediate: true },
)
function onEffortChange(val: string) {
  localEffort.value = val
  emit('set-effort', val)
}

const modelDisplay = computed(() => props.usage?.model_name || props.usage?.model_key || '')

const contextBarClass = computed(() => {
  // percent 已按 trigger_ratio (0.8) 缩放: 100% = 即将触发压缩
  const p = props.usage?.percent ?? 0
  if (p >= 100) return 'ctx-danger'
  if (p >= 70) return 'ctx-warn'
  return 'ctx-ok'
})

const contextBarText = computed(() => {
  const u = props.usage
  if (!u || u.max_context <= 0) return ''
  const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
  return `${u.percent.toFixed(1)}% · ${fmt(u.input_tokens)}/${fmt(u.max_context)}`
})

function submit() {
  const text = draft.value.trim()
  if (!text) return
  emit('send', text)
  draft.value = ''
}

function scrollToBottom() {
  if (!stickToBottom.value) return
  nextTick(() => {
    const el = scrollbarRef.value?.scrollbarInstRef?.containerRef
    if (!el) return
    el.scrollTop = el.scrollHeight
    // 长输出 / 复杂 DOM (markdown, pre) 可能在下一帧才完成布局,
    // rAF 双保险确保滚到真正的底部.
    requestAnimationFrame(() => {
      if (stickToBottom.value) el.scrollTop = el.scrollHeight
    })
  })
}

watch(() => props.messages?.length, scrollToBottom)
watch(() => props.streaming, scrollToBottom, { deep: true })
// 工具 results 从 running → done 时是替换现有消息的 results[0] (不新增消息),
// messages.length 不变; 此时 streaming 已被 finishStreamingForTools 置 null.
// deep watch 最后一条消息捕获 results 内容更新, 触发滚动.
watch(
  () => props.messages?.[props.messages.length - 1],
  scrollToBottom,
  { deep: true },
)

function toolStatusType(result: ToolResult) {
  if (result.error) return 'error'
  if (result.status === 'running') return 'info'
  return 'success'
}

/** tool-block 边框状态:running(虚线黄)/ done(实线绿)/ error(实线红). */

// 消息分组: 连续的 tool messages 合并成一组, 其余各自独立.
// 这样 21 次 ipython 调用会合并成一个折叠块, 而不是 21 个独立行.
type MessageGroup =
  | { type: 'single'; groupId: string; msg: PanelMessage }
  | { type: 'tool-group'; groupId: string; msgs: PanelMessage[] }

const groupedMessages = computed<MessageGroup[]>(() => {
  const groups: MessageGroup[] = []
  let currentToolGroup: { type: 'tool-group'; groupId: string; msgs: PanelMessage[] } | null = null
  for (const msg of props.messages) {
    if (msg.role === 'tool') {
      if (!currentToolGroup) {
        currentToolGroup = { type: 'tool-group', groupId: `tg-${msg.id}`, msgs: [] }
        groups.push(currentToolGroup)
      }
      currentToolGroup.msgs.push(msg)
    } else {
      currentToolGroup = null
      groups.push({ type: 'single', groupId: msg.id, msg })
    }
  }
  return groups
})

function toolGroupStatus(msgs: PanelMessage[]): string {
  if (msgs.some(m => !m.final)) return 'tool-block-running'
  if (msgs.some(m => m.results?.some(r => r.error))) return 'tool-block-error'
  return 'tool-block-done'
}

// 工具块折叠状态: 默认 running 时展开(看进度), done 后自动折叠.
// 用户手动 toggle 后状态被记录, 不会被默认逻辑覆盖.
const toolOpenState = ref<Record<string, boolean>>({})

function isToolGroupOpen(group: MessageGroup): boolean {
  const id = group.groupId
  if (id in toolOpenState.value) return toolOpenState.value[id]
  // 默认: 有任一 running 就展开
  return group.type === 'tool-group' && group.msgs.some(m => !m.final)
}

function onToolGroupToggle(group: MessageGroup, e: Event) {
  toolOpenState.value[group.groupId] = (e.target as HTMLDetailsElement).open
}

/** 折叠时 summary 显示的摘要: 收集组内所有 results, 同名去重. */
function toolGroupSummary(msgs: PanelMessage[]): string {
  const allResults = msgs.flatMap(m => m.results ?? [])
  if (allResults.length === 0) return '工具调用'
  if (allResults.length === 1) {
    const r = allResults[0]
    const summary = toolInputSummary(r)
    return summary ? `${r.name} · ${summary}` : r.name
  }
  // 同名去重, 避免出现 "ipython, ipython, ipython +18" 这种丑列表.
  const counts = new Map<string, number>()
  for (const r of allResults) {
    counts.set(r.name, (counts.get(r.name) ?? 0) + 1)
  }
  const entries = [...counts.entries()]
  const parts = entries.map(([name, n]) => n > 1 ? `${name} ×${n}` : name)
  const display = parts.slice(0, 3).join(', ')
  const extra = parts.length > 3 ? ` +${parts.length - 3}` : ''
  return `${allResults.length} 次调用: ${display}${extra}`
}

function toolInputSummary(result: ToolResult) {
  const input = result.input
  if (!input) return ''
  const name = result.name.toLowerCase()

  if (name === 'read' || name === 'write' || name === 'edit') {
    return shortenPath(String(input.file_path ?? ''))
  }
  if (name === 'glob') {
    return String(input.pattern ?? '')
  }
  if (name === 'grep') {
    const pattern = String(input.pattern ?? '')
    const path = String(input.path ?? '')
    return path ? `"${pattern}" in ${shortenPath(path)}` : `"${pattern}"`
  }
  if (name === 'bash') {
    const command = String(input.command ?? '')
    return command.length > 90 ? `${command.slice(0, 90)}...` : command
  }
  if (name === 'ipython') {
    // 取第一行非空非注释代码做预览, 不把多行压成一坨.
    const lines = String(input.code ?? '').split('\n').map(l => l.trim()).filter(Boolean)
    const first = lines.find(l => !l.startsWith('#')) ?? lines[0] ?? ''
    return first.length > 60 ? `${first.slice(0, 60)}...` : first
  }
  if (name === 'todowrite') {
    const todos = Array.isArray(input.todos) ? input.todos : []
    return `${todos.length} item${todos.length === 1 ? '' : 's'}`
  }

  return compactJson(input)
}

function toolResultSummary(result: ToolResult) {
  const value = result.result
  if (value === undefined || value === null) return ''
  const name = result.name.toLowerCase()

  if (typeof value !== 'string') {
    if (name === 'todowrite' && isRecord(value)) {
      const next = Array.isArray(value.newTodos) ? value.newTodos : []
      return `updated todo list (${next.length} item${next.length === 1 ? '' : 's'})`
    }
    return compactJson(value)
  }

  const text = value.trim()
  if (!text) return ''
  if (name === 'read') {
    const lines = text.split('\n').filter(line => /^\s*\d+\|/.test(line))
    if (lines.length) return `${lines.length} lines`
  }
  if (name === 'glob') {
    const paths = text.split('\n').filter(Boolean)
    return paths.length === 1 ? shortenPath(paths[0] ?? '') : `${paths.length} files`
  }
  if (name === 'grep') {
    const matches = text.split('\n').filter(Boolean)
    return `${matches.length} matches`
  }
  if (name === 'bash') {
    const lines = text.split('\n').filter(line => line.trim())
    return (lines[lines.length - 1] ?? text).slice(0, 180)
  }

  return text.split('\n')[0]?.slice(0, 180) ?? ''
}

function shortenPath(path: string) {
  if (!path) return ''
  const parts = path.replace(/\\/g, '/').split('/').filter(Boolean)
  return parts.length > 2 ? `.../${parts.slice(-2).join('/')}` : path
}

function compactJson(value: unknown) {
  try {
    const text = JSON.stringify(value)
    return text.length > 180 ? `${text.slice(0, 180)}...` : text
  } catch {
    return String(value)
  }
}

function formatFullValue(value: unknown) {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

// ── ipython cell 独立折叠 ──

function isAllIpython(msgs: PanelMessage[]): boolean {
  return msgs.every(m => m.results?.every(r => r.name === 'ipython'))
}

function cellStatusClass(r: ToolResult): string {
  if (r.error) return 'jp-cell-error'
  if (r.status === 'running') return 'jp-cell-running'
  return 'jp-cell-done'
}

// 每个 cell 折叠逻辑 (无状态记录, Vue 完全控制 :open):
// - running → 展开
// - done/error → 只有当前 group 是消息流最后一个 group 时, 末尾 cell 保持展开;
//   后面出现新消息 (文本/tool) 时, 旧 group 不再是最后, 所有 cell 自动折叠.
function isCellOpen(msgs: PanelMessage[], r: ToolResult, isLastGroup: boolean): boolean {
  if (r.status === 'running') return true
  if (!isLastGroup) return false
  const lastMsg = msgs[msgs.length - 1]
  const lastResults = lastMsg?.results ?? []
  return lastResults[lastResults.length - 1] === r
}

function codePreview(code: unknown): string {
  const lines = String(code ?? '').split('\n').map(l => l.trim()).filter(Boolean)
  const first = lines.find(l => !l.startsWith('#')) ?? lines[0] ?? ''
  return first.length > 70 ? `${first.slice(0, 70)}...` : first
}

// Python 语法高亮: prismjs (按需加载 python 语言包, tomorrow 主题)
function highlightPython(code: string): string {
  if (!code) return ''
  return Prism.highlight(code, Prism.languages.python, 'python')
}

// ── cell 执行时间 ──
// running 时定时器每 100ms 触发重渲染, done 后停止.
const now = ref(Date.now())
let timer: ReturnType<typeof setInterval> | null = null

const hasRunningCell = computed(() =>
  props.messages.some(m =>
    m.role === 'tool' && m.results?.some(r => r.status === 'running'),
  ),
)

watch(hasRunningCell, (running) => {
  if (running && !timer) {
    timer = setInterval(() => { now.value = Date.now() }, 100)
  } else if (!running && timer) {
    clearInterval(timer)
    timer = null
  }
}, { immediate: true })

onUnmounted(() => {
  if (timer) { clearInterval(timer); timer = null }
})

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  return `${m}m${Math.round(s % 60)}s`
}

function cellDuration(r: ToolResult): string {
  if (r.status === 'running' && r.startedAt) {
    return formatDuration(now.value - r.startedAt)
  }
  if (r.duration != null) {
    return formatDuration(r.duration)
  }
  return ''
}
</script>

<style scoped>
.panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  overflow: hidden;
}

.messages-scroll {
  flex: 1;
}

.messages {
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.empty {
  color: #4a5568;
  text-align: center;
  margin-top: 40px;
  font-size: 13px;
}

/* user bubble */
.msg.user { align-self: flex-end; max-width: 80%; }

.user-bubble {
  background: #4f46e5;
  border-radius: 12px 12px 2px 12px;
  padding: 10px 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: #fff;
}

.from-agent-tag {
  display: inline-block;
  font-size: 11px;
  opacity: 0.75;
  margin-bottom: 4px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.15);
}

/* assistant bubble */
.msg.assistant { align-self: flex-start; max-width: 80%; display: flex; flex-direction: column; gap: 6px; }

/* thinking block */
.thinking-block {
  background: #12151f;
  border: 1px solid #2a2d45;
  border-left: 3px solid #4a4e7a;
  border-radius: 8px;
  font-size: 12px;
  overflow: hidden;
}

.thinking-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  color: #64748b;
  user-select: none;
  list-style: none;
  outline: none;
}
.thinking-summary::-webkit-details-marker { display: none; }
.thinking-summary::before {
  content: '▶';
  font-size: 9px;
  color: #4a4e7a;
  transition: transform 0.15s;
}
details[open] .thinking-summary::before { transform: rotate(90deg); }

.thinking-icon { font-size: 13px; }

.thinking-live {
  color: #6366f1;
  animation: blink 1s step-end infinite;
}

.thinking-content {
  padding: 8px 12px 10px;
  color: #4a5568;
  font-style: italic;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  border-top: 1px solid #1e2235;
}

.bubble {
  background: #1e2235;
  border: 1px solid #2d3148;
  border-radius: 12px 12px 12px 2px;
  padding: 10px 14px;
  line-height: 1.6;
  word-break: break-word;
  color: #e2e8f0;
}
.image-bubble {
  margin-top: 8px;
  border: 1px solid #2d3148;
  border-radius: 12px 12px 12px 2px;
  overflow: hidden;
  max-width: 100%;
}
.show-image {
  display: block;
  max-width: 100%;
  height: auto;
}
.image-caption {
  padding: 6px 12px;
  font-size: 12px;
  color: #94a3b8;
  background: #16182a;
  border-top: 1px solid #2d3148;
}

/* markdown rendered via v-html (unscoped -> :deep) */
.md-body { white-space: normal; overflow-x: auto; }
.md-body :deep(p) { margin: 4px 0; }
.md-body :deep(p:first-child) { margin-top: 0; }
.md-body :deep(p:last-child) { margin-bottom: 0; }
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4),
.md-body :deep(h5),
.md-body :deep(h6) {
  color: #f1f5f9;
  font-weight: 600;
  margin: 12px 0 4px;
  line-height: 1.3;
}
.md-body :deep(h1) { font-size: 18px; }
.md-body :deep(h2) { font-size: 16px; }
.md-body :deep(h3) { font-size: 15px; }
.md-body :deep(h4),
.md-body :deep(h5),
.md-body :deep(h6) { font-size: 14px; }
.md-body :deep(ul),
.md-body :deep(ol) { padding-left: 22px; margin: 4px 0; }
.md-body :deep(li) { margin: 2px 0; }
.md-body :deep(li > input[type="checkbox"]) { margin-right: 6px; }
.md-body :deep(code) {
  background: #0f1117;
  padding: 1px 5px;
  border-radius: 3px;
  font-family: 'Consolas', ui-monospace, monospace;
  font-size: 12px;
  color: #a5b4fc;
}
.md-body :deep(pre) {
  background: #0f1117;
  border: 1px solid #2d3148;
  padding: 10px 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}
.md-body :deep(pre code) {
  background: none;
  padding: 0;
  color: #e2e8f0;
  font-size: 12px;
}
.md-body :deep(blockquote) {
  border-left: 3px solid #4a4e7a;
  margin: 6px 0;
  padding: 2px 12px;
  color: #94a3b8;
}
.md-body :deep(a) { color: #818cf8; }
.md-body :deep(strong) { color: #f1f5f9; }
.md-body :deep(hr) {
  border: none;
  border-top: 1px solid #2d3148;
  margin: 10px 0;
}
.md-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
  width: max-content;
  max-width: 100%;
}
.md-body :deep(thead) { background: #2a2d48; }
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #3d4270;
  padding: 5px 10px;
  text-align: left;
}
.md-body :deep(th) { color: #f1f5f9; font-weight: 600; }
.md-body :deep(td) { color: #cbd5e1; }

.cursor {
  display: inline-block;
  animation: blink 0.8s step-end infinite;
  color: #818cf8;
  margin-left: 1px;
}

@keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

/* tool block */
.msg.tool { align-self: stretch; }

.tool-block {
  background: #141720;
  border: 1px solid #2d3148;
  border-left: 3px solid #6366f1;
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  transition: border-color 0.2s, border-style 0.2s;
}

/* running:黄色虚线边框 + 脉动动画,暗示执行中 */
.tool-block-running {
  border: 1px dashed #fbbf24;
  border-left: 3px dashed #fbbf24;
  animation: tool-pulse 1.5s ease-in-out infinite;
}

/* done:绿色实线边框 */
.tool-block-done {
  border: 1px solid #10b981;
  border-left: 3px solid #10b981;
}

/* error:红色实线边框 */
.tool-block-error {
  border: 1px solid #ef4444;
  border-left: 3px solid #ef4444;
}

@keyframes tool-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}

.tool-header {
  color: #818cf8;
  font-weight: 600;
  margin-bottom: 0;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  list-style: none;
  outline: none;
}
.tool-header::-webkit-details-marker { display: none; }
.tool-header::before {
  content: '▶';
  font-size: 9px;
  color: #6366f1;
  transition: transform 0.15s;
  flex-shrink: 0;
}
details[open] > .tool-header::before { transform: rotate(90deg); }

.tool-summary-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-status-badge {
  font-size: 10px;
  font-weight: 500;
  text-transform: lowercase;
  letter-spacing: 0;
  padding: 1px 6px;
  border-radius: 3px;
  flex-shrink: 0;
}
.tool-status-badge.tool-block-done { color: #10b981; background: rgba(16, 185, 129, 0.1); }
.tool-status-badge.tool-block-error { color: #ef4444; background: rgba(239, 68, 68, 0.1); }

.tool-header-live {
  color: #fbbf24;
  font-size: 10px;
  font-weight: 500;
  text-transform: lowercase;
  letter-spacing: 0;
  animation: blink 1s step-end infinite;
}

.tool-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 0;
  color: #94a3b8;
}

.tool-item + .tool-item {
  border-top: 1px solid #24283a;
}

/* === ipython: jupyter cell 风格 === */
.jp-cell {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid #2d3148;
  border-radius: 6px;
  overflow: hidden;
  background: #0f1117;
}
.jp-prompt-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: #161821;
  border-bottom: 1px solid #1f2230;
}
.jp-prompt {
  font-family: ui-monospace, 'Consolas', monospace;
  font-size: 11px;
  color: #6b7280;
  font-weight: 600;
}
.jp-prompt-out {
  display: block;
  padding: 4px 10px 0;
  background: #0f1117;
  font-size: 11px;
}
.jp-status { font-size: 10px; }
.jp-code {
  margin: 0;
  padding: 8px 10px;
  background: #0f1117;
  color: #e2e8f0;
  font-family: ui-monospace, 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}
.jp-output {
  margin: 0;
  padding: 8px 10px;
  background: #12151f;
  color: #86efac;
  border-top: 1px solid #1f2230;
  font-family: ui-monospace, 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}
.jp-output-error {
  color: #fca5a5;
  background: #1a0f12;
}

/* === ipython cell 列表 (全 ipython 组, 无 tool-header) === */
.jp-cell-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.jp-cell-list > details.jp-cell {
  border-radius: 6px;
  overflow: hidden;
  transition: border-color 0.2s;
}

.jp-cell-list > details.jp-cell > .jp-cell-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  cursor: pointer;
  user-select: none;
  list-style: none;
  outline: none;
  background: #161821;
  font-family: ui-monospace, 'Consolas', monospace;
  font-size: 11px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
}
.jp-cell-list > details.jp-cell > .jp-cell-summary::-webkit-details-marker { display: none; }
.jp-cell-list > details.jp-cell > .jp-cell-summary::before {
  content: '▶';
  font-size: 8px;
  color: #4a5568;
  transition: transform 0.15s;
  flex-shrink: 0;
}
.jp-cell-list > details[open].jp-cell > .jp-cell-summary::before {
  transform: rotate(90deg);
}

.jp-cell-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.jp-cell-running .jp-cell-indicator {
  background: #fbbf24;
  animation: blink 1s step-end infinite;
}
.jp-cell-done .jp-cell-indicator { background: #10b981; }
.jp-cell-error .jp-cell-indicator { background: #ef4444; }

.jp-cell-code-preview {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.jp-cell-live {
  color: #fbbf24;
  font-size: 10px;
  font-weight: 500;
  text-transform: lowercase;
  animation: blink 1s step-end infinite;
  flex-shrink: 0;
}

.jp-cell-dur {
  color: #6b7280;
  font-size: 10px;
  font-family: ui-monospace, 'Consolas', monospace;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.jp-cell-dur.running {
  color: #fbbf24;
}

.jp-cell-body {
  border-top: 1px solid #1f2230;
}

.jp-cell-list .jp-prompt-row-out {
  background: #12151f;
  border-bottom: none;
  border-top: 1px solid #1f2230;
}

/* running 状态: 黄色虚线边框 */
.jp-cell-list > details.jp-cell-running {
  border: 1px dashed #fbbf24;
  animation: tool-pulse 1.5s ease-in-out infinite;
}
/* done 状态: 绿色实线边框 */
.jp-cell-list > details.jp-cell-done {
  border: 1px solid #2d3148;
}
.jp-cell-list > details.jp-cell-done > .jp-cell-summary { color: #6b7280; }
/* error 状态: 红色实线边框 */
.jp-cell-list > details.jp-cell-error {
  border: 1px solid #ef4444;
}

/* === Python 语法高亮: prism-tomorrow 主题 (全局 CSS, scoped 下用 :deep 穿透) === */
.jp-code :deep(.token) { background: none; }

.tool-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.tool-name {
  color: #a5b4fc;
  min-width: 92px;
  font-family: monospace;
  font-weight: 600;
}

.tool-input {
  color: #cbd5e1;
  font-family: monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.tool-result {
  color: #86efac;
  font-family: monospace;
  font-size: 12px;
  line-height: 1.45;
  padding-left: 100px;
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-result.error {
  color: #fca5a5;
}

.tool-details {
  margin-left: 100px;
  border: 1px solid #24283a;
  border-radius: 6px;
  background: #0f121a;
  overflow: hidden;
}

.tool-details > summary {
  padding: 5px 8px;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
  font-size: 11px;
}

.tool-detail-section {
  border-top: 1px solid #24283a;
}

.tool-detail-title {
  padding: 6px 8px 0;
  color: #818cf8;
  font-family: monospace;
  font-size: 11px;
}

.tool-detail-section pre {
  margin: 0;
  padding: 6px 8px 8px;
  max-height: 420px;
  overflow: auto;
  color: #cbd5e1;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.45;
}

/* input bar */
.input-bar {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 10px 14px;
  background: #1a1d27;
  border-top: 1px solid #2d3148;
  flex-shrink: 0;
}

.model-info {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.model-tag {
  font-size: 11px;
  opacity: 0.7;
}

.ctx-ring {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: conic-gradient(var(--ctx-color, #22c55e) var(--ctx-deg, 0deg), #2d3148 0);
  transition: --ctx-deg 0.3s ease;
}

.ctx-ring::before {
  content: '';
  position: absolute;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #1a1d27;
}

.ctx-ring-text {
  position: relative;
  font-size: 9px;
  color: #9ca3af;
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

.ctx-ring.ctx-ok { --ctx-color: #22c55e; }
.ctx-ring.ctx-warn { --ctx-color: #f59e0b; }
.ctx-ring.ctx-danger { --ctx-color: #ef4444; }

.ctx-ring.ctx-danger .ctx-ring-text { color: #ef4444; }

.input { flex: 1; }

.stopped-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  color: #6b7280;
  font-size: 13px;
  font-style: italic;
}
.stopped-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #4a5568;
  flex-shrink: 0;
}
</style>
