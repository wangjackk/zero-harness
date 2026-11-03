<script setup lang="ts">
/* 真实样本：routines/user/dag/run_dag.py + dags/hello_dag.yaml。
   RunDag 作为普通 routine 被调用；内部按 ready-set 启动节点 routine，
   节点 done 回流后解锁下游，最终把 summary 聚合为整个 DAG 的 result。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const PHASES = [
  { short: '调用', title: '外部只调用一个 routine', detail: "call('run_dag', kwargs={'dag': 'hello_dag'}) 进入 RunDag；此时不需要知道内部是一张图。" },
  { short: '并发', title: '就绪节点成为命令实例', detail: 'fetch_a 与 fetch_b 没有前置依赖，同时进入 ready-set；两个节点都解析为 query_weather，但拥有各自实例。' },
  { short: '汇合', title: '完成结果解锁下游', detail: '两个 fetch 节点完成后，结果进入 outputs；merge 的 depends_on 得到满足，merge_weather 开始执行。' },
  { short: '传播', title: '依赖继续向下解析', detail: 'merge 完成后，summary 获得 $merge.output，summary_weather 开始执行。' },
  { short: '返回', title: '内部图聚合为一次调用结果', detail: 'hello_dag 将 summary 声明为整体输出；RunDag 返回 result，外部仍只面对一次 routine 调用。' }
]
const NODES = [
  { id: 'fetch_a', routine: 'query_weather', x: 370, y: 185 },
  { id: 'fetch_b', routine: 'query_weather', x: 370, y: 315 },
  { id: 'merge', routine: 'merge_weather', x: 580, y: 250 },
  { id: 'summary', routine: 'summary_weather', x: 770, y: 250 }
]
const DWELL = [1700, 2500, 2500, 2500, 2800]
const phase = ref(0)
const playing = ref(true)
const visible = ref(false)
const runId = ref(0)
const rootRef = ref<HTMLDivElement>()
const stageRef = ref<HTMLDivElement>()
let timer: number | undefined
let observer: IntersectionObserver | undefined
const progress = computed(() => phase.value / (PHASES.length - 1))

function schedule() {
  window.clearTimeout(timer)
  if (!playing.value || !visible.value) return
  timer = window.setTimeout(() => {
    if (phase.value === PHASES.length - 1) { phase.value = 0; runId.value += 1 }
    else phase.value += 1
  }, DWELL[phase.value])
}
function nodeState(id: string) {
  if (phase.value === 0) return 'pending'
  if (phase.value === 1) return id.startsWith('fetch_') ? 'running' : 'pending'
  if (phase.value === 2) {
    if (id.startsWith('fetch_')) return 'completed'
    return id === 'merge' ? 'running' : 'pending'
  }
  if (phase.value === 3) return id === 'summary' ? 'running' : 'completed'
  return 'completed'
}
function stateLabel(id: string) {
  return { pending: '等待依赖', running: 'running', completed: 'completed' }[nodeState(id)]
}
function edgeActive(edge: string) {
  if (edge === 'call') return phase.value === 0
  if (edge === 'fetch') return phase.value === 1
  if (edge === 'merge') return phase.value === 2
  if (edge === 'summary') return phase.value === 3
  return phase.value === 4
}
function syncViewport() {
  if (!stageRef.value || window.innerWidth > 640) return
  stageRef.value.scrollLeft = 110
}
watch([phase, playing, visible, runId], schedule, { immediate: true })
watch(phase, syncViewport, { flush: 'post' })
onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    phase.value = PHASES.length - 1
    playing.value = false
  }
  observer = new IntersectionObserver(([entry]) => { visible.value = entry.isIntersecting }, { threshold: .15 })
  if (rootRef.value) observer.observe(rootRef.value)
  window.addEventListener('resize', syncViewport)
  syncViewport()
})
onBeforeUnmount(() => {
  window.clearTimeout(timer)
  observer?.disconnect()
  window.removeEventListener('resize', syncViewport)
})
</script>

<template>
  <div ref="rootRef" class="dag-routine" :class="{ paused: !playing || !visible }">
    <div ref="stageRef" class="dag-stage" tabindex="0" aria-label="DAG 作为 routine 的执行动画，窄屏可横向滚动">
      <svg :key="runId" viewBox="0 0 960 500" class="dag-svg" role="img"
        aria-label="外部调用 run_dag，内部两个 query_weather 实例并发执行，随后 merge 与 summary 依次执行，最终返回整个 DAG 的结果">
        <defs>
          <radialGradient id="dag-bg" cx="56%" cy="44%" r="65%">
            <stop offset="0%" stop-color="#5FCEDD" stop-opacity=".065" />
            <stop offset="100%" stop-color="#5FCEDD" stop-opacity="0" />
          </radialGradient>
          <marker id="dag-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" class="dag-arrow" />
          </marker>
        </defs>
        <rect width="960" height="500" fill="#0b0d12" />
        <rect width="960" height="500" fill="url(#dag-bg)" />
        <text x="42" y="48" class="dag-kicker">已实现样本 · HELLO_DAG</text>
        <text x="918" y="48" text-anchor="end" class="dag-note">节点与编排器都是 routine</text>

        <!-- 外部调用只抵达 RunDag 边界。 -->
        <path d="M 154 250 H 210" class="dag-edge" :class="{ active: edgeActive('call') }" marker-end="url(#dag-arrow)" />
        <text x="182" y="235" text-anchor="middle" class="dag-edge-label">调用</text>
        <g transform="translate(90 250)" class="dag-caller" :class="{ received: phase === 4 }">
          <rect x="-64" y="-34" width="128" height="68" rx="11" class="dag-caller-body" />
          <text y="-7" text-anchor="middle" class="dag-node-id">routine</text>
          <text y="13" text-anchor="middle" class="dag-node-routine">上层调用者</text>
          <text y="29" text-anchor="middle" class="dag-node-state">{{ phase === 4 ? '收到 result' : '发起调用' }}</text>
        </g>

        <!-- RunDag 是普通 routine；虚线内部是 hello_dag 的实现细节。 -->
        <rect x="220" y="82" width="680" height="330" rx="15" class="dag-boundary" :class="{ complete: phase === 4 }" />
        <text x="244" y="112" class="dag-boundary-name">run_dag</text>
        <text x="318" y="112" class="dag-boundary-sub">RunDag · routine</text>
        <text x="876" y="112" text-anchor="end" class="dag-file">hello_dag.yaml</text>

        <path d="M 429 185 L 510 235 M 429 315 L 510 265" class="dag-edge"
          :class="{ active: edgeActive('fetch'), done: phase > 1 }" marker-end="url(#dag-arrow)" />
        <path d="M 639 250 H 701" class="dag-edge" :class="{ active: edgeActive('merge') || edgeActive('summary'), done: phase > 3 }" marker-end="url(#dag-arrow)" />

        <g v-for="node in NODES" :key="node.id" :transform="`translate(${node.x} ${node.y})`"
          class="dag-node" :class="nodeState(node.id)">
          <rect x="-59" y="-34" width="118" height="68" rx="10" class="dag-node-body" />
          <text y="-12" text-anchor="middle" class="dag-node-id">{{ node.id }}</text>
          <text y="7" text-anchor="middle" class="dag-node-routine">{{ node.routine }}</text>
          <text y="25" text-anchor="middle" class="dag-node-state">{{ stateLabel(node.id) }}</text>
        </g>

        <!-- 最后一个内部输出成为整个 routine 的 result。 -->
        <path d="M 770 284 V 438 H 90 V 290" class="dag-edge result-edge" :class="{ active: edgeActive('result') }" marker-end="url(#dag-arrow)" />
        <text x="530" y="429" text-anchor="middle" class="dag-result-label" :class="{ on: phase === 4 }">outputs: summary → result</text>
        <text x="244" y="392" class="dag-internal-note">{{ phase === 0 ? '内部结构尚未展开' : phase === 1 ? 'ready = { fetch_a, fetch_b }' : phase === 2 ? 'fetch_a / fetch_b completed → merge ready' : phase === 3 ? 'merge completed → summary ready' : '内部节点退出，调用边界保留到结果返回' }}</text>

        <line x1="42" y1="474" x2="918" y2="474" class="dag-axis" />
        <rect x="42" y="473.2" width="876" height="1.6" class="dag-progress" :style="{ transform: `scaleX(${progress})` }" />
      </svg>
    </div>
    <div class="dag-scroll-hint">图示可横向滚动</div>

    <div class="dag-foot">
      <span class="dag-phase mono">0{{ phase + 1 }}<i>/05</i></span>
      <div class="dag-caption"><b>{{ PHASES[phase].title }}</b><p>{{ PHASES[phase].detail }}</p></div>
    </div>
    <div class="dag-steps" aria-label="DAG 执行阶段">
      <span v-for="(item, i) in PHASES" :key="item.short" class="dag-step" :class="{ active: phase === i }"
        :aria-current="phase === i ? 'step' : undefined">
        <span class="mono">0{{ i + 1 }}</span>{{ item.short }}
      </span>
    </div>
    <div class="dag-evidence">
      <span class="mono">run_dag.py · hello_dag.yaml</span>
      <p><code>nested_dag.yaml</code> 的节点再次调用 <code>run_dag</code>：子 DAG 对父级仍只是一个普通节点，内部各自维护 outputs。</p>
    </div>
  </div>
</template>

<style scoped>
.dag-routine { border: 1px solid var(--line); border-radius: var(--r-lg); background: #0b0d12; overflow: hidden; }
.dag-stage { overflow: hidden; }
.dag-stage:focus-visible { outline: 2px solid #7FE3EF; outline-offset: -2px; }
.dag-svg { display: block; width: 100%; height: auto; }
.dag-scroll-hint { display: none; }
.paused * { animation-play-state: paused !important; }
.dag-kicker { fill: rgba(127,227,239,.58); font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em; }
.dag-note { fill: rgba(178,205,214,.42); font-size: 10px; }
.dag-arrow { fill: #7FDCE8; }
.dag-edge { fill: none; stroke: rgba(135,164,178,.2); stroke-width: 1.2; transition: stroke .3s ease; }
.dag-edge.active { stroke: rgba(127,220,232,.88); stroke-dasharray: 7 6; animation: dag-flow 1s linear infinite; }
.dag-edge.done { stroke: rgba(127,220,232,.38); }
.dag-edge-label { fill: rgba(174,204,213,.5); font-size: 9px; }
.dag-caller-body { fill: #111a22; stroke: rgba(127,197,209,.3); }
.dag-caller.received .dag-caller-body { stroke: rgba(127,227,239,.72); }
.dag-boundary { fill: rgba(18,30,39,.32); stroke: rgba(127,227,239,.38); stroke-width: 1; stroke-dasharray: 5 5; }
.dag-boundary.complete { stroke: rgba(127,227,239,.72); }
.dag-boundary-name { fill: #d9eef2; font-family: var(--font-mono); font-size: 13px; }
.dag-boundary-sub, .dag-file { fill: rgba(169,208,217,.48); font-size: 9px; }
.dag-file { font-family: var(--font-mono); }
.dag-node-body { fill: #111a22; stroke: rgba(127,197,209,.25); transition: fill .35s ease,stroke .35s ease; }
.dag-node.running .dag-node-body { fill: #14232b; stroke: rgba(127,227,239,.86); animation: dag-node-pulse 1.8s ease-in-out infinite; }
.dag-node.completed .dag-node-body { fill: rgba(20,35,43,.72); stroke: rgba(127,220,232,.48); }
.dag-node-id { fill: #d9eef2; font-family: var(--font-mono); font-size: 11px; }
.dag-node-routine { fill: rgba(169,208,217,.58); font-family: var(--font-mono); font-size: 8px; }
.dag-node-state { fill: rgba(169,208,217,.42); font-size: 8px; }
.dag-node.running .dag-node-state, .dag-node.completed .dag-node-state, .dag-caller.received .dag-node-state { fill: rgba(127,227,239,.74); }
.dag-internal-note { fill: rgba(127,227,239,.48); font-family: var(--font-mono); font-size: 9px; }
.result-edge { stroke: rgba(185,167,228,.2); }
.result-edge.active { stroke: rgba(185,167,228,.82); }
.dag-result-label { fill: rgba(185,167,228,.34); font-family: var(--font-mono); font-size: 9px; }
.dag-result-label.on { fill: rgba(207,191,241,.82); }
.dag-axis { stroke: rgba(255,255,255,.08); }
.dag-progress { fill: rgba(127,227,239,.55); transform-box: fill-box; transform-origin: 0 50%; transition: transform .7s var(--ease); }
@keyframes dag-flow { to { stroke-dashoffset: -13; } }
@keyframes dag-node-pulse { 50% { stroke: rgba(127,227,239,.4); } }
.dag-foot { min-height: 88px; padding: 16px 22px; border-top: 1px solid rgba(255,255,255,.08); display: flex; align-items: flex-start; gap: 14px; }
.dag-phase { padding-top: 2px; color: #7FE3EF; font-size: 11px; }
.dag-phase i { color: #4d5665; font-style: normal; }
.dag-caption { flex: 1; }
.dag-caption b { color: #e6edf3; font-size: 13px; font-weight: 500; }
.dag-caption p { margin: 5px 0 0; max-width: 760px; color: #8B98A6; font-size: 12px; line-height: 1.7; }
.dag-steps { display: grid; grid-template-columns: repeat(5,1fr); border-top: 1px solid rgba(255,255,255,.08); }
.dag-step { min-height: 45px; padding: 12px 0; border-right: 1px solid rgba(255,255,255,.07); color: rgba(178,195,205,.65); font-size: 11px; text-align: center; }
.dag-step:last-child { border-right: 0; }
.dag-step span { margin-right: 8px; color: rgba(127,227,239,.46); font-size: 9px; }
.dag-step.active { color: #E1F3F6; background: rgba(127,227,239,.05); }
.dag-step.active span { color: #7FE3EF; }
.dag-evidence { padding: 13px 22px 15px; border-top: 1px solid rgba(255,255,255,.07); display: flex; align-items: baseline; gap: 18px; }
.dag-evidence > span { flex: none; color: rgba(127,227,239,.52); font-size: 9px; }
.dag-evidence p { margin: 0; color: #778692; font-size: 10.5px; line-height: 1.65; }
.dag-evidence code { color: #AAB9C2; font-family: var(--font-mono); font-size: 10px; }
@media (max-width:720px) {
  .dag-foot { flex-wrap: wrap; }
  .dag-caption { flex-basis: calc(100% - 48px); }
  .dag-evidence { display: block; }
  .dag-evidence p { margin-top: 7px; }
}
@media (max-width:640px) {
  .dag-stage { overflow-x: auto; scrollbar-width: thin; }
  .dag-svg { width: 820px; max-width: none; }
  .dag-scroll-hint { display: block; padding: 5px 12px; text-align: right; color: rgba(178,198,208,.42); font-size: 9px; }
  .dag-step span { display: block; margin: 0 0 3px; }
}
@media (prefers-reduced-motion:reduce) { .dag-routine *, .dag-routine *::before, .dag-routine *::after { animation: none !important; transition: none !important; } }
</style>
