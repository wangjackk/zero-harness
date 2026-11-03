<script setup lang="ts">
import ModuleConflictPk from '../components/ModuleConflictPk.vue'
import ModuleTreeDemo from '../components/ModuleTreeDemo.vue'
import { pageNo } from '../router'
</script>

<template>
  <div class="mods">
    <header class="page mods-head">
      <div class="mods-kicker">
        <span>模块系统 · Module System</span>
        <span class="dim">{{ pageNo('/modules') }}</span>
      </div>
      <h1>声明所需，<br /><em>协调执行。</em></h1>
      <p class="lede">
        模块系统让 routine 声明需要占用什么，让内核识别执行冲突，并在冲突时按优先级仲裁。
        它是 Routine Core 的通用机制，不依赖模型、Agent 或机器人。
      </p>
    </header>

    <section class="page mods-tree" aria-label="模块树与冲突范围">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">模块树 · 占用与冲突</span>
        <h2>模块描述占用什么，<br />routine 描述谁在执行。</h2>
        <p class="sec-lede">模块是被占用的资源节点，不是另一种 routine，也不是命令实例。系统用模块树组织这些节点，用另一棵命令实例树管理调用与生命周期。</p>
      </div>
      <div class="tree-layout">
        <div class="tree-card body-card" v-reveal>
          <header><span class="mono">MODULE TREE / CONFLICT CONE</span><small>人体概念示例 · 非系统固定结构</small></header>
          <ModuleTreeDemo />
          <footer><strong>占用右臂，就会与右手冲突；左臂与右臂可以并行。</strong>模块树可以动态增删节点，挂载节点本身不等于占用。</footer>
        </div>
        <div class="tree-rules" v-reveal="70">
          <article>
            <span class="mono">01 · 声明</span>
            <h3><code>modules()</code> 给出占用声明</h3>
            <p>声明是“类型 + 入参”的纯函数，默认返回空集。创建实例时，声明随 created 回执交给内核与父调用方。</p>
          </article>
          <article>
            <span class="mono">02 · 冲突锥</span>
            <h3>祖先、自己、后代共同参与检测</h3>
            <p>每个声明模块都有祖先、自身、后代构成的冲突锥。一次声明多个模块时检查整组冲突，任一冲突都不能忽略；自己或祖先已经占用时，允许父子共占。</p>
          </article>
          <article>
            <span class="mono">03 · 占用</span>
            <h3>启动占用，也可以运行中申请</h3>
            <p><code>start</code> 会尝试占用声明的模块；运行中还可通过 <code>acquire()</code> 申请、<code>release()</code> 释放，停止时由内核全量回收。</p>
          </article>
        </div>
      </div>
    </section>

    <section class="page mods-priority" aria-label="模块冲突时的优先级仲裁">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">优先级 · 只在冲突时仲裁</span>
        <h2>不是全局排序，<br />也不隐式排队。</h2>
        <p class="sec-lede">没有模块冲突时，调用直接启动。一次声明多个模块时，<code>priority</code> 面对的是全部冲突持有者，而不是把每个模块拆成互不相干的单独裁决。</p>
      </div>
      <ModuleConflictPk v-reveal />
      <div class="priority-rules" v-reveal>
        <article><span class="rule-sign allow">✓</span><div><h3>没有冲突</h3><p>直接启动或占用，不需要比较优先级。</p></div></article>
        <article><span class="rule-sign evict">↑</span><div><h3>严格高于全部冲突持有者</h3><p>驱逐这些第三方持有者，再启动或占用。</p></div></article>
        <article><span class="rule-sign reject">×</span><div><h3>平级或更低</h3><p>启动或申请失败并返回错误，不在内核里隐式等待。</p></div></article>
      </div>
    </section>

    <section class="page mods-example" aria-label="机器人 wheels 模块与优先级示例">
      <div class="sec-head" v-reveal>
        <span class="eyebrow">应用示例 · 机器人底盘</span>
        <h2>同一套通用机制，<br />协调同一套底盘。</h2>
        <p class="sec-lede">运动、旋转与跟踪等 routine 都声明 <code>wheels</code>。这里的 5、10、15 是机器人业务选择的执行档位，不是 Routine Core 固定的等级制度。</p>
      </div>
      <div class="example-layout">
        <div class="declaration-card" v-reveal>
          <span class="mono">DECLARATION / car motion</span>
          <pre><code>@classmethod
def modules(cls, kwargs=None):
    return Modules(['wheels'])</code></pre>
          <p>声明只表达“需要底盘”。移动、转向还是跟踪，仍由各 routine 自己的执行实现决定。</p>
        </div>
        <div class="wheels-example" v-reveal="70">
          <header><div><span class="mono">MODULE / wheels</span><h3>同一底盘，三种执行档位</h3></div><i></i></header>
          <div class="priority-row"><span class="priority-value low">05</span><div><code>car/gaze</code><p>后台注视，为显式动作让位</p></div><small>后台</small></div>
          <div class="priority-row"><span class="priority-value normal">10</span><div><code>car/move · car/track</code><p>普通动作使用默认优先级</p></div><small>默认</small></div>
          <div class="priority-row"><span class="priority-value high">15</span><div><code>car/wake_track</code><p>唤醒响应可抢占普通动作与注视</p></div><small>抢占</small></div>
          <footer><strong>5 &lt; 10 &lt; 15</strong><span>只有严格更高，才可以抢占</span></footer>
        </div>
      </div>
      <div class="core-application" v-reveal>
        <p><strong>机制属于 Core，档位属于应用。</strong>同样的模块系统也可以协调其他软件或硬件资源，不限于机器人。</p>
        <RouterLink to="/robot" class="text-link">查看机器人实现 <span>→</span></RouterLink>
      </div>
    </section>
  </div>
</template>

<style scoped>
.mods { padding-bottom: 56px; }
.mods-head { padding-top: clamp(64px, 9vw, 110px); padding-bottom: clamp(56px, 7vw, 88px); }
.mods-kicker { display: flex; justify-content: space-between; color: var(--ink-4); font-family: var(--font-mono); font-size: 10px; letter-spacing: .1em; }
.mods-head h1 { max-width: 900px; margin-top: 22px; font-size: clamp(36px, 5vw, 64px); line-height: 1.16; letter-spacing: -.04em; font-weight: 520; }
.mods-head h1 em { color: var(--accent); font-style: normal; }
.mods-head .lede { max-width: 790px; margin-top: 26px; }
.sec-head { max-width: 820px; margin-bottom: 32px; }
.sec-head h2 { margin: 14px 0 18px; }
.sec-lede { color: var(--ink-3); font-size: 14px; line-height: 1.85; }
code { font-family: var(--font-mono); }
.sec-lede code, .tree-rules code { color: var(--ink-2); font-size: .92em; }
.mods-tree, .mods-priority, .mods-example { padding-bottom: clamp(72px, 9vw, 116px); }
.tree-layout { display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1.05fr); gap: 22px; align-items: stretch; }
.tree-card { overflow: hidden; border: 1px solid var(--line-2); border-radius: var(--r-lg); background: color-mix(in oklab, var(--bg-deep) 65%, var(--surface-solid)); }
.tree-card > header { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid var(--line); color: var(--accent); font-size: 9px; letter-spacing: .08em; }
.tree-card > header small { color: var(--ink-4); font-size: 10px; letter-spacing: 0; }
.body-card { background: radial-gradient(circle at 50% 42%, color-mix(in oklab, var(--accent) 6%, transparent), transparent 48%), var(--surface); }
.tree-card > footer { padding: 15px 20px; border-top: 1px solid var(--line); color: var(--ink-4); font-size: 11px; line-height: 1.75; }
.tree-card > footer strong { display: block; margin-bottom: 4px; color: var(--ink-2); font-weight: 520; }
.tree-rules { display: grid; gap: 12px; }
.tree-rules article { display: grid; grid-template-columns: 104px 1fr; column-gap: 20px; padding: 20px 22px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.tree-rules article > span { grid-row: 1 / 3; padding-top: 4px; color: var(--accent); font-size: 9px; letter-spacing: .07em; }
.tree-rules h3 { color: var(--ink); font-size: 16px; font-weight: 520; line-height: 1.5; }
.tree-rules h3 code { color: var(--accent); font-size: .9em; }
.tree-rules p { margin-top: 8px; color: var(--ink-3); font-size: 12.5px; line-height: 1.8; }
.mods-priority { padding-top: clamp(54px, 7vw, 82px); border-top: 1px solid var(--line-2); }
.priority-rules { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.priority-rules article { display: flex; gap: 17px; padding: 24px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.rule-sign { display: grid; place-items: center; flex: 0 0 36px; width: 36px; height: 36px; border: 1px solid currentColor; border-radius: 50%; font-family: var(--font-mono); font-size: 16px; }
.rule-sign.allow { color: #62bfa4; }
.rule-sign.evict { color: var(--accent); }
.rule-sign.reject { color: #c48686; }
.priority-rules h3 { color: var(--ink); font-size: 16px; font-weight: 520; line-height: 1.5; }
.priority-rules p { margin-top: 9px; color: var(--ink-3); font-size: 12.5px; line-height: 1.8; }
.mods-example { padding-top: clamp(54px, 7vw, 82px); border-top: 1px solid var(--line-2); }
.example-layout { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
.declaration-card { padding: 27px; border: 1px solid var(--line); border-radius: var(--r-lg); background: var(--surface); }
.declaration-card > span { color: var(--accent); font-size: 9px; letter-spacing: .09em; }
.declaration-card pre { margin: 24px 0; padding: 22px; overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: color-mix(in oklab, var(--bg-deep) 65%, var(--surface-solid)); }
.declaration-card pre code { color: var(--ink-2); font-size: 12.5px; line-height: 1.85; }
.declaration-card p { color: var(--ink-3); font-size: 13px; line-height: 1.85; }
.wheels-example { display: flex; flex-direction: column; padding: 26px; border: 1px solid color-mix(in oklab, var(--accent) 30%, var(--line)); border-radius: var(--r-lg); background: radial-gradient(circle at 90% 0, color-mix(in oklab, var(--accent) 8%, transparent), transparent 50%), var(--surface); }
.wheels-example > header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 18px; }
.wheels-example > header span { color: var(--accent); font-size: 9px; letter-spacing: .09em; }
.wheels-example > header h3 { margin-top: 10px; color: var(--ink); font-size: 20px; font-weight: 520; }
.wheels-example > header i { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 18px var(--accent); }
.priority-row { display: grid; grid-template-columns: 50px 1fr auto; align-items: center; gap: 15px; padding: 19px 0; border-top: 1px solid var(--line); }
.priority-value { display: grid; place-items: center; width: 44px; height: 44px; border: 1px solid currentColor; border-radius: 50%; font-family: var(--font-mono); font-size: 14px; }
.priority-value.low { color: #7f91a5; }
.priority-value.normal { color: #78b9d0; }
.priority-value.high { color: #b9a3f4; box-shadow: 0 0 20px color-mix(in oklab, #a98bfa 14%, transparent); }
.priority-row code { color: var(--ink-2); font-size: 12px; }
.priority-row p { margin-top: 6px; color: var(--ink-3); font-size: 11.5px; line-height: 1.65; }
.priority-row small { color: var(--ink-4); font-size: 10px; }
.wheels-example > footer { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin-top: auto; padding-top: 18px; border-top: 1px solid var(--line); }
.wheels-example > footer strong { color: var(--accent); font-family: var(--font-mono); font-size: 13px; font-weight: 500; }
.wheels-example > footer span { color: var(--ink-3); font-size: 11px; }
.core-application { display: flex; align-items: center; justify-content: space-between; gap: 28px; margin-top: 22px; padding: 20px 24px; border-left: 2px solid var(--accent); background: color-mix(in oklab, var(--accent) 4%, transparent); }
.core-application p { color: var(--ink-3); font-size: 13px; line-height: 1.85; }
.core-application strong { color: var(--ink-2); font-weight: 550; }
.core-application .text-link { flex-shrink: 0; }
@media (max-width: 900px) {
  .tree-layout, .example-layout { grid-template-columns: 1fr; }
  .priority-rules { grid-template-columns: 1fr; }
}
@media (max-width: 600px) {
  .mods-head h1 { font-size: 36px; }
  .tree-rules article { grid-template-columns: 1fr; gap: 9px; padding: 20px; }
  .tree-rules article > span { grid-row: auto; }
  .priority-rules article, .declaration-card, .wheels-example { padding: 21px; }
  .priority-row { grid-template-columns: 44px 1fr; gap: 12px; }
  .priority-row small { display: none; }
  .core-application { align-items: flex-start; flex-direction: column; gap: 14px; }
}
</style>
