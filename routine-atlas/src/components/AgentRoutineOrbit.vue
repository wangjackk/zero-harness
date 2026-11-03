<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type Capability = { name: string; short: string; color: string; radius: number; speed: number; offset: number }
type Point = { x: number; y: number }
const DURATION = 9
const ESCAPE = 4.1
const CAPTURE = 7.7
const capabilities: Capability[] = [
  { name: '运动', short: 'move', color: '#b79aff', radius: 102, speed: .85, offset: 0 },
  { name: '跟踪', short: 'track', color: '#70d7f4', radius: 137, speed: -.70, offset: 1.05 },
  { name: '导航', short: 'navigate', color: '#ffc177', radius: 172, speed: .55, offset: 2.1 },
  { name: '状态', short: 'status', color: '#78e4b0', radius: 119, speed: -.78, offset: 3.15 },
  { name: '相机', short: 'snap', color: '#ff91b5', radius: 154, speed: .62, offset: 4.2 },
  { name: '设备桥接', short: 'ROS / bus', color: '#b4b9ff', radius: 190, speed: -.45, offset: 5.25 }
]
const agents = [
  { name: 'Pi', sub: '托管会话', x: 240, y: 205 },
  { name: 'Flow', sub: '事件循环', x: 760, y: 205 },
  { name: 'XML', sub: '流式行动', x: 760, y: 500 },
  { name: 'Moss', sub: '连续编排', x: 240, y: 500 }
]
const stars = Array.from({ length: 110 }, (_, i) => ({
  x: (i * 137.51 + 31) % 1000,
  y: (i * 83.73 + 17) % 700,
  r: i % 11 === 0 ? 1.4 : i % 4 === 0 ? .9 : .5,
  delay: -(i % 17) * .31
}))

const root = ref<HTMLElement | null>(null)
const time = ref(0)
const playing = ref(false)
const agentIndex = computed(() => Math.floor(time.value / DURATION) % agents.length)
const nextIndex = computed(() => (agentIndex.value + 1) % agents.length)
const local = computed(() => time.value % DURATION)
const stage = computed(() => local.value < ESCAPE ? 'orbit' : local.value < 5 ? 'collapse' : local.value < CAPTURE ? 'transfer' : 'capture')
const activeName = computed(() => stage.value === 'capture' ? agents[nextIndex.value].name : agents[agentIndex.value].name)
let observer: IntersectionObserver | undefined
let motion: MediaQueryList | undefined
let frame = 0
let previous = 0
let elapsed = .8

function clamp01(n: number) { return Math.max(0, Math.min(1, n)) }
function smooth(n: number) { const u = clamp01(n); return u * u * (3 - 2 * u) }
function orbitPosition(index: number, capability: Capability, at: number): Point {
  const agent = agents[index]
  const angle = capability.offset + at * capability.speed + index * .42
  return { x: agent.x + Math.cos(angle) * capability.radius, y: agent.y + Math.sin(angle) * capability.radius * .48 }
}
function positionAt(capability: Capability, at: number): Point {
  const index = Math.floor(at / DURATION) % agents.length
  const next = (index + 1) % agents.length
  const localTime = at % DURATION
  const cycleStart = Math.floor(at / DURATION) * DURATION
  if (localTime <= ESCAPE) return orbitPosition(index, capability, at)
  if (localTime >= CAPTURE) return orbitPosition(next, capability, at)
  const start = orbitPosition(index, capability, cycleStart + ESCAPE)
  const end = orbitPosition(next, capability, cycleStart + CAPTURE)
  const u = smooth((localTime - ESCAPE) / (CAPTURE - ESCAPE))
  const a = agents[index]
  const b = agents[next]
  const dx = b.x - a.x
  const dy = b.y - a.y
  const length = Math.hypot(dx, dy)
  const bend = (capabilities.indexOf(capability) % 2 ? -1 : 1) * 95
  const control = { x: (start.x + end.x) / 2 - dy / length * bend, y: (start.y + end.y) / 2 + dx / length * bend }
  const v = 1 - u
  return { x: v * v * start.x + 2 * v * u * control.x + u * u * end.x, y: v * v * start.y + 2 * v * u * control.y + u * u * end.y }
}
function position(capability: Capability): Point {
  return positionAt(capability, time.value)
}
function trail(capability: Capability): Point[] {
  return [Math.max(0, time.value - .20), Math.max(0, time.value - .10), time.value].map(at => positionAt(capability, at))
}
function starStyle(index: number) {
  // 四颗恒星始终共存；明暗表示当前由谁组织这批能力。
  let opacity = .28
  let scale = .55
  if (stage.value !== 'capture' && index === agentIndex.value) {
    const collapse = smooth((local.value - ESCAPE) / .9)
    opacity = 1 - collapse * .72
    scale = 1 - collapse * .45
  } else if ((stage.value === 'transfer' || stage.value === 'capture') && index === nextIndex.value) {
    const attraction = smooth((local.value - 6.3) / 1.4)
    opacity = .28 + attraction * .72
    scale = .55 + attraction * .45
  } else if (stage.value === 'capture' && index === nextIndex.value) {
    opacity = 1
    scale = 1
  }
  return { opacity, transform: `translate(${agents[index].x}px, ${agents[index].y}px) scale(${scale})` }
}
function stop() { cancelAnimationFrame(frame); frame = 0; previous = 0 }
function tick(now: number) {
  if (!previous) previous = now
  const dt = Math.min((now - previous) / 1000, .05)
  previous = now
  elapsed += dt
  time.value = elapsed
  frame = requestAnimationFrame(tick)
}
function sync() {
  stop()
  playing.value = !motion?.matches
  if (playing.value) frame = requestAnimationFrame(tick)
}
function preference() {
  if (motion?.matches) { elapsed = .8; time.value = elapsed }
  sync()
}
onMounted(() => {
  motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  motion.addEventListener('change', preference)
  observer = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) sync()
    else { stop(); playing.value = false }
  }, { threshold: .15 })
  if (root.value) observer.observe(root.value)
})
onBeforeUnmount(() => { stop(); observer?.disconnect(); motion?.removeEventListener('change', preference) })
</script>

<template>
  <div ref="root" class="systems" :class="{ playing }" :data-stage="stage">
    <div class="systems-legend" aria-label="共享能力图例">
      <span v-for="capability in capabilities" :key="capability.name"><i :style="{ background: capability.color }"></i>{{ capability.name }}</span>
      <small>同一批共享 routine</small>
    </div>

    <svg class="systems-map" viewBox="0 0 1000 700" role="img" aria-label="Pi、Flow、XML、Moss 共享调用同一批 routine">
      <defs>
        <radialGradient id="sun-core"><stop offset="0" stop-color="#f1eaff" stop-opacity=".48" /><stop offset=".45" stop-color="#a98bfa" stop-opacity=".18" /><stop offset="1" stop-color="#a98bfa" stop-opacity=".015" /></radialGradient>
        <radialGradient id="nebula"><stop offset="0" stop-color="#694bc0" stop-opacity=".14" /><stop offset=".55" stop-color="#315b85" stop-opacity=".045" /><stop offset="1" stop-color="#13111f" stop-opacity="0" /></radialGradient>
        <filter id="soft-glow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation="4" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
        <filter id="wide-glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="14" /></filter>
      </defs>
      <ellipse cx="320" cy="300" rx="440" ry="330" fill="url(#nebula)" />
      <ellipse cx="760" cy="420" rx="360" ry="280" fill="url(#nebula)" transform="rotate(-24 760 420)" />
      <g><circle v-for="(star, i) in stars" :key="i" class="star" :cx="star.x" :cy="star.y" :r="star.r" :style="{ animationDelay: `${star.delay}s` }" /></g>
      <path class="space-current" d="M-80 585 C190 410 425 690 1080 95" />
      <path class="space-current current-b" d="M-60 80 C290 280 610 -80 1060 520" />

      <g class="agent-addresses">
        <g v-for="(agent, i) in agents" :key="agent.name" :transform="`translate(${agent.x} ${agent.y})`" :class="{ current: activeName === agent.name }">
          <circle r="56" /><text y="-70">{{ agent.name }}</text><text class="address-sub" y="78">{{ agent.sub }}</text>
        </g>
      </g>

      <g v-for="(agent, index) in agents" :key="agent.name + '-sun'" class="agent-sun" :style="starStyle(index)">
        <circle class="sun-aura" r="54" filter="url(#wide-glow)" />
        <circle class="sun-halo" r="64" />
        <circle class="sun-body" r="43" />
        <circle class="sun-rim" r="49" />
        <text class="agent-name" y="-1">{{ agent.name }}</text>
        <text class="agent-sub" y="17">{{ agent.sub }}</text>
      </g>

      <g v-if="stage === 'collapse'" class="supernova" :transform="`translate(${agents[agentIndex].x} ${agents[agentIndex].y})`">
        <circle r="60" /><circle r="78" /><circle r="96" />
      </g>

      <g v-if="stage !== 'capture'" class="orbit-field" :style="{ opacity: stage === 'transfer' ? .12 : 1 }">
        <ellipse v-for="capability in capabilities" :key="capability.name" :cx="agents[agentIndex].x" :cy="agents[agentIndex].y" :rx="capability.radius" :ry="capability.radius * .48" />
      </g>
      <g v-if="stage === 'transfer' || stage === 'capture'" class="orbit-field destination" :style="{ opacity: stage === 'capture' ? 1 : smooth((local - 6.3) / 1.4) * .45 }">
        <ellipse v-for="capability in capabilities" :key="capability.name" :cx="agents[nextIndex].x" :cy="agents[nextIndex].y" :rx="capability.radius" :ry="capability.radius * .48" />
      </g>

      <g v-for="capability in capabilities" :key="capability.name + '-trail'" class="planet-trail">
        <polyline :points="trail(capability).map(p => `${p.x},${p.y}`).join(' ')" :style="{ stroke: capability.color }" />
      </g>
      <g v-for="capability in capabilities" :key="capability.name" class="planet" :transform="`translate(${position(capability).x} ${position(capability).y})`">
        <circle class="planet-halo" r="14" :style="{ fill: capability.color }" filter="url(#soft-glow)" />
        <circle class="planet-body" r="7" :style="{ fill: capability.color }" />
        <circle class="planet-core" r="2.2" />
        <text y="-15">{{ capability.name }}</text>
        <title>{{ activeName }} 可调用 {{ capability.name }} routine（{{ capability.short }}）</title>
      </g>
    </svg>

    <div class="systems-caption">
      <div class="caption-scene"><span class="mono">SHARED ROUTINES</span><h3>四种 Agent，共享同一批 routine。</h3><p>运动、跟踪、导航、状态、相机与设备桥接，都可以被不同 Agent 调用和组合。</p></div>
      <div class="sequence"><span v-for="(agent, i) in agents" :key="agent.name" :class="{ active: activeName === agent.name }">{{ agent.name }}</span></div>
    </div>
  </div>
</template>

<style scoped>
.systems { position: relative; overflow: hidden; border: 1px solid color-mix(in oklab, #a98bfa 25%, var(--line)); border-radius: var(--r-lg); background: #090a13; box-shadow: inset 0 0 100px rgb(82 55 148 / 9%), 0 24px 70px rgb(0 0 0 / 14%); }
.systems::after { position: absolute; inset: 0; background: repeating-linear-gradient(0deg, transparent 0 3px, rgb(170 155 255 / 1.2%) 4px); content: ''; pointer-events: none; }
.systems-legend { position: relative; z-index: 1; display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 12px 20px; padding: 19px 24px; border-bottom: 1px solid rgb(176 155 255 / 12%); background: rgb(12 13 25 / 70%); }
.systems-legend > span { display: flex; align-items: center; gap: 7px; color: #a4a2b6; font-size: 11px; }
.systems-legend i { width: 6px; height: 6px; border-radius: 50%; box-shadow: 0 0 9px currentColor; }
.systems-legend small { margin-left: 10px; color: #777289; font-family: var(--font-mono); font-size: 9px; }
.systems-map { display: block; width: 100%; height: auto; }
.star { fill: #d9d4ff; opacity: .2; }
.playing .star { animation: twinkle 3.4s ease-in-out infinite alternate; }
.space-current { fill: none; stroke: #9172ed; stroke-width: .7; stroke-dasharray: 1 18; opacity: .12; }
.playing .space-current { animation: current-flow 7s linear infinite; }
.current-b { stroke: #62c9ed; animation-direction: reverse !important; }
.agent-addresses circle { fill: none; stroke: #8c7bac; stroke-width: .7; stroke-dasharray: 2 8; opacity: .12; }
.agent-addresses text { fill: #514c63; font-size: 13px; font-weight: 550; text-anchor: middle; transition: fill .4s; }
.agent-addresses .address-sub { fill: #403c4d; font-size: 9px; font-weight: 400; }
.agent-addresses .current text { fill: #827799; }
.agent-sun { transform-box: view-box; }
.sun-aura { fill: #a98bfa; opacity: .26; }
.sun-halo { fill: #a98bfa; opacity: .09; transform-origin: center; transform-box: fill-box; }
.sun-body { fill: url(#sun-core); stroke: #c4b2ff; stroke-width: 1.4; }
.sun-rim { fill: none; stroke: #b9a3f4; stroke-width: .8; stroke-dasharray: 4 11; opacity: .55; transform-origin: center; transform-box: fill-box; }
.playing .sun-rim { animation: sun-spin 7s linear infinite; }
.playing .sun-halo { animation: sun-breathe 2s ease-in-out infinite; }
.agent-name { fill: #f3efff; font-size: 16px; font-weight: 550; text-anchor: middle; }
.agent-sub { fill: #a39aaf; font-size: 9px; text-anchor: middle; }
.orbit-field { transition: opacity .25s; }
.orbit-field ellipse { fill: none; stroke: #887aa8; stroke-width: .7; stroke-dasharray: 2 6; opacity: .35; }
.destination ellipse { stroke: #73c9e7; }
.planet-trail polyline { fill: none; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; opacity: .20; }
.planet-halo { opacity: .28; }
.planet-body { stroke: #0a0a13; stroke-width: 1.5; }
.planet-core { fill: #fff; opacity: .85; }
.planet text { fill: #b7b1c5; font-size: 11px; text-anchor: middle; paint-order: stroke; stroke: #090a13; stroke-width: 3px; stroke-linejoin: round; }
.supernova circle { fill: none; stroke: #d9c9ff; stroke-width: 1.2; opacity: 0; transform-origin: center; transform-box: fill-box; animation: shockwave 1.25s ease-out infinite; }
.supernova circle:nth-child(2) { animation-delay: .2s; stroke: #8fdcf4; }
.supernova circle:nth-child(3) { animation-delay: .4s; }
.systems-caption { position: relative; z-index: 1; display: flex; align-items: center; justify-content: space-between; gap: 28px; min-height: 110px; padding: 20px 26px 22px; border-top: 1px solid rgb(176 155 255 / 12%); background: rgb(12 13 25 / 84%); }
.caption-scene { animation: caption-in .45s ease both; }
.caption-scene span { color: #b9a3f4; font-size: 9px; letter-spacing: .1em; }
.caption-scene h3 { margin-top: 8px; color: #ece7f6; font-size: 18px; font-weight: 520; }
.caption-scene p { margin-top: 6px; color: #8f899e; font-size: 12px; }
.sequence { display: flex; align-items: center; gap: 7px; flex: 0 0 auto; }
.sequence span { padding: 6px 9px; border: 1px solid #302b40; border-radius: 20px; color: #615a72; font-family: var(--font-mono); font-size: 9px; transition: color .4s, border-color .4s, background .4s; }
.sequence span.active { border-color: #9d83df; background: rgb(169 139 250 / 10%); color: #d6c8f7; }
@keyframes twinkle { 0% { opacity: .08; } 55% { opacity: .65; } 100% { opacity: .22; } }
@keyframes current-flow { to { stroke-dashoffset: -190; } }
@keyframes sun-spin { to { transform: rotate(360deg); } }
@keyframes sun-breathe { 50% { opacity: .18; transform: scale(1.16); } }
@keyframes shockwave { 0% { opacity: .55; transform: scale(.45); } 100% { opacity: 0; transform: scale(2.1); } }
@keyframes caption-in { from { opacity: .2; transform: translateY(4px); } }
@media (max-width: 650px) {
  .systems-legend { justify-content: flex-start; gap: 10px 15px; padding: 16px 19px; }
  .systems-legend small { width: 100%; margin: 3px 0 0; }
  .systems-map { width: 155%; max-width: none; margin-left: -27.5%; }
  .systems-caption { align-items: flex-start; flex-direction: column; gap: 16px; padding: 18px 20px; }
  .caption-scene h3 { font-size: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .star, .space-current, .sun-rim, .sun-halo, .supernova circle, .caption-scene { animation: none !important; }
}
</style>
