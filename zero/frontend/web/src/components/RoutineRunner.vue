<template>
  <div class="routine-runner">
    <!-- 顶部工具栏 -->
    <div class="rr-header">
      <span class="rr-title">Routine Runner</span>
      <div class="rr-actions">
        <NButton size="small" quaternary @click="clear">⌫ 清空</NButton>
        <NButton
          size="small"
          type="primary"
          :loading="running"
          :disabled="!canRun"
          @click="run"
        >
          {{ running ? '执行中' : '▶ 运行' }}
        </NButton>
      </div>
    </div>

    <div class="rr-layout">
      <!-- 左侧:routine 列表 -->
      <div class="rr-sidebar">
        <div class="rr-filter-row">
          <NInput
            v-model:value="filter"
            size="small"
            placeholder="搜索..."
            clearable
            class="rr-search"
          />
          <NSelect
            v-model:value="hubFilter"
            size="small"
            :options="hubOptions"
            class="rr-hub-select"
          />
        </div>
        <div class="rr-list">
        <NScrollbar style="height:100%">
          <div
            v-for="r in filteredRoutines"
            :key="r.name"
            class="rr-routine"
            :class="{ hidden: r.meta?.hidden, passive: r.is_passive, active: selected?.name === r.name }"
            @click="selectRoutine(r)"
          >
            <span class="rr-routine-name">{{ r.name }}</span>
            <NTag v-if="r.meta?.hidden" size="tiny" :bordered="false" style="color:#6b7280">hidden</NTag>
            <NTag v-if="r.is_passive" size="tiny" type="info" :bordered="false">passive</NTag>
          </div>
          <div v-if="!filteredRoutines.length" class="rr-empty">无结果</div>
        </NScrollbar>
        </div>
      </div>

      <!-- 右侧:表单 + 结果 -->
      <div class="rr-body">
        <div v-if="!selected" class="rr-form-empty">请从左侧选择一个 routine</div>
        <NScrollbar v-else style="height:100%">
          <div class="rr-form">
            <div class="rr-form-name">{{ selected.name }}</div>
            <p v-if="selected.meta?.description" class="rr-form-desc">{{ selected.meta?.description }}</p>
            <div v-if="selected.is_passive" class="rr-form-noparams rr-form-readonly">passive routine 由系统自动拉起,不可手动运行</div>
            <div v-else-if="!paramsList.length" class="rr-form-noparams">该 routine 无参数,直接点运行</div>
            <div v-for="p in paramsList" :key="p.name" class="rr-form-field">
              <div class="rr-form-head">
                <span class="rr-form-label">{{ p.name }}</span>
                <span class="rr-form-type">{{ p.type }}</span>
                <span class="rr-form-flag" :class="p.required ? 'is-required' : 'is-optional'">{{ p.required ? '必填' : '可选' }}</span>
              </div>
              <div class="rr-form-input">
                <NInputNumber
                  v-if="p.type === 'number' || p.type === 'integer'"
                  v-model:value="formValues[p.name]"
                  size="small"
                  style="width:100%"
                />
                <NSwitch
                  v-else-if="p.type === 'boolean'"
                  v-model:value="formValues[p.name]"
                  size="small"
                />
                <NInput
                  v-else
                  v-model:value="formValues[p.name]"
                  size="small"
                  :placeholder="p.default ? `默认 ${p.default}` : ''"
                />
              </div>
              <div v-if="p.description" class="rr-form-hint">{{ p.description }}</div>
            </div>
          </div>
        </NScrollbar>

        <template v-if="results.length || error">
          <NAlert v-if="error" type="error" :show-icon="false" class="rr-error">
            <pre>{{ error }}</pre>
          </NAlert>
          <div class="rr-results">
          <NScrollbar style="height:100%">
            <div v-for="(r, i) in results" :key="i" class="rr-result-item">
              <span class="rr-result-name">{{ r.name }}</span>
              <pre class="rr-result-value">{{ formatResult(r.result) }}</pre>
            </div>
          </NScrollbar>
          </div>
        </template>
        <div v-else-if="!running" class="rr-hint">填好参数点运行</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NInput, NInputNumber, NSwitch, NButton, NTag, NSelect, NScrollbar, NAlert } from 'naive-ui'

interface JsonSchemaProperty {
  type?: string
  description?: string
  default?: unknown
  title?: string
  [key: string]: unknown
}

interface JsonSchema {
  type?: string
  title?: string
  description?: string
  properties?: Record<string, JsonSchemaProperty>
  required?: string[]
  [key: string]: unknown
}

export interface RoutineMeta {
  description?: string
  input_schema?: JsonSchema
  output_schema?: JsonSchema
  hidden?: boolean
  tool?: boolean
  [key: string]: unknown
}

export interface RoutineInfo {
  name: string
  is_passive?: boolean
  hub_id?: string
  meta?: RoutineMeta
}

interface RunResult {
  name: string
  result: unknown
}

const props = defineProps<{
  routines: RoutineInfo[]
}>()

interface RunPayload {
  name: string
  kwargs: Record<string, unknown>
}

const emit = defineEmits<{
  run: [payload: RunPayload, onResult: (data: unknown[]) => void, onError: (msg: string) => void, onDone: () => void]
}>()

const filter = ref('')
const hubFilter = ref<string>('')
// 表单值: 按 schema type 初始化为 number|null / boolean / string,
// 绑定到 NInputNumber/NSwitch/NInput 各自的 v-model (联合类型, 不细分).
const formValues = ref<Record<string, any>>({})
const selected = ref<RoutineInfo | null>(null)
const running = ref(false)
const results = ref<RunResult[]>([])
const error = ref('')

// 选中 routine 变化时,用 input_schema 默认值初始化表单(类型安全:number→null,
// boolean→false,其余→'' ).无 schema 的 routine 表单空着,靠 paramsList 兜底为空.
watch(selected, (r) => {
  formValues.value = {}
  if (!r?.meta?.input_schema?.properties) return
  for (const [name, prop] of Object.entries(r.meta.input_schema.properties)) {
    const t = prop.type
    if (t === 'number' || t === 'integer') {
      formValues.value[name] = prop.default !== undefined ? Number(prop.default) : null
    } else if (t === 'boolean') {
      formValues.value[name] = prop.default !== undefined ? Boolean(prop.default) : false
    } else {
      formValues.value[name] = prop.default !== undefined ? String(prop.default) : ''
    }
  }
})

const filteredRoutines = computed(() => {
  const q = filter.value.toLowerCase()
  const hub = hubFilter.value
  return props.routines.filter(r => {
    if (hub && (r.hub_id || '') !== hub) return false
    if (!q) return true
    return r.name.includes(q)
      || r.meta?.description?.toLowerCase().includes(q)
  })
})

const hubOptions = computed(() => {
  const hubs = new Set<string>()
  for (const r of props.routines) {
    if (r.hub_id) hubs.add(r.hub_id)
  }
  return [
    { label: '全部 hub', value: '' },
    ...[...hubs].sort().map(h => ({ label: h, value: h })),
  ]
})

interface ParamItem {
  name: string
  type: string
  description: string
  default?: string
  required: boolean
}

const paramsList = computed<ParamItem[]>(() => {
  const r = selected.value
  if (!r?.meta?.input_schema?.properties) return []
  const required = new Set(r.meta.input_schema.required ?? [])
  return Object.entries(r.meta.input_schema.properties).map(([name, prop]) => ({
    name,
    type: prop.type ?? 'any',
    description: prop.description ?? '',
    default: prop.default !== undefined ? JSON.stringify(prop.default) : undefined,
    required: required.has(name),
  }))
})

function selectRoutine(r: RoutineInfo): void {
  selected.value = selected.value?.name === r.name ? null : r
}

const canRun = computed(() => {
  if (running.value) return false
  // passive routine 由 kernel 自动拉起,不允许手动运行(可选中查看文档,但 Run 禁用)
  if (selected.value?.is_passive) return false
  return !!selected.value
})

function clear(): void {
  results.value = []
  error.value = ''
}

function formatResult(val: unknown): string {
  if (typeof val === 'object' && val !== null) return JSON.stringify(val, null, 2)
  return String(val ?? '')
}

async function run(): Promise<void> {
  if (running.value || !canRun.value) return
  const r = selected.value
  if (!r) return
  running.value = true
  error.value = ''
  results.value = []
  emit('run', { name: r.name, kwargs: { ...formValues.value } }, onResult, onError, onDone)
}

function onResult(data: unknown[]): void {
  results.value = Array.isArray(data)
    ? (data as RunResult[])
    : [{ name: 'result', result: data }]
}

function onError(msg: string): void {
  error.value = msg
}

function onDone(): void {
  running.value = false
}

defineExpose({ onResult, onError, onDone })
</script>

<style scoped>
.routine-runner {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #13151f;
}

.rr-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  border-bottom: 1px solid #2d3148;
  flex-shrink: 0;
}

.rr-title {
  font-size: 12px;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.rr-actions { display: flex; gap: 6px; }

.rr-layout {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* 左侧 */
.rr-sidebar {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #2d3148;
  overflow: hidden;
}

.rr-filter-row { display: flex; gap: 0; border-bottom: 1px solid #2d3148; }
.rr-search { flex: 1; border-radius: 0 !important; border: none; }
.rr-hub-select { width: 96px; flex-shrink: 0; }
.rr-hub-select :deep(.n-base-selection) { border-radius: 0 !important; border: none; border-left: 1px solid #2d3148; }

.rr-list { flex: 1; overflow: hidden; }

.rr-routine {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  cursor: pointer;
  font-size: 14px;
  color: #cbd5e1;
  transition: background 0.1s;
  user-select: none;
}
.rr-routine:hover { background: #1e2235; color: #fff; }
.rr-routine.active { background: #252a40; color: #a5b4fc; }
.rr-routine.hidden { color: #6b7280; }
.rr-routine.hidden:hover { color: #94a3b8; }
.rr-routine.passive { color: #6b7280; }
.rr-routine.passive:hover { color: #94a3b8; }

.rr-routine-name {
  flex: 1;
  font-family: monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rr-empty { font-size: 13px; color: #4b5280; text-align: center; padding: 14px; }

/* 右侧表单区 */
.rr-body {
  display: flex;
  flex-direction: column;
  flex: 3;
  min-width: 0;
  overflow: hidden;
  padding: 10px;
  gap: 8px;
}

.rr-form-empty { margin: auto; color: #4b5280; font-size: 13px; text-align: center; }

.rr-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 4px 20px;
}

.rr-form-name {
  font-family: monospace;
  font-size: 16px;
  font-weight: 700;
  color: #c7d2fe;
}

.rr-form-desc { margin: -4px 0 4px; font-size: 12px; color: #94a3b8; line-height: 1.5; }

.rr-form-noparams { color: #6b7280; font-size: 13px; padding: 8px 0; }
.rr-form-readonly { color: #fca5a5; }

.rr-form-field {
  background: #1a1d27;
  border: 1px solid #2d3148;
  border-radius: 5px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.rr-form-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.rr-form-label { font-family: monospace; font-size: 12px; color: #c7d2fe; font-weight: 600; }
.rr-form-type { font-family: monospace; font-size: 11px; color: #818cf8; background: rgba(129,140,248,0.1); padding: 1px 5px; border-radius: 3px; }
.rr-form-flag { font-size: 10px; padding: 1px 5px; border-radius: 3px; font-weight: 500; }
.rr-form-flag.is-required { color: #fca5a5; background: rgba(252,165,165,0.08); }
.rr-form-flag.is-optional { color: #6b7280; background: rgba(107,114,128,0.1); }
.rr-form-hint { font-size: 11px; color: #6b7280; line-height: 1.5; }

.rr-error {
  flex-shrink: 0;
}
.rr-error pre {
  margin: 0;
  font-family: monospace;
  font-size: 13px;
  white-space: pre-wrap;
}

.rr-results { flex-shrink: 0; max-height: 40%; overflow: hidden; }

.rr-result-item {
  background: #131720;
  border: 1px solid #2d3148;
  border-left: 3px solid #22c55e;
  border-radius: 5px;
  padding: 8px 12px;
  margin-bottom: 6px;
}

.rr-result-name {
  display: block;
  font-size: 13px;
  color: #86efac;
  font-weight: 600;
  margin-bottom: 5px;
  font-family: monospace;
}

.rr-result-value {
  font-size: 13px;
  color: #cbd5e1;
  font-family: 'Consolas', monospace;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

.rr-hint {
  font-size: 13px;
  color: #4b5280;
  text-align: center;
  padding: 4px;
}
</style>
