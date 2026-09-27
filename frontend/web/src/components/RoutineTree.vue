<template>
  <div class="rt-card">
    <div class="rt-head">
      <button v-if="rootName" class="rt-back" title="返回上一级" @click="goBack">←</button>
      <span class="rt-title">Routine Tree</span>
      <span v-if="rootName" class="rt-mode" :title="`全景子树: ${rootName} (含已结束)`">⌖ {{ rootName }}</span>
      <span class="rt-count">{{ rows.length }}</span>
    </div>
    <div class="rt-list">
      <div
        v-for="row in rows"
        :key="row.id"
        class="rt-row"
        :class="{ active: row.id === selected, ended: row.state === 'ended' }"
        :style="{ paddingLeft: row.depth * 14 + 10 + 'px' }"
        @click="$emit('select', row.id)"
      >
        <span class="rt-dot" :class="row.state" />
        <span class="rt-name">{{ row.name }}</span>
        <span class="rt-id">#{{ row.id }}</span>
      </div>
      <div v-if="!rows.length" class="rt-empty">no routines</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, type Ref } from 'vue'
import type { LifecycleEvent } from './RoutineTimeline.vue'

export interface RoutineNode {
  id: string
  parent_id: string
  name: string
  state: string
}

interface TreeRow extends RoutineNode {
  depth: number
}

const props = defineProps<{
  nodes: Ref<Map<string, RoutineNode>> | Map<string, RoutineNode>
  events?: LifecycleEvent[]
  selected?: string | null
}>()

const emit = defineEmits<{ (e: 'select', id: string): void; (e: 'back'): void }>()

// 返回上一级: 从当前全景根的 parent 找事件历史里的 pid
const goBack = () => {
  const evs = props.events ?? []
  for (let i = evs.length - 1; i >= 0; i--) {
    if (evs[i].id === props.selected) {
      const pid = evs[i].pid
      emit('select', pid === '0' ? '' : pid)
      return
    }
  }
  emit('back')
}

// 全景模式: 选中节点后从事件历史重建该子树(含已结束实例),
// live Map 里 stopped 节点已删, 历史结构只能从 events 拿.
const histRows = computed<TreeRow[]>(() => {
  const evs = props.events ?? []
  const root = props.selected
  if (!root) return []
  // id → 最新状态(按事件顺序覆盖), 首帧拿 name/pid
  const info = new Map<string, { pid: string; name: string; state: string }>()
  for (const e of evs) {
    const cur = info.get(e.id)
    if (!cur) info.set(e.id, { pid: e.pid, name: e.name, state: e.ev })
    else cur.state = e.ev
  }
  if (!info.has(root)) return []
  const inTree = (id: string): boolean => {
    for (let i = 0, cur = id; i < 64; i++, cur = info.get(cur)?.pid ?? '0') {
      if (cur === root) return true
      if (cur === '0') return false
    }
    return false
  }
  const children = new Map<string, TreeRow[]>()
  const roots: TreeRow[] = []
  for (const [id, v] of info) {
    if (!inTree(id)) continue
    const state = v.state === 'started' ? 'running' : v.state === 'created' ? 'created' : 'ended'
    const row: TreeRow = { id, parent_id: v.pid, name: v.name, state, depth: 0 }
    if (id === root) { roots.push(row); continue }
    const list = children.get(v.pid) ?? []
    list.push(row)
    children.set(v.pid, list)
  }
  const out: TreeRow[] = []
  const walk = (n: TreeRow, depth: number) => {
    out.push({ ...n, depth })
    for (const c of children.get(n.id) ?? []) walk(c, depth + 1)
  }
  roots.forEach(r => walk(r, 0))
  return out
})

const rootName = computed(() => {
  if (!props.selected || !histRows.value.length) return ''
  return histRows.value[0].name
})

// 全局 live 树(未选中) / 全景子树(选中后)
const rows = computed<TreeRow[]>(() => {
  if (props.selected && histRows.value.length) return histRows.value
  const map: Map<string, RoutineNode> =
    props.nodes instanceof Map ? props.nodes : props.nodes.value
  const children = new Map<string, RoutineNode[]>()
  const roots: RoutineNode[] = []
  for (const n of map.values()) {
    if (n.parent_id && n.parent_id !== '0' && map.has(n.parent_id)) {
      const list = children.get(n.parent_id) ?? []
      list.push(n)
      children.set(n.parent_id, list)
    } else {
      roots.push(n)
    }
  }
  const out: TreeRow[] = []
  const walk = (n: RoutineNode, depth: number) => {
    out.push({ ...n, depth })
    for (const c of children.get(n.id) ?? []) walk(c, depth + 1)
  }
  roots.forEach(r => walk(r, 0))
  return out
})
</script>

<style scoped>
.rt-card {
  display: flex;
  flex-direction: column;
  flex: 0 0 260px;
  background: #1e2130;
  border: 1px solid #3d4270;
  border-radius: 10px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
  overflow: hidden;
}

.rt-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #3d4270;
}

.rt-title { font-size: 13px; color: #e2e8f0; font-weight: 600; }

.rt-back {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid #3d4270;
  border-radius: 6px;
  background: #2a2e44;
  color: #cbd3e1;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.rt-back:hover { background: #32406a; color: #fff; }

.rt-mode {
  font-size: 11px;
  color: #f59e0b;
  background: #3a3222;
  border-radius: 8px;
  padding: 1px 8px;
  font-family: ui-monospace, Consolas, monospace;
}

.rt-count {
  font-size: 11px;
  color: #7a8199;
  background: #2a2e44;
  border-radius: 8px;
  padding: 1px 8px;
}

.rt-list { overflow-y: auto; padding: 6px 0; }

.rt-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 3px 12px 3px 10px;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: nowrap;
  cursor: pointer;
}

.rt-row:hover { background: #2a2e44; }
.rt-row.active { background: #32406a; }
.rt-row.ended .rt-name, .rt-row.ended .rt-id { color: #565d75; }

.rt-dot {
  flex-shrink: 0;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #555b70;
}
.rt-dot.running {
  background: #4ade80;
  box-shadow: 0 0 5px rgba(74, 222, 128, 0.55);
}
.rt-dot.ended { background: transparent; border: 1px solid #565d75; }

.rt-name { color: #cbd3e1; overflow: hidden; text-overflow: ellipsis; }
.rt-id { color: #565d75; font-size: 11px; margin-left: auto; }

.rt-empty { padding: 16px; text-align: center; color: #565d75; font-size: 12px; }
</style>
