<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const root = ref<HTMLElement | null>(null)
const phase = ref(0)
const running = ref(false)
const reduced = ref(false)
const durations = [1400, 1600, 1800, 2600, 3200]
let timer: number | null = null
let observer: IntersectionObserver | null = null

const phaseText = computed(() => {
  if (phase.value === 0) return '不同设备各自运行 RoutineHub，接入同一个系统'
  if (phase.value === 1) return '只要能通过 gRPC 连接，就可以拨入同一个 kernel'
  if (phase.value === 2) return '本机能力随 hub 接入自动进入同一注册表，无需逐设备另做调用体系'
  if (phase.value === 3) return '手机的 phone/sensors/watch 主动发布感知，桌面 hub 订阅并接收事件'
  return '桌面 hub 主动 call("car/move")，kernel 路由到机器人，结果返回桌面'
})

const liveText = computed(() => {
  if (phase.value === 3) return 'PUBLISH · phone/sensors'
  if (phase.value === 4) return 'CALL · desktop → robot'
  return 'DEVICE → HUB → KERNEL'
})

function stopTimer() {
  if (timer !== null) window.clearTimeout(timer)
  timer = null
}

function schedule() {
  stopTimer()
  if (!running.value || reduced.value) return
  timer = window.setTimeout(() => {
    phase.value = phase.value < 4 ? phase.value + 1 : 0
    schedule()
  }, durations[phase.value])
}

onMounted(() => {
  reduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (reduced.value) {
    phase.value = 4
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    running.value = entry.isIntersecting
    if (running.value) schedule()
    else stopTimer()
  }, { threshold: 0.18 })
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
    class="device-interconnect"
    :class="[`device-phase-${phase}`, { paused: !running && !reduced, reduced }]"
    role="img"
    aria-label="多类型设备双向互联动画：手机、机器人和桌面电脑分别运行 RoutineHub，通过 gRPC 拨入同一个 kernel；手机主动发布传感器事件，桌面 hub 订阅后主动调用机器人上的 car/move 并接收结果"
  >
    <div class="di-head">
      <span class="mono">ROUTINEHUB / PUBLISH / SUBSCRIBE / CALL</span>
      <small>设备成为节点 · 能力自动集成 · 全系统协作</small>
    </div>

    <svg viewBox="0 0 1000 560" aria-hidden="true">
      <defs>
        <pattern id="device-grid" width="38" height="38" patternUnits="userSpaceOnUse">
          <path d="M38 0H0V38" fill="none" stroke="currentColor" stroke-width=".7" />
        </pattern>
        <filter id="device-glow" x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="6" result="blur" />
          <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
        </filter>
      </defs>
      <rect class="device-grid" width="1000" height="560" fill="url(#device-grid)" />

      <g class="network-links">
        <path class="network-link link-phone" d="M275 145 C330 150 340 190 375 225" />
        <path class="network-link link-desktop" d="M725 145 C670 150 660 190 625 225" />
        <path class="network-link link-robot" d="M500 420 V375" />
        <path class="network-flow flow-phone" d="M275 145 C330 150 340 190 375 225" />
        <path class="network-flow flow-desktop" d="M725 145 C670 150 660 190 625 225" />
        <path class="network-flow flow-robot" d="M500 420 V375" />
      </g>

      <g class="link-labels">
        <text x="300" y="181">gRPC</text><text x="700" y="181" text-anchor="end">gRPC</text><text x="516" y="405">gRPC</text>
      </g>

      <g class="story-node pubsub-story">
        <rect x="355" y="26" width="290" height="62" rx="15" />
        <text class="story-title" x="500" y="51" text-anchor="middle">手机主动发布 → 桌面订阅</text>
        <text class="story-sub" x="500" y="72" text-anchor="middle">publish("phone/sensors", event)</text>
      </g>
      <g class="story-node call-story">
        <rect x="355" y="26" width="290" height="62" rx="15" />
        <text class="story-title" x="500" y="51" text-anchor="middle">桌面 Hub 主动调用机器人</text>
        <text class="story-sub" x="500" y="72" text-anchor="middle">call("car/move") → result</text>
      </g>

      <g class="register-packets">
        <circle class="register-packet register-phone" r="4.5" />
        <circle class="register-packet register-desktop" r="4.5" />
        <circle class="register-packet register-robot" r="4.5" />
      </g>
      <g class="event-packets">
        <circle class="event-packet event-from-phone" r="5" />
        <circle class="event-packet event-to-desktop" r="5" />
      </g>
      <g class="call-packets">
        <circle class="call-packet request-from-desktop" r="5" />
        <circle class="call-packet request-to-robot" r="5" />
        <circle class="call-packet result-from-robot" r="5" />
        <circle class="call-packet result-to-desktop" r="5" />
      </g>

      <g class="kernel-node">
        <circle class="kernel-outline" cx="500" cy="280" r="94" />
        <circle class="kernel-card" cx="500" cy="280" r="80" />
        <text class="kernel-title" x="500" y="287" text-anchor="middle">kernel</text>
      </g>

      <g class="device-node phone-node">
        <rect class="device-card" x="65" y="65" width="210" height="115" rx="16" />
        <rect class="phone-icon" x="84" y="91" width="25" height="43" rx="5" /><circle class="icon-detail" cx="96.5" cy="128" r="1.8" />
        <text class="device-name" x="126" y="108">Android 手机</text>
        <text class="device-hub" x="126" y="137">RoutineHub</text>
        <text class="device-routines" x="126" y="160">phone/sensors/watch</text>
      </g>

      <g class="device-node desktop-node">
        <rect class="device-card" x="725" y="65" width="210" height="115" rx="16" />
        <rect class="desktop-icon" x="744" y="91" width="34" height="28" rx="3" /><path class="icon-detail" d="M753 127H769M761 119V127" />
        <text class="device-name" x="794" y="108">桌面电脑</text>
        <text class="device-hub" x="794" y="137">RoutineHub</text>
        <text class="device-routines" x="794" y="160">pi/agent</text>
      </g>

      <g class="device-node robot-node">
        <rect class="device-card" x="395" y="420" width="210" height="115" rx="16" />
        <rect class="robot-icon" x="414" y="450" width="34" height="31" rx="8" /><path class="icon-detail" d="M431 441V450M424 461H426M436 461H438M424 471H438" />
        <text class="device-name" x="464" y="463">机器人</text>
        <text class="device-hub" x="464" y="492">RoutineHub</text>
        <text class="device-routines" x="464" y="515">car/move</text>
      </g>
    </svg>

    <footer class="di-result">
      <span class="di-step mono">0{{ phase + 1 }} / 05</span>
      <p>{{ phaseText }}</p>
      <span class="di-live mono">{{ liveText }}</span>
    </footer>
  </div>
</template>

<style scoped>
.device-interconnect { --device-accent: #a98bfa; --device-line: #78b9d0; --device-event: #62bfa4; overflow: hidden; border: 1px solid var(--line-2); border-radius: var(--r-lg); background: radial-gradient(ellipse at 50% 48%, color-mix(in oklab, var(--accent) 8%, transparent), transparent 48%), var(--surface); }
.di-head { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 16px 20px; border-bottom: 1px solid var(--line); }
.di-head > span { color: var(--accent); font-size: 9px; letter-spacing: .09em; }
.di-head small { color: var(--ink-4); font-size: 10px; }
.device-interconnect svg { display: block; width: 100%; height: auto; }
.device-grid { color: var(--line); opacity: .3; }
.network-link { fill: none; stroke: var(--line-2); stroke-width: 1.6; stroke-dasharray: 420; stroke-dashoffset: 420; transition: stroke-dashoffset 1s var(--ease), stroke .45s var(--ease); }
.network-flow { fill: none; stroke: var(--device-line); stroke-width: 2.5; stroke-linecap: round; stroke-dasharray: 3 15; opacity: 0; animation: device-flow 1.2s linear infinite; transition: opacity .5s var(--ease); }
.device-phase-1 .network-link, .device-phase-2 .network-link, .device-phase-3 .network-link, .device-phase-4 .network-link { stroke-dashoffset: 0; }
.device-phase-1 .network-flow, .device-phase-2 .network-flow, .device-phase-3 .network-flow, .device-phase-4 .network-flow { opacity: .65; }
.link-labels { fill: var(--ink-4); font-family: var(--font-mono); font-size: 10px; opacity: 0; transition: opacity .5s var(--ease); }
.device-phase-1 .link-labels, .device-phase-2 .link-labels, .device-phase-3 .link-labels, .device-phase-4 .link-labels { opacity: 1; }
.story-node { opacity: 0; transform: translateY(7px); transition: opacity .45s var(--ease), transform .55s var(--ease); }
.story-node rect { fill: var(--surface-solid); stroke: var(--line-2); }
.story-title { fill: var(--ink); font-family: var(--font-sans); font-size: 14px; }
.story-sub { fill: var(--accent); font-family: var(--font-mono); font-size: 10px; }
.device-phase-3 .pubsub-story, .device-phase-4 .call-story { opacity: 1; transform: translateY(0); }
.pubsub-story rect { stroke: color-mix(in oklab, var(--device-event) 60%, var(--line)); }
.pubsub-story .story-sub { fill: var(--device-event); }
.kernel-outline { fill: none; stroke: var(--accent); stroke-width: 1.8; stroke-linecap: round; stroke-dasharray: 18 14; opacity: .45; animation: kernel-run 2s linear infinite; }
.kernel-card { fill: color-mix(in oklab, var(--accent) 6%, var(--surface-solid)); stroke: color-mix(in oklab, var(--accent) 55%, var(--line)); }
.kernel-title { fill: var(--ink); font-family: var(--font-mono); font-size: 19px; }
.device-node { transition: opacity .45s var(--ease), filter .45s var(--ease); }
.device-card { fill: color-mix(in oklab, var(--surface-solid) 95%, transparent); stroke: var(--line-2); stroke-width: 1.4; transition: fill .45s var(--ease), stroke .45s var(--ease); }
.device-name { fill: var(--ink); font-family: var(--font-sans); font-size: 14px; }
.device-hub { fill: var(--device-line); font-family: var(--font-mono); font-size: 11px; }
.device-routines { fill: var(--ink-3); font-family: var(--font-mono); font-size: 10px; }
.phone-icon, .desktop-icon, .robot-icon { fill: color-mix(in oklab, var(--device-line) 7%, transparent); stroke: var(--device-line); stroke-width: 1.4; }
.icon-detail { fill: none; stroke: var(--device-line); stroke-width: 1.4; stroke-linecap: round; }
circle.icon-detail { fill: var(--device-line); stroke: none; }
.device-phase-3 .phone-node .device-card, .device-phase-3 .desktop-node .device-card { fill: color-mix(in oklab, var(--device-event) 10%, var(--surface-solid)); stroke: var(--device-event); }
.device-phase-3 .link-phone, .device-phase-3 .link-desktop { stroke: var(--device-event); stroke-width: 2.5; }
.device-phase-3 .flow-phone, .device-phase-3 .flow-desktop { stroke: var(--device-event); opacity: 1; }
.device-phase-4 .desktop-node .device-card, .device-phase-4 .robot-node .device-card { fill: color-mix(in oklab, var(--accent) 11%, var(--surface-solid)); stroke: var(--accent); }
.device-phase-4 .link-desktop, .device-phase-4 .link-robot { stroke: var(--accent); stroke-width: 2.5; }
.device-phase-4 .flow-desktop, .device-phase-4 .flow-robot { stroke: var(--accent); opacity: 1; }
.register-packet, .event-packet, .call-packet { filter: url(#device-glow); opacity: 0; }
.register-packet, .call-packet { fill: var(--accent); }
.event-packet { fill: var(--device-event); }
.register-phone, .event-from-phone { offset-path: path('M275 145 C330 150 340 190 375 225'); }
.register-desktop, .event-to-desktop, .request-from-desktop, .result-to-desktop { offset-path: path('M725 145 C670 150 660 190 625 225'); }
.register-robot, .request-to-robot, .result-from-robot { offset-path: path('M500 420 V375'); }
.device-phase-2 .register-phone { animation: packet-travel 1.7s linear infinite; }
.device-phase-2 .register-desktop { animation: packet-travel 1.7s -.55s linear infinite; }
.device-phase-2 .register-robot { animation: packet-travel 1.7s -1.1s linear infinite; }
.device-phase-3 .event-from-phone { animation: packet-travel 2.6s linear infinite; }
.device-phase-3 .event-to-desktop { animation: packet-travel-reverse 2.6s .85s linear infinite; }
.device-phase-4 .request-from-desktop { animation: packet-travel 3.2s linear infinite; }
.device-phase-4 .request-to-robot { animation: packet-travel-reverse 3.2s .75s linear infinite; }
.device-phase-4 .result-from-robot { animation: packet-travel 3.2s 1.5s linear infinite; }
.device-phase-4 .result-to-desktop { animation: packet-travel-reverse 3.2s 2.25s linear infinite; }
.di-result { display: grid; grid-template-columns: 60px 1fr auto; align-items: center; gap: 14px; min-height: 66px; padding: 14px 20px; border-top: 1px solid var(--line); background: color-mix(in oklab, var(--bg-deep) 32%, transparent); }
.di-step { color: var(--accent); font-size: 9px; }
.di-result p { color: var(--ink-3); font-size: 12px; line-height: 1.65; }
.di-live { color: var(--ink-4); font-size: 9px; }
.paused *, .reduced * { animation-play-state: paused !important; }
.reduced * { animation: none !important; transition: none !important; }
.reduced .register-packet, .reduced .event-packet, .reduced .call-packet { display: none; }
@keyframes device-flow { to { stroke-dashoffset: -36; } }
@keyframes kernel-run { to { stroke-dashoffset: -64; } }
@keyframes packet-travel { 0% { offset-distance: 0%; opacity: 0; } 12% { opacity: 1; } 82% { opacity: 1; } 100% { offset-distance: 100%; opacity: 0; } }
@keyframes packet-travel-reverse { 0% { offset-distance: 100%; opacity: 0; } 12% { opacity: 1; } 82% { opacity: 1; } 100% { offset-distance: 0%; opacity: 0; } }
@media (max-width: 760px) {
  .di-head { align-items: flex-start; flex-direction: column; gap: 7px; }
  .device-interconnect { overflow-x: auto; }
  .device-interconnect svg { width: 780px; max-width: none; }
  .di-result { position: sticky; left: 0; grid-template-columns: 48px 1fr; width: calc(100vw - 48px); }
  .di-live { display: none; }
}
</style>
