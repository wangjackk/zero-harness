<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const STEPS = [
  { short: '共存', title: '不同参与者进入同一个网络', detail: '人、工具、服务与不同实现的 Agent 都可以成为 routine；统一的是参与方式，不是内部类型。' },
  { short: '协作', title: '已有参与者之间直接通信', detail: '人与 Agent、Agent 与 Agent 可以交换消息。通信不创建节点，也不要求双方使用同一种 Harness。' },
  { short: '调用', title: '一个 Agent 生长出异构子 Agent', detail: 'Flow Agent 可以调用 Pi Agent。父子关系来自这次调用，而不是来自相同的类型或实现体系。' },
  { short: '开放', title: '接口 routine 把外部 Agent 接入网络', detail: '适配器转换协议、任务与结果。内部子树可以级联停止；外部任务能否取消，取决于外部接口。' }
]

const phase = ref(0)
const rootRef = ref<HTMLElement>()
const current = computed(() => STEPS[phase.value])
let timer: ReturnType<typeof setInterval> | undefined
let observer: IntersectionObserver | undefined

function linkState(activePhase: number) {
  return { active: phase.value === activePhase, done: phase.value > activePhase }
}
function stopLoop() {
  clearInterval(timer)
  timer = undefined
}
function startLoop() {
  if (timer !== undefined) return
  timer = setInterval(() => { phase.value = (phase.value + 1) % STEPS.length }, 2600)
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
  <section ref="rootRef" class="sn" aria-labelledby="sn-title">
    <div class="sn-head">
      <div>
        <span class="eyebrow">异构网络 · 自动循环</span>
        <h2 id="sn-title">不是同类实例的集合，<br />而是不同参与者的社会。</h2>
      </div>
      <span class="sn-count mono">0{{ phase + 1 }} / 04</span>
    </div>

    <div class="sn-stage">
      <div class="sn-scroll" tabindex="0" aria-label="异构 Routine 网络，窄屏可横向滚动">
        <svg viewBox="0 0 1000 600" class="sn-svg" role="img" :aria-label="current.title">
          <defs>
            <marker id="sn-arrow-call" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10Z" class="sn-arrow-call" />
            </marker>
            <marker id="sn-arrow-message" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10Z" class="sn-arrow-message" />
            </marker>
          </defs>

          <text x="36" y="36" class="sn-cap">ROUTINE SOCIETY</text>
          <text x="964" y="36" text-anchor="end" class="sn-cap">类型可以不同 · 关系保持统一</text>
          <rect x="770" y="62" width="190" height="160" rx="18" class="sn-outside" />
          <text x="790" y="88" class="sn-outside-label">系统边界之外</text>

          <!-- 已存在参与者之间的通信 -->
          <path d="M169 157C226 176 265 205 316 231" class="sn-link message" :class="linkState(1)" marker-start="url(#sn-arrow-message)" marker-end="url(#sn-arrow-message)" />
          <path d="M421 223C486 184 538 165 596 158" class="sn-link message" :class="linkState(1)" marker-start="url(#sn-arrow-message)" marker-end="url(#sn-arrow-message)" />
          <path d="M174 399C230 357 275 314 324 278" class="sn-link message ambient" />
          <path d="M417 277C475 323 524 371 573 412" class="sn-link message ambient" />
          <path d="M698 184C750 222 782 266 807 300" class="sn-link message ambient" />

          <!-- 调用只决定父子，不限定子节点类型 -->
          <path d="M378 303V423" class="sn-link call" :class="linkState(2)" marker-end="url(#sn-arrow-call)" />
          <text x="397" y="365" class="sn-edge call-text" :class="{ on: phase >= 2 }">调用 · 异构父子</text>

          <!-- 外部系统通过接口 routine 参与 -->
          <path d="M841 289C862 252 868 218 865 184" class="sn-link message external" :class="linkState(3)" marker-start="url(#sn-arrow-message)" marker-end="url(#sn-arrow-message)" />
          <text x="907" y="258" text-anchor="middle" class="sn-edge message-text" :class="{ on: phase >= 3 }">接口适配</text>

          <g v-if="phase === 1" class="sn-packet message">
            <circle r="5"><animateMotion dur="2s" repeatCount="indefinite" path="M169 157C226 176 265 205 316 231" /></circle>
            <circle r="5"><animateMotion dur="2s" repeatCount="indefinite" path="M596 158C538 165 486 184 421 223" /></circle>
          </g>
          <g v-if="phase === 2" class="sn-packet call"><circle r="5"><animateMotion dur="1.6s" repeatCount="indefinite" path="M378 303V423" /></circle></g>
          <g v-if="phase === 3" class="sn-packet message">
            <circle r="5"><animateMotion dur="1.8s" repeatCount="indefinite" path="M841 289C862 252 868 218 865 184" /></circle>
            <circle r="5"><animateMotion dur="1.8s" repeatCount="indefinite" path="M865 184C868 218 862 252 841 289" /></circle>
          </g>

          <g transform="translate(120 140)" class="sn-node human" :class="{ active: phase === 1 }">
            <circle r="65" class="node-halo" /><circle r="51" class="node-ring" /><circle r="42" class="node-core" />
            <text y="5" text-anchor="middle" class="node-name">人</text>
            <text y="76" text-anchor="middle" class="node-sub">判断 · 授权 · 协作</text>
          </g>
          <g transform="translate(370 250)" class="sn-node agent" :class="{ active: phase === 1 || phase === 2 }">
            <circle r="72" class="node-halo" /><circle r="56" class="node-ring" /><circle r="46" class="node-core" />
            <text y="-3" text-anchor="middle" class="node-name">Agent</text><text y="16" text-anchor="middle" class="node-type">Flow</text>
            <text y="82" text-anchor="middle" class="node-sub">一种 Harness</text>
          </g>
          <g transform="translate(650 160)" class="sn-node agent" :class="{ active: phase === 1 }">
            <circle r="65" class="node-halo" /><circle r="51" class="node-ring" /><circle r="42" class="node-core" />
            <text y="-3" text-anchor="middle" class="node-name">Agent</text><text y="16" text-anchor="middle" class="node-type">XML</text>
            <text y="76" text-anchor="middle" class="node-sub">另一种 Harness</text>
          </g>
          <g transform="translate(130 420)" class="sn-node tool">
            <circle r="60" class="node-halo" /><circle r="48" class="node-ring" /><circle r="39" class="node-core" />
            <text y="5" text-anchor="middle" class="node-name">工具</text>
            <text y="72" text-anchor="middle" class="node-sub">文件 · 命令 · 设备</text>
          </g>
          <g transform="translate(620 435)" class="sn-node service">
            <circle r="60" class="node-halo" /><circle r="48" class="node-ring" /><circle r="39" class="node-core" />
            <text y="5" text-anchor="middle" class="node-name">服务</text>
            <text y="72" text-anchor="middle" class="node-sub">模型 · ROS · 其他</text>
          </g>
          <g transform="translate(830 340)" class="sn-node adapter" :class="{ active: phase === 3 }">
            <circle r="65" class="node-halo" /><circle r="51" class="node-ring" /><circle r="42" class="node-core" />
            <text y="-3" text-anchor="middle" class="node-name">接口</text><text y="16" text-anchor="middle" class="node-type">routine</text>
            <text y="76" text-anchor="middle" class="node-sub">协议 · 任务 · 结果</text>
          </g>
          <g transform="translate(865 135)" class="sn-node external-node" :class="{ active: phase === 3 }">
            <circle r="61" class="node-halo" /><circle r="47" class="node-ring" /><circle r="38" class="node-core" />
            <text y="-3" text-anchor="middle" class="node-name">外部</text><text y="16" text-anchor="middle" class="node-type">Agent</text>
            <text y="70" text-anchor="middle" class="node-sub">独立生命周期</text>
          </g>

          <g v-if="phase >= 2" transform="translate(380 480)" class="sn-node agent child" :class="{ active: phase === 2 }">
            <circle r="62" class="node-halo" /><circle r="49" class="node-ring" /><circle r="40" class="node-core" />
            <text y="-3" text-anchor="middle" class="node-name">子 Agent</text><text y="16" text-anchor="middle" class="node-type">Pi</text>
            <text y="73" text-anchor="middle" class="node-sub">随调用生灭</text>
          </g>
          <g v-else transform="translate(380 480)" class="sn-empty">
            <circle r="42" /><text y="5" text-anchor="middle">尚未调用</text>
          </g>
        </svg>
      </div>

      <div class="sn-status" aria-live="polite">
        <div><span class="mono">{{ current.short }}</span><h3>{{ current.title }}</h3><p>{{ current.detail }}</p></div>
      </div>
      <div class="sn-progress" aria-hidden="true"><i v-for="(s, i) in STEPS" :key="s.short" :class="{ on: i === phase }" /></div>
    </div>

    <div class="sn-legend">
      <span><i class="dashed" />通信：已有参与者之间</span>
      <span><i class="solid" />调用：创建异构子实例</span>
      <span>外部任务能否取消取决于外部接口</span>
    </div>
  </section>
</template>

<style scoped>
.sn-head { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 28px; }
.sn-head h2 { margin-top: 14px; }
.sn-count { color: var(--ink-4); font-size: 10px; }
.sn-stage { overflow: hidden; border: 1px solid var(--line-2); border-radius: var(--r-lg); background: var(--surface); }
.sn-scroll { overflow-x: auto; }
.sn-svg { display: block; width: 100%; min-width: 820px; height: auto; }
.sn-cap { fill: var(--ink-3); font-family: var(--font-mono); font-size: 10px; letter-spacing: .09em; }
.sn-outside { fill: transparent; stroke: var(--line-2); stroke-dasharray: 4 6; }
.sn-outside-label { fill: var(--ink-3); font-size: 10px; }
.sn-link { fill: none; stroke-width: 1.5; opacity: .18; transition: opacity .35s ease, stroke .35s ease; }
.sn-link.message { stroke: #a98bfa; stroke-dasharray: 5 6; }
.sn-link.call { stroke: var(--accent); }
.sn-link.ambient { opacity: .16; }
.sn-link.active { opacity: .95; stroke-width: 2; }
.sn-link.done { opacity: .48; }
.sn-arrow-call { fill: var(--accent); }
.sn-arrow-message { fill: #a98bfa; }
.sn-packet.message { fill: #a98bfa; }
.sn-packet.call { fill: var(--accent); }
.sn-edge { fill: var(--ink-4); font-size: 10px; opacity: .25; transition: opacity .3s ease; }
.sn-edge.on { opacity: 1; }
.call-text { fill: var(--accent); }
.message-text { fill: #a98bfa; }
.sn-node { --node: var(--accent); }
.sn-node.human, .sn-node.tool { --node: #3ed2e0; }
.sn-node.agent { --node: #a98bfa; }
.sn-node.service { --node: #e5b567; }
.sn-node.adapter { --node: #56c6d6; }
.sn-node.external-node { --node: var(--ink-4); }
.node-halo { fill: var(--node); opacity: .04; transition: opacity .35s ease; }
.node-ring { fill: var(--surface-solid); stroke: var(--node); stroke-width: 1; }
.node-core { fill: color-mix(in oklab, var(--node) 7%, var(--surface-solid)); }
.sn-node.active .node-halo { opacity: .18; }
.sn-node.active .node-ring { stroke-width: 1.8; }
.node-name { fill: var(--ink); font-size: 13px; font-weight: 550; }
.node-type { fill: var(--node); font-family: var(--font-mono); font-size: 10px; }
.node-sub { fill: var(--ink-3); font-size: 11px; }
.sn-node.child { animation: sn-born .55s var(--ease) both; transform-origin: 380px 480px; }
.sn-empty circle { fill: transparent; stroke: var(--line-2); stroke-dasharray: 3 6; }
.sn-empty text { fill: var(--ink-4); font-size: 10px; }
.sn-status { min-height: 122px; padding: 22px 26px; border-top: 1px solid var(--line); }
.sn-status > div { display: grid; grid-template-columns: 56px 1fr; column-gap: 16px; }
.sn-status span { padding-top: 4px; color: var(--accent); font-size: 10px; }
.sn-status h3 { color: var(--ink); font-size: 16px; font-weight: 520; }
.sn-status p { grid-column: 2; margin-top: 7px; max-width: 850px; color: var(--ink-3); font-size: 12px; line-height: 1.8; }
.sn-progress { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; padding: 0 26px 18px; }
.sn-progress i { height: 2px; background: var(--line-2); }
.sn-progress i.on { background: var(--accent); }
.sn-legend { display: flex; flex-wrap: wrap; gap: 12px 26px; margin-top: 16px; color: var(--ink-4); font-size: 11px; }
.sn-legend span { display: flex; align-items: center; gap: 8px; }
.sn-legend i { width: 30px; border-top: 1.5px solid var(--accent); }
.sn-legend i.dashed { border-color: #a98bfa; border-top-style: dashed; }
@keyframes sn-born { from { opacity: 0; } to { opacity: 1; } }
@media (prefers-reduced-motion: no-preference) {
  .sn-node.active .node-halo { animation: sn-breathe 2.2s ease-in-out infinite; }
}
@keyframes sn-breathe { 0%, 100% { opacity: .1; } 50% { opacity: .24; } }
@media (max-width: 700px) {
  .sn-head { align-items: start; }
  .sn-head h2 { font-size: 25px; }
  .sn-status > div { grid-template-columns: 1fr; }
  .sn-status h3 { margin-top: 8px; }
  .sn-status p { grid-column: 1; }
}
</style>
