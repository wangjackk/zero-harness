<template>
  <div class="rp-picker">
    <!-- 已选汇总: 平铺 tag, 单个 ✕ 移除 (选中项散在长列表中难回看,
         这里一眼看全当前白名单) -->
    <div class="rp-selected">
      <template v-if="modelValue.length">
        <span
          v-for="name in modelValue"
          :key="name"
          class="rp-tag"
          :title="descOf(name)"
        >{{ name }}<span class="rp-tag-x" title="移除" @click="remove(name)"><Icon :comp="X" :size="10" /></span></span>
      </template>
      <span v-else class="rp-selected-empty">未选 = 该 agent 类型默认白名单</span>
    </div>

    <div class="rp-filter">
      <input
        v-model="filter"
        class="rp-filter-input"
        type="text"
        placeholder="过滤 (name / 描述)…"
      >
      <span class="rp-filter-count">{{ modelValue.length }}/{{ routines.length }}</span>
    </div>
    <div class="rp-list">
      <div
        v-for="r in filtered"
        :key="r.name"
        class="rp-chip"
        :class="{ on: modelValue.includes(r.name) }"
        :title="r.description"
        @click="toggle(r.name)"
      >
        <span class="rp-chip-name">{{ r.name }}</span>
        <span class="rp-chip-desc">{{ r.description }}</span>
      </div>
      <div v-if="!filtered.length" class="rp-empty">无匹配 routine</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import Icon from './Icon.vue'
import { X } from '@vicons/tabler'

/**
 * 统一 routine 白名单选择器: 平台级组件, 声明 uses_routines 的
 * agent 类型共用. 数据来自 routine/list (registered, kernel 全局
 * catalog 跨 hub 视野); hidden routine 不列. 选中值是 name 数组
 * (提交时作 prompt_routines 参数, agent manager 自行处理) --
 * 本组件只管选.
 */
const props = defineProps<{
  httpBase: string
  modelValue: string[]
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: string[]): void
}>()

interface RoutineOption { name: string; description: string }
const routines = ref<RoutineOption[]>([])
const filter = ref('')

const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return routines.value
  return routines.value.filter(r => r.name.toLowerCase().includes(q))
})

function toggle(name: string) {
  const next = [...props.modelValue]
  const i = next.indexOf(name)
  if (i >= 0) next.splice(i, 1)
  else next.push(name)
  emit('update:modelValue', next)
}

function remove(name: string) {
  emit('update:modelValue', props.modelValue.filter(n => n !== name))
}

function descOf(name: string): string {
  return routines.value.find(r => r.name === name)?.description || ''
}

onMounted(async () => {
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'routine/list', kwargs: {} }),
    })
    if (!res.ok) return
    const data = await res.json()
    const list = (data?.result?.routines || []) as Array<{
      name: string; meta?: { hidden?: boolean; description?: string }
    }>
    routines.value = list
      .filter(r => r.name && !r.meta?.hidden)
      .map(r => ({ name: r.name, description: r.meta?.description || '' }))
  } catch {
    routines.value = []
  }
})
</script>

<style scoped>
.rp-picker { display: flex; flex-direction: column; gap: 8px; }

/* ---- 已选汇总: 可删除 tag 平铺 ---- */
.rp-selected {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  padding: 8px 10px;
  border: 1px solid #262c40; border-radius: 10px;
  background: #12141d;
}
.rp-tag {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px;
  border: 1px solid #4c4372; border-radius: 999px;
  background: #1b1f33; color: #c7cdff;
  font-family: Consolas, monospace; font-size: 11px;
  line-height: 1.6;
}
.rp-tag-x {
  display: inline-flex; align-items: center; justify-content: center;
  width: 14px; height: 14px; border-radius: 50%;
  color: #94a3b8;
  cursor: pointer; user-select: none;
  transition: color .15s, background .15s;
}
.rp-tag-x:hover { color: #f87171; background: #7f3a3a; }
.rp-selected-empty {
  font-size: 11px; color: #4b5563;
}

.rp-filter { display: flex; align-items: center; gap: 8px; }
.rp-filter-input {
  flex: 1;
  padding: 5px 12px;
  font-size: 12px; font-family: inherit;
  color: #cbd5e1;
  background: #12141d;
  border: 1px solid #2d3148; border-radius: 999px;
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.rp-filter-input:focus {
  border-color: #6366f1;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, .12);
}
.rp-filter-count {
  flex-shrink: 0;
  font-size: 10px; color: #64748b;
  font-family: Consolas, monospace;
}
.rp-list {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  gap: 8px;
  border: 1px solid #262c40; border-radius: 10px;
  background: #161a25; padding: 10px;
  max-height: 188px; overflow-y: auto;
}
.rp-chip {
  display: flex; flex-direction: column; gap: 1px;
  padding: 7px 10px;
  border: 1px solid #262c40; border-radius: 8px;
  background: #12141d;
  cursor: pointer; user-select: none;
  transition: border-color .15s, background .15s, box-shadow .15s;
}
.rp-chip:hover { background: #1f2433; border-color: #3a4160; }
.rp-chip.on {
  border-color: #6366f1; background: #1b1f33;
  box-shadow: 0 0 0 2px rgba(99, 102, 241, .12);
}
.rp-chip-name {
  font-family: Consolas, monospace; font-size: 11px;
  color: #cbd5e1;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rp-chip.on .rp-chip-name { color: #c7cdff; }
.rp-chip-desc {
  font-size: 10px; color: #4b5563;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rp-empty {
  grid-column: 1 / -1;
  padding: 12px; font-size: 11px; color: #4b5563; text-align: center;
}
</style>
