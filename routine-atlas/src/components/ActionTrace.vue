<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

type Trace = {
  id: string
  name: string
  input: string
  output: string
  duration: string
}

const TRACES: Trace[] = [
  { id: '#13', name: 'Read', input: "path: 'src/agent.ts'", output: '已读取文件内容', duration: '18 ms' },
  { id: '#14', name: 'Grep', input: "pattern: 'call'", output: '找到 4 处匹配', duration: '11 ms' },
  { id: '#15', name: 'Write', input: "path: 'src/agent.ts'", output: '文件写入完成', duration: '23 ms' },
  { id: '#16', name: 'Bash', input: "command: 'npm test'", output: '测试通过', duration: '842 ms' }
]

const cursor = ref(0)
const hovered = ref<number | null>(null)
const rootRef = ref<HTMLElement>()
let timer: ReturnType<typeof setInterval> | undefined
let observer: IntersectionObserver | undefined
let holding = false

const selected = computed(() => hovered.value ?? Math.min(cursor.value, TRACES.length - 1))
const current = computed(() => TRACES[selected.value])
const running = computed(() => (cursor.value < TRACES.length ? cursor.value : -1))
const visible = computed(() => Math.min(cursor.value + 1, TRACES.length))

function stopTimer() {
  clearInterval(timer)
  timer = undefined
}
function tick() {
  if (cursor.value < TRACES.length) {
    cursor.value += 1
    return
  }
  // 全部记录已显示，停留一拍后从头再来。
  if (!holding) holding = true
  else { holding = false; cursor.value = 0 }
}
function startLoop() {
  if (timer !== undefined) return
  timer = setInterval(tick, 1400)
}
function inspect(index: number) {
  hovered.value = index
}
function release() {
  hovered.value = null
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    cursor.value = TRACES.length
    return
  }
  observer = new IntersectionObserver(([entry]) => {
    if (entry.isIntersecting) startLoop()
    else stopTimer()
  }, { threshold: .25 })
  if (rootRef.value) observer.observe(rootRef.value)
})
onBeforeUnmount(() => {
  stopTimer()
  observer?.disconnect()
})
</script>

<template>
  <section ref="rootRef" class="action-trace" aria-labelledby="trace-title">
    <header class="trace-head">
      <div>
        <span class="eyebrow">行动轨迹 · 记录复查</span>
        <h2 id="trace-title">每一步，都是一次 routine 调用。</h2>
        <p>逐条查看输入与返回，记录按原顺序自动回放。</p>
      </div>
      <span class="trace-badge mono">记录示意</span>
    </header>

    <div class="trace-demo">
      <div class="trace-toolbar">
        <span class="mono">agent #12 · 一次任务</span>
        <span class="mono">只读取已有记录</span>
      </div>

      <div class="trace-body">
        <ol class="trace-list" aria-label="routine 调用记录">
          <li v-for="(trace, index) in TRACES" :key="trace.id" v-show="index < visible">
            <div
              class="trace-row" :class="{ selected: selected === index, active: running === index }"
              @mouseenter="inspect(index)" @mouseleave="release"
            >
              <span class="trace-no mono">0{{ index + 1 }}</span>
              <span class="trace-name mono">{{ trace.name }}</span>
              <span class="trace-id mono">{{ trace.id }}</span>
              <span class="trace-duration mono">{{ trace.duration }}</span>
              <span class="trace-status mono" :class="{ running: running === index }">
                {{ running === index ? 'running' : 'done' }}
              </span>
            </div>
          </li>
        </ol>

        <aside class="trace-inspector">
          <span class="eyebrow">单步复查</span>
          <div class="trace-inspector-title">
            <strong class="mono">{{ current.name }}</strong>
            <span class="mono">{{ current.id }}</span>
          </div>
          <dl>
            <div><dt>调用者</dt><dd class="mono">agent #12</dd></div>
            <div><dt>输入</dt><dd class="mono">{{ current.input }}</dd></div>
            <div><dt>返回</dt><dd>{{ selected === running ? '执行中…' : current.output }}</dd></div>
            <div><dt>耗时</dt><dd class="mono">{{ current.duration }}</dd></div>
          </dl>
        </aside>
      </div>
      <p class="trace-note">回放只重现已有记录，不会再次调用工具或修改文件。</p>
    </div>
  </section>
</template>

<style scoped>
.action-trace { margin-top: 62px; padding-top: 34px; border-top: 1px solid var(--line); }
.trace-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; }
.trace-head h2 { margin: 12px 0 9px; font-size: clamp(22px, 2.4vw, 30px); font-weight: 550; letter-spacing: -.015em; }
.trace-head p { margin: 0; color: var(--ink-3); font-size: 13px; line-height: 1.8; }
.trace-badge { flex: none; padding: 5px 8px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink-4); font-size: 9px; }
.trace-demo { margin-top: 22px; overflow: hidden; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); }
.trace-toolbar { min-height: 52px; padding: 9px 15px; display: flex; align-items: center; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); color: var(--ink-3); font-size: 10px; }
.trace-toolbar > span:last-child { color: var(--ink-4); }
.trace-body { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(260px, .8fr); min-height: 274px; }
.trace-list { list-style: none; margin: 0; padding: 16px; display: flex; flex-direction: column; gap: 7px; }
.trace-row { width: 100%; min-height: 48px; padding: 0 13px; display: grid; grid-template-columns: 28px 1fr 52px 64px 58px; align-items: center; gap: 9px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-solid); text-align: left; animation: trace-in .35s ease both; }
.trace-row.selected { border-color: color-mix(in oklab, var(--accent) 48%, var(--line)); background: color-mix(in oklab, var(--accent) 5%, var(--surface-solid)); }
.trace-no, .trace-id, .trace-duration { color: var(--ink-4); font-size: 9px; }
.trace-name { color: var(--ink-1); font-size: 12px; }
.trace-duration, .trace-status { text-align: right; }
.trace-status { color: var(--accent); font-size: 9px; }
.trace-status.running { color: #F0B24A; }
.trace-inspector { padding: 22px; border-left: 1px solid var(--line); background: color-mix(in oklab, var(--bg-deep) 42%, transparent); }
.trace-inspector-title { margin: 14px 0 19px; display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.trace-inspector-title strong { color: var(--ink-1); font-size: 18px; font-weight: 500; }
.trace-inspector-title span { color: var(--ink-4); font-size: 10px; }
.trace-inspector dl { margin: 0; display: grid; gap: 13px; }
.trace-inspector dl > div { display: grid; grid-template-columns: 48px minmax(0, 1fr); gap: 12px; }
.trace-inspector dt { color: var(--ink-4); font-size: 10px; }
.trace-inspector dd { margin: 0; color: var(--ink-2); font-size: 11px; line-height: 1.6; overflow-wrap: anywhere; }
.trace-inspector dd.mono { font-size: 10px; }
.trace-note { margin: 0; padding: 11px 16px; border-top: 1px solid var(--line); color: var(--ink-4); font-size: 10px; line-height: 1.6; }
@keyframes trace-in { from { opacity: 0; transform: translateY(7px); } to { opacity: 1; transform: translateY(0); } }
@media (max-width: 760px) {
  .trace-body { grid-template-columns: 1fr; }
  .trace-inspector { border-top: 1px solid var(--line); border-left: 0; }
  .trace-row { grid-template-columns: 24px 1fr 44px 52px; }
  .trace-duration { display: none; }
}
@media (max-width: 480px) {
  .trace-head { display: block; }
  .trace-badge { display: inline-block; margin-top: 14px; }
  .trace-toolbar { align-items: flex-start; flex-direction: column; }
  .trace-list { padding: 10px; }
  .trace-row { padding: 0 9px; gap: 6px; }
}
@media (prefers-reduced-motion: reduce) {
  .trace-row { animation: none; }
}
</style>
