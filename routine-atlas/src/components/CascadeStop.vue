<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type NodeId = 'agent12' | 'shell13' | 'say14' | 'code15' | 'bash16'
type NodeState = 'pending' | 'creating' | 'running' | 'stopping' | 'stopped'

const STEPS = [
  { t: '创建顶层实例', d: '调用先建立顶层单元格，实例开始运行。' },
  { t: '子实例向外展开', d: '继续调用时，子单元格从父单元格右侧展开。' },
  { t: '调用关系继续延伸', d: '子实例再调用其他单元，形成完整的实例树。' },
  { t: '实例树运行中', d: '光晕与状态点表示实例仍在运行，调用关系保持清晰。' },
  { t: '顶层收到停止', d: '停止从目标单元格开始，不影响其他实例树。' },
  { t: '停止向下传播', d: '停止信号沿调用连线传向所有后代。' },
  { t: '孙实例先停止', d: '最末端单元格先退出，父实例等待后代完成清理。' },
  { t: '子实例随后停止', d: '后代完成后，中间单元格依次退出。' },
  { t: '整棵子树已停止', d: '顶层最后退出；灰色虚线保留本次调用的结构轮廓。' }
]
const DWELL = [1300, 1500, 1600, 1900, 1400, 1600, 1700, 1700, 2200]
const CREATED_AT: Record<NodeId, number> = { agent12: 0, shell13: 1, say14: 2, code15: 1, bash16: 2 }
const STOPPING_AT: Record<NodeId, number> = { agent12: 4, shell13: 5, say14: 5, code15: 5, bash16: 5 }
const STOPPED_AT: Record<NodeId, number> = { agent12: 8, shell13: 7, say14: 6, code15: 7, bash16: 6 }

const NODES: { id: NodeId; name: string; num: string; x: number; y: number; tone: string }[] = [
  { id: 'agent12', name: 'agent', num: '#12', x: 42, y: 198, tone: 'root' },
  { id: 'shell13', name: 'shell', num: '#13', x: 264, y: 112, tone: 'cyan' },
  { id: 'say14', name: 'say', num: '#14', x: 486, y: 112, tone: 'cyan' },
  { id: 'code15', name: 'code', num: '#15', x: 264, y: 284, tone: 'violet' },
  { id: 'bash16', name: 'Bash', num: '#16', x: 486, y: 284, tone: 'violet' }
]
const LINKS: { from: NodeId; to: NodeId; d: string }[] = [
  { from: 'agent12', to: 'shell13', d: 'M182 225 C224 225 222 139 264 139' },
  { from: 'shell13', to: 'say14', d: 'M404 139 H486' },
  { from: 'agent12', to: 'code15', d: 'M182 225 C224 225 222 311 264 311' },
  { from: 'code15', to: 'bash16', d: 'M404 311 H486' }
]

const phase = ref(0)
const playing = ref(false)
const rootRef = ref<HTMLElement>()
let timer: ReturnType<typeof setTimeout> | undefined
let observer: IntersectionObserver | undefined
const current = computed(() => STEPS[phase.value])

function state(id: NodeId): NodeState {
  if (phase.value < CREATED_AT[id]) return 'pending'
  if (phase.value >= STOPPED_AT[id]) return 'stopped'
  if (phase.value >= STOPPING_AT[id]) return 'stopping'
  if (phase.value === CREATED_AT[id] && phase.value < 3) return 'creating'
  return 'running'
}
function status(id: NodeId) {
  return { pending: '等待创建', creating: '已创建', running: '运行中', stopping: '停止中', stopped: '已停止' }[state(id)]
}
function schedule() {
  clearTimeout(timer)
  if (!playing.value) return
  timer = setTimeout(() => {
    phase.value = (phase.value + 1) % STEPS.length
    schedule()
  }, DWELL[phase.value])
}
onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    phase.value = 3
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    playing.value = entry.isIntersecting
    if (entry.isIntersecting) schedule()
    else clearTimeout(timer)
  }, { threshold: .15 })
  if (rootRef.value) observer.observe(rootRef.value)
})
onBeforeUnmount(() => {
  clearTimeout(timer)
  observer?.disconnect()
})
</script>

<template>
  <section ref="rootRef" class="section cascade" aria-labelledby="cascade-title">
    <div class="page">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">生命周期 · 级联诞生 / 级联打断</span>
        <h2 id="cascade-title">调用让实例图展开，<br />停止让整棵子树退场。</h2>
        <p class="lede">单元格表示命令实例，连线表示调用关系。停止沿连线传播，后代先退出，顶层最后结束。</p>
      </div>

      <div class="cascade-demo" v-reveal>
        <div class="demo-head">
          <div><span class="demo-kicker mono">COMMAND INSTANCES</span><strong>{{ current.t }}</strong></div>
          <span class="auto-note mono">AUTO LOOP</span>
        </div>
        <div class="stage-scroll">
          <svg class="mindmap" viewBox="0 0 680 430" role="img" :aria-label="current.t">
            <text x="42" y="40" class="group-label">{{ phase < 4 ? '展开 · agent #12 的实例子树' : '停止目标 · agent #12 的实例子树' }}</text>

            <g v-for="link in LINKS" :key="link.to" :class="['link-wrap', state(link.to)]">
              <path :d="link.d" class="link" />
              <path v-if="phase === 5 && state(link.to) === 'stopping'" :d="link.d" class="stop-flow" />
            </g>

            <g v-for="node in NODES" :key="node.id" :class="['node', node.tone, state(node.id)]" :transform="`translate(${node.x} ${node.y})`">
              <rect x="-7" y="-7" width="154" height="68" rx="15" class="halo" />
              <rect width="140" height="54" rx="10" class="cell" />
              <path d="M1 10 V44" class="tone-line" />
              <circle cx="17" cy="18" r="3.5" class="status-dot" />
              <text x="29" y="22" class="node-name">{{ node.name }} <tspan>{{ node.num }}</tspan></text>
              <text x="17" y="41" class="node-status">{{ status(node.id) }}</text>
              <rect v-if="state(node.id) === 'stopping'" x="-3" y="-3" width="146" height="60" rx="13" class="stop-ring" />
            </g>

            <g transform="translate(42 387)" class="legend">
              <circle cx="4" cy="-4" r="4" class="legend-live" /><text x="16">运行中</text>
              <circle cx="102" cy="-4" r="4" class="legend-stopping" /><text x="114">停止中</text>
              <rect x="200" y="-10" width="16" height="12" rx="3" class="legend-stopped" /><text x="225">已停止</text>
            </g>
          </svg>
        </div>
        <div class="demo-foot"><span class="phase mono">0{{ phase + 1 }} / 09</span><p>{{ current.d }}</p></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.cascade { border-top: 1px solid var(--line); }
.sec-head { max-width: 760px; margin-bottom: 34px; }
.sec-head h2 { margin: 14px 0 18px; }
.sec-head .lede { max-width: 650px; }
.cascade-demo { overflow: hidden; border: 1px solid var(--line-2); border-radius: 16px; background: color-mix(in oklab, var(--bg-deep) 64%, var(--surface-solid)); box-shadow: var(--shadow-2); }
.demo-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 78px; padding: 17px 24px; border-bottom: 1px solid var(--line); }
.demo-head > div { display: grid; gap: 6px; }
.demo-kicker, .auto-note { color: var(--ink-4); font-size: 9px; letter-spacing: .12em; }
.demo-head strong { color: var(--ink); font-size: 15px; font-weight: 550; }
.stage-scroll { overflow-x: auto; }
.mindmap { display: block; width: 100%; min-width: 680px; height: auto; }
.group-label { fill: var(--ink-4); font-family: var(--font-mono); font-size: 10px; letter-spacing: .04em; }
.link { fill: none; stroke: color-mix(in oklab, var(--accent) 45%, var(--line-2)); stroke-width: 1.6; }
.link-wrap.pending { opacity: 0; }
.link-wrap.creating { animation: link-in .55s ease both; }
.link-wrap.stopping .link { stroke: #e5ad57; stroke-dasharray: 5 5; }
.link-wrap.stopped .link { stroke: var(--line-2); stroke-dasharray: 3 6; opacity: .55; }
.stop-flow { fill: none; stroke: #ffd58b; stroke-width: 2.4; stroke-linecap: round; stroke-dasharray: 2 14; animation: flow 1s linear infinite; }
.node { transform-box: fill-box; transform-origin: center; }
.node.pending { opacity: 0; }
.node.creating { animation: cell-in .55s cubic-bezier(.2,.8,.2,1) both; }
.cell { fill: color-mix(in oklab, var(--surface-solid) 90%, var(--accent) 10%); stroke: color-mix(in oklab, var(--accent) 45%, var(--line-2)); stroke-width: 1.2; }
.violet .cell { fill: color-mix(in oklab, var(--surface-solid) 92%, #a98bfa 8%); stroke: color-mix(in oklab, #a98bfa 44%, var(--line-2)); }
.root .cell { stroke-width: 1.6; }
.tone-line { stroke: var(--accent); stroke-width: 3; stroke-linecap: round; }
.violet .tone-line { stroke: #a98bfa; }
.halo { fill: none; stroke: var(--accent); stroke-width: 2; opacity: 0; }
.violet .halo { stroke: #a98bfa; }
.running .halo { animation: breathe 2.4s ease-in-out infinite; }
.status-dot { fill: var(--accent); }
.violet .status-dot { fill: #a98bfa; }
.running .status-dot { animation: heartbeat 1.8s ease-in-out infinite; }
.node-name { fill: var(--ink); font-family: var(--font-mono); font-size: 12px; }
.node-name tspan { fill: var(--ink-4); font-size: 10px; }
.node-status { fill: var(--ink-3); font-size: 10px; }
.stopping .cell { fill: color-mix(in oklab, #e5ad57 9%, var(--surface-solid)); stroke: #e5ad57; }
.stopping .tone-line, .stopping .status-dot { stroke: #e5ad57; fill: #e5ad57; }
.stopping .node-status { fill: #e5ad57; }
.stop-ring { fill: none; stroke: #e5ad57; stroke-width: 1.4; animation: stopping 1.5s ease-out infinite; }
.stopped .cell { fill: transparent; stroke: var(--ink-4); stroke-dasharray: 4 4; opacity: .6; }
.stopped .tone-line { stroke: var(--ink-4); opacity: .5; }
.stopped .status-dot { fill: var(--ink-4); opacity: .6; }
.stopped .node-name, .stopped .node-status, .stopped .node-name tspan { fill: var(--ink-4); opacity: .65; }
.legend { fill: var(--ink-4); font-size: 10px; }
.legend-live { fill: var(--accent); }
.legend-stopping { fill: #e5ad57; }
.legend-stopped { fill: none; stroke: var(--ink-4); stroke-dasharray: 3 2; }
.demo-foot { display: grid; grid-template-columns: 62px 1fr; align-items: center; gap: 12px; min-height: 58px; padding: 12px 24px; border-top: 1px solid var(--line); }
.phase { color: var(--accent); font-size: 10px; }
.demo-foot p { margin: 0; color: var(--ink-3); font-size: 12px; line-height: 1.6; }
@keyframes breathe { 0%,100% { opacity: .12; } 50% { opacity: .55; } }
@keyframes heartbeat { 0%,100% { opacity: .55; } 50% { opacity: 1; } }
@keyframes stopping { from { opacity: .85; transform: scale(1); } to { opacity: 0; transform: scale(1.12); } }
@keyframes cell-in { from { opacity: 0; scale: .82; } to { opacity: 1; scale: 1; } }
@keyframes link-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes flow { to { stroke-dashoffset: -32; } }
@media (max-width: 640px) {
  .demo-head { padding: 15px 17px; }
  .auto-note { display: none; }
  .mindmap { min-width: 680px; }
  .demo-foot { grid-template-columns: 52px 1fr; padding: 12px 17px; }
}
@media (prefers-reduced-motion: reduce) {
  .node, .halo, .status-dot, .stop-ring, .stop-flow, .link-wrap { animation: none !important; }
}
</style>
