<template>
  <div class="sk-picker">
    <div
      v-for="s in skills"
      :key="s.name"
      class="sk-chip"
      :class="{ on: levelOf(s.name) !== null }"
      :title="s.description"
      @click="toggle(s.name)"
    >
      <div class="sk-chip-main">
        <span class="sk-chip-name">{{ s.name }}</span>
        <span class="sk-chip-desc">{{ s.description }}</span>
      </div>
      <span
        class="sk-view"
        title="查看详情"
        @click.stop="openDetail(s.name)"
      ><Icon :comp="InfoCircle" :size="13" /></span>
      <span
        v-if="levelOf(s.name) !== null"
        class="sk-level"
        :class="{ full: levelOf(s.name) === 'full' }"
        @click.stop="cycleLevel(s.name)"
      >{{ levelOf(s.name) === 'full' ? '全文' : '摘要' }}</span>
    </div>
    <div v-if="!skills.length" class="sk-empty">共享库无 skill (skill/repo/list)</div>

    <!-- skill 详情浮层: skill/repo/get 按需拉全文
         (Teleport 到 .create-view 内, 相对创建窗口居中, 不做全局居中) -->
    <Teleport to=".create-view">
      <div v-if="detail || detailLoading" class="skd-overlay" @click.self="closeDetail">
        <div class="skd-panel">
          <template v-if="detail">
            <header class="skd-head">
              <span class="skd-name">{{ detail.name }}</span>
              <span v-if="detail.version" class="skd-ver">v{{ detail.version }}</span>
              <button class="skd-close" @click="closeDetail"><Icon :comp="X" :size="14" /></button>
            </header>
            <div v-if="detail.agents?.length" class="skd-agents">受众: {{ detail.agents.join(' / ') }}</div>
            <div v-if="detail.description" class="skd-desc">{{ detail.description }}</div>
            <div v-if="detail.files?.length" class="skd-files">{{ detail.files.join(' · ') }}</div>
            <div v-if="detail.body" class="skd-content md-body" v-html="renderMarkdown(detail.body)" />
            <div v-else-if="detail.error" class="skd-error">{{ detail.error }}</div>
          </template>
          <div v-else class="skd-loading">加载中…</div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import type { SkillPick } from '../composables/useAgents'
import { renderMarkdown } from '../utils/markdown'
import Icon from './Icon.vue'
import { InfoCircle, X } from '@vicons/tabler'

/**
 * 统一初始 skill 选择器: 平台级组件, 所有 agent 类型共用.
 * 数据来自 skill 共享库 skill/repo/list routine (~/.zero/skills 唯一
 * 事实源). 选中值是 [{name, level}] ---- level 体现渐进式披露等级:
 * 'summary' 摘要级 (agent 侧渐进加载), 'full' 全文预载; 各 agent
 * manager 拿到后自行处理 ---- 本组件只管选.
 *
 * 交互: 点击 chip 选中 (默认摘要级) / 取消; 点选中态的等级徽标在
 * 摘要 <-> 全文间切换; 点 ⓘ 拉取 skill/repo/get 弹详情浮层.
 */
const props = defineProps<{
  httpBase: string
  modelValue: SkillPick[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: SkillPick[]): void
}>()

interface SkillOption { name: string; description: string }
const skills = ref<SkillOption[]>([])

interface SkillDetail {
  name: string
  description: string
  version?: string
  agents?: string[]
  files?: string[]
  body?: string
  error?: string
}
const detail = ref<SkillDetail | null>(null)
const detailLoading = ref(false)

function levelOf(name: string): 'full' | 'summary' | null {
  return props.modelValue.find(p => p.name === name)?.level ?? null
}

function toggle(name: string) {
  const next = [...props.modelValue]
  const i = next.findIndex(p => p.name === name)
  if (i >= 0) next.splice(i, 1)
  else next.push({ name, level: 'summary' })
  emit('update:modelValue', next)
}

function cycleLevel(name: string) {
  const next = props.modelValue.map(p =>
    p.name === name ? { ...p, level: p.level === 'full' ? 'summary' as const : 'full' as const } : p,
  )
  emit('update:modelValue', next)
}

async function openDetail(name: string) {
  detail.value = null
  detailLoading.value = true
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'skill/repo/get', kwargs: { name } }),
    })
    if (res.ok) detail.value = (await res.json())?.result ?? null
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  detail.value = null
  detailLoading.value = false
}

onMounted(async () => {
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'skill/repo/list', kwargs: {} }),
    })
    if (!res.ok) return
    const data = await res.json()
    const list = (data?.result?.skills || []) as Array<{ name: string; description?: string }>
    skills.value = list.map(s => ({ name: s.name, description: s.description || '' }))
  } catch {
    skills.value = []
  }
})
</script>

<style scoped>
.sk-picker {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 8px;
  border: 1px solid #262c40; border-radius: 10px;
  background: #161a25; padding: 10px;
  max-height: 188px; overflow-y: auto;
}
.sk-chip {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 10px;
  border: 1px solid #262c40; border-radius: 8px;
  background: #12141d;
  cursor: pointer; user-select: none;
  transition: border-color .15s, background .15s, box-shadow .15s;
}
.sk-chip:hover { background: #1f2433; border-color: #3a4160; }
.sk-chip.on {
  border-color: #6366f1; background: #1b1f33;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, .12);
}
.sk-chip-main {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column; gap: 1px;
}
.sk-chip-name {
  font-family: Consolas, monospace; font-size: 11px;
  color: #cbd5e1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.sk-chip.on .sk-chip-name { color: #c7cdff; }
.sk-chip-desc {
  font-size: 10px; color: #4b5563;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* 查看详情按钮 (ⓘ): 不参与选/取消 */
.sk-view {
  flex-shrink: 0;
  width: 17px; height: 17px;
  display: inline-flex; align-items: center; justify-content: center;
  color: #64748b; background: #0f1117;
  border: 1px solid #2d3148; border-radius: 50%;
  cursor: pointer; user-select: none;
  transition: color .15s, border-color .15s;
}
.sk-view:hover { color: #818cf8; border-color: #4c4372; }
.sk-level {
  flex-shrink: 0;
  font-size: 10px; padding: 2px 8px; border-radius: 999px;
  background: #2d3148; color: #94a3b8; line-height: 1.5;
  transition: background .15s, color .15s;
}
.sk-level:hover { background: #3b4160; color: #e2e8f0; }
.sk-level.full {
  background: #4c4372; color: #c4b5fd;
}
.sk-empty {
  grid-column: 1 / -1;
  padding: 12px; font-size: 11px; color: #4b5563; text-align: center;
}

/* ---- 详情浮层 ---- */
/* 相对 .create-view 绝对定位 (非视口): 与创建窗口同中心 */
.skd-overlay {
  position: absolute; inset: 0; z-index: 1000;
  display: flex; align-items: center; justify-content: center;
  padding: 40px;
  background: rgba(0, 0, 0, .55);
}
.skd-panel {
  display: flex; flex-direction: column; gap: 10px;
  width: min(1100px, 100%); max-height: 100%;
  padding: 18px 20px;
  border: 1px solid #262c40; border-radius: 12px;
  background: #12141d;
}
.skd-head { display: flex; align-items: center; gap: 10px; }
.skd-name {
  font-family: Consolas, monospace; font-size: 14px; color: #e2e8f0;
}
.skd-ver {
  font-size: 10px; color: #818cf8;
  border: 1px solid #4c4372; border-radius: 999px; padding: 1px 8px;
}
.skd-close {
  margin-left: auto;
  width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  color: #94a3b8; background: #161a25;
  border: 1px solid #2d3148; border-radius: 6px;
  cursor: pointer;
  transition: color .15s, border-color .15s;
}
.skd-close:hover { color: #f87171; border-color: #7f3a3a; }
.skd-agents {
  font-size: 11px; color: #64748b;
}
.skd-desc {
  font-size: 12px; line-height: 1.5; color: #a5aec4;
}
.skd-files {
  font-family: Consolas, monospace;
  font-size: 10px; color: #4b5563;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.skd-content {
  flex: 1; min-height: 0;
  padding: 12px 16px;
  overflow: auto;
  font-size: 12px; line-height: 1.6; color: #94a3b8;
  background: #0f1117;
  border: 1px solid #262c40; border-radius: 8px;
}
/* markdown rendered via v-html (unscoped -> :deep) */
.skd-content :deep(p) { margin: 6px 0; }
.skd-content :deep(p:first-child) { margin-top: 0; }
.skd-content :deep(p:last-child) { margin-bottom: 0; }
.skd-content :deep(h1),
.skd-content :deep(h2),
.skd-content :deep(h3),
.skd-content :deep(h4),
.skd-content :deep(h5),
.skd-content :deep(h6) {
  margin: 14px 0 6px; color: #e2e8f0; font-weight: 600;
}
.skd-content :deep(h1) { font-size: 16px; }
.skd-content :deep(h2) { font-size: 14px; }
.skd-content :deep(h3) { font-size: 13px; }
.skd-content :deep(h4),
.skd-content :deep(h5),
.skd-content :deep(h6) { font-size: 12px; }
.skd-content :deep(ul),
.skd-content :deep(ol) { margin: 6px 0; padding-left: 22px; }
.skd-content :deep(li) { margin: 2px 0; }
.skd-content :deep(code) {
  font-family: Consolas, monospace; font-size: 11px;
  padding: 1px 5px;
  background: #161a25; border: 1px solid #2d3148; border-radius: 4px;
  color: #c7cdff;
}
.skd-content :deep(pre) {
  padding: 10px 12px; margin: 8px 0;
  background: #161a25; border: 1px solid #262c40; border-radius: 8px;
  overflow-x: auto;
}
.skd-content :deep(pre code) {
  padding: 0; background: none; border: none; color: #a5b4fc;
}
.skd-content :deep(blockquote) {
  margin: 8px 0; padding: 2px 12px;
  border-left: 3px solid #4c4372; color: #64748b;
}
.skd-content :deep(table) { border-collapse: collapse; margin: 8px 0; }
.skd-content :deep(th),
.skd-content :deep(td) {
  padding: 4px 10px;
  border: 1px solid #2d3148; font-size: 11px;
}
.skd-content :deep(th) { color: #a5aec4; background: #161a25; }
.skd-content :deep(a) { color: #818cf8; }
.skd-content :deep(hr) { border: none; border-top: 1px solid #262c40; margin: 10px 0; }
.skd-error {
  padding: 12px 14px; font-size: 12px; color: #fca5a5;
  border: 1px solid #7f3a3a; border-radius: 8px; background: #2a1618;
}
.skd-loading {
  padding: 40px; text-align: center;
  font-size: 12px; color: #64748b;
}
</style>
