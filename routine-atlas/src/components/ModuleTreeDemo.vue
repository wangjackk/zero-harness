<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const root = ref<HTMLElement | null>(null)
const phase = ref(0)
const running = ref(false)
const reduced = ref(false)
const durations = [1500, 1600, 2500, 2100]
let timer: number | null = null
let observer: IntersectionObserver | null = null

const phaseText = computed(() => [
  '模块不是平铺清单，而是按父子关系组织成树。',
  'Routine 1 正在运行，并占用 right_arm。',
  'right_arm 的冲突锥覆盖祖先 body、自身与后代 right_hand。',
  'left_arm 分支不在冲突锥内，仍可由其他 routine 并行使用。'
][phase.value])

function stopTimer() {
  if (timer !== null) window.clearTimeout(timer)
  timer = null
}

function schedule() {
  stopTimer()
  if (!running.value || reduced.value) return
  timer = window.setTimeout(() => {
    phase.value = (phase.value + 1) % durations.length
    schedule()
  }, durations[phase.value])
}

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduced.value) {
    phase.value = 2
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    running.value = entry.isIntersecting
    if (running.value) schedule()
    else stopTimer()
  }, { threshold: 0.25 })
  if (root.value) observer.observe(root.value)
})

onBeforeUnmount(() => {
  stopTimer()
  observer?.disconnect()
})
</script>

<template>
  <div
    ref="root"
    class="module-tree-demo"
    :class="[`tree-phase-${phase}`, { paused: !running && !reduced, reduced }]"
    role="img"
    aria-label="人体模块树动画：body 分为 left_arm 和 right_arm，两臂分别连接 left_hand 和 right_hand；占用 right_arm 后，冲突锥覆盖祖先 body 与后代 right_hand，左侧分支可以并行"
  >
    <svg viewBox="0 0 680 380" aria-hidden="true">
      <defs>
        <filter id="tree-node-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="5" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>

      <g class="tree-edges">
        <path class="tree-edge" d="M340 86 C340 120 170 110 170 150" />
        <path class="tree-edge" d="M340 86 C340 120 510 110 510 150" />
        <path class="tree-edge" d="M170 214 V278" />
        <path class="tree-edge" d="M510 214 V278" />
        <path class="tree-flow cone-flow" d="M340 86 C340 120 510 110 510 150" />
        <path class="tree-flow cone-flow" d="M510 214 V278" />
        <path class="tree-flow free-flow" d="M340 86 C340 120 170 110 170 150" />
        <path class="tree-flow free-flow" d="M170 214 V278" />
      </g>

      <g class="tree-node body-node">
        <rect x="270" y="30" width="140" height="56" rx="12" />
        <text class="node-name" x="340" y="56" text-anchor="middle">body</text>
        <text class="node-sub" x="340" y="74" text-anchor="middle">根模块</text>
        <text class="cone-tag ancestor-tag" x="420" y="60">祖先</text>
      </g>

      <g class="tree-node left-arm-node">
        <rect x="95" y="150" width="150" height="64" rx="12" />
        <text class="node-name" x="170" y="179" text-anchor="middle">left_arm</text>
        <text class="node-sub" x="170" y="199" text-anchor="middle">左臂</text>
        <text class="free-tag" x="170" y="137" text-anchor="middle">可并行</text>
      </g>
      <g class="tree-node right-arm-node">
        <rect class="occupied-outline" x="428" y="143" width="164" height="78" rx="17" />
        <rect x="435" y="150" width="150" height="64" rx="12" />
        <text class="node-name" x="510" y="179" text-anchor="middle">right_arm</text>
        <text class="node-sub" x="510" y="199" text-anchor="middle">右臂</text>
        <text class="cone-tag occupied-tag" x="595" y="185">已占用</text>
      </g>

      <g class="tree-node left-hand-node">
        <rect x="95" y="278" width="150" height="64" rx="12" />
        <text class="node-name" x="170" y="307" text-anchor="middle">left_hand</text>
        <text class="node-sub" x="170" y="327" text-anchor="middle">左手 · 子模块</text>
      </g>
      <g class="tree-node right-hand-node">
        <rect x="435" y="278" width="150" height="64" rx="12" />
        <text class="node-name" x="510" y="307" text-anchor="middle">right_hand</text>
        <text class="node-sub" x="510" y="327" text-anchor="middle">右手 · 子模块</text>
        <text class="cone-tag descendant-tag" x="595" y="313">后代</text>
      </g>
    </svg>

    <div class="tree-stage-copy">
      <span class="mono">0{{ phase + 1 }} / 04</span>
      <p>{{ phaseText }}</p>
    </div>
  </div>
</template>

<style scoped>
.module-tree-demo { --tree-cone: #a98bfa; --tree-free: #62bfa4; }
.module-tree-demo svg { display: block; width: 100%; height: auto; overflow: visible; }
.tree-edge { fill: none; stroke: var(--line-2); stroke-width: 1.6; }
.tree-flow { fill: none; stroke-width: 2.5; stroke-linecap: round; stroke-dasharray: 3 14; opacity: 0; animation: tree-flow 1.1s linear infinite; transition: opacity .5s var(--ease); }
.cone-flow { stroke: var(--tree-cone); }
.free-flow { stroke: var(--tree-free); animation-duration: 1.45s; }
.tree-node > rect:not(.occupied-outline) { fill: color-mix(in oklab, var(--surface-solid) 94%, transparent); stroke: var(--line-2); stroke-width: 1.4; transition: fill .5s var(--ease), stroke .5s var(--ease); }
.node-name { fill: var(--ink); font-family: var(--font-mono); font-size: 15px; }
.node-sub { fill: var(--ink-4); font-family: var(--font-sans); font-size: 10px; }
.occupied-outline { fill: none; stroke: var(--tree-cone); stroke-width: 2; stroke-linecap: round; stroke-dasharray: 14 11; opacity: 0; animation: occupied-spin 1.8s linear infinite; transition: opacity .45s var(--ease); }
.cone-tag, .free-tag { font-family: var(--font-sans); font-size: 10px; opacity: 0; transition: opacity .45s var(--ease); }
.cone-tag { fill: var(--tree-cone); }
.free-tag { fill: var(--tree-free); }
.tree-phase-0 .tree-flow { opacity: .2; }
.tree-phase-1 .cone-flow { opacity: .45; }
.tree-phase-1 .right-arm-node > rect:not(.occupied-outline), .tree-phase-2 .right-arm-node > rect:not(.occupied-outline), .tree-phase-3 .right-arm-node > rect:not(.occupied-outline) { fill: color-mix(in oklab, var(--tree-cone) 12%, var(--surface-solid)); stroke: var(--tree-cone); }
.tree-phase-1 .occupied-outline, .tree-phase-2 .occupied-outline, .tree-phase-3 .occupied-outline { opacity: 1; }
.tree-phase-2 .body-node > rect, .tree-phase-2 .right-hand-node > rect, .tree-phase-3 .body-node > rect, .tree-phase-3 .right-hand-node > rect { fill: color-mix(in oklab, var(--tree-cone) 7%, var(--surface-solid)); stroke: color-mix(in oklab, var(--tree-cone) 65%, var(--line)); }
.tree-phase-2 .cone-flow, .tree-phase-3 .cone-flow { opacity: .95; }
.tree-phase-2 .cone-tag, .tree-phase-3 .cone-tag { opacity: 1; }
.tree-phase-3 .left-arm-node > rect, .tree-phase-3 .left-hand-node > rect { fill: color-mix(in oklab, var(--tree-free) 8%, var(--surface-solid)); stroke: var(--tree-free); }
.tree-phase-3 .free-flow, .tree-phase-3 .free-tag { opacity: 1; }
.tree-stage-copy { display: grid; grid-template-columns: 58px 1fr; align-items: center; gap: 12px; min-height: 60px; padding: 12px 20px; border-top: 1px solid var(--line); }
.tree-stage-copy > span { color: var(--tree-cone); font-size: 9px; }
.tree-stage-copy p { color: var(--ink-3); font-size: 11.5px; line-height: 1.65; }
.paused *, .reduced * { animation-play-state: paused !important; }
.reduced * { animation: none !important; transition: none !important; }
.reduced .tree-flow { display: none; }
@keyframes tree-flow { to { stroke-dashoffset: -34; } }
@keyframes occupied-spin { to { stroke-dashoffset: -50; } }
@media (max-width: 600px) {
  .module-tree-demo { overflow-x: auto; }
  .module-tree-demo svg { width: 560px; max-width: none; }
  .tree-stage-copy { position: sticky; left: 0; grid-template-columns: 48px 1fr; width: calc(100vw - 70px); padding: 12px 16px; }
  .cone-tag { display: none; }
}
</style>
