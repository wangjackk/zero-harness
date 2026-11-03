<script setup lang="ts">
import CascadeStop from '../components/CascadeStop.vue'
import { pageNo } from '../router'
import { computed, ref } from 'vue'
import { CRITERIA, SPECIMEN } from '../data/criteria'
import { hl } from '../lib/highlight'

const spec = SPECIMEN
const marks = spec.marks
const active = ref('run')
const range = computed(() => marks[active.value] || null)

function isOn(i: number) {
  const r = range.value
  return !!r && i >= r[0] && i <= r[1]
}
function isEdge(i: number) {
  const r = range.value
  return !!r && (i === r[0] || i === r[1])
}
</script>

<template>
  <div class="an">
    <section class="page page-head">
      <div class="kicker">
        <span>解剖 · Anatomy</span>
        <span class="dim">{{ pageNo('/anatomy') }}</span>
      </div>
      <h1>一个单元，<br />从注册到停止。</h1>
      <p class="lede">
        右侧说明注册、声明、执行、停止与查询分别负责什么。
        左侧只是一份参考样本，用来帮助对照；具体单元的实现可以不同。
      </p>
    </section>

    <section class="page">
      <div class="an-grid">
        <!-- ------------------------------ 代码 ------------------------------ -->
        <div class="code-col">
          <div class="spec-title">
            <span class="spec-name mono">{{ spec.name }}</span>
            <span class="spec-class mono">参考样本 · {{ spec.cls }} · Python</span>
          </div>

          <div class="code-card">
            <div class="code-head">
              <span class="dots" aria-hidden="true"><i /><i /><i /></span>
              <span class="code-file mono">{{ spec.file }}</span>
              <span class="code-tag mono">{{ spec.tag }}</span>
            </div>
            <div class="code-body">
              <ol class="lines">
                <li
                  v-for="(l, i) in spec.code"
                  :key="i"
                  :class="{ on: isOn(i + 1), edge: isEdge(i + 1) }"
                >
                  <span class="ln mono">{{ i + 1 }}</span>
                  <code class="src" v-html="hl(l)" />
                </li>
              </ol>
            </div>
          </div>

          <div class="system-query" :class="{ on: active === 'discover' }">
            <span class="tag">系统查询</span>
            <p><code>routine/list</code> 查询注册与运行状态，<code>routine/doc</code> 读取声明。</p>
            <span class="query-note">位于业务实现之外，因此不对应左侧代码行。</span>
          </div>
        </div>

        <!-- ------------------------------ 判据 ------------------------------ -->
        <div class="crit-col">
          <div class="crit-head">
            <span class="tag">各部分负责什么</span>
            <span class="crit-count mono">通用职责 · 不绑定样本</span>
          </div>

          <div class="crit-list">
            <div
              v-for="c in CRITERIA"
              :key="c.key"
              class="cr"
              :class="{ on: active === c.key, none: c.system }"
            >
              <button
                class="cr-btn"
                @click="active = c.key"
                @mouseenter="active = c.key"
                :aria-pressed="active === c.key"
              >
                <span class="cr-no mono">{{ c.no }}</span>
                <span class="cr-body">
                  <span class="cr-t">{{ c.title }}</span>
                  <span class="cr-en mono">{{ c.en }}</span>
                </span>
                <span class="cr-hl mono">
                  <template v-if="marks[c.key]">L{{ marks[c.key][0] }}<template v-if="marks[c.key][1] !== marks[c.key][0]">–{{ marks[c.key][1] }}</template></template>
                  <template v-else>{{ c.hint }}</template>
                </span>
              </button>
              <div class="cr-note-wrap" :class="{ open: active === c.key }">
                <div class="cr-note-in">
                  <p class="cr-note">{{ c.note }}</p>
                </div>
              </div>
            </div>
          </div>

          <div class="crit-foot">
            <p>
              <b>注册</b>让单元可被发现，<b>声明</b>描述能力与输入，<b>执行</b>完成一次任务，
              <b>停止</b>负责中断与清理，<b>查询</b>提供外部观察。左侧代码只是这些职责的一种参考写法。
            </p>
          </div>
        </div>
      </div>
    </section>

    <CascadeStop />


  </div>
</template>

<style scoped>
.page-head h1 { margin-top: 16px; }
.page-head .lede { margin-top: 22px; }
.em { font-style: normal; color: var(--ink); font-weight: 500; }

/* ------------------------------ 布局 ------------------------------ */
.an-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.45fr) minmax(0, 1fr);
  gap: clamp(20px, 3vw, 40px);
  align-items: start;
}

/* ------------------------------ 代码 ------------------------------ */
.code-col { display: grid; gap: 16px; position: sticky; top: calc(var(--nav-h) + 20px); }
.spec-title { display: flex; align-items: baseline; gap: 12px; padding: 0 2px; }
.spec-name { color: var(--ink); font-size: 16px; }
.spec-class { color: var(--ink-4); font-size: 10px; }

.code-card {
  border: 1px solid var(--line-2);
  border-radius: 12px;
  overflow: hidden;
  background: color-mix(in oklab, var(--bg-deep) 72%, var(--surface-solid));
  box-shadow: var(--shadow-2);
}
html[data-theme='light'] .code-card { background: #fff; }
.code-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}
.dots { display: flex; gap: 4px; }
.dots i { width: 7px; height: 7px; border-radius: 99px; background: var(--line-2); }
.dots i:first-child { background: color-mix(in oklab, var(--danger) 55%, transparent); }
.dots i:nth-child(2) { background: color-mix(in oklab, #f0b24a 55%, transparent); }
.dots i:nth-child(3) { background: color-mix(in oklab, #46d8a0 55%, transparent); }
.code-file { font-size: 10.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.code-tag {
  margin-left: auto;
  font-size: 8.5px;
  letter-spacing: 0.14em;
  color: var(--ink-4);
  border: 1px solid var(--line);
  padding: 2px 5px;
  border-radius: 4px;
  white-space: nowrap;
}
.code-body { max-height: min(52vh, 520px); overflow: auto; }
.lines { margin: 0; padding: 10px 0; list-style: none; counter-reset: l; }
.lines li {
  display: grid;
  grid-template-columns: 42px 1fr;
  align-items: baseline;
  padding: 1px 0;
  border-left: 2px solid transparent;
  transition: background 0.2s var(--ease), border-color 0.2s var(--ease);
}
.lines li.on {
  background: color-mix(in oklab, #a98bfa 10%, transparent);
  border-left-color: #a98bfa;
}
.lines li.edge { background: color-mix(in oklab, #a98bfa 16%, transparent); }
.ln {
  font-size: 10px;
  color: var(--ink-4);
  text-align: right;
  padding-right: 12px;
  user-select: none;
}
.src {
  font-family: var(--font-mono);
  font-size: 12.2px;
  line-height: 1.72;
  color: var(--ink-2);
  white-space: pre;
  padding-right: 14px;
}
.src :deep(.k) { color: #c8a9ff; font-style: normal; }
.src :deep(.q) { color: #86dcc0; font-style: normal; }
.src :deep(.c) { color: var(--ink-4); font-style: normal; }
.src :deep(.n) { color: #f0b24a; font-style: normal; }
.src :deep(.t) { color: #8fb8ff; font-style: normal; }
.src :deep(.s) { color: #e6b673; font-style: normal; font-weight: 500; }
html[data-theme='light'] .src :deep(.k) { color: #6b4ed8; }
html[data-theme='light'] .src :deep(.q) { color: #0f7c63; }
html[data-theme='light'] .src :deep(.n) { color: #a35c00; }
html[data-theme='light'] .src :deep(.t) { color: #1d4ed8; }
html[data-theme='light'] .src :deep(.s) { color: #92400e; }

.system-query { padding: 14px 16px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); transition: border-color .2s var(--ease); }
.system-query.on { border-color: color-mix(in oklab, #a98bfa 55%, transparent); }
.system-query p { margin: 9px 0 5px; color: var(--ink-2); font-size: 12px; line-height: 1.7; }
.system-query code { font-family: var(--font-mono); color: #a98bfa; }
.query-note { color: var(--ink-4); font-size: 10px; line-height: 1.6; }

/* ------------------------------ 判据列 ------------------------------ */
.crit-col { display: grid; gap: 14px; }
.crit-head { display: flex; align-items: baseline; justify-content: space-between; }
.crit-count { font-size: 9.5px; color: var(--ink-4); letter-spacing: 0.04em; }
.crit-list { border-top: 1px solid var(--line); }
.cr { border-bottom: 1px solid var(--line); }
.cr-btn {
  display: grid;
  grid-template-columns: 26px 1fr auto;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 14px 4px;
  text-align: left;
}
.cr-no { font-size: 9.5px; color: var(--ink-4); letter-spacing: 0.06em; }
.cr-body { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.cr-t { font-size: 15.5px; color: var(--ink-2); letter-spacing: -0.015em; transition: color 0.2s var(--ease); }
.cr-en { font-size: 9.5px; color: var(--ink-4); }
.cr-hl { font-size: 9.5px; color: var(--ink-4); }
.cr.on .cr-t { color: var(--ink); }
.cr.on .cr-hl { color: #a98bfa; }
.cr.on .cr-no { color: #a98bfa; }
.cr.none .cr-t { color: var(--ink-4); }

.cr-note-wrap { display: grid; grid-template-rows: 0fr; transition: grid-template-rows 0.36s var(--ease); }
.cr-note-wrap.open { grid-template-rows: 1fr; }
.cr-note-in { overflow: hidden; }
.cr-note {
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--ink-3);
  padding: 0 4px 16px 36px;
  max-width: 46ch;
}
.crit-foot {
  margin-top: 4px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
}
.crit-foot p { font-size: 12.5px; line-height: 1.8; color: var(--ink-3); }
.crit-foot b { color: var(--ink-2); }

@media (max-width: 1000px) {
  .an-grid { grid-template-columns: 1fr; }
  .code-col { position: static; }
  .cr-note { max-width: 68ch; }
}
@media (min-width: 1001px) and (max-height: 860px) {
  .code-body { max-height: 44vh; }
}
@media (max-width: 640px) {
  .code-body { max-height: 480px; }
  .code-file { max-width: 58vw; }
  .crit-head { align-items: flex-start; gap: 12px; }
  .cr-note { padding-left: 4px; }
}
</style>
