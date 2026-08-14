<template>
  <NCard class="td-card" :bordered="false" content-style="padding: 0;">
    <div class="td-header">
      <span class="td-title">{{ title || '结果' }}</span>
      <span v-if="subtitle" class="td-sub">{{ subtitle }}</span>
      <NButton size="small" quaternary @click="$emit('cancel')">✕</NButton>
    </div>

    <div class="td-body">
      <table class="td-table">
        <thead>
          <tr>
            <th v-for="col in columns" :key="col.key" :style="col.width ? { width: col.width } : {}">
              {{ col.label }}
            </th>
            <th v-if="rowActions?.length" class="td-th-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, i) in rows"
            :key="i"
            :class="{ 'td-row-dim': row[dimKey ?? ''] === false }"
          >
            <td v-for="col in columns" :key="col.key">
              <!-- link_key:文字显示 key,href 取 link_key 字段 -->
              <a
                v-if="col.link_key && row[col.link_key]"
                :href="(row[col.link_key] as string)"
                target="_blank"
                class="td-link td-cell"
                :class="{ 'td-cell-wrap': col.wrap }"
              >{{ row[col.key] ?? '--' }}</a>
              <!-- link 类型:文字与 href 同字段 -->
              <a
                v-else-if="col.type === 'link' && row[col.key]"
                :href="(row[col.key] as string)"
                target="_blank"
                class="td-link"
              >{{ row[col.key] }}</a>
              <!-- badge 类型 -->
              <span v-else-if="col.type === 'badge'" class="td-badge" :class="`td-badge-${row[col.key]}`">
                {{ row[col.key] }}
              </span>
              <!-- 默认文本 -->
              <span v-else class="td-cell" :class="{ 'td-cell-wrap': col.wrap }">{{ row[col.key] ?? '--' }}</span>
            </td>
            <td v-if="rowActions?.length" class="td-td-actions">
              <template v-for="act in rowActions" :key="act.emit">
                <NButton
                  v-if="act.condition_key == null || row[act.condition_key]"
                  size="tiny"
                  :type="act.type ?? 'default'"
                  :loading="pending === `${i}-${act.emit}`"
                  :disabled="done.has(`${i}-${act.emit}`)"
                  style="margin-left:4px"
                  @click="handleAction(act, row, i)"
                >
                  {{ done.has(`${i}-${act.emit}`) ? (act.done_label ?? act.label) : act.label }}
                </NButton>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="td-footer">
      <transition name="td-toast">
        <span v-if="toastMsg" class="td-toast">✓ 已复制:{{ toastMsg }}</span>
      </transition>
      <slot name="footer" />
      <NButton size="small" @click="$emit('cancel')">关闭</NButton>
    </div>
  </NCard>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { NCard, NButton } from 'naive-ui'

export interface TableColumn {
  key: string
  label: string
  width?: string
  type?: 'text' | 'link' | 'badge'
  wrap?: boolean
  link_key?: string  // 用该字段的值作为 href,显示 key 字段的文字
}

export interface RowAction {
  label: string
  done_label?: string
  emit: string
  type?: 'default' | 'primary' | 'warning' | 'error'
  condition_key?: string
  value_key?: string
}

export interface TableDialogProps {
  title?: string
  subtitle?: string
  // optional + defaulted below: the backend pushes these per protocol, but
  // defaulting keeps the dialog from crashing (and satisfies v-bind's union
  // type-check) if a payload is ever missing them.
  columns?: TableColumn[]
  rows?: Record<string, unknown>[]
  rowActions?: RowAction[]
  dimKey?: string
}

const props = withDefaults(defineProps<TableDialogProps>(), {
  columns: () => [],
  rows: () => [],
})

const emit = defineEmits<{
  cancel: []
  action: [payload: { emit: string; row: Record<string, unknown>; value: unknown }]
}>()

const pending = ref<string | null>(null)
const done = ref<Set<string>>(new Set())

const toastMsg = ref<string | null>(null)

function handleAction(act: RowAction, row: Record<string, unknown>, idx: number) {
  const key = `${idx}-${act.emit}`
  pending.value = key
  const value = act.value_key ? row[act.value_key] : row
  emit('action', { emit: act.emit, row, value })
  done.value = new Set([...done.value, key])
  pending.value = null

  // 安装类操作:复制安装命令到剪贴板并提示
  if (act.emit === 'install' && value) {
    const cmd = `npx skills add ${value}`
    navigator.clipboard?.writeText(cmd).catch(() => {})
    toastMsg.value = cmd
    setTimeout(() => { toastMsg.value = null }, 4000)
  }
}
</script>

<style scoped>
.td-card {
  width: 1000px;
  max-width: 96vw;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  background: #1a1d2e;
  border-radius: 12px !important;
  box-shadow: 0 24px 60px rgba(0,0,0,.7);
  overflow: hidden;
}

.td-header {
  display: flex; align-items: center; gap: 10px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid #252840; flex-shrink: 0;
}
.td-title { font-size: 14px; font-weight: 700; color: #e2e8f0; }
.td-sub   { font-size: 12px; color: #6366f1; flex: 1; }

.td-body {
  flex: 1; overflow: auto;
}

.td-table {
  width: 100%; border-collapse: collapse; font-size: 12.5px;
}

.td-table thead th {
  position: sticky; top: 0; z-index: 1;
  background: #1e2235;
  padding: 8px 14px;
  text-align: left;
  color: #64748b;
  font-weight: 600;
  border-bottom: 1px solid #2d3148;
  white-space: nowrap;
}
.td-th-actions { text-align: right; }

.td-table tbody tr {
  border-bottom: 1px solid #1e2130;
  transition: background .1s;
}
.td-table tbody tr:hover { background: #1e2235; }
.td-row-dim { opacity: .4; }

.td-table tbody td {
  padding: 9px 14px;
  vertical-align: middle;
  color: #94a3b8;
}
.td-td-actions { text-align: right; white-space: nowrap; }

.td-cell {
  display: block;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.td-cell-wrap {
  white-space: normal;
  overflow: visible;
  text-overflow: unset;
  max-width: 360px;
  line-height: 1.55;
}

.td-link {
  color: #818cf8; text-decoration: none;
}
.td-link:hover { text-decoration: underline; }

.td-badge {
  font-size: 10px; padding: 2px 6px; border-radius: 4px;
  background: #1e2235; color: #64748b;
}
.td-badge-true, .td-badge-pass { background: #1a2e1a; color: #4ade80; }
.td-badge-false, .td-badge-fail { background: #2e1a1a; color: #f87171; }

.td-footer {
  display: flex; justify-content: flex-end; align-items: center; gap: 8px;
  padding: 10px 20px 14px;
  border-top: 1px solid #252840; flex-shrink: 0;
}

.td-toast {
  flex: 1; font-size: 11px; color: #4ade80;
  font-family: 'JetBrains Mono', monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.td-toast-enter-active, .td-toast-leave-active { transition: opacity .3s; }
.td-toast-enter-from, .td-toast-leave-to { opacity: 0; }
</style>
