<script setup lang="ts">
import HarnessComposition from '../components/HarnessComposition.vue'
import HarnessFeatureFlow from '../components/HarnessFeatureFlow.vue'
import { pageNo } from '../router'

const FEATURES = [
  {
    id: 'loop', title: '执行循环',
    feature: '持续接收输入、推进任务，直到收敛或中断。',
    implementation: 'Agent 自身是 routine，执行循环发生在它的 run 中。'
  },
  {
    id: 'context', title: '模型与上下文',
    feature: '组织模型输入，维持当前会话与决策所需的信息。',
    implementation: '上下文进入模型请求，模型输出回到后续步骤；模型也可封装为 routine。'
  },
  {
    id: 'call', title: '能力调用',
    feature: '把模型的行动意图变成真实执行，并带回结果。',
    implementation: '能力注册为 routine，调用结果回到 Agent 的后续上下文。'
  },
  {
    id: 'permission', title: '权限许可',
    feature: '行动前取得允许或拒绝，决定这次调用能否继续。',
    implementation: '许可也是 routine；拒绝时不调用目标 routine。'
  },
  {
    id: 'memory', title: '记忆与恢复',
    feature: '保存会话事实，让后续步骤或重新进入时能够继续。',
    implementation: '记忆可由独立 routine 提供，也可由 Harness 内部的 EventLog、SQLite 承载。'
  },
  {
    id: 'stop', title: '中断与回收',
    feature: '停止当前决策与在途行动，释放不再需要的执行单元。',
    implementation: '停止沿调用产生的父子实例树向下传播。'
  }
] as const
</script>

<template>
  <div class="ha">
    <section class="page ha-head">
      <div class="kicker"><span>Harness · 特点与实现</span><span class="dim">{{ pageNo('/harness') }}</span></div>
      <h1>Harness 的特点，<br /><em>Routine 如何实现。</em></h1>
      <p class="lede">
        Harness 负责一个 Agent 的持续运行；Routine 提供执行、调用与生命周期底座。
        关键不是增加一套特殊概念，而是看每项 Agent 职责如何落进同一个系统。
      </p>
      <div class="shared-base" v-reveal>
        <span class="mono">共同底座</span>
        <strong>注册与发现</strong><i>·</i><strong>create → start → wait</strong><i>·</i><strong>通信与级联停止</strong>
        <p>模型与执行循环可以替换，能力仍通过 routine 创建、调用和回收。</p>
      </div>
    </section>

    <section class="page ha-features" aria-label="Harness 特点与 Routine 实现">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">六个特点 · 对应实现</span>
        <h2>职责属于 Harness，<br />能力复用 Routine。</h2>
        <p class="sec-lede">不是把每个内部对象都做成 routine，而是在需要执行、调用、等待和停止的地方复用同一套机制。</p>
      </div>
      <div class="feature-map">
        <article v-for="(item, i) in FEATURES" :key="item.title" :class="'feature-' + item.id" v-reveal="i * 45">
          <header><span class="mono">0{{ i + 1 }}</span><h3>{{ item.title }}</h3></header>
          <p class="feature-description">{{ item.feature }}</p>
          <HarnessFeatureFlow :kind="item.id" :title="item.title" />
          <p class="feature-note">{{ item.implementation }}</p>
        </article>
      </div>
    </section>

    <section class="page ha-composition">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">具体案例 · 四种实现</span>
        <h2>循环在哪里，<br />状态由谁保管，行动如何发生。</h2>
        <p class="sec-lede">
          下面不是四种产品分类，而是仓库中四套实现的真实结构。链上保留实际注册名与组件名，
          以便直接对照代码。
        </p>
      </div>
      <HarnessComposition />
    </section>

    <section class="page ha-outro" v-reveal>
      <div>
        <span class="eyebrow">实现不同，底座不变</span>
        <h2>Harness 决定个体如何行动，<br />Routine 让这些行动进入系统。</h2>
      </div>
      <div class="outro-side">
        <p>Agent 本身仍是 routine。Flow、Pi、XML 或 Moss 的内部差异，不会把它们变成另一类基础单元。</p>
        <div class="outro-links">
          <RouterLink to="/assemble" class="btn btn-solid">查看最小装配 <span class="arw">→</span></RouterLink>
          <RouterLink to="/robot" class="text-link">进入机器人实现 <span>→</span></RouterLink>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ha { padding-bottom: 40px; }
.ha-head { padding-top: clamp(64px, 9vw, 110px); padding-bottom: clamp(38px, 5vw, 60px); }
.kicker { display: flex; justify-content: space-between; color: var(--ink-4); font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; }
.ha-head h1 { max-width: 1040px; margin-top: 22px; font-size: clamp(32px, 4.6vw, 60px); line-height: 1.2; letter-spacing: -.04em; font-weight: 520; }
.ha-head h1 em { color: #b9a3f4; font-style: normal; }
.ha-head .lede { max-width: 810px; margin-top: 26px; }
.shared-base { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; margin-top: 36px; padding: 19px 0; border-top: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); }
.shared-base > span { margin-right: 8px; color: #a98bfa; font-size: 9px; letter-spacing: .08em; }
.shared-base strong { color: var(--ink-2); font-family: var(--font-mono); font-size: 10.5px; font-weight: 450; }
.shared-base i { color: var(--ink-4); font-style: normal; }
.shared-base p { flex-basis: 100%; margin: 1px 0 0; color: var(--ink-4); font-size: 11px; line-height: 1.8; }
.ha-features { padding-top: clamp(32px, 4vw, 50px); }
.feature-map { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 18px; }
.feature-loop, .feature-permission, .feature-stop { grid-column: span 7; }
.feature-context, .feature-call, .feature-memory { grid-column: span 5; }
.feature-map article { min-width: 0; padding: clamp(24px, 2.6vw, 32px); border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.feature-map article > header { display: flex; align-items: baseline; gap: 14px; }
.feature-map article > header > span { color: #a98bfa; font-size: 11px; }
.feature-map h3 { color: var(--ink); font-size: 21px; font-weight: 520; letter-spacing: -.02em; }
.feature-description { min-height: 50px; margin-top: 13px; color: var(--ink-3); font-size: 14px; line-height: 1.8; }
.feature-note { margin-top: 21px; padding-top: 17px; border-top: 1px solid var(--line); color: var(--ink-3); font-size: 12.5px; line-height: 1.8; }
.ha-composition { padding-top: clamp(64px, 8vw, 100px); }
.sec-head { max-width: 780px; margin-bottom: 34px; }
.sec-head h2 { margin: 14px 0 18px; }
.sec-lede { color: var(--ink-3); font-size: 14px; line-height: 1.85; }
.ha-outro { display: grid; grid-template-columns: 1.4fr 1fr; gap: 50px; align-items: end; padding-top: 38px; padding-bottom: 38px; border-top: 1px solid var(--line-2); border-bottom: 1px solid var(--line-2); }
.ha-outro h2 { margin-top: 16px; font-size: clamp(22px, 2.8vw, 34px); line-height: 1.4; }
.outro-side p { color: var(--ink-3); font-size: 13px; line-height: 1.85; }
.outro-links { display: flex; flex-wrap: wrap; align-items: center; gap: 18px 24px; margin-top: 22px; }
.text-link { color: var(--ink-3); font-size: 12px; }
.text-link:hover { color: var(--ink); }
.text-link span { margin-left: 5px; color: var(--accent); }
@media (max-width: 1050px) {
  .feature-map { grid-template-columns: 1fr; }
  .feature-map article { grid-column: auto; }
  .feature-description { min-height: 0; }
}
@media (max-width: 900px) {
  .ha-outro { grid-template-columns: 1fr; gap: 24px; }
}
@media (max-width: 700px) {
  .feature-map h3 { font-size: 19px; }
}
@media (max-width: 540px) {
  .ha-head h1 { font-size: 32px; }
  .shared-base { gap: 9px 10px; }
  .shared-base > span { flex-basis: 100%; }
}
</style>
