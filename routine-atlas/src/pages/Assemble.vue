<script setup lang="ts">
/* Agent Harness 是 Routine 的一种应用：在通用运行时上接入模型、组织上下文，
   再让模型通过调用单元行动。本页用一个执行循环与四个工具展示最小形态。 */
import Assembly from '../components/Assembly.vue'
import ActionTrace from '../components/ActionTrace.vue'
</script>

<template>
  <div class="as">
    <div class="page">
      <header class="as-head">
        <span class="eyebrow">装配 · Agent Harness</span>
        <h1 class="as-h">
          让通用单元，<br />
          进入一个<em class="ah-c">智能体</em>
        </h1>
        <p class="as-lede">
          Routine Core 不依赖模型。Agent Harness 在其上接入模型、组织上下文，
          让模型选择并调用 routine，再把行动结果交还下一轮决策。
          这里用一个执行循环与 <code>Read</code>、<code>Grep</code>、<code>Write</code>、<code>Bash</code> 展示最小形态。
        </p>
      </header>

      <Assembly />

      <ActionTrace />

      <p class="as-note">
        这一章讨论 Agent Harness，不是给 routine 增加特殊类型。agent 与工具仍是同形单元，连线只表示调用关系。<br />
        执行循环负责模型与上下文，工具负责读取、检索、写入和命令执行；整个装配产物仍可被其他单元调用。
      </p>
      <RouterLink to="/robot" class="btn btn-solid as-next">继续查看机器人装配 <span class="arw">→</span></RouterLink>
    </div>
  </div>
</template>

<style scoped>
.as { padding: 96px 0 110px; }
.as .page { max-width: 1320px; }

.as-head { max-width: 820px; margin-bottom: 30px; }
.as-h {
  font-size: clamp(28px, 3.2vw, 42px);
  line-height: 1.22; letter-spacing: -0.02em;
  margin: 14px 0 16px; font-weight: 600;
}
/* 标题里那两个 routine 是同一个词，所以同一种排法 —— 只是分属两个位置 */
.as-h em {
  font-style: normal;
  font-family: var(--font-mono); font-size: 0.88em;
  letter-spacing: -0.02em;
}
.as-h .ah-c { color: var(--accent); }
.as-lede {
  font-size: 14px; line-height: 1.9; color: var(--ink-3);
  max-width: 660px;
}
.as-lede code {
  font-family: var(--font-mono); font-size: 0.94em;
  color: var(--ink-2);
  background: var(--surface-2);
  padding: 1px 5px; border-radius: 5px;
}

.as-note {
  margin: 18px 0 0; font-size: 12px; line-height: 1.9; color: var(--ink-4);
  letter-spacing: 0.01em;
}
.as-next { margin-top: 24px; }
</style>
