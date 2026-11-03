<script setup lang="ts">
/* 同一组外围单元、同一个中央位置、同一种调用语义。
   前三态分别观察 person（经验）、sop（文本）与 agent（模型）承担编排。
   三者并不相互淘汰；第四态让不同实现进入同一棵父子实例树。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const CH = [
  { no: '01', ax: '经验编排', ti: '人依据判断组织调用', d: '人也是一个单元。调用哪些单元、按什么次序调用，由人的经验与当下判断决定；编排尚未获得独立的文本形式。' },
  { no: '02', ax: '规程编排', ti: '文本固定调用规则', d: '顺序、分支与条件被写成规程。文本规程同样可以作为单元存在，执行者依照明确规则调用其他单元。' },
  { no: '03', ax: '模型编排', ti: '模型依据目标生成调用', d: 'agent 以模型为内核，依据目标与上下文决定后续调用。它与其他单元使用相同的调用语义，不享有抽象特权。' },
  { no: '04', ax: '混合编排', ti: '不同类型在同一调用树中嵌套', d: '人、规程与 agent 都可以成为其中一个环节。手工确认、模型判断与自动执行按照调用关系组合，形成父子实例树；父停子停。' }
]
const UNITS = [
  { n: '单元 A', x: 225, y: 170 },
  { n: '单元 B', x: 500, y: 410 },
  { n: '单元 C', x: 775, y: 170 },
  { n: '单元 D', x: 775, y: 335 },
  { n: '单元 E', x: 225, y: 335 }
]
const SLOT = { x: 500, y: 260, w: 128, h: 66 }
function cut(tx: number, ty: number) {
  const dx = tx - SLOT.x
  const dy = ty - SLOT.y
  const k = Math.min(SLOT.w / 2 / Math.abs(dx || .0001), SLOT.h / 2 / Math.abs(dy || .0001))
  return { x: SLOT.x + dx * k, y: SLOT.y + dy * k }
}
const CALLS = UNITS.map((unit, i) => {
  const start = cut(unit.x, unit.y)
  const dx = unit.x - SLOT.x
  const dy = unit.y - SLOT.y
  const k = Math.min(46 / Math.abs(dx || .0001), 20 / Math.abs(dy || .0001))
  const end = { x: unit.x - dx * k, y: unit.y - dy * k }
  return { ...unit, d: `M ${start.x} ${start.y} L ${end.x} ${end.y}`, delay: .7 + i * .72 }
})
const TREE_LINKS = [
  'M 500 113 V 143',
  'M 470 205 L 345 273',
  'M 530 205 L 655 273',
  'M 290 337 L 245 389',
  'M 350 337 L 395 389',
  'M 680 337 V 389'
]
const phase = ref(0)
const settled = ref(false)
const runId = ref(0)
const rootRef = ref<HTMLDivElement>()
const stageRef = ref<HTMLDivElement>()
const DWELL = [5700, 5400, 6200, 7600]
let timer: number | undefined
const fill = computed(() => phase.value / 3)

function schedule() {
  window.clearTimeout(timer)
  if (settled.value) return
  timer = window.setTimeout(() => {
    if (phase.value < 3) phase.value += 1
    else { runId.value += 1; phase.value = 0 }
  }, DWELL[phase.value])
}
function syncViewport() {
  if (!stageRef.value || window.innerWidth > 600) return
  stageRef.value.scrollLeft = (stageRef.value.scrollWidth - stageRef.value.clientWidth) / 2
}
watch([phase, runId], schedule, { immediate: true })
watch(phase, syncViewport, { flush: 'post' })
onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    phase.value = 3; settled.value = true
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
  <div ref="rootRef" class="ev" :class="{ settled }">
    <div ref="stageRef" class="ev-stage" tabindex="0" aria-label="编排演进图示，窄屏可横向滚动">
      <svg :key="runId" viewBox="0 0 1000 530" class="ev-s" role="img"
        aria-label="人、文本规程与模型分别承担编排，随后作为不同实现类型进入同一棵混合编排的父子命令实例树">
        <defs>
          <radialGradient id="ev-glow" cx="50%" cy="45%" r="58%">
            <stop offset="0%" stop-color="#5FCEDD" stop-opacity=".075" />
            <stop offset="100%" stop-color="#5FCEDD" stop-opacity="0" />
          </radialGradient>
          <marker id="ev-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#7FDCE8" />
          </marker>
        </defs>
        <rect width="1000" height="530" fill="#0b0d12" />
        <rect width="1000" height="530" fill="url(#ev-glow)" />
        <text class="ch-no" x="92" y="62">{{ CH[phase].no }} / 04</text>
        <text class="ch-ti" x="92" y="98">{{ CH[phase].ti }}</text>
        <text class="scene-note" x="908" y="62">单元调用单元</text>

        <!-- 前三态：外围单元不变，调用路径只随编排方式改变。 -->
        <g v-if="phase < 3" class="orchestration">
          <g v-for="(call, i) in CALLS" :key="call.n" class="call" :class="`p${phase}`" :style="{ '--i': i, '--delay': `${call.delay}s` }">
            <path :d="call.d" class="call-base" />
            <path :d="call.d" class="call-active" pathLength="1" marker-end="url(#ev-arrow)" />
            <text v-if="phase === 1" :x="(500 + call.x) / 2" :y="(260 + call.y) / 2 - 8" class="call-order">0{{ i + 1 }}</text>
          </g>
          <g v-for="unit in UNITS" :key="unit.n" :transform="`translate(${unit.x} ${unit.y})`" class="unit">
            <rect x="-46" y="-20" width="92" height="40" rx="9" class="unit-body" />
            <text text-anchor="middle" dominant-baseline="central" class="unit-name">{{ unit.n }}</text>
          </g>

          <g v-if="phase === 0" class="slot person">
            <text x="500" y="172" text-anchor="middle" class="slot-input">经验 · 当下判断</text>
            <rect :x="SLOT.x - 64" :y="SLOT.y - 33" width="128" height="66" rx="11" class="slot-body" />
            <text x="500" y="255" text-anchor="middle" class="slot-name">person</text>
            <text x="500" y="276" text-anchor="middle" class="slot-sub">人脑</text>
          </g>
          <g v-else-if="phase === 1" class="slot sop">
            <text x="500" y="172" text-anchor="middle" class="slot-input">明确规则</text>
            <rect :x="SLOT.x - 64" :y="SLOT.y - 33" width="128" height="66" rx="11" class="slot-body" />
            <text x="500" y="250" text-anchor="middle" class="slot-name">sop</text>
            <text x="500" y="272" text-anchor="middle" class="slot-sub">顺序 · 分支 · 条件</text>
          </g>
          <g v-else class="slot agent">
            <text x="500" y="172" text-anchor="middle" class="slot-input">目标 + 上下文</text>
            <path d="M 500 184 V 217" class="goal-line" marker-end="url(#ev-arrow)" />
            <rect :x="SLOT.x - 64" :y="SLOT.y - 33" width="128" height="66" rx="11" class="slot-body agent-body" />
            <text x="500" y="255" text-anchor="middle" class="slot-name">agent</text>
            <text x="500" y="276" text-anchor="middle" class="slot-sub">model</text>
          </g>
          <text x="500" y="455" text-anchor="middle" class="stage-caption">
            {{ phase === 0 ? '编排存在于人的判断中' : phase === 1 ? '编排获得可传递的文本形式' : '编排路径由模型按目标生成' }}
          </text>
        </g>

        <!-- 第四态：person → agent → person 与 person → sop → tool 共存。 -->
        <g v-if="phase === 3" class="tree">
          <text x="516" y="124" class="tree-top-label">上层调用</text>
          <g v-for="(d, i) in TREE_LINKS" :key="i" class="tree-link" :style="{ '--i': i }">
            <path :d="d" class="tree-base" />
            <path :d="d" class="tree-flow" pathLength="1" marker-end="url(#ev-arrow)" />
          </g>
          <g transform="translate(500 175)" class="tree-unit manual parent-unit">
            <rect x="-70" y="-32" width="140" height="64" rx="11" class="tree-body" />
            <text y="-5" text-anchor="middle" class="tree-name">person · #01</text>
            <text y="15" text-anchor="middle" class="tree-sub">手工 · 父实例</text>
          </g>
          <g transform="translate(320 305)" class="tree-unit intelligent child-unit">
            <rect x="-70" y="-32" width="140" height="64" rx="11" class="tree-body" />
            <text y="-5" text-anchor="middle" class="tree-name">agent · #02</text>
            <text y="15" text-anchor="middle" class="tree-sub">智能 · 子实例</text>
          </g>
          <g transform="translate(680 305)" class="tree-unit automatic child-unit second">
            <rect x="-70" y="-32" width="140" height="64" rx="11" class="tree-body" />
            <text y="-5" text-anchor="middle" class="tree-name">sop · #03</text>
            <text y="15" text-anchor="middle" class="tree-sub">自动 · 子实例</text>
          </g>
          <g transform="translate(245 413)" class="tree-leaf">
            <rect x="-46" y="-20" width="92" height="40" rx="9" class="unit-body" />
            <text text-anchor="middle" dominant-baseline="central" class="unit-name">单元 A</text>
          </g>
          <g transform="translate(395 413)" class="tree-unit manual leaf-unit">
            <rect x="-70" y="-24" width="140" height="48" rx="9" class="tree-body" />
            <text y="-3" text-anchor="middle" class="tree-name">person · #04</text>
            <text y="14" text-anchor="middle" class="tree-sub">手工 · 确认</text>
          </g>
          <g transform="translate(680 413)" class="tree-leaf">
            <rect x="-46" y="-20" width="92" height="40" rx="9" class="unit-body" />
            <text text-anchor="middle" dominant-baseline="central" class="unit-name">单元 B</text>
          </g>
          <text x="320" y="466" text-anchor="middle" class="tree-role">agent 执行到确认环节，再调用人</text>
          <text x="680" y="466" text-anchor="middle" class="tree-role">规程按既定规则自动执行</text>
          <text x="908" y="440" text-anchor="end" class="tree-stop">父停子停</text>
        </g>

        <line x1="92" y1="486" x2="908" y2="486" class="ax-base" />
        <rect x="92" y="485.2" width="816" height="1.6" class="ax-fill" :style="{ transform: `scaleX(${fill})` }" />
        <g v-for="(item, i) in CH" :key="item.no" class="ax-tick" :class="{ on: phase >= i }">
          <circle :cx="92 + i * 272" cy="486" r="3" />
          <text :x="92 + i * 272" y="509">{{ item.ax }}</text>
        </g>
      </svg>
    </div>
    <div class="ev-scroll-hint">图示可横向滚动</div>
    <div class="ev-foot">
      <span class="ev-prog" aria-hidden="true"><i :key="`${runId}-${phase}`" :style="{ animationDuration: DWELL[phase] + 'ms' }" /></span>
      <span class="ev-no mono">{{ CH[phase].no }}<i>/04</i></span>
      <b class="ev-t">{{ CH[phase].ti }}</b>
      <span class="ev-d">{{ CH[phase].d }}</span>
    </div>
    <div class="ev-steps" aria-label="动画阶段">
      <span v-for="(item, i) in CH" :key="item.no" class="ev-step" :class="{ active: phase === i }"
        :aria-current="phase === i ? 'step' : undefined">
        <span class="mono">{{ item.no }} · {{ item.ax }}</span>{{ item.ti }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.ev { border: 1px solid var(--line); border-radius: var(--r-lg); background: #0b0d12; overflow: hidden; }
.ev-stage { overflow: hidden; }
.ev-stage:focus-visible { outline: 2px solid #7FE3EF; outline-offset: -2px; }
.ev-s { display: block; width: 100%; height: auto; }
.ev-scroll-hint { display: none; }
.ch-no { font-family: var(--font-mono); font-size: 10px; fill: rgba(127,227,239,.55); letter-spacing: .18em; }
.ch-ti { font-size: 22px; font-weight: 500; fill: #eef6f9; }
.scene-note { font-size: 10px; fill: rgba(178,205,214,.4); text-anchor: end; letter-spacing: .08em; }
.unit-body { fill: #111a22; stroke: rgba(127,197,209,.28); stroke-width: 1; }
.unit-name { font-size: 10px; fill: rgba(190,215,223,.65); }
.unit { filter: drop-shadow(0 5px 10px rgba(0,0,0,.3)); }
.call-base { fill: none; stroke: rgba(135,164,178,.12); stroke-width: 1; }
.call-active { fill: none; stroke: rgba(139,226,238,.74); stroke-width: 1.2; }
.call-order { font-family: var(--font-mono); font-size: 9px; fill: rgba(127,227,239,.6); text-anchor: middle; }
.slot { transform-origin: 500px 260px; animation: slot-in .7s ease both; }
.slot-body { fill: #121e27; stroke: rgba(127,227,239,.55); stroke-width: 1; }
.slot-name { font-family: var(--font-mono); font-size: 15px; fill: #d9eef2; }
.slot-sub { font-size: 9px; fill: rgba(169,208,217,.55); }
.slot-input { font-size: 10px; fill: rgba(127,227,239,.5); letter-spacing: .04em; }
.agent-body { fill: #14232b; stroke: rgba(127,227,239,.78); }
.goal-line { fill: none; stroke: rgba(127,220,232,.5); stroke-width: 1; }
.stage-caption { font-size: 10px; fill: rgba(127,227,239,.44); letter-spacing: .05em; }
@keyframes slot-in { from { opacity: .25; transform: scale(.9); } to { opacity: 1; transform: scale(1); } }

/* 经验：逐次判断；规程：固定且可重复；模型：路径按目标生成。 */
.call.p0 .call-active { opacity: 0; stroke-dasharray: .12 .88; animation: judgment 5.7s linear both; animation-delay: var(--delay); }
.call.p1 .call-active { stroke-dasharray: 1; stroke-dashoffset: 1; animation: fixed-call 1.6s calc(var(--i) * .15s) ease forwards; }
.call.p2 .call-active { opacity: .08; stroke-dasharray: .1 .9; animation: model-call 2.8s calc(var(--i) * .36s) ease-in-out infinite; }
.call.p2:nth-child(2) .call-active, .call.p2:nth-child(5) .call-active { opacity: .02; animation: none; }
@keyframes judgment { 0% { opacity: 0; stroke-dashoffset: 1; } 2% { opacity: 1; } 10% { opacity: 1; stroke-dashoffset: 0; } 13%,100% { opacity: 0; stroke-dashoffset: 0; } }
@keyframes fixed-call { to { stroke-dashoffset: 0; } }
@keyframes model-call { 0% { opacity: 0; stroke-dashoffset: 1; } 15% { opacity: .9; } 60% { opacity: .9; stroke-dashoffset: 0; } 100% { opacity: 0; stroke-dashoffset: 0; } }

/* 混合：颜色只区分实现类型；所有单元保持相同调用语义。 */
.tree-unit { transform-box: fill-box; transform-origin: center; animation: tree-node-in .65s ease both; }
.child-unit { animation-delay: .35s; }
.child-unit.second { animation-delay: .55s; }
.tree-leaf, .leaf-unit { animation: tree-node-in .65s .8s ease both; }
.tree-base { fill: none; stroke: rgba(135,164,178,.25); stroke-width: 1; }
.tree-flow { fill: none; stroke: rgba(127,220,232,.72); stroke-width: 1.2; stroke-dasharray: .12 .88; animation: tree-call 3s calc(var(--i) * .25s) linear infinite; }
.tree-body { fill: #121f28; stroke: rgba(127,227,239,.58); stroke-width: 1; }
.manual .tree-body { fill: #211d19; stroke: rgba(231,195,145,.62); }
.manual .tree-name { fill: #eadbc6; }
.automatic .tree-body { fill: #1d1a26; stroke: rgba(185,167,228,.62); }
.automatic .tree-name { fill: #ded5ef; }
.parent-unit .tree-body { stroke-width: 1.4; }
.tree-name { font-family: var(--font-mono); font-size: 12px; fill: #d2e9ee; }
.tree-sub { font-size: 9px; fill: rgba(167,202,211,.62); }
.tree-top-label, .tree-role { font-size: 9px; fill: rgba(174,204,213,.45); }
.tree-stop { font-size: 10px; fill: rgba(127,227,239,.58); }
@keyframes tree-node-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes tree-call { from { stroke-dashoffset: 1; } to { stroke-dashoffset: 0; } }

/* 减少动态效果时直接呈现第四态的完整构图。 */
.settled .orchestration *, .settled .tree * { animation: none !important; transition: none !important; }
.settled .call.p0 .call-active { opacity: .55; stroke-dashoffset: 0; }
.settled .call.p1 .call-active { stroke-dashoffset: 0; }
.settled .call.p2 .call-active { opacity: .65; stroke-dashoffset: 0; }
.ax-base { stroke: rgba(255,255,255,.08); stroke-width: 1; }
.ax-fill { fill: rgba(127,227,239,.5); transform-box: fill-box; transform-origin: 0 50%; transition: transform 1s var(--ease); }
.ax-tick circle { fill: #0b0d12; stroke: rgba(255,255,255,.22); stroke-width: 1.2; }
.ax-tick text { font-size: 9.5px; fill: rgba(255,255,255,.28); text-anchor: middle; }
.ax-tick.on circle { stroke: rgba(127,227,239,.9); }
.ax-tick.on text { fill: rgba(127,227,239,.72); }
.ev-foot { position: relative; min-height: 78px; display: flex; align-items: center; gap: 13px; flex-wrap: wrap; padding: 16px 22px; border-top: 1px solid rgba(255,255,255,.08); }
.ev-prog { position: absolute; top: -1px; left: 0; right: 0; height: 2px; pointer-events: none; background: rgba(255,255,255,.06); }
.ev-prog i { display: block; height: 100%; transform-origin: 0 50%; background: linear-gradient(90deg,rgba(127,227,239,.15),rgba(127,227,239,.95)); animation: ev-prog linear both; }
@keyframes ev-prog { from { transform: scaleX(0); } to { transform: scaleX(1); } }
.ev-no { font-size: 11px; color: #7FE3EF; }
.ev-no i { color: #4d5665; font-style: normal; }
.ev-t { font-size: 13.5px; font-weight: 500; color: #e6edf3; }
.ev-d { flex: 1 1 420px; font-size: 12.5px; line-height: 1.75; color: #8B98A6; }
.ev-steps { display: grid; grid-template-columns: repeat(4,1fr); border-top: 1px solid rgba(255,255,255,.08); }
.ev-step { min-height: 58px; padding: 10px 14px; border-right: 1px solid rgba(255,255,255,.07); color: rgba(178,195,205,.68); font-size: 10.5px; line-height: 1.45; }
.ev-step:last-child { border-right: 0; }
.ev-step span { display: block; margin-bottom: 4px; font-size: 9px; color: rgba(127,227,239,.58); }
.ev-step.active { color: #E1F3F6; background: rgba(127,227,239,.045); }
.ev-step.active span { color: #7FE3EF; }
@media (max-width:760px) {
  .ev-steps { grid-template-columns: repeat(2,1fr); }
  .ev-step:nth-child(2) { border-right: 0; }
  .ev-step:nth-child(-n+2) { border-bottom: 1px solid rgba(255,255,255,.07); }
  .ev-foot { align-items: flex-start; }
  .ev-d { flex-basis: 100%; order: 4; }
}
@media (max-width:600px) {
  .ev-stage { overflow-x: auto; scrollbar-width: thin; }
  .ev-s { width: 760px; max-width: none; }
  .ev-scroll-hint { display: block; padding: 5px 12px; font-size: 9px; text-align: right; color: rgba(178,198,208,.42); }
}
@media (prefers-reduced-motion:reduce) { .ev *, .ev *::before, .ev *::after { animation: none !important; transition: none !important; } }
</style>
