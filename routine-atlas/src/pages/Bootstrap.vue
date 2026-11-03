<script setup lang="ts">
import { pageNo } from '../router'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { BEATS, BOOT_AXIOM } from '../data/bootstrap'
import { TOTAL_NAMES } from '../data/names'

const idx = ref(0)
let timer: number | null = null

const beat = computed(() => BEATS[idx.value])
const logs = computed(() => BEATS.slice(0, idx.value + 1).flatMap((b, bi) => b.logs.map((l, li) => ({ ...l, k: `${bi}-${li}` }))))

function stop() {
  if (timer) window.clearTimeout(timer)
  timer = null
}

function schedule() {
  stop()
  timer = window.setTimeout(() => {
    idx.value = idx.value >= BEATS.length - 1 ? 0 : idx.value + 1
    schedule()
  }, 2600)
}

onMounted(() => {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
  schedule()
})
onBeforeUnmount(stop)
</script>

<template>
  <div class="bs">
    <section class="page page-head">
      <div class="kicker">
        <span>自举 · Bootstrap</span>
        <span class="dim">{{ pageNo('/bootstrap') }}</span>
      </div>
      <h1>从系统自举，<br />到 AI 自迭代。</h1>
      <p class="lede">
        系统可以从最小起点建立自身，也可以在运行中持续扩展与更新能力。
        自举让它开始，自迭代让它继续生长。
      </p>
    </section>

    <section class="page">
      <!-- ------------------------------ 时间轴 ------------------------------ -->
      <div class="tl-wrap">
        <div class="tl" :style="{ '--p': (idx / (BEATS.length - 1)) * 100 + '%' }">
          <div class="tl-line"><i /></div>
          <span
            v-for="(b, i) in BEATS"
            :key="b.id"
            class="tl-i"
            :class="{ on: i === idx, done: i < idx }"
            :aria-current="i === idx ? 'step' : undefined"
          >
            <span class="tl-no mono">{{ b.no }}</span>
            <span class="tl-dot" />
            <span class="tl-t">{{ b.title }}</span>
          </span>
        </div>
      </div>

      <!-- ------------------------------ 主体 ------------------------------ -->
      <div class="grid">
        <div class="left">
          <span class="eyebrow">阶段 {{ beat.no }}</span>
          <h2 class="b-title">{{ beat.title }}</h2>
          <p class="b-claim">{{ beat.claim }}</p>
          <p class="b-detail">{{ beat.detail }}</p>

          <div v-if="beat.metric?.length" class="b-metrics">
            <div v-for="m in beat.metric" :key="m.k" class="b-metric">
              <span class="bm-v">{{ m.v }}</span>
              <span class="bm-k">{{ m.k }}</span>
            </div>
          </div>
        </div>

        <div class="term">
          <div class="term-head">
            <span class="dots"><i /><i /><i /></span>
            <span class="term-title mono">kernel · zero-harness runtime</span>
            <span class="term-live mono"><i />live</span>
          </div>
          <div class="term-body">
            <TransitionGroup name="log" tag="div" class="term-lines">
              <div v-for="l in logs" :key="l.k" class="ln" :class="l.kind">
                <span class="pr mono">{{ l.kind === 'ok' ? '✓' : l.kind === 'warn' ? '!' : l.kind === 'dim' ? '·' : '›' }}</span>
                <span class="tx mono">{{ l.text }}</span>
              </div>
            </TransitionGroup>
            <span class="caret" />
          </div>
          <div class="term-foot mono">
            示意日志 —— 格式取自 <code>routines/loader.py</code> 与 watcher 的 _log 调用
          </div>
        </div>
      </div>

      <!-- ------------------------------ 闭合 ------------------------------ -->
      <div class="loop-note">
        <span class="loop-ico">↺</span>
        <p>
          阶段 03 注册了 <code>routines_watcher</code> 等注册名。
          完成该阶段后，<b>代码改动无需人工重启即可加载</b>，系统由此可以在运行中更新能力。
        </p>
      </div>
    </section>

    <section class="page self-iteration">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">自迭代 · Self-iteration</span>
        <h2>能力，<br />可以在运行中生长。</h2>
        <p>Routine 不只承载既定能力，也让新的能力进入系统、参与组合，并在后续调用中持续演进。</p>
      </div>
      <div class="iteration-flow" v-reveal="60" aria-label="能力自迭代闭环">
        <article><span>01 · 目标</span><h3>需要一种新能力</h3><p>任务提出新的能力需求，或已有 routine 出现值得改进的地方。</p></article>
        <i>→</i>
        <article><span>02 · 创作</span><h3>编写或修改 routine</h3><p>人或 Agent 形成候选实现，并给出用于验收的输入与预期结果。</p></article>
        <i>→</i>
        <article><span>03 · 加载</span><h3>更新系统能力</h3><p>候选实现经过必要检查后进入系统，原有注册名可以指向新的实现。</p></article>
        <i>→</i>
        <article><span>04 · 生长</span><h3>进入下一次调用</h3><p>注册名对应的新实现进入系统，成为后续任务可以使用的能力。</p></article>
      </div>
      <p class="iteration-note" v-reveal>
        无论是人写下的工具，还是 Agent 生成的新能力，最终都以 routine 的形式进入同一个系统。
      </p>
    </section>

    <!-- ------------------------------ 公理 ------------------------------ -->
    <section class="section axiom">
      <div class="page">
        <div class="ax" v-reveal>
          <span class="eyebrow">结论</span>
          <p class="ax-q">{{ BOOT_AXIOM }}</p>
          <div class="ax-row">
            <RouterLink to="/topology" class="btn btn-solid">查看拓扑 <span class="arw">→</span></RouterLink>
            <RouterLink to="/registry" class="btn">查看注册清单 <span class="arw">→</span></RouterLink>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page-head h1 { margin-top: 16px; }
.page-head .lede { margin-top: 22px; }

/* ------------------------------ 时间轴 ------------------------------ */
.tl-wrap { display: flex; align-items: flex-start; gap: 24px; }
.tl {
  position: relative;
  flex: 1;
  display: grid;
  grid-template-columns: repeat(8, 1fr);
  gap: 4px;
  padding-top: 6px;
}
.tl-line {
  position: absolute;
  left: 6%;
  right: 6%;
  top: 52px;
  height: 1px;
  background: var(--line-2);
}
.tl-line i {
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--p);
  background: linear-gradient(90deg, #a98bfa, #3ed2e0);
  transition: width 0.5s var(--ease);
}
.tl-i {
  position: relative;
  display: grid;
  justify-items: center;
  gap: 6px;
  padding: 0 2px 8px;
  color: var(--ink-4);
  transition: color 0.24s var(--ease);
}
.tl-no { font-size: 9px; letter-spacing: 0.08em; }
.tl-dot {
  width: 9px;
  height: 9px;
  border-radius: 99px;
  border: 1px solid var(--line-3);
  background: var(--bg);
  transition: background 0.3s var(--ease), border-color 0.3s var(--ease), transform 0.3s var(--ease), box-shadow 0.3s var(--ease);
}
.tl-t { font-size: 11.5px; letter-spacing: -0.01em; white-space: nowrap; }
.tl-i.done { color: var(--ink-3); }
.tl-i.done .tl-dot { background: var(--line-3); border-color: var(--line-3); }
.tl-i.on { color: var(--ink); }
.tl-i.on .tl-dot {
  background: #a98bfa;
  border-color: #a98bfa;
  transform: scale(1.35);
  box-shadow: 0 0 0 4px color-mix(in oklab, #a98bfa 16%, transparent), 0 0 18px color-mix(in oklab, #a98bfa 55%, transparent);
}
.tl-i.on .tl-t { font-weight: 500; }

/* ------------------------------ 主体 ------------------------------ */
.grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.15fr);
  gap: clamp(24px, 4vw, 56px);
  margin-top: clamp(32px, 5vw, 56px);
  align-items: start;
}
.left { display: grid; gap: 16px; align-content: start; }
.b-title { font-size: clamp(28px, 3.4vw, 40px); letter-spacing: -0.03em; }
.b-claim { font-size: 17px; color: var(--ink); letter-spacing: -0.01em; }
.b-detail { font-size: 13.5px; line-height: 1.85; color: var(--ink-3); max-width: 54ch; }
.b-metrics { display: flex; gap: 34px; margin-top: 10px; padding-top: 18px; border-top: 1px solid var(--line); }
.b-metric { display: grid; gap: 2px; }
.bm-v { font-size: 22px; font-weight: 540; letter-spacing: -0.03em; }
.bm-k { font-size: 10.5px; color: var(--ink-4); }

/* ------------------------------ 终端 ------------------------------ */
.term {
  border: 1px solid var(--line-2);
  border-radius: 12px;
  overflow: hidden;
  background: #07080b;
  box-shadow: var(--shadow-2);
  min-height: 380px;
  display: flex;
  flex-direction: column;
}
html[data-theme='light'] .term { background: #0d1016; }
.term-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(255, 255, 255, 0.02);
}
.dots { display: flex; gap: 4px; }
.dots i { width: 7px; height: 7px; border-radius: 99px; background: rgba(255, 255, 255, 0.14); }
.dots i:first-child { background: #f4798f66; }
.dots i:nth-child(2) { background: #f0b24a66; }
.dots i:nth-child(3) { background: #46d8a066; }
.term-title { font-size: 10px; color: #8b95a6; letter-spacing: 0.03em; }
.term-live { margin-left: auto; display: flex; align-items: center; gap: 5px; font-size: 8.5px; color: #6ee7b7; letter-spacing: 0.12em; text-transform: uppercase; }
.term-live i { width: 4px; height: 4px; border-radius: 99px; background: #34d399; animation: pulse 1.8s ease-in-out infinite; }

.term-body {
  position: relative;
  flex: 1;
  padding: 14px 14px 22px;
  overflow: auto;
  max-height: 420px;
}
.term-lines { display: grid; gap: 5px; }
.ln { display: grid; grid-template-columns: 12px 1fr; gap: 8px; align-items: baseline; }
.pr { font-size: 10px; color: #4b5563; }
.tx { font-size: 11.2px; line-height: 1.6; color: #9aa4b4; word-break: break-word; }
.ln.ok .pr { color: #34d399; }
.ln.ok .tx { color: #c8f7dd; }
.ln.sys .pr { color: #7aa2ff; }
.ln.sys .tx { color: #dbe4ff; }
.ln.dim .tx { color: #6b7484; }
.ln.warn .pr { color: #f0b24a; }
.ln.warn .tx { color: #f7d9a4; }

.caret {
  display: inline-block;
  width: 6px;
  height: 12px;
  margin-top: 6px;
  background: #7aa2ff;
  opacity: 0.85;
  animation: blink 1.1s steps(2, start) infinite;
}
@keyframes blink { 50% { opacity: 0; } }

.term-foot {
  padding: 8px 12px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  font-size: 9px;
  color: #58606e;
  letter-spacing: 0.02em;
}
.term-foot code { color: #7d8798; }

.log-enter-active { transition: opacity 0.4s var(--ease), transform 0.4s var(--ease); }
.log-enter-from { opacity: 0; transform: translateY(6px); }

/* ------------------------------ 闭合 ------------------------------ */
.loop-note {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  margin-top: 26px;
  padding: 16px 18px;
  border: 1px dashed var(--line-2);
  border-radius: 10px;
  background: var(--surface);
}
.loop-ico { color: #a98bfa; font-size: 14px; line-height: 1.6; }
.loop-note p { font-size: 12.5px; line-height: 1.8; color: var(--ink-3); max-width: 80ch; }
.loop-note b { color: var(--ink-2); }
.loop-note code { font-size: 11.5px; color: var(--ink-2); }

/* ------------------------------ AI 自迭代 ------------------------------ */
.self-iteration { padding-top: clamp(64px, 8vw, 100px); }
.sec-head { max-width: 790px; }
.sec-head h2 { margin: 14px 0 18px; }
.sec-head p { color: var(--ink-3); font-size: 14px; line-height: 1.85; }
.iteration-flow { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr; gap: 14px; align-items: center; margin-top: 32px; }
.iteration-flow article { height: 100%; padding: 22px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.iteration-flow article > span { color: var(--accent); font-family: var(--font-mono); font-size: 9px; }
.iteration-flow h3 { margin-top: 22px; color: var(--ink); font-size: 16px; font-weight: 520; }
.iteration-flow p { margin-top: 10px; color: var(--ink-3); font-size: 12px; line-height: 1.8; }
.iteration-flow > i { color: var(--ink-4); font-style: normal; }
.iteration-note { margin-top: 18px; padding-left: 15px; border-left: 2px solid var(--accent); color: var(--ink-3); font-size: 12px; line-height: 1.85; }
.iteration-note strong { color: var(--ink-2); font-weight: 520; }

/* ------------------------------ 公理 ------------------------------ */
.axiom { border-top: 1px solid var(--line); margin-top: clamp(48px, 7vw, 96px); }
.ax { display: grid; gap: 22px; max-width: 900px; }
.ax-q {
  font-size: clamp(22px, 3vw, 34px);
  line-height: 1.5;
  letter-spacing: -0.03em;
  color: var(--ink);
  font-weight: 420;
}
.ax-row { display: flex; gap: 10px; flex-wrap: wrap; }

@media (max-width: 1000px) {
  .grid { grid-template-columns: 1fr; }
  .tl-wrap { flex-direction: column; gap: 8px; }
  .tl { width: 100%; }
  .tl-t { font-size: 9.5px; }
  .iteration-flow { grid-template-columns: 1fr; }
  .iteration-flow > i { display: none; }
}
@media (max-width: 720px) {
  .tl-no, .tl-t { display: none; }
  .tl-i { padding-bottom: 2px; }
  .tl-line { top: 20px; }
  .b-metrics { gap: 20px; }
}
</style>
