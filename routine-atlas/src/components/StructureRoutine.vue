<script setup lang="ts">
/* 概念示意：本组件不声称仓库已有行为树或状态机实现。
   只展示共同的组合原则：结构整体可作为 routine 被调用，内部再调用 routine。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ kind: 'behavior' | 'state' }>()
const isBehavior = computed(() => props.kind === 'behavior')
const title = computed(() => isBehavior.value ? '行为树' : '状态机')
const english = computed(() => isBehavior.value ? 'BEHAVIOR TREE' : 'STATE MACHINE')
const phases = computed(() => isBehavior.value ? [
  { short: '调用', title: '整棵树作为一个 routine 被调用', detail: '上层不需要了解树的分支与控制节点，只面对统一的调用边界。' },
  { short: '判断', title: '选择节点调用条件 routine', detail: '条件 routine 返回结果；选择节点据此决定后续分支。' },
  { short: '执行', title: '控制结构调用动作 routine', detail: '条件满足后进入动作分支；动作仍是普通 routine，不是树的专属节点类型。' },
  { short: '返回', title: '动作结果沿调用关系返回', detail: '内部执行结束，整棵树把结果返回给上层调用者。' }
] : [
  { short: '调用', title: '整个状态机作为一个 routine 被调用', detail: '上层只发起一次调用；初始状态与迁移规则留在实现内部。' },
  { short: '迁移', title: '事件驱动状态迁移', detail: '事件使当前状态从待命切换到执行；迁移决定接下来调用什么。' },
  { short: '执行', title: '当前状态调用动作 routine', detail: '状态负责选择与组织，实际工作由被调用的普通 routine 完成。' },
  { short: '返回', title: '动作完成后返回整体结果', detail: '内部状态与动作不必暴露给上层；结果仍沿原调用关系返回。' }
])
const phase = ref(0)
const playing = ref(true)
const visible = ref(false)
const runId = ref(0)
const rootRef = ref<HTMLElement>()
let timer: number | undefined
let observer: IntersectionObserver | undefined

function schedule() {
  window.clearTimeout(timer)
  if (!playing.value || !visible.value) return
  timer = window.setTimeout(() => {
    if (phase.value === 3) { phase.value = 0; runId.value += 1 }
    else phase.value += 1
  }, phase.value === 0 ? 1800 : 2400)
}
watch([phase, playing, visible, runId], schedule, { immediate: true })
onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    phase.value = 3
    playing.value = false
  }
  observer = new IntersectionObserver(([entry]) => { visible.value = entry.isIntersecting }, { threshold: .15 })
  if (rootRef.value) observer.observe(rootRef.value)
})
onBeforeUnmount(() => { window.clearTimeout(timer); observer?.disconnect() })
</script>

<template>
  <article ref="rootRef" class="structure-routine" :class="{ paused: !playing || !visible }">
    <header class="structure-head">
      <div><span class="structure-en mono">{{ english }}</span><h3>{{ title }}</h3></div>
      <span class="structure-badge">结构示意 · 非现有实现</span>
    </header>
    <div class="structure-stage">
      <svg :key="runId" viewBox="0 0 620 330" class="structure-svg" role="img"
        :aria-label="isBehavior ? '行为树作为 routine 被调用，选择节点先调用条件 routine，再调用动作 routine，最后返回结果' : '状态机作为 routine 被调用，事件触发状态迁移，当前状态调用动作 routine，最后返回结果'">
        <defs>
          <marker :id="`${kind}-structure-arrow`" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" class="structure-arrow" />
          </marker>
        </defs>
        <rect width="620" height="330" class="structure-bg" />
        <text x="25" y="31" class="structure-caption">内部结构</text>
        <text x="595" y="31" text-anchor="end" class="structure-caption">外部仍是 routine 调用</text>
        <path d="M 91 166 H 130" class="structure-edge" :class="{ active: phase === 0 }" :marker-end="`url(#${kind}-structure-arrow)`" />
        <g transform="translate(52 166)" class="structure-caller" :class="{ received: phase === 3 }">
          <rect x="-37" y="-27" width="74" height="54" rx="9" class="structure-node-body" />
          <text y="-3" text-anchor="middle" class="structure-node-name">routine</text>
          <text y="13" text-anchor="middle" class="structure-node-sub">{{ phase === 3 ? '收到结果' : '调用者' }}</text>
        </g>
        <rect x="140" y="50" width="445" height="231" rx="13" class="structure-boundary" :class="{ complete: phase === 3 }" />
        <text x="158" y="75" class="structure-boundary-name">{{ title }} · routine</text>
        <text x="565" y="75" text-anchor="end" class="structure-boundary-sub">统一调用边界</text>

        <template v-if="isBehavior">
          <path d="M 274 137 V 157 M 274 157 H 218 V 188 M 274 157 H 423 V 188" class="structure-edge" />
          <path d="M 218 174 V 187" class="structure-edge" :class="{ active: phase === 1, done: phase > 1 }" :marker-end="`url(#${kind}-structure-arrow)`" />
          <path d="M 423 174 V 187" class="structure-edge" :class="{ active: phase === 2, done: phase > 2 }" :marker-end="`url(#${kind}-structure-arrow)`" />
          <g transform="translate(274 119)" class="structure-node control" :class="{ running: phase === 1 || phase === 2 }">
            <rect x="-50" y="-22" width="100" height="44" rx="9" class="structure-node-body" />
            <text y="-2" text-anchor="middle" class="structure-node-name">选择</text>
            <text y="13" text-anchor="middle" class="structure-node-sub">控制节点</text>
          </g>
          <g transform="translate(218 213)" class="structure-node" :class="{ running: phase === 1, completed: phase > 1 }">
            <rect x="-62" y="-27" width="124" height="54" rx="9" class="structure-node-body" />
            <text y="-6" text-anchor="middle" class="structure-node-name">条件 routine</text>
            <text y="13" text-anchor="middle" class="structure-node-sub">{{ phase === 0 ? '等待' : phase === 1 ? '返回条件结果' : '条件满足' }}</text>
          </g>
          <g transform="translate(423 213)" class="structure-node" :class="{ running: phase === 2, completed: phase === 3 }">
            <rect x="-62" y="-27" width="124" height="54" rx="9" class="structure-node-body" />
            <text y="-6" text-anchor="middle" class="structure-node-name">动作 routine</text>
            <text y="13" text-anchor="middle" class="structure-node-sub">{{ phase < 2 ? '等待分支' : phase === 2 ? 'running' : 'completed' }}</text>
          </g>
          <path d="M 423 242 V 304 H 52 V 198" class="structure-edge structure-result" :class="{ active: phase === 3 }" :marker-end="`url(#${kind}-structure-arrow)`" />
        </template>

        <template v-else>
          <path d="M 263 124 H 405" class="structure-edge" :class="{ active: phase === 1, done: phase > 1 }" :marker-end="`url(#${kind}-structure-arrow)`" />
          <text x="334" y="113" text-anchor="middle" class="structure-event">事件</text>
          <path d="M 477 153 V 187" class="structure-edge" :class="{ active: phase === 2, done: phase > 2 }" :marker-end="`url(#${kind}-structure-arrow)`" />
          <g transform="translate(218 124)" class="structure-node state" :class="{ completed: phase > 1 }">
            <rect x="-55" y="-25" width="110" height="50" rx="25" class="structure-node-body" />
            <text y="-3" text-anchor="middle" class="structure-node-name">待命</text>
            <text y="13" text-anchor="middle" class="structure-node-sub">{{ phase <= 1 ? '当前状态' : '已迁移' }}</text>
          </g>
          <g transform="translate(477 124)" class="structure-node state" :class="{ running: phase >= 2 }">
            <rect x="-55" y="-25" width="110" height="50" rx="25" class="structure-node-body" />
            <text y="-3" text-anchor="middle" class="structure-node-name">执行</text>
            <text y="13" text-anchor="middle" class="structure-node-sub">{{ phase < 2 ? '下一状态' : '当前状态' }}</text>
          </g>
          <g transform="translate(477 213)" class="structure-node" :class="{ running: phase === 2, completed: phase === 3 }">
            <rect x="-68" y="-27" width="136" height="54" rx="9" class="structure-node-body" />
            <text y="-6" text-anchor="middle" class="structure-node-name">动作 routine</text>
            <text y="13" text-anchor="middle" class="structure-node-sub">{{ phase < 2 ? '等待状态调用' : phase === 2 ? 'running' : 'completed' }}</text>
          </g>
          <text x="219" y="218" text-anchor="middle" class="structure-internal-note">状态组织调用<br />routine 完成工作</text>
          <path d="M 477 242 V 304 H 52 V 198" class="structure-edge structure-result" :class="{ active: phase === 3 }" :marker-end="`url(#${kind}-structure-arrow)`" />
        </template>
        <text x="320" y="296" text-anchor="middle" class="structure-result-label" :class="{ on: phase === 3 }">内部结果 → 整体返回</text>
      </svg>
    </div>
    <div class="structure-foot">
      <span class="structure-phase mono">0{{ phase + 1 }}<i>/04</i></span>
      <div class="structure-copy"><b>{{ phases[phase].title }}</b><p>{{ phases[phase].detail }}</p></div>
    </div>
    <div class="structure-steps" :aria-label="`${title}结构示意阶段`">
      <span v-for="(item, i) in phases" :key="item.short" class="structure-step" :class="{ active: phase === i }"
        :aria-current="phase === i ? 'step' : undefined">
        <span class="mono">0{{ i + 1 }}</span>{{ item.short }}
      </span>
    </div>
  </article>
</template>

<style scoped>
.structure-routine { min-width: 0; border: 1px solid var(--line); border-radius: var(--r-lg); background: #0b0d12; overflow: hidden; }
.structure-head { min-height: 76px; padding: 15px 20px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid rgba(255,255,255,.08); }
.structure-head h3 { margin: 4px 0 0; color: #e6edf3; font-size: 17px; font-weight: 550; }
.structure-en { color: rgba(127,227,239,.55); font-size: 8px; letter-spacing: .12em; }
.structure-badge { flex: none; padding: 4px 7px; border: 1px solid rgba(255,255,255,.09); border-radius: 5px; color: #778692; font-size: 9px; }
.structure-svg { display: block; width: 100%; height: auto; }
.structure-bg { fill: #0b0d12; }
.structure-caption { fill: rgba(178,205,214,.35); font-size: 8px; }
.structure-arrow { fill: #7FDCE8; }
.structure-edge { fill: none; stroke: rgba(135,164,178,.22); stroke-width: 1.1; }
.structure-edge.active { stroke: rgba(127,220,232,.88); stroke-dasharray: 7 6; animation: structure-flow 1s linear infinite; }
.structure-edge.done { stroke: rgba(127,220,232,.4); }
.structure-result { stroke: rgba(185,167,228,.18); }
.structure-result.active { stroke: rgba(185,167,228,.82); }
.structure-boundary { fill: rgba(18,30,39,.3); stroke: rgba(127,227,239,.32); stroke-width: 1; stroke-dasharray: 5 5; }
.structure-boundary.complete { stroke: rgba(127,227,239,.65); }
.structure-boundary-name { fill: #d9eef2; font-family: var(--font-mono); font-size: 10px; }
.structure-boundary-sub { fill: rgba(169,208,217,.4); font-size: 8px; }
.structure-node-body { fill: #111a22; stroke: rgba(127,197,209,.25); transition: fill .3s ease,stroke .3s ease; }
.structure-node.running .structure-node-body { fill: #14232b; stroke: rgba(127,227,239,.86); animation: structure-pulse 1.8s ease-in-out infinite; }
.structure-node.completed .structure-node-body,.structure-caller.received .structure-node-body { stroke: rgba(127,220,232,.55); }
.structure-node.control .structure-node-body { fill: #141d27; }
.structure-node-name { fill: #d9eef2; font-family: var(--font-mono); font-size: 10px; }
.structure-node-sub { fill: rgba(169,208,217,.48); font-size: 8px; }
.structure-node.running .structure-node-sub,.structure-node.completed .structure-node-sub { fill: rgba(127,227,239,.7); }
.structure-event { fill: rgba(127,227,239,.55); font-size: 8px; }
.structure-internal-note { fill: rgba(169,208,217,.4); font-size: 9px; line-height: 1.7; }
.structure-result-label { fill: rgba(185,167,228,.28); font-size: 8px; }
.structure-result-label.on { fill: rgba(207,191,241,.82); }
.paused * { animation-play-state: paused !important; }
.structure-foot { min-height: 114px; padding: 14px 18px; display: flex; align-items: flex-start; gap: 11px; border-top: 1px solid rgba(255,255,255,.08); }
.structure-phase { padding-top: 2px; color: #7FE3EF; font-size: 10px; }
.structure-phase i { color: #4d5665; font-style: normal; }
.structure-copy { flex: 1; }
.structure-copy b { color: #e6edf3; font-size: 12px; font-weight: 500; }
.structure-copy p { margin: 5px 0 0; color: #8B98A6; font-size: 11px; line-height: 1.65; }
.structure-steps { display: grid; grid-template-columns: repeat(4,1fr); border-top: 1px solid rgba(255,255,255,.08); }
.structure-step { min-height: 43px; padding: 14px 0; border-right: 1px solid rgba(255,255,255,.07); color: rgba(178,195,205,.65); font-size: 10px; text-align: center; }
.structure-step:last-child { border-right: 0; }
.structure-step span { margin-right: 7px; color: rgba(127,227,239,.46); font-size: 8px; }
.structure-step.active { color: #E1F3F6; background: rgba(127,227,239,.05); }
.structure-step.active span { color: #7FE3EF; }
@keyframes structure-flow { to { stroke-dashoffset: -13; } }
@keyframes structure-pulse { 50% { stroke: rgba(127,227,239,.4); } }
@media (max-width:1100px) {
  .structure-foot { flex-wrap: wrap; }
  .structure-copy { flex-basis: calc(100% - 43px); }
  .structure-foot { min-height: 140px; }
}
@media (max-width:640px) {
  .structure-head { align-items: flex-start; padding: 13px 15px; }
  .structure-badge { max-width: 96px; line-height: 1.5; text-align: center; }
  .structure-foot { min-height: 0; padding: 13px 15px; }
  .structure-step span { display: block; margin: 0 0 3px; }
}
@media (prefers-reduced-motion:reduce) { .structure-routine *, .structure-routine *::before, .structure-routine *::after { animation: none !important; transition: none !important; } }
</style>
