<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

defineProps<{
  kind: 'loop' | 'context' | 'call' | 'permission' | 'memory' | 'stop'
  title: string
}>()

const root = ref<HTMLElement | null>(null)
const paused = ref(true)
let observer: IntersectionObserver | undefined

onMounted(() => {
  if (!root.value) return
  observer = new IntersectionObserver(([entry]) => {
    paused.value = !entry.isIntersecting
  }, { threshold: .2 })
  observer.observe(root.value)
})

onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div ref="root" class="feature-diagram" :class="{ paused }" :aria-label="title + '的 Routine 调用意图'">
    <!-- 执行循环：结果回到 Agent，形成闭环 -->
    <svg v-if="kind === 'loop'" class="diagram" viewBox="0 0 560 230" role="img">
      <defs><marker id="hf-arrow-loop" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <path class="flow" d="M38 110 H190" marker-end="url(#hf-arrow-loop)" />
      <text class="edge-label" x="112" y="96">输入</text>
      <rect class="node accent" x="190" y="75" width="160" height="72" rx="12" />
      <text class="label" x="270" y="106">Agent routine</text>
      <text class="sub" x="270" y="128">run · turn / step</text>
      <rect class="node" x="405" y="75" width="125" height="72" rx="12" />
      <text class="label" x="467" y="106">子 routine</text>
      <text class="sub" x="467" y="128">执行能力</text>
      <path class="flow" d="M350 101 H405" marker-end="url(#hf-arrow-loop)" />
      <text class="edge-label" x="378" y="88">调用</text>
      <path class="flow return" d="M405 137 C365 207 225 207 270 147" marker-end="url(#hf-arrow-loop)" />
      <text class="edge-label" x="350" y="198">结果 · 下一轮</text>
    </svg>

    <!-- 模型与上下文：多路信息先汇聚，再形成一次模型请求 -->
    <svg v-else-if="kind === 'context'" class="diagram" viewBox="0 0 560 230" role="img">
      <defs><marker id="hf-arrow-context" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <rect class="node small" x="20" y="22" width="130" height="44" rx="9" /><text class="label small-label" x="85" y="50">当前输入</text>
      <rect class="node small" x="20" y="93" width="130" height="44" rx="9" /><text class="label small-label" x="85" y="121">历史事件</text>
      <rect class="node small" x="20" y="164" width="130" height="44" rx="9" /><text class="label small-label" x="85" y="192">可用能力</text>
      <path class="flow" d="M150 44 C205 44 195 91 235 96" />
      <path class="flow" d="M150 115 H235" />
      <path class="flow" d="M150 186 C205 186 195 139 235 134" />
      <path class="flow" d="M345 115 H410" marker-end="url(#hf-arrow-context)" />
      <rect class="node accent" x="235" y="78" width="110" height="74" rx="12" />
      <text class="label" x="290" y="108">模型上下文</text><text class="sub" x="290" y="130">本步所见</text>
      <rect class="node" x="410" y="78" width="125" height="74" rx="12" />
      <text class="label" x="472" y="108">模型</text><text class="sub" x="472" y="130">文本 / 行动意图</text>
      <path class="flow return" d="M472 152 C472 210 290 218 290 152" marker-end="url(#hf-arrow-context)" />
      <text class="edge-label" x="383" y="207">输出进入后续步骤</text>
    </svg>

    <!-- 能力调用：去程是调用，回程是结果 -->
    <svg v-else-if="kind === 'call'" class="diagram" viewBox="0 0 560 230" role="img">
      <defs><marker id="hf-arrow-call" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <rect class="node accent" x="28" y="77" width="145" height="76" rx="12" />
      <text class="label" x="100" y="108">Agent routine</text><text class="sub" x="100" y="131">提出行动意图</text>
      <rect class="node" x="390" y="77" width="145" height="76" rx="12" />
      <text class="label" x="462" y="108">能力 routine</text><text class="sub" x="462" y="131">真实执行</text>
      <path class="flow" d="M173 91 C245 20 323 20 390 91" marker-end="url(#hf-arrow-call)" />
      <text class="edge-label" x="282" y="47">create → start → wait</text>
      <path class="flow return" d="M390 139 C323 210 245 210 173 139" marker-end="url(#hf-arrow-call)" />
      <text class="edge-label" x="282" y="192">结果写回后续上下文</text>
      <text class="registry-note" x="282" y="119">同一个注册表 · 同一种调用</text>
    </svg>

    <!-- 权限许可：先过闸门；允许与拒绝是两条不同路径 -->
    <svg v-else-if="kind === 'permission'" class="diagram" viewBox="0 0 560 230" role="img">
      <defs><marker id="hf-arrow-permission" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <rect class="node accent" x="20" y="76" width="130" height="72" rx="12" />
      <text class="label" x="85" y="106">Agent routine</text><text class="sub" x="85" y="128">准备行动</text>
      <path class="flow" d="M150 112 H207" marker-end="url(#hf-arrow-permission)" />
      <path class="gate" d="M275 51 L345 112 L275 173 L205 112 Z" />
      <text class="label" x="275" y="107">许可 routine</text><text class="sub" x="275" y="130">等待决定</text>
      <path class="flow allow" d="M345 112 H425" marker-end="url(#hf-arrow-permission)" />
      <text class="allow-label" x="384" y="94">允许</text>
      <rect class="node target" x="425" y="76" width="115" height="72" rx="12" />
      <text class="label" x="482" y="106">目标 routine</text><text class="sub" x="482" y="128">允许后才调用</text>
      <path class="reject" d="M275 173 V205 H420" />
      <text class="reject-label" x="348" y="196">拒绝 · 不调用目标</text>
    </svg>

    <!-- 记忆与恢复：事实沉积在日志中，之后重新进入上下文 -->
    <svg v-else-if="kind === 'memory'" class="diagram" viewBox="0 0 560 230" role="img">
      <defs><marker id="hf-arrow-memory" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <rect class="node accent" x="22" y="78" width="135" height="74" rx="12" />
      <text class="label" x="89" y="108">Agent routine</text><text class="sub" x="89" y="131">产生事实</text>
      <rect class="log-layer back" x="230" y="43" width="130" height="110" rx="9" />
      <rect class="log-layer middle" x="220" y="53" width="130" height="110" rx="9" />
      <rect class="node" x="210" y="63" width="130" height="110" rx="9" />
      <text class="label" x="275" y="96">记忆</text><text class="sub" x="275" y="119">EventLog / SQLite</text>
      <path class="log-line" d="M234 137 H316" /><path class="log-line" d="M234 151 H296" />
      <path class="flow" d="M157 115 H210" marker-end="url(#hf-arrow-memory)" />
      <text class="edge-label" x="184" y="98">写入</text>
      <rect class="node" x="415" y="78" width="120" height="74" rx="12" />
      <text class="label" x="475" y="108">后续步骤</text><text class="sub" x="475" y="131">继续任务</text>
      <path class="flow return" d="M340 115 H415" marker-end="url(#hf-arrow-memory)" />
      <text class="edge-label" x="378" y="98">读取 · 恢复</text>
      <text class="registry-note" x="280" y="207">事实不随一次模型请求消失</text>
    </svg>

    <!-- 中断与回收：沿调用形成的父子实例树传播 -->
    <svg v-else class="diagram" viewBox="0 0 560 250" role="img">
      <defs><marker id="hf-arrow-stop" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      <rect class="node stop-node stop-1" x="210" y="15" width="140" height="54" rx="11" />
      <text class="label" x="280" y="48">父 routine</text>
      <path class="stop-flow stop-line-1" d="M245 69 C220 91 170 91 145 116" marker-end="url(#hf-arrow-stop)" />
      <path class="stop-flow stop-line-2" d="M315 69 C340 91 390 91 415 116" marker-end="url(#hf-arrow-stop)" />
      <text class="stop-label" x="190" y="91">stop</text><text class="stop-label" x="370" y="91">stop</text>
      <rect class="node stop-node stop-2" x="75" y="116" width="140" height="54" rx="11" />
      <text class="label" x="145" y="149">子 routine</text>
      <rect class="node stop-node stop-2" x="345" y="116" width="140" height="54" rx="11" />
      <text class="label" x="415" y="149">子 routine</text>
      <path class="stop-flow stop-line-3" d="M145 170 V184" marker-end="url(#hf-arrow-stop)" />
      <path class="stop-flow stop-line-3" d="M415 170 V184" marker-end="url(#hf-arrow-stop)" />
      <rect class="node stop-node stop-3" x="85" y="188" width="120" height="36" rx="9" />
      <text class="label small-label" x="145" y="211">孙 routine</text>
      <rect class="node stop-node stop-3" x="355" y="188" width="120" height="36" rx="9" />
      <text class="label small-label" x="415" y="211">孙 routine</text>
      <text class="stop-caption" x="280" y="243">调用形成父子关系，停止沿同一关系回收整棵实例树</text>
    </svg>
  </div>
</template>

<style scoped>
.feature-diagram { min-width: 0; margin-top: 22px; }
.diagram { display: block; width: 100%; height: auto; overflow: visible; }
.node { fill: var(--surface-solid); stroke: var(--line-2); stroke-width: 1; }
.node.accent { fill: color-mix(in oklab, #a98bfa 8%, var(--surface-solid)); stroke: color-mix(in oklab, #a98bfa 55%, var(--line)); }
.node.target { fill: color-mix(in oklab, #78bda6 5%, var(--surface-solid)); stroke: color-mix(in oklab, #78bda6 45%, var(--line)); }
.label { fill: var(--ink); font-family: var(--font-mono); font-size: 13px; font-weight: 500; text-anchor: middle; }
.small-label { font-size: 12px; }
.sub { fill: var(--ink-4); font-family: var(--font-mono); font-size: 9.5px; text-anchor: middle; }
.edge-label { fill: var(--ink-3); font-family: var(--font-mono); font-size: 10px; text-anchor: middle; }
.registry-note { fill: var(--ink-4); font-size: 10px; text-anchor: middle; }
.flow { fill: none; stroke: #a98bfa; stroke-width: 1.5; stroke-dasharray: 5 7; animation: dash 1.2s linear infinite; }
.flow.return { stroke: #78bda6; }
marker path { fill: #a98bfa; }
#hf-arrow-stop path { fill: #c88e86; }
.paused * { animation-play-state: paused !important; }
.gate { fill: color-mix(in oklab, #a98bfa 7%, var(--surface-solid)); stroke: #a98bfa; stroke-width: 1.2; }
.flow.allow { stroke: #78bda6; }
.allow-label { fill: #8bc9b4; font-size: 11px; text-anchor: middle; }
.reject { fill: none; stroke: #b97770; stroke-width: 1.2; stroke-dasharray: 3 6; }
.reject-label { fill: #c88e86; font-size: 10px; text-anchor: middle; }
.log-layer { fill: var(--surface); stroke: var(--line); }
.log-layer.back { opacity: .45; }
.log-layer.middle { opacity: .7; }
.log-line { fill: none; stroke: var(--line-2); stroke-width: 1; }
.stop-flow { fill: none; stroke: #c88e86; stroke-width: 1.4; stroke-dasharray: 5 7; animation: dash 1.2s linear infinite; }
.stop-line-2 { animation-delay: .35s; }
.stop-line-3 { animation-delay: .7s; }
.stop-node { animation: stop-node 4.8s ease-in-out infinite; }
.stop-node.stop-2 { animation-delay: .45s; }
.stop-node.stop-3 { animation-delay: .9s; }
.stop-label { fill: #c88e86; font-family: var(--font-mono); font-size: 10px; text-anchor: middle; }
.stop-caption { fill: var(--ink-4); font-size: 10.5px; text-anchor: middle; }
@keyframes dash { to { stroke-dashoffset: -24; } }
@keyframes stop-node {
  0%, 18%, 100% { fill: var(--surface-solid); stroke: var(--line-2); }
  42%, 82% { fill: color-mix(in oklab, #c88e86 7%, var(--surface-solid)); stroke: #b97770; }
}
@media (prefers-reduced-motion: reduce) {
  .flow, .stop-flow, .stop-node { animation: none; }
  .flow { stroke-dasharray: none; }
}
</style>
