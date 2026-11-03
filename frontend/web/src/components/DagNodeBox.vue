<template>
  <div class="node" :class="[`s-${data.state}`]" :style="{ width: NODE_W + 'px' }">

    <!-- 左侧状态竖条 -->
    <div class="stripe" />

    <!-- 头部:名称 + routine + 时长 -->
    <div class="head">
      <div class="head-main">
        <span class="node-id">{{ data.spec.id }}</span>
        <span v-if="duration" class="node-dur">{{ duration }}</span>
      </div>
      <div class="node-routine">{{ data.spec.routine }}</div>
    </div>

    <!-- 输入槽区 ─────────────────────────────────── -->
    <div v-if="hasPorts" class="ports">

      <!-- ordering 依赖(纯排序,左侧 Handle,排在 data/cond 之上) -->
      <div v-if="triggerPort" class="order-row">
        <Handle
          :id="triggerPort.id"
          type="target"
          :position="Position.Left"
          :connectable="false"
          class="h-order"
          :style="triggerHandleStyle"
        />
        <span class="order-label">after</span>
        <span class="order-sources">{{ triggerPort.sources?.join(', ') }}</span>
      </div>

      <!-- data / cond 槽(左侧 handle,每行一个) -->
      <template v-for="(port, i) in connectedPorts" :key="port.id">
        <Handle
          :id="port.id"
          type="target"
          :position="Position.Left"
          :connectable="false"
          :class="port.type === 'cond' ? 'h-cond' : 'h-data'"
          :style="leftStyle(i)"
        />
        <div class="port-row" :class="port.type === 'cond' ? 'pr-cond' : 'pr-data'">
          <span class="pdot" :class="port.type === 'cond' ? 'pdot-cond' : 'pdot-data'" />
          <span class="pname">{{ port.type === 'cond' ? 'if' : port.param }}</span>
          <span class="psrc">
            {{ port.type === 'data' ? port.sourceNodes?.join(', ') : port.sourceNode }}
          </span>
        </div>
      </template>

      <!-- 静态输入(无 handle,仅展示) -->
      <div v-for="port in staticPorts" :key="port.id" class="port-row pr-static">
        <span class="pstatic-key">{{ port.param }}</span>
        <span v-if="port.type === 'wi'" class="pstatic-val wi-val">↑ {{ port.wiKey }}</span>
        <span v-else class="pstatic-val lit-val">{{ truncate(String(port.value ?? ''), 24) }}</span>
      </div>

    </div>

    <!-- 运行输出 / 错误 -->
    <div v-if="outputPreview" class="tail tail-ok">
      <span class="tail-val">{{ outputPreview }}</span>
    </div>
    <div v-if="data.error" class="tail tail-err" :title="data.error">
      <span class="tail-val">{{ shortError }}</span>
    </div>

    <!-- 状态行 -->
    <div class="status-row" :class="`st-${data.state}`">
      <span class="sdot" />
      <span class="slabel">{{ STATE_LABEL[data.state] ?? data.state }}</span>
      <span v-if="data.attempt > 1" class="sattempt">#{{ data.attempt }}</span>
    </div>

    <!-- 右侧 output Handle -->
    <Handle
      id="output"
      type="source"
      :position="Position.Right"
      :connectable="false"
      class="h-out"
    />

  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import type { NodeState } from '../composables/useDag'
import type { InputPort } from './DagPanel.vue'
import { NODE_W, HEADER_H, ROW_H, SEC_PAD_T } from './dagConstants'

const STATE_LABEL: Record<string, string> = {
  pending: 'waiting', running: 'running', completed: 'done',
  failed: 'failed', skipped: 'skipped', retrying: 'retrying',
}

interface NodeData {
  spec: {
    id: string; routine: string
    inputs: Record<string, unknown>; depends_on: string[]
    when: string | null; retry: unknown
  }
  inputPorts: InputPort[]
  state: NodeState; output: unknown; outputFormat: string; error: string | null
  attempt: number; startedAt: number | null; doneAt: number | null
}

const props = defineProps<{ data: NodeData }>()

// trigger: 排序依赖(顶部单个 handle)
const triggerPort = computed(() =>
  props.data.inputPorts.find(p => p.type === 'trigger') ?? null,
)
// data / cond: 有左侧 handle 的槽位
const connectedPorts = computed(() =>
  props.data.inputPorts.filter(p => p.type === 'data' || p.type === 'cond'),
)
// wi / literal: 纯展示行
const staticPorts = computed(() =>
  props.data.inputPorts.filter(p => p.type === 'wi' || p.type === 'literal'),
)

const hasPorts = computed(() =>
  props.data.inputPorts.length > 0,
)

// trigger handle 垂直位置(ports 区第一行中心)
const triggerHandleStyle = computed((): Record<string, string> => {
  const top = HEADER_H + 1 + SEC_PAD_T + ROW_H / 2
  return { top: `${top}px`, transform: 'translateY(-50%)' }
})

// data/cond handle 垂直位置(trigger 行之后依次排布)
function leftStyle(connIndex: number): Record<string, string> {
  const triggerOffset = triggerPort.value ? ROW_H : 0
  const top = HEADER_H + 1 + SEC_PAD_T + triggerOffset + connIndex * ROW_H + ROW_H / 2
  return { top: `${top}px`, transform: 'translateY(-50%)' }
}

function truncate(s: string, n: number) { return s.length > n ? s.slice(0, n) + '...' : s }

const duration = computed(() => {
  const { startedAt, doneAt, state } = props.data
  if (!startedAt) return ''
  const end = doneAt ?? (state === 'running' || state === 'retrying' ? Date.now() : null)
  if (!end) return ''
  const ms = end - startedAt
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
})

const outputPreview = computed(() => {
  const { output, state } = props.data
  if (state !== 'completed' || output == null) return ''
  const s = typeof output === 'string' ? output : JSON.stringify(output)
  return truncate(s, 46)
})
const shortError = computed(() => truncate(props.data.error ?? '', 46))
</script>

<style scoped>
/* ─── 色盘(Darcula)─────────────────────────────────
   bg-deep  #1e1f22   bg-card  #2d2f31   bg-panel  #2b2b2b
   border   #454749   border2  #3a3c3f
   text     #bababa   muted    #6e7176   dim      #4a4e54
   blue     #4f84c4   green    #57965c   red      #c75450
   amber    #d09b5c   purple   #9876aa
   ────────────────────────────────────────────────── */

.node {
  border-radius: 7px;
  background: #2d2f31;
  border: 1px solid #454749;
  box-shadow: 0 2px 8px rgba(0,0,0,.35);
  display: flex; flex-direction: column;
  position: relative;
  overflow: visible;
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', ui-monospace, monospace;
  font-size: 11px;
  cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}
.node:hover { border-color: #5a5e62; box-shadow: 0 4px 14px rgba(0,0,0,.4); }

/* 状态左竖条 */
.stripe {
  position: absolute; left:0; top:0; bottom:0; width:3px;
  border-radius:7px 0 0 7px; background:#454749;
  transition: background .2s; pointer-events:none;
}
.s-running   .stripe { background: #4f84c4; }
.s-completed .stripe { background: #57965c; }
.s-failed    .stripe { background: #c75450; }
.s-skipped   .stripe { background: #555759; }
.s-retrying  .stripe { background: #d09b5c; }

/* 运行时边框微发光 */
.s-running  { border-color: #3a5c8a; animation: glow 2s ease-in-out infinite; }
.s-completed{ border-color: #2e5e38; }
.s-failed   { border-color: #6b3533; }
@keyframes glow {
  0%,100% { box-shadow: 0 0 0 2px rgba(79,132,196,.12), 0 2px 8px rgba(0,0,0,.35); }
  50%     { box-shadow: 0 0 0 4px rgba(79,132,196,.2),  0 2px 8px rgba(0,0,0,.35); }
}

/* ─── 头部 ─── */
.head {
  padding: 9px 12px 8px 14px;
}
.head-main {
  display: flex; align-items: baseline; justify-content: space-between; gap: 6px;
  margin-bottom: 2px;
}
.node-id {
  font-size: 13px; font-weight: 700; color: #d4d6db;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
}
.node-dur { font-size: 10px; color: #4a4e54; flex-shrink: 0; }
.node-routine { font-size: 10px; color: #5c6065; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ─── 端口区 ─── */
.ports {
  border-top: 1px solid #3a3c3f;
  padding: 5px 0 3px;
}

/* ordering 行(连到顶部 handle) */
.order-row {
  display: flex; align-items: center; gap: 5px;
  padding: 3px 12px 3px 14px;
  min-height: 26px;
  color: #4a4e54;
}
.order-label  { font-size: 10px; color: #4a4e54; flex-shrink: 0; font-style: italic; }
.order-sources{ font-size: 10px; color: #4a4e54; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

/* 端口行 */
.port-row {
  display: flex; align-items: center; gap: 7px;
  padding: 3px 12px 3px 20px;
  min-height: 26px;
  transition: background .1s;
}
.port-row:hover { background: #313437; }

/* 行内圆点(对齐 handle) */
.pdot {
  width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0;
}
.pdot-data { background: #2e5080; border: 1.5px solid #4f84c4; }
.pdot-cond { background: #3a2860; border: 1.5px solid #9876aa; }

.pname {
  font-size: 11px; font-weight: 600; color: #8bacc8; flex-shrink: 0; min-width: 30px;
}
.pr-cond .pname { color: #a080c0; }

.psrc {
  font-size: 11px; color: #4a5a6a;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.pr-cond .psrc { color: #5a4870; }

/* 静态行(WI / literal,无 handle) */
.pr-static {
  padding-left: 14px;
  gap: 6px;
  opacity: .55;
}
.pr-static:hover { background: #2e3033; }
.pstatic-key { font-size: 11px; color: #6e7176; flex-shrink: 0; min-width: 30px; }
.pstatic-val { font-size: 10px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.wi-val  { color: #4a5e70; }
.lit-val { color: #5a5030; }

/* ─── 输出 / 错误尾行 ─── */
.tail {
  margin: 3px 10px; padding: 3px 8px; border-radius: 4px;
  font-size: 10px; overflow: hidden;
}
.tail-ok  { background: #1e2b1e; }
.tail-err { background: #2b1e1e; }
.tail-val { display: block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tail-ok  .tail-val { color: #5a8a5a; }
.tail-err .tail-val { color: #a05050; }

/* ─── 状态行 ─── */
.status-row {
  display: flex; align-items: center; gap: 5px;
  padding: 4px 12px 5px 14px;
  border-top: 1px solid #3a3c3f;
  color: #4a4e54;
}
.sdot {
  width: 6px; height: 6px; border-radius: 50%;
  background: #454749; flex-shrink: 0;
}
.slabel { font-size: 10px; }
.sattempt { font-size: 9px; color: #6e7176; margin-left: auto; }

.st-running   .sdot { background: #4f84c4; animation: blink 1s infinite; }
.st-running   { color: #5a7a9a; }
.st-completed .sdot { background: #57965c; }
.st-completed { color: #4a7a4a; }
.st-failed    .sdot { background: #c75450; }
.st-failed    { color: #8a4040; }
.st-retrying  .sdot { background: #d09b5c; animation: blink 1s infinite; }
.st-retrying  { color: #7a6030; }
.st-skipped   { color: #3a4048; }

@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }

/* ─── Handle 样式 ─── */
/* 统一尺寸基础 */
:deep(.vue-flow__handle) {
  width: 10px !important; height: 10px !important;
  min-width: unset !important; min-height: unset !important;
  border-radius: 50% !important;
}

.h-data {
  background: #1e3050 !important;
  border: 2px solid #4f84c4 !important;
}
.h-cond {
  background: #28183a !important;
  border: 2px solid #9876aa !important;
  border-radius: 0 !important; /* 菱形 */
  transform: translateY(-50%) rotate(45deg) !important;
  width: 9px !important; height: 9px !important;
}
.h-order {
  background: #1e1f22 !important;
  border: 1.5px dashed #454749 !important;
  left: -5px !important;
}
.h-out {
  background: #1e3050 !important;
  border: 2px solid #4f84c4 !important;
  right: -5px !important;
  top: 50% !important;
  transform: translateY(-50%) !important;
}
</style>
