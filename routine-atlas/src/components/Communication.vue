<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const STEPS = [
  { short: '在线', title: 'listener 与 agent 都是 routine', detail: '它们是网络中两个已经存在的节点，各自独立运行。' },
  { short: '通信', title: '两个 routine 交换消息', detail: 'listener 收到输入后与 agent 通信。消息可以双向流动，但不创建节点，也不形成父子关系。' },
  { short: '调用', title: 'agent 调用另一个 routine', detail: '调用创建一个临时子节点，并建立父子生命周期。' },
  { short: '结束', title: '临时节点结束，网络继续运行', detail: '调用完成，子节点消失；listener 与 agent 仍在运行，通信关系不变。' }
]
const phase = ref(0)
const rootRef = ref<HTMLElement>()
let timer: ReturnType<typeof setInterval> | undefined
let observer: IntersectionObserver | undefined

const current = computed(() => STEPS[phase.value])
function activeAt(...phases: number[]) {
  return phases.includes(phase.value)
}
function linkState(activePhase: number) {
  return { active: phase.value === activePhase, done: phase.value > activePhase }
}
function stopLoop() {
  clearInterval(timer)
  timer = undefined
}
function startLoop() {
  if (timer !== undefined) return
  timer = setInterval(() => {
    phase.value = (phase.value + 1) % STEPS.length
  }, 2200)
}
onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    phase.value = STEPS.length - 1
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) startLoop()
    else stopLoop()
  }, { threshold: .2 })
  if (rootRef.value) observer.observe(rootRef.value)
})
onBeforeUnmount(() => {
  stopLoop()
  observer?.disconnect()
})
</script>

<template>
  <section ref="rootRef" class="communication" aria-labelledby="communication-demo-title">
    <div class="comm-head">
      <div>
        <span class="eyebrow">通信 + 调用 · 节点网络</span>
        <h2 id="communication-demo-title">通信连接节点，调用生长节点。</h2>
      </div>
    </div>

    <div class="comm-demo">
      <div class="comm-stage">
        <svg viewBox="0 0 960 570" role="img" :aria-label="`${current.title}。listener 与 agent 是已有节点，调用会产生一个临时 routine 子节点。`">
          <defs>
            <marker id="comm-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10Z" class="comm-arrow" />
            </marker>
            <radialGradient id="agent-core">
              <stop offset="0" stop-color="var(--accent)" stop-opacity=".2" />
              <stop offset="1" stop-color="var(--accent)" stop-opacity=".03" />
            </radialGradient>
          </defs>

          <text x="38" y="38" class="comm-network-title">ACTOR NETWORK</text>
          <text x="922" y="38" text-anchor="end" class="comm-network-meta">已有节点持续运行 · 执行节点随调用生灭</text>

          <!-- 背景：网络中的其他独立节点 -->
          <g class="ambient-network">
            <path d="M112 112L226 238L356 105L478 218L637 105L744 266L850 142" />
            <path d="M124 424L226 238L403 438L478 218L628 438L744 266L850 424" />
            <path d="M356 105L403 438M637 105L628 438M850 142L850 424" />
            <g transform="translate(112 112)"><circle r="18" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(356 105)"><circle r="22" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(637 105)"><circle r="19" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(850 142)"><circle r="16" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(124 424)"><circle r="20" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(403 438)"><circle r="17" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(628 438)"><circle r="21" /><circle r="3" class="ambient-core" /></g>
            <g transform="translate(850 424)"><circle r="18" /><circle r="3" class="ambient-core" /></g>
          </g>

          <!-- 系统只有两种关系：通信与调用 -->
          <path d="M278 245H425" class="comm-link request" :class="linkState(1)" marker-start="url(#comm-arrow)" marker-end="url(#comm-arrow)" />
          <path d="M535 242C610 240 660 255 701 278" class="comm-link call" :class="linkState(2)" marker-end="url(#comm-arrow)" />

          <g v-if="phase === 1" class="comm-packet request">
            <circle r="6"><animateMotion dur="1.8s" repeatCount="indefinite" path="M278 245H425" /></circle>
            <circle r="6"><animateMotion dur="1.8s" repeatCount="indefinite" path="M425 245H278" /></circle>
          </g>
          <g v-if="phase === 2" class="comm-packet call"><circle r="6"><animateMotion dur="1.8s" repeatCount="indefinite" path="M535 242C610 240 660 255 701 278" /></circle></g>

          <text x="350" y="226" text-anchor="middle" class="comm-message request-text" :class="{ on: phase >= 1 }">通信</text>
          <text x="620" y="224" text-anchor="middle" class="comm-message call-text" :class="{ on: phase >= 2 }">调用</text>

          <!-- 已有节点 -->
          <g transform="translate(230 245)" class="network-node listener" :class="{ active: activeAt(1) }">
            <circle r="60" class="node-halo" />
            <circle r="46" class="node-ring" />
            <circle r="36" class="node-core" />
            <circle cx="30" cy="-31" r="4" class="running-dot" />
            <text y="5" text-anchor="middle" class="node-name">listener</text>
            <text y="78" text-anchor="middle" class="node-subtitle">routine · 常驻</text>
          </g>

          <g transform="translate(480 230)" class="network-node agent" :class="{ active: activeAt(1, 2) }">
            <circle r="70" class="node-halo" />
            <circle r="55" class="node-ring" />
            <circle r="44" class="node-core agent-core" />
            <circle cx="36" cy="-37" r="4" class="running-dot" />
            <text y="5" text-anchor="middle" class="node-name">agent</text>
            <text y="88" text-anchor="middle" class="node-subtitle">routine · 常驻</text>
          </g>

          <!-- 调用产生的临时节点 -->
          <g transform="translate(744 285)" class="network-node routine" :class="{ active: phase === 2, waiting: phase < 2, ended: phase === 3 }">
            <circle r="56" class="node-halo" />
            <circle r="41" class="node-ring" />
            <circle r="31" class="node-core" />
            <circle cx="27" cy="-28" r="4" class="running-dot" />
            <text y="5" text-anchor="middle" class="node-name">routine</text>
            <text y="72" text-anchor="middle" class="node-subtitle">{{ phase < 2 ? '尚未创建' : phase === 2 ? '临时子节点' : '已结束' }}</text>
          </g>

          <g transform="translate(480 520)" class="comm-summary">
            <path d="M-218 -4H-196" class="summary-line summary-request" />
            <text x="-186" class="request-text">通信：双向虚线</text>
            <path d="M12 -4H34" class="summary-line summary-call" />
            <text x="44" class="call-text">调用：单向实线</text>
          </g>
        </svg>
      </div>

      <div class="comm-caption">
        <span class="comm-phase mono">0{{ phase + 1 }} / 04</span>
        <div><strong>{{ current.title }}</strong><p>{{ current.detail }}</p></div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.communication { margin-top: 28px; }
.comm-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; }
.comm-head h2 { margin: 12px 0 0; font-size: clamp(23px, 2.6vw, 34px); font-weight: 550; letter-spacing: -.02em; }
.comm-demo { margin-top: 22px; overflow: hidden; border: 1px solid var(--line); border-radius: 15px; background: var(--surface); }
.comm-stage { overflow-x: auto; background: color-mix(in oklab, var(--bg-deep) 42%, var(--surface)); }
.comm-stage svg { display: block; width: 100%; min-width: 780px; height: auto; }
.comm-network-title { fill: var(--ink-3); font-family: var(--font-mono); font-size: 11px; letter-spacing: .12em; }
.comm-network-meta { fill: var(--ink-4); font-family: var(--font-mono); font-size: 10px; }
.ambient-network { opacity: .4; }
.ambient-network path { fill: none; stroke: var(--line); stroke-width: 1; }
.ambient-network g > circle:first-child { fill: var(--surface); stroke: var(--line-2); }
.ambient-core { fill: var(--ink-4); }
.comm-link { fill: none; stroke: var(--line-2); stroke-width: 1.4; transition: stroke .35s ease, stroke-width .35s ease, opacity .35s ease; }
.comm-link.input.active, .comm-link.input.done { stroke: var(--ink-3); }
.comm-link.request { stroke-dasharray: 4 7; stroke-linecap: round; }
.comm-link.request.active, .comm-link.request.done { stroke: #A98BFA; }
.comm-link.call { stroke-width: 2; }
.comm-link.call.active, .comm-link.call.done { stroke: var(--accent); }
.comm-link.active { stroke-width: 2.4; }
.comm-link.done { opacity: .46; }
.comm-arrow { fill: var(--ink-4); }
.comm-packet { filter: drop-shadow(0 0 7px currentColor); }
.comm-packet.input { fill: var(--ink-2); color: var(--ink-2); }
.comm-packet.request { fill: #A98BFA; color: #A98BFA; }
.comm-packet.call { fill: var(--accent); color: var(--accent); }
.network-node { transition: opacity .4s ease; }
.node-halo { fill: var(--accent); opacity: 0; transition: opacity .4s ease; }
.node-ring { fill: var(--surface-solid); stroke: var(--line-2); stroke-width: 1.2; transition: stroke .35s ease, fill .35s ease; }
.node-core { fill: color-mix(in oklab, var(--surface-solid) 88%, var(--bg-deep)); stroke: var(--line); }
.agent-core { fill: url(#agent-core); }
.network-node.active .node-halo { opacity: .055; }
.network-node.active .node-ring { stroke: var(--accent); stroke-width: 1.8; }
.running-dot { fill: #72B7A5; }
.node-name { fill: var(--ink); font-family: var(--font-mono); font-size: 13px; }
.node-subtitle { fill: var(--ink-4); font-family: var(--font-mono); font-size: 9px; }
.routine.waiting, .routine.ended { opacity: .32; }
.routine.waiting .node-ring, .routine.ended .node-ring { stroke-dasharray: 4 5; }
.routine.waiting .running-dot, .routine.ended .running-dot { fill: var(--ink-4); }
.user-ring { fill: transparent; stroke: var(--line-2); stroke-width: 1.2; }
.user-core { fill: var(--ink-4); }
.comm-user.active .user-ring { stroke: var(--ink-2); }
.comm-user.active .user-core { fill: var(--ink); }
.request-text { fill: #A98BFA !important; }
.call-text { fill: var(--accent) !important; }
.comm-message { font-family: var(--font-mono); font-size: 10px; opacity: .38; transition: opacity .3s ease; }
.comm-message.on { opacity: 1; }
.comm-summary text { font-size: 11px; }
.summary-line { fill: none; stroke-width: 2; }
.summary-request { stroke: #A98BFA; stroke-dasharray: 3 4; stroke-linecap: round; }
.summary-call { stroke: var(--accent); }
.comm-caption { display: grid; grid-template-columns: 62px 1fr; gap: 18px; padding: 20px 24px 17px; border-top: 1px solid var(--line); }
.comm-phase { padding-top: 3px; color: var(--accent); font-size: 10px; }
.comm-caption strong { color: var(--ink); font-size: 14px; font-weight: 550; }
.comm-caption p { margin: 6px 0 0; color: var(--ink-3); font-size: 12px; line-height: 1.75; }
@media (max-width: 680px) {
  .comm-head { align-items: flex-start; flex-direction: column; }
  .comm-caption { grid-template-columns: 48px 1fr; padding: 17px; gap: 10px; }
}
@media (prefers-reduced-motion: reduce) {
  .comm-packet { display: none; }
  .network-node, .node-halo, .node-ring, .comm-link { transition: none; }
}
</style>
