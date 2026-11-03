<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const scenarios = [
  {
    incoming: 15,
    winner: 'incoming',
    title: 'Routine 2 的 15 高于 Routine 1 的 10',
    detail: 'Routine 1 停止并释放 A、B、C；Routine 2 启动，占用 C、D。'
  },
  {
    incoming: 10,
    winner: 'holder',
    title: 'Routine 2 的 10 没有高于 Routine 1 的 10',
    detail: 'Routine 2 整组启动失败；Routine 1 继续运行，D 仍未被占用。'
  }
] as const

const root = ref<HTMLElement | null>(null)
const round = ref(0)
const phase = ref(0)
const running = ref(false)
const reduced = ref(false)
const scenario = computed(() => scenarios[round.value])
const durations = [1600, 2200, 1800, 3300]
let timer: number | null = null
let observer: IntersectionObserver | null = null

const phaseText = computed(() => {
  if (phase.value === 0) return 'Routine 1 正在运行，同时占用 A、B、C'
  if (phase.value === 1) return 'Routine 2 请求 C、D；请求流到交集模块 C'
  if (phase.value === 2) return 'D 没有冲突；只有 C 上的两个占用者触发 PK'
  return scenario.value.detail
})

const routineOneStatus = computed(() =>
  phase.value === 3 && scenario.value.winner === 'incoming'
    ? '已停止 · 释放 A + B + C'
    : '运行中 · 占用 A + B + C'
)

const routineTwoStatus = computed(() => {
  if (phase.value < 3) return `尝试启动 · 请求 C + D`
  return scenario.value.winner === 'incoming'
    ? '运行中 · 占用 C + D'
    : 'StartError · 整组未启动'
})

function moduleState(name: 'A' | 'B' | 'C' | 'D') {
  if (phase.value < 3) return name === 'D' ? '空闲' : 'Routine 1'
  if (scenario.value.winner === 'holder') return name === 'D' ? '仍未占用' : 'Routine 1'
  if (name === 'A' || name === 'B') return '已释放'
  return 'Routine 2'
}

function stopTimer() {
  if (timer !== null) window.clearTimeout(timer)
  timer = null
}

function schedule() {
  stopTimer()
  if (!running.value || reduced.value) return
  timer = window.setTimeout(() => {
    if (phase.value < 3) {
      phase.value += 1
    } else {
      round.value = (round.value + 1) % scenarios.length
      phase.value = 0
    }
    schedule()
  }, durations[phase.value])
}

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduced.value) {
    phase.value = 3
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    running.value = entry.isIntersecting
    if (running.value) schedule()
    else stopTimer()
  }, { threshold: 0.2 })
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
    class="conflict-pk"
    :class="[`phase-${phase}`, `winner-${scenario.winner}`, { paused: !running && !reduced, reduced }]"
    role="img"
    aria-label="两个 routine 的多模块依赖交集裁决：Routine 1 正在占用 A、B、C，Routine 2 尝试占用 C、D，请求流到共同模块 C 时触发优先级裁决"
  >
    <header class="pk-head">
      <div><span class="mono">SHARED MODULE → CONFLICT</span><h3>依赖在 C 相交，冲突在 C 发生。</h3></div>
      <span class="pk-round mono">0{{ round + 1 }} / 02</span>
    </header>

    <div class="set-stage">
      <svg viewBox="0 0 1000 450" aria-hidden="true">
        <defs>
          <pattern id="stage-grid" width="36" height="36" patternUnits="userSpaceOnUse">
            <path d="M36 0H0V36" fill="none" stroke="currentColor" stroke-width=".7" />
          </pattern>
          <marker id="routine-one-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0L10 5L0 10Z" />
          </marker>
          <marker id="routine-two-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0L10 5L0 10Z" />
          </marker>
          <filter id="soft-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="7" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
        </defs>

        <rect class="stage-grid" width="1000" height="450" fill="url(#stage-grid)" />
        <text class="modules-caption" x="34" y="346">MODULES</text>

        <g class="routine-one-links">
          <path class="dependency-link r1-link" d="M285 108 C260 160 190 196 172 276" marker-end="url(#routine-one-arrow)" />
          <path class="dependency-link r1-link" d="M335 108 C340 160 340 210 340 276" marker-end="url(#routine-one-arrow)" />
          <path class="dependency-link r1-link link-to-c" d="M385 108 C430 165 520 190 558 276" marker-end="url(#routine-one-arrow)" />
          <path class="dependency-flow r1-flow flow-a" d="M285 108 C260 160 190 196 172 276" />
          <path class="dependency-flow r1-flow flow-b" d="M335 108 C340 160 340 210 340 276" />
          <path class="dependency-flow r1-flow flow-c" d="M385 108 C430 165 520 190 558 276" />
        </g>

        <g class="routine-two-links">
          <path class="dependency-link r2-link request-c" d="M675 108 C650 160 600 195 582 276" marker-end="url(#routine-two-arrow)" />
          <path class="dependency-link r2-link request-d" d="M765 108 C790 165 800 215 800 276" marker-end="url(#routine-two-arrow)" />
          <path class="dependency-flow r2-flow flow-c" d="M675 108 C650 160 600 195 582 276" />
          <path class="dependency-flow r2-flow flow-d" d="M765 108 C790 165 800 215 800 276" />
          <circle class="request-packet packet-c" r="5" />
          <circle class="request-packet packet-d" r="5" />
        </g>

        <g class="routine-node routine-one">
          <rect class="running-outline routine-one-outline" x="203" y="21" width="234" height="94" rx="18" />
          <rect x="210" y="28" width="220" height="80" rx="13" />
          <text class="routine-type" x="320" y="56" text-anchor="middle">CURRENTLY RUNNING</text>
          <text class="routine-name" x="320" y="84" text-anchor="middle">Routine 1 · P 10</text>
        </g>
        <g class="routine-node routine-two">
          <rect class="running-outline routine-two-outline" x="623" y="21" width="234" height="94" rx="18" />
          <rect x="630" y="28" width="220" height="80" rx="13" />
          <text class="routine-type" x="740" y="56" text-anchor="middle">INCOMING ROUTINE</text>
          <text class="routine-name" x="740" y="84" text-anchor="middle">Routine 2 · P {{ scenario.incoming }}</text>
        </g>

        <g class="conflict-badge">
          <rect x="511" y="224" width="118" height="30" rx="15" />
          <text x="570" y="244" text-anchor="middle">PK @ 交集 C</text>
        </g>
        <circle class="conflict-ripple ripple-one" cx="570" cy="340" r="61" />
        <circle class="conflict-ripple ripple-two" cx="570" cy="340" r="61" />

        <g class="module-node module-a">
          <circle cx="170" cy="340" r="52" />
          <text class="module-name" x="170" y="348" text-anchor="middle">A</text>
          <text class="module-state" x="170" y="420" text-anchor="middle">{{ moduleState('A') }}</text>
        </g>
        <g class="module-node module-b">
          <circle cx="340" cy="340" r="52" />
          <text class="module-name" x="340" y="348" text-anchor="middle">B</text>
          <text class="module-state" x="340" y="420" text-anchor="middle">{{ moduleState('B') }}</text>
        </g>
        <g class="module-node module-c">
          <circle cx="570" cy="340" r="52" />
          <text class="module-name" x="570" y="348" text-anchor="middle">C</text>
          <text class="module-state" x="570" y="420" text-anchor="middle">{{ moduleState('C') }}</text>
        </g>
        <g class="module-node module-d">
          <circle cx="800" cy="340" r="52" />
          <text class="module-name" x="800" y="348" text-anchor="middle">D</text>
          <text class="module-state" x="800" y="420" text-anchor="middle">{{ moduleState('D') }}</text>
        </g>
      </svg>
    </div>

    <div class="routine-status">
      <div class="status-one"><i></i><span>Routine 1</span><strong>{{ routineOneStatus }}</strong></div>
      <div class="status-two"><i></i><span>Routine 2</span><strong>{{ routineTwoStatus }}</strong></div>
    </div>

    <div class="decision-rail" :class="{ active: phase >= 2 }">
      <div class="holder-equation"><span>依赖交集</span><strong>C</strong><i>·</i><span>D 无冲突</span></div>
      <div class="compare-equation">
        <span>Routine 2</span><b>{{ scenario.incoming }}</b>
        <em>{{ scenario.incoming > 10 ? '>' : '=' }}</em>
        <b>10</b><span>Routine 1</span>
      </div>
      <div class="decision-outcome">
        <span>{{ phase < 3 ? '等待裁决' : scenario.winner === 'incoming' ? '整组启动' : '整组拒绝' }}</span>
        <strong>{{ phase < 3 ? '···' : scenario.winner === 'incoming' ? 'START C + D' : 'START ERROR' }}</strong>
      </div>
    </div>

    <footer class="pk-result" :class="{ decided: phase === 3 }">
      <span class="result-mark">{{ phase === 3 ? (scenario.winner === 'incoming' ? '✓' : '×') : '∩' }}</span>
      <div><strong>{{ phase === 3 ? scenario.title : phaseText }}</strong><p>{{ phase === 3 ? phaseText : 'A、B、D 不参与 PK；但 Routine 2 的启动结果仍然整组生效。' }}</p></div>
      <div class="phase-dots" aria-hidden="true"><i v-for="i in 4" :key="i" :class="{ on: phase >= i - 1 }"></i></div>
    </footer>
  </div>
</template>

<style scoped>
.conflict-pk { --routine-one: #62bfa4; --routine-two: #a98bfa; overflow: hidden; margin-bottom: 18px; border: 1px solid color-mix(in oklab, var(--accent) 28%, var(--line)); border-radius: var(--r-lg); background: radial-gradient(ellipse at 56% 60%, color-mix(in oklab, var(--accent) 7%, transparent), transparent 42%), var(--surface); }
.pk-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 23px 26px 18px; border-bottom: 1px solid var(--line); }
.pk-head > div > span { color: var(--accent); font-size: 9px; letter-spacing: .1em; }
.pk-head h3 { margin-top: 9px; color: var(--ink); font-size: 20px; font-weight: 520; }
.pk-round { flex-shrink: 0; padding-top: 3px; color: var(--ink-4); font-size: 9px; letter-spacing: .08em; }
.set-stage { padding: 12px 24px 0; }
.set-stage svg { display: block; width: 100%; height: auto; overflow: visible; }
.stage-grid { color: var(--line); opacity: .3; }
.modules-caption { fill: var(--ink-4); font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em; }
.routine-node, .module-node, .routine-two-links, .conflict-badge { transform-box: fill-box; transform-origin: center; }
.routine-node { transition: opacity .55s var(--ease), transform .7s var(--ease), filter .5s var(--ease); }
.routine-node rect { fill: var(--surface-solid); stroke-width: 1.6; transition: fill .5s var(--ease), stroke .5s var(--ease), opacity .45s var(--ease); }
.routine-node .running-outline { fill: none; stroke-width: 2.2; stroke-linecap: round; stroke-dasharray: 18 14; animation: outline-run 1.7s linear infinite; }
.routine-one-outline { stroke: var(--routine-one); filter: drop-shadow(0 0 5px color-mix(in oklab, var(--routine-one) 55%, transparent)); }
.routine-two-outline { stroke: var(--routine-two); filter: drop-shadow(0 0 5px color-mix(in oklab, var(--routine-two) 55%, transparent)); opacity: 0; animation: none; }
.routine-one rect { fill: color-mix(in oklab, var(--routine-one) 8%, var(--surface-solid)); stroke: var(--routine-one); }
.routine-two rect { fill: color-mix(in oklab, var(--routine-two) 8%, var(--surface-solid)); stroke: var(--routine-two); }
.routine-type { font-family: var(--font-mono); font-size: 9px; letter-spacing: .08em; }
.routine-one .routine-type { fill: var(--routine-one); }
.routine-two .routine-type { fill: var(--routine-two); }
.routine-name { fill: var(--ink); font-family: var(--font-sans); font-size: 17px; }
.routine-two { opacity: 0; transform: translateY(-12px); }
.phase-1 .routine-two, .phase-2 .routine-two, .phase-3 .routine-two { opacity: 1; transform: translateY(0); }
.dependency-link { fill: none; stroke-width: 2; }
.dependency-flow { fill: none; stroke-width: 3; stroke-linecap: round; stroke-dasharray: 2 16; animation: dependency-flow 1.15s linear infinite; pointer-events: none; }
.r1-flow { stroke: #b0edda; opacity: .9; }
.r2-flow { stroke: #d2c3ff; opacity: 0; transition: opacity .45s .75s var(--ease); }
.flow-b { animation-duration: 1.4s; }
.flow-c { animation-duration: .95s; }
.phase-1 .r2-flow, .phase-2 .r2-flow, .phase-3.winner-incoming .r2-flow { opacity: .9; }
.r1-link { stroke: var(--routine-one); }
#routine-one-arrow path { fill: var(--routine-one); }
#routine-two-arrow path { fill: var(--routine-two); }
.routine-one-links { transition: opacity .6s var(--ease); }
.routine-two-links { opacity: 0; transition: opacity .35s var(--ease); }
.r2-link { stroke: var(--routine-two); stroke-dasharray: 260; stroke-dashoffset: 260; transition: stroke-dashoffset 1.05s var(--ease), stroke .4s var(--ease); }
.phase-1 .routine-two-links, .phase-2 .routine-two-links, .phase-3 .routine-two-links { opacity: 1; }
.phase-1 .r2-link, .phase-2 .r2-link, .phase-3 .r2-link { stroke-dashoffset: 0; }
.request-packet { fill: var(--routine-two); filter: url(#soft-glow); opacity: 0; }
.packet-c { offset-path: path('M675 108 C650 160 600 195 582 276'); }
.packet-d { offset-path: path('M765 108 C790 165 800 215 800 276'); }
.phase-1 .packet-c { animation: request-c 1.45s .18s var(--ease) infinite; }
.phase-1 .packet-d { animation: request-d 1.45s .18s var(--ease) infinite; }
.module-node { transition: opacity .5s var(--ease); }
.module-node > circle { fill: color-mix(in oklab, var(--routine-one) 10%, var(--surface-solid)); stroke: var(--routine-one); stroke-width: 1.5; transition: fill .55s var(--ease), stroke .55s var(--ease), stroke-width .4s var(--ease); }
.module-d > circle { fill: var(--surface-solid); stroke: var(--line-2); stroke-dasharray: 5 5; }
.module-name { fill: var(--ink); font-family: var(--font-mono); font-size: 24px; }
.module-state { fill: var(--ink-4); font-family: var(--font-sans); font-size: 11px; }
.conflict-badge { opacity: 0; transform: translateY(6px); transition: opacity .4s var(--ease), transform .5s var(--ease); }
.conflict-badge rect { fill: color-mix(in oklab, var(--accent) 13%, var(--surface-solid)); stroke: var(--accent); }
.conflict-badge text { fill: var(--accent); font-family: var(--font-sans); font-size: 11px; }
.phase-1 .conflict-badge { opacity: 1; transform: translateY(0); transition-delay: .8s; }
.phase-2 .conflict-badge { opacity: 1; transform: translateY(0); }
.conflict-ripple { fill: none; stroke: var(--accent); stroke-width: 2; opacity: 0; transform-box: fill-box; transform-origin: center; }
.phase-1 .conflict-ripple, .phase-2 .conflict-ripple { animation: conflict-pulse 1.6s .8s ease-out infinite; }
.phase-2 .ripple-two { animation-delay: -.8s; }
.phase-1 .module-c > circle, .phase-2 .module-c > circle { stroke: var(--accent); stroke-width: 2.5; }
.phase-3.winner-incoming .routine-one { opacity: .25; filter: saturate(.2); }
.phase-3.winner-incoming .routine-one-outline { opacity: 0; animation: none; }
.phase-3.winner-incoming .routine-two-outline { opacity: 1; animation: outline-run 1.7s linear infinite; }
.phase-3.winner-incoming .routine-one-links { opacity: .14; }
.phase-3.winner-incoming .r1-flow { opacity: 0; animation: none; }
.phase-3.winner-incoming .module-a > circle, .phase-3.winner-incoming .module-b > circle { fill: var(--surface-solid); stroke: var(--line-2); stroke-dasharray: 4 5; }
.phase-3.winner-incoming .module-c > circle, .phase-3.winner-incoming .module-d > circle { fill: color-mix(in oklab, var(--routine-two) 12%, var(--surface-solid)); stroke: var(--routine-two); stroke-dasharray: none; }
.phase-3.winner-incoming .routine-two rect { filter: drop-shadow(0 0 12px color-mix(in oklab, var(--routine-two) 24%, transparent)); }
.phase-3.winner-holder .routine-two { opacity: .25; transform: translateY(-8px); filter: saturate(.2); }
.phase-3.winner-holder .routine-two-links { opacity: .15; }
.phase-3.winner-holder .r2-link { stroke: #c48686; }
.phase-3.winner-holder .r2-flow { opacity: 0; animation: none; }
.routine-status { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 0 26px 12px; }
.routine-status > div { display: flex; align-items: center; gap: 9px; min-width: 0; padding: 11px 14px; border: 1px solid var(--line); border-radius: 8px; background: color-mix(in oklab, var(--bg-deep) 32%, transparent); }
.routine-status i { flex: 0 0 7px; width: 7px; height: 7px; border-radius: 50%; animation: status-breathe 1.8s ease-in-out infinite; }
.status-one i { background: var(--routine-one); box-shadow: 0 0 10px var(--routine-one); }
.status-two i { background: var(--routine-two); box-shadow: 0 0 10px var(--routine-two); }
.routine-status span { flex-shrink: 0; color: var(--ink-3); font-family: var(--font-mono); font-size: 10px; }
.routine-status strong { overflow: hidden; color: var(--ink-2); font-size: 11px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.phase-3.winner-incoming .status-one, .phase-3.winner-holder .status-two { opacity: .45; }
.decision-rail { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 22px; margin: 0 26px 22px; padding: 16px 20px; border: 1px solid var(--line); border-radius: 12px; background: color-mix(in oklab, var(--bg-deep) 45%, transparent); opacity: .48; transition: opacity .5s var(--ease), border-color .5s var(--ease); }
.decision-rail.active { opacity: 1; border-color: color-mix(in oklab, var(--accent) 35%, var(--line)); }
.holder-equation, .compare-equation, .decision-outcome { display: flex; align-items: center; gap: 9px; }
.holder-equation > span, .compare-equation > span { color: var(--ink-4); font-size: 10px; }
.holder-equation strong { display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid var(--accent); border-radius: 50%; color: var(--accent); font-family: var(--font-mono); font-size: 13px; font-weight: 500; }
.holder-equation i { color: var(--ink-4); font-style: normal; }
.compare-equation { justify-content: center; }
.compare-equation b { color: var(--ink); font-family: var(--font-mono); font-size: 22px; font-weight: 500; }
.compare-equation em { padding: 0 5px; color: var(--accent); font-family: var(--font-mono); font-size: 25px; font-style: normal; }
.decision-outcome { justify-content: flex-end; }
.decision-outcome span { color: var(--ink-4); font-size: 10px; }
.decision-outcome strong { color: var(--ink-3); font-family: var(--font-mono); font-size: 12px; font-weight: 500; }
.phase-3.winner-incoming .decision-outcome strong { color: var(--accent); }
.phase-3.winner-holder .decision-outcome strong { color: #c48686; }
.pk-result { display: grid; grid-template-columns: 36px 1fr auto; align-items: center; gap: 15px; min-height: 82px; padding: 17px 26px; border-top: 1px solid var(--line); background: color-mix(in oklab, var(--bg-deep) 38%, transparent); }
.result-mark { display: grid; place-items: center; width: 32px; height: 32px; border: 1px solid var(--line-2); border-radius: 50%; color: var(--accent); font-family: var(--font-mono); }
.pk-result strong { color: var(--ink-2); font-size: 13px; font-weight: 520; }
.pk-result p { margin-top: 5px; color: var(--ink-4); font-size: 11px; line-height: 1.6; }
.pk-result.decided strong { color: var(--accent); }
.phase-dots { display: flex; gap: 5px; }
.phase-dots i { width: 16px; height: 2px; border-radius: 99px; background: var(--line-2); transition: background .3s var(--ease); }
.phase-dots i.on { background: var(--accent); }
@keyframes outline-run { to { stroke-dashoffset: -64; } }
@keyframes dependency-flow { to { stroke-dashoffset: -36; } }
@keyframes status-breathe { 0%, 100% { opacity: .5; transform: scale(.82); } 50% { opacity: 1; transform: scale(1.2); } }
@keyframes request-c { 0% { offset-distance: 0%; opacity: 0; } 15% { opacity: 1; } 82% { opacity: 1; } 100% { offset-distance: 100%; opacity: 0; } }
@keyframes request-d { 0% { offset-distance: 0%; opacity: 0; } 15% { opacity: .8; } 82% { opacity: .8; } 100% { offset-distance: 100%; opacity: 0; } }
@keyframes conflict-pulse { 0% { opacity: .75; transform: scale(.82); } 100% { opacity: 0; transform: scale(1.5); } }
.paused *, .reduced * { animation-play-state: paused !important; }
.reduced * { animation: none !important; transition: none !important; }
.reduced .request-packet, .reduced .conflict-ripple { display: none; }
@media (max-width: 800px) {
  .set-stage { padding: 8px 8px 0; }
  .decision-rail { grid-template-columns: 1fr 1fr; gap: 13px; margin: 0 18px 18px; }
  .compare-equation { justify-content: flex-end; }
  .decision-outcome { grid-column: 1 / -1; justify-content: center; padding-top: 11px; border-top: 1px solid var(--line); }
  .routine-status { margin: 0 18px 12px; }
}
@media (max-width: 600px) {
  .pk-head { padding: 20px; }
  .pk-head h3 { font-size: 17px; line-height: 1.5; }
  .pk-round { display: none; }
  .set-stage { overflow-x: auto; padding: 4px 0 0; }
  .set-stage svg { width: 720px; max-width: none; }
  .routine-status { grid-template-columns: 1fr; margin: 8px 16px 12px; }
  .decision-rail { grid-template-columns: 1fr; margin: 0 16px 16px; padding: 15px; }
  .holder-equation, .compare-equation, .decision-outcome { justify-content: center; }
  .decision-outcome { grid-column: auto; }
  .pk-result { grid-template-columns: 30px 1fr; padding: 16px 18px; }
  .phase-dots { display: none; }
  .result-mark { width: 28px; height: 28px; }
}
</style>
