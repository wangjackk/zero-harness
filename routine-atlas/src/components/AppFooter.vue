<script setup lang="ts">
const scopes = [
  { en: 'ROUTINE CORE', text: '让不同能力进入共同的执行与协作秩序。' },
  { en: 'AGENT HARNESS', text: '让一个 Agent 获得持续行动的支撑。' },
  { en: 'ROUTINE SOCIETY', text: '让异构主体组合成开放的系统网络。' }
]
</script>

<template>
  <footer class="ft">
    <div class="ft-in">
      <div class="ft-top">
        <div class="ft-brand">
          <span class="ft-mark">R/A</span>
          <p class="ft-claim">
            系统由单元构成，<br />
            秩序在调用中形成。
          </p>
        </div>

        <div class="ft-scopes">
          <div v-for="s in scopes" :key="s.en" class="scope">
            <span class="scope-en mono">{{ s.en }}</span>
            <p>{{ s.text }}</p>
          </div>
        </div>

        <div class="ft-nav">
          <span class="tag">继续探索</span>
          <ul>
            <li><RouterLink to="/anatomy">从一个单元开始</RouterLink></li>
            <li><RouterLink to="/communication">看能力如何协作</RouterLink></li>
            <li><RouterLink to="/registry">查看当前应用清单</RouterLink></li>
          </ul>
        </div>
      </div>

      <div class="ft-bot">
        <span>2026 · Routine Atlas</span>
        <span class="ft-dot" />
        <span>通用概念与当前应用实例分开呈现</span>
        <span class="ft-right mono">routine / world</span>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.ft {
  position: relative;
  z-index: 1;
  margin-top: clamp(40px, 8vw, 96px);
  border-top: 1px solid var(--line);
  background: linear-gradient(180deg, transparent, color-mix(in oklab, var(--bg-deep) 70%, transparent));
}
.ft-in { max-width: var(--maxw); margin: 0 auto; padding: 46px clamp(20px, 5vw, 56px) 28px; }
.ft-top { display: grid; grid-template-columns: 1.05fr 1.5fr 1fr; gap: 40px; padding-bottom: 38px; }
.ft-mark { display: inline-grid; place-items: center; width: 34px; height: 34px; border-radius: 9px; border: 1px solid var(--line-2); font-family: var(--font-mono); font-size: 10px; letter-spacing: .04em; color: var(--ink-2); margin-bottom: 16px; }
.ft-claim { font-size: 15px; line-height: 1.6; color: var(--ink-2); letter-spacing: -.01em; }
.ft-scopes { display: grid; gap: 16px; align-content: start; }
.scope-en { color: var(--ink-4); font-size: 9px; letter-spacing: .09em; }
.scope p { margin-top: 5px; color: var(--ink-3); font-size: 12px; line-height: 1.65; }
.ft-nav ul { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 9px; }
.ft-nav li { font-size: 12px; }
.ft-nav a { color: var(--ink-3); transition: color .2s var(--ease); }
.ft-nav a:hover { color: var(--ink); }
.ft-bot { display: flex; align-items: center; gap: 10px; padding-top: 18px; border-top: 1px solid var(--line); font-size: 11px; color: var(--ink-4); flex-wrap: wrap; }
.ft-dot { width: 3px; height: 3px; border-radius: 99px; background: var(--ink-4); }
.ft-right { margin-left: auto; letter-spacing: .06em; font-size: 9.5px; text-transform: uppercase; }
@media (max-width: 860px) {
  .ft-top { grid-template-columns: 1fr; gap: 30px; }
  .ft-right { display: none; }
}
</style>
