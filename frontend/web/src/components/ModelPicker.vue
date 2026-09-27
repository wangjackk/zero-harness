<template>
  <div class="mp-picker">
    <div class="mp-filters">
      <button
        class="mp-filter"
        :class="{ on: filter === '' }"
        @click="filter = ''"
      >全部</button>
      <button
        v-for="p in providers"
        :key="p"
        class="mp-filter"
        :class="{ on: filter === p }"
        @click="filter = filter === p ? '' : p"
      >{{ p }}<span class="mp-filter-n">{{ modelsForProvider(p).length }}</span></button>
    </div>
    <div class="mp-list">
      <div
        v-for="m in filteredModels"
        :key="m.value"
        class="mp-item"
        :class="{ on: modelValue === m.value }"
        @click="select(m.value)"
      >
        <div class="mp-item-name">{{ m.label }}</div>
        <div class="mp-item-key">{{ m.value }}</div>
        <span v-if="modelValue === m.value" class="mp-check"><Icon :comp="Check" :size="10" /></span>
      </div>
      <div v-if="!filteredModels.length" class="mp-empty">—</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import Icon from './Icon.vue'
import { Check } from '@vicons/tabler'

/**
 * 统一模型选择器: 平台级组件, 所有 agent 类型共用.
 * 数据来自中立模型体系 models/list routine (models.yaml 唯一事实源);
 * 全部模型一次性铺进多列网格 (provider 只作顶部筛选 chip, 再点取消),
 * 溢出即滚动; 选中值 (model key, 如 'seed/glm-5.3') 由各 agent 自己
 * 解析处理 ---- 本组件只管选.
 */
const props = defineProps<{
  httpBase: string
  modelValue: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
}>()

interface ModelOption { label: string; value: string; provider: string }
const allModels = ref<ModelOption[]>([])
const filter = ref('')

const providers = computed<string[]>(() => {
  const set = new Set<string>()
  for (const m of allModels.value) set.add(m.provider)
  return [...set].sort()
})

function modelsForProvider(p: string): ModelOption[] {
  if (!p) return []
  return allModels.value.filter(m => m.provider === p)
}

const filteredModels = computed<ModelOption[]>(() =>
  filter.value ? modelsForProvider(filter.value) : allModels.value,
)

function select(v: string) {
  emit('update:modelValue', v)
}

async function loadModels() {
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'models/list', kwargs: {} }),
    })
    if (!res.ok) return
    const data = await res.json()
    const models = (data?.result?.models || []) as Array<{
      key: string; provider: string; name?: string
    }>
    allModels.value = models.map(m => ({
      label: m.name || m.key,
      value: m.key,
      provider: m.provider,
    }))
  } catch {
    allModels.value = []
  }
}

onMounted(() => { loadModels() })
</script>

<style scoped>
.mp-picker {
  display: flex; flex-direction: column;
  height: 260px;
  border: 1px solid #262c40; border-radius: 10px;
  overflow: hidden; background: #161a25;
}
/* provider 筛选 chip 行 (点选中态再点取消 = 回全部) */
.mp-filters {
  display: flex; flex-wrap: wrap; gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid #262c40;
  background: #12141d;
}
.mp-filter {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 10px; border-radius: 999px;
  border: 1px solid #2d3148; background: #161a25;
  font-size: 11px; color: #94a3b8; cursor: pointer; user-select: none;
  transition: border-color .15s, color .15s, background .15s;
}
.mp-filter:hover { color: #cbd5e1; border-color: #3a4160; }
.mp-filter.on {
  border-color: #6366f1; background: #1b1f33; color: #c7cdff;
}
.mp-filter-n {
  min-width: 15px; text-align: center;
  font-family: Consolas, monospace; font-size: 10px;
  color: #64748b; background: #0f1117;
  border-radius: 999px; padding: 0 5px;
}
.mp-filter.on .mp-filter-n { color: #818cf8; }
/* 全量模型多列网格: 铺满横向空间, 溢出纵向滚动 */
.mp-list {
  flex: 1; min-height: 0; overflow-y: auto;
  padding: 10px;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 8px; align-content: start;
}
.mp-item {
  position: relative;
  display: flex; flex-direction: column; gap: 2px;
  padding: 8px 30px 8px 12px; border-radius: 8px;
  border: 1px solid #262c40; background: #12141d;
  cursor: pointer; user-select: none;
  transition: background .15s, border-color .15s;
}
.mp-item:hover { background: #1f2433; border-color: #3a4160; }
.mp-item.on {
  background: #1b1f33; border-color: #4c4372;
}
.mp-item-name {
  font-size: 12px; color: #e2e8f0;
}
.mp-item.on .mp-item-name { color: #c7cdff; }
.mp-item-key {
  font-family: Consolas, monospace; font-size: 10px; color: #4b5563;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.mp-check {
  position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
  width: 16px; height: 16px;
  display: inline-flex; align-items: center; justify-content: center;
  color: #0f1117;
  background: #818cf8; border-radius: 50%;
}
.mp-empty {
  grid-column: 1 / -1;
  padding: 12px 10px; font-size: 11px; color: #4b5563;
  text-align: center;
}
</style>
