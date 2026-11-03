<script setup lang="ts">
/* 装配页先证明 agent 与 tool 的平等性：五个同形、同尺度单元从平级位置移动，
   再建立调用关系。agent 不扩大为容器；注册态与实例生命周期不在这页展开。 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Tool = { name: string; role: string; flatX: number; x: number; y: number; born: number }

const TOOLS: Tool[] = [
  { name: 'Read', role: '读取文件', flatX: 314, x: 760, y: 120, born: 2 },
  { name: 'Grep', role: '检索内容', flatX: 500, x: 760, y: 220, born: 2 },
  { name: 'Write', role: '写入文件', flatX: 686, x: 760, y: 320, born: 3 },
  { name: 'Bash', role: '执行命令', flatX: 872, x: 760, y: 420, born: 3 }
]

const STEPS = [
  {
    t: '平级单元',
    d: 'agent 与 tool 具有相同的单元形式。这里没有内外、包含或类型层级，也尚未建立调用关系。'
  },
  {
    t: '一个执行循环',
    d: 'agent 组织上下文、请求模型、调用模型指定的单元，并将结果写回后续上下文。'
  },
  {
    t: '接入读取与检索',
    d: 'Read 提供文件读取，Grep 提供内容检索。两个单元通过调用关系接入执行循环。'
  },
  {
    t: '接入写入与执行',
    d: 'Write 提供文件写入，Bash 提供命令执行。四种外部操作构成这里的最小工具集。'
  },
  {
    t: '形成最小 agent',
    d: '模型选择要调用的单元，单元结果返回 agent，再进入下一轮上下文，直至不再产生调用。'
  },
  {
    t: '装配产物仍可被调用',
    d: '这个组合对外仍使用 agent 这一注册名。上层调用它，与它调用工具单元，采用相同的调用形式。'
  }
]

const DWELL = [4200, 3600, 3600, 3600, 5200, 4400]
const phase = ref(0)
const playing = ref(true)
const runId = ref(0)
const stageRef = ref<HTMLDivElement>()
const rootRef = ref<HTMLDivElement>()

function edge(t: Tool) {
  const x1 = 480
  const y1 = 270
  const x2 = t.x - 86
  const bend = Math.max(40, (x2 - x1) * .45)
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${t.y}, ${x2} ${t.y}`
}

let timer: number | undefined
function schedule() {
  window.clearTimeout(timer)
  if (!playing.value) return
  timer = window.setTimeout(() => {
    if (phase.value === STEPS.length - 1) {
      runId.value += 1
      phase.value = 0
    } else phase.value += 1
  }, DWELL[phase.value])
}
watch([phase, playing, runId], schedule, { immediate: true })
function syncViewport() {
  if (!stageRef.value || window.innerWidth > 600) return
  const max = stageRef.value.scrollWidth - stageRef.value.clientWidth
  stageRef.value.scrollLeft = phase.value === 0 ? 0 : max * .7
}
watch([phase, runId], syncViewport, { flush: 'post' })
onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    phase.value = 4
    playing.value = false
  }
  window.addEventListener('resize', syncViewport)
  syncViewport()
})
onBeforeUnmount(() => {
  window.clearTimeout(timer)
  window.removeEventListener('resize', syncViewport)
})
</script>

<template>
  <div ref="rootRef" class="sg" :class="{ paused: !playing }">
    <div ref="stageRef" class="sg-stage" tabindex="0" aria-label="最小 agent 装配图，窄屏可横向滚动">
      <svg
        :key="runId" viewBox="0 0 1000 540" class="sg-s" role="img"
        aria-label="agent 与 Read、Grep、Write、Bash 先以同形同尺度的平级单元排列，随后移动到装配位置并建立调用关系；装配产物仍可被上层调用"
      >
        <defs>
          <radialGradient id="sg-field" cx="55%" cy="45%" r="68%">
            <stop offset="0%" stop-color="#5FCEDD" stop-opacity=".065" />
            <stop offset="100%" stop-color="#5FCEDD" stop-opacity="0" />
          </radialGradient>
          <linearGradient id="sg-agent" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stop-color="#1B3038" />
            <stop offset="100%" stop-color="#111A22" />
          </linearGradient>
          <marker id="sg-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 Z" fill="rgba(127,227,239,.65)" />
          </marker>
        </defs>

        <rect width="1000" height="540" fill="#0b0d12" />
        <rect width="1000" height="540" fill="url(#sg-field)" />
        <text class="sg-kicker" x="60" y="48">{{ phase === 0 ? 'EQUAL UNITS · 平级单元' : 'MINIMAL AGENT · 单元组合' }}</text>
        <text class="sg-schema" x="60" y="70">{{ phase === 0 ? 'agent 与 tool 具有相同的单元形式' : '1 个执行循环 + 4 个工具单元' }}</text>

        <!-- 外部调用只指向 agent，不把工具画成 agent 的内部零件。 -->
        <g v-if="phase >= 5" class="sg-external">
          <text x="62" y="225" class="sg-call-label">上层调用</text>
          <text x="62" y="247" class="sg-call-code">call("agent")</text>
          <path d="M 62 270 H 268" pathLength="1" class="sg-external-line" marker-end="url(#sg-arrow)" />
        </g>
        <g v-else class="sg-empty-note">
          <text x="62" y="255">{{ phase === 0 ? '没有内外' : '位置已经改变' }}</text>
          <text x="62" y="278">{{ phase === 0 ? '没有类型层级' : '关系仍未建立' }}</text>
        </g>

        <!-- 五个单元始终使用相同外框。phase 0 平级排列，随后同一批单元移动。 -->
        <g v-for="t in TOOLS" :key="t.name" class="sg-relation" :class="{ born: phase >= t.born }">
          <path :d="edge(t)" class="sg-edge-base" />
          <path
            v-if="phase >= t.born" :d="edge(t)" pathLength="1"
            :class="phase >= 4 ? 'sg-edge-flow' : 'sg-edge-enter'"
          />
        </g>

        <g
          v-for="t in TOOLS" :key="t.name" class="sg-tool"
          :style="{ transform: `translate(${phase === 0 ? t.flatX : t.x}px, ${phase === 0 ? 270 : t.y}px)` }"
        >
          <rect x="-86" y="-31" width="172" height="62" rx="11" class="sg-tool-body" />
          <rect x="-85.5" y="-30.5" width="171" height="61" rx="10.5" class="sg-tool-in" />
          <text x="-64" y="-3" class="sg-tool-name">{{ t.name }}</text>
          <text x="-64" y="17" class="sg-tool-role">{{ t.role }}</text>
          <circle cx="64" cy="-10" r="2.5" class="sg-tool-dot" />
        </g>

        <g class="sg-agent" :style="{ transform: `translate(${phase === 0 ? 128 : 375}px, 270px)` }">
          <rect x="-86" y="-31" width="172" height="62" rx="11" class="sg-tool-body" />
          <rect x="-85.5" y="-30.5" width="171" height="61" rx="10.5" class="sg-tool-in" />
          <text x="-64" y="-3" class="sg-tool-name">agent</text>
          <text x="-64" y="17" class="sg-tool-role">执行循环</text>
          <circle cx="64" cy="-10" r="2.5" class="sg-tool-dot" />
          <path
            v-if="phase >= 1"
            d="M -66 -48 H 48 C 82 -48 82 48 48 48 H -48 C -82 48 -82 -48 -66 -48"
            pathLength="1" class="sg-loop-base"
          />
          <path
            v-if="phase >= 4"
            d="M -66 -48 H 48 C 82 -48 82 48 48 48 H -48 C -82 48 -82 -48 -66 -48"
            pathLength="1" class="sg-loop-flow"
          />
          <text v-if="phase >= 1" x="0" y="72" class="sg-agent-foot">调用 → 返回 → 下一轮</text>
        </g>

        <g class="sg-caption">
          <text x="375" y="420" text-anchor="middle" class="sg-caption-main">{{ phase === 0 ? '位置不构成层级' : 'agent 不实现这些外部操作' }}</text>
          <text x="375" y="441" text-anchor="middle" class="sg-caption-sub">{{ phase === 0 ? '五个单元具有相同的声明与调用形式' : '它通过调用组织这些单元' }}</text>
        </g>

        <g class="sg-legend">
          <line x1="60" y1="505" x2="95" y2="505" class="sg-edge-base" />
          <text x="105" y="509">可调用的关系</text>
          <line x1="230" y1="505" x2="265" y2="505" pathLength="1" class="sg-legend-flow" />
          <text x="275" y="509">调用与结果返回</text>
          <rect x="435" y="499" width="12" height="12" rx="3" class="sg-tool-body" />
          <text x="457" y="509">同一种单元形式</text>
        </g>
      </svg>
    </div>
    <div class="sg-scroll-hint">图示可横向滚动</div>

    <div class="sg-foot" aria-live="off">
      <span class="sg-prog" aria-hidden="true">
        <i v-if="playing" :key="`${runId}-${phase}`" :style="{ animationDuration: DWELL[phase] + 'ms' }" />
      </span>
      <span class="sg-no mono">{{ String(phase + 1).padStart(2, '0') }}<i>/06</i></span>
      <b class="sg-t">{{ STEPS[phase].t }}</b>
      <span class="sg-d">{{ STEPS[phase].d }}</span>
    </div>

    <div class="sg-steps" aria-label="动画阶段">
      <span
        v-for="(s, i) in STEPS" :key="s.t" class="sg-step"
        :class="{ active: phase === i }" :aria-current="phase === i ? 'step' : undefined"
      >
        <span class="mono">{{ String(i + 1).padStart(2, '0') }}</span>{{ s.t }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.sg { border: 1px solid var(--line); border-radius: var(--r-lg); background: #0b0d12; overflow: hidden; color: #dce8ee; }
.sg-stage { overflow: hidden; }
.sg-stage:focus-visible { outline: 2px solid #7FE3EF; outline-offset: -2px; }
.sg-s { display: block; width: 100%; height: auto; }
.sg-scroll-hint { display: none; }
.paused * { animation-play-state: paused !important; }
.sg-kicker { font-family: var(--font-mono); font-size: 10px; letter-spacing: .12em; fill: rgba(127,227,239,.66); }
.sg-schema { font-family: var(--font-mono); font-size: 10px; fill: rgba(190,211,222,.38); }

.sg-agent, .sg-tool { transition: transform 1.15s var(--ease); filter: drop-shadow(0 6px 12px rgba(0,0,0,.3)); }
.sg-agent-foot { font-family: var(--font-mono); font-size: 9px; fill: rgba(158,204,213,.48); text-anchor: middle; }
.sg-loop-base { fill: none; stroke: rgba(127,227,239,.24); stroke-width: 1; }
.sg-loop-flow {
  fill: none; stroke: rgba(183,246,252,.92); stroke-width: 2; stroke-linecap: round;
  stroke-dasharray: .08 .92; animation: sg-loop 3s linear infinite;
  filter: drop-shadow(0 0 3px rgba(127,227,239,.4));
}
@keyframes sg-loop { to { stroke-dashoffset: -1; } }
.sg-tool-body { fill: #121A22; stroke: rgba(104,194,208,.38); stroke-width: 1; }
.sg-tool-in { fill: none; stroke: rgba(199,236,243,.055); }
.sg-tool-name { font-family: var(--font-mono); font-size: 14px; fill: #D1E9EE; }
.sg-tool-role { font-size: 10px; fill: rgba(169,193,203,.58); }
.sg-tool-dot { fill: rgba(127,227,239,.68); }

.sg-relation { opacity: 0; transition: opacity .55s; }
.sg-relation.born { opacity: 1; }
.sg-edge-base { fill: none; stroke: rgba(135,164,178,.25); stroke-width: 1; }
.sg-edge-enter {
  fill: none; stroke: rgba(127,227,239,.55); stroke-width: 1.2;
  stroke-dasharray: 1; animation: sg-enter .8s var(--ease) both;
}
@keyframes sg-enter { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }
.sg-edge-flow, .sg-legend-flow {
  fill: none; stroke: rgba(158,238,247,.9); stroke-width: 1.8; stroke-linecap: round;
  stroke-dasharray: .07 .93; animation: sg-flow 2.8s linear infinite;
  filter: drop-shadow(0 0 3px rgba(127,227,239,.35));
}
@keyframes sg-flow {
  0% { stroke-dashoffset: 1; opacity: 0; }
  6% { opacity: 1; }
  38% { stroke-dashoffset: 0; opacity: 1; }
  47% { opacity: 0; }
  55% { stroke-dashoffset: 0; opacity: 0; }
  62% { opacity: .75; }
  91% { stroke-dashoffset: 1; opacity: .75; }
  100% { stroke-dashoffset: 1; opacity: 0; }
}

.sg-empty-note text { font-size: 11px; fill: rgba(164,185,195,.34); }
.sg-external { opacity: 1; }
.sg-call-label { font-size: 11px; fill: rgba(181,205,214,.56); }
.sg-call-code { font-family: var(--font-mono); font-size: 10px; fill: rgba(127,227,239,.72); }
.sg-external-line { fill: none; stroke: rgba(127,227,239,.65); stroke-width: 1.3; }
.sg-caption-main { font-size: 12px; fill: rgba(205,225,231,.62); }
.sg-caption-sub { font-size: 10px; fill: rgba(166,190,200,.38); }
.sg-legend text { font-size: 10px; fill: rgba(178,198,208,.42); }
.sg-legend .sg-legend-flow { animation-duration: 3s; }

.sg-foot {
  position: relative; min-height: 78px; display: flex; align-items: center; gap: 13px; flex-wrap: wrap;
  padding: 16px 22px; border-top: 1px solid rgba(255,255,255,.08);
}
.sg-prog { position: absolute; top: -1px; left: 0; right: 0; height: 2px; background: rgba(255,255,255,.05); }
.sg-prog i { display: block; height: 100%; transform-origin: 0 50%; background: linear-gradient(90deg,rgba(127,227,239,.15),rgba(127,227,239,.95)); animation: sg-prog linear both; }
@keyframes sg-prog { from { transform: scaleX(0); } to { transform: scaleX(1); } }
.sg-no { font-size: 11px; color: #7FE3EF; letter-spacing: .06em; font-variant-numeric: tabular-nums; }
.sg-no i { color: #596572; font-style: normal; }
.sg-t { font-size: 14px; font-weight: 500; color: #E5F0F4; }
.sg-d { flex: 1 1 430px; font-size: 12.5px; line-height: 1.75; color: #8B98A6; }
.sg-steps { display: grid; grid-template-columns: repeat(6,1fr); border-top: 1px solid rgba(255,255,255,.08); }
.sg-step { min-height: 52px; padding: 10px 12px; border-right: 1px solid rgba(255,255,255,.07); color: rgba(178,195,205,.68); font-size: 10.5px; line-height: 1.45; }
.sg-step:last-child { border-right: 0; }
.sg-step span { display: block; margin-bottom: 3px; font-size: 9px; color: rgba(127,227,239,.58); }
.sg-step.active { color: #E1F3F6; background: rgba(127,227,239,.045); }
.sg-step.active span { color: #7FE3EF; }

@media (max-width: 760px) {
  .sg-steps { grid-template-columns: repeat(3,1fr); }
  .sg-step:nth-child(3) { border-right: 0; }
  .sg-step:nth-child(-n+3) { border-bottom: 1px solid rgba(255,255,255,.07); }
  .sg-foot { align-items: flex-start; }
  .sg-d { flex-basis: 100%; order: 4; }
}
@media (max-width: 600px) {
  .sg-stage { overflow-x: auto; scrollbar-width: thin; scrollbar-color: rgba(127,227,239,.25) transparent; }
  .sg-s { width: 760px; max-width: none; }
  .sg-scroll-hint { display: block; padding: 5px 12px; font-size: 9px; text-align: right; color: rgba(178,198,208,.42); }
}
@media (max-width: 480px) {
  .sg-step { padding: 9px 8px; font-size: 10px; }
  .sg-foot { padding: 14px; gap: 10px; }
  .sg-t { font-size: 13px; }
}
@media (prefers-reduced-motion: reduce) {
  .sg *, .sg *::before, .sg *::after { animation: none !important; transition: none !important; }
}
</style>
