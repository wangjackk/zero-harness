<template>
  <div class="cf-form">
    <div class="cf-cols">
      <div class="cf-col cf-col-left">
        <div class="am-field">
          <label class="am-label">preset</label>
          <div class="cf-preset-row">
            <div
              v-for="p in props.presets"
              :key="p.id"
              class="cf-preset-chip"
              :class="{ on: form.preset === p.id, user: p.source === 'user' }"
              :title="p.description"
              @click="selectPreset(p.id)"
            >
              {{ p.name }}
              <span
                v-if="p.source === 'user'"
                class="cf-preset-del"
                title="删除此预设"
                @click.stop="onDeletePreset(p.id)"
              >×</span>
            </div>
            <div
              class="cf-preset-chip cf-preset-add"
              title="复制当前预设为新预设 (copy-only)"
              @click="showCopy = !showCopy"
            >+</div>
          </div>
          <div v-if="showCopy" class="cf-preset-copy">
            <NInput v-model:value="copyId" size="tiny" placeholder="新 preset id (小写字母/数字/_)" />
            <NInput v-model:value="copyName" size="tiny" placeholder="显示名 (可选)" />
            <NButton size="tiny" :loading="copying" @click="onCopyPreset">复制</NButton>
          </div>
        </div>

        <div v-if="!presetLocked" class="am-field">
          <label class="am-label">project_dir</label>
          <NAutoComplete
            v-model:value="form.project_dir"
            :options="projectSuggestions"
            filterable
            size="small"
            placeholder="输入文件夹路径或从历史选..."
          />
        </div>

        <div v-if="!presetLocked" class="am-advanced-toggle" @click="showAdvanced = !showAdvanced">
          {{ showAdvanced ? '▾' : '▸' }} 高级参数
        </div>
        <div v-if="showAdvanced && !presetLocked" class="am-advanced">
          <div class="am-field">
            <label class="am-label">model <span v-if="form.model" class="cf-model-clear" @click="form.model = null">×</span></label>
            <div class="cf-model-picker">
              <div class="cf-model-provs">
                <div
                  v-for="p in providers"
                  :key="p"
                  class="cf-model-prov"
                  :class="{ on: selectedProvider === p }"
                  @click="selectedProvider = p"
                >{{ p }}</div>
              </div>
              <div class="cf-model-list">
                <div
                  v-for="m in modelsForProvider(selectedProvider)"
                  :key="m.value"
                  class="cf-model-item"
                  :class="{ on: form.model === m.value }"
                  @click="form.model = m.value"
                >{{ m.short }}</div>
                <div v-if="!modelsForProvider(selectedProvider).length" class="cf-model-empty">—</div>
              </div>
            </div>
          </div>
          <div class="am-field am-row">
            <label class="am-label">plan_mode</label>
            <NSwitch v-model:value="form.plan_mode" size="small" />
          </div>
          <div class="am-field">
            <label class="am-label">max_turns</label>
            <NInputNumber v-model:value="form.max_turns" size="small" :show-button="false" placeholder="留空不限" style="width:100%" />
          </div>
          <div class="am-field">
            <label class="am-label">extra_instructions</label>
            <NInput v-model:value="form.extra_instructions" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" size="small" />
          </div>
        </div>
      </div>

      <div class="cf-col cf-col-right">
        <div class="cf-desc-bar">
          {{ selectedDesc || '点击 preset 或下方任意行查看描述' }}
        </div>

        <div v-if="presetLocked" class="am-field">
          <label class="am-label">skills (preset 已声明, copy preset 才能改)</label>
          <div class="cf-checklist cf-locked-list">
            <div v-for="s in selectedPreset?.preload_skills" :key="`l2:${s}`" class="cf-row cf-row-locked">
              <span class="cf-seg-btn cf-seg-l2 on">L2</span>
              <span class="cf-row-name">{{ s }}</span>
            </div>
            <div v-for="s in selectedPreset?.level1_skills" :key="`l1:${s}`" class="cf-row cf-row-locked">
              <span class="cf-seg-btn cf-seg-l1 on">L1</span>
              <span class="cf-row-name">{{ s }}</span>
            </div>
            <div
              v-if="!(selectedPreset?.preload_skills?.length || selectedPreset?.level1_skills?.length)"
              class="cf-hint"
            >preset 未声明 skills</div>
          </div>
        </div>

        <div v-else class="am-field">
          <label class="am-label">
            skills (L1=仅注入 name+desc, L2=全量预加载; 互斥, 不勾=不加载)
          </label>
          <NInput
            v-model:value="skillFilter"
            size="small"
            placeholder="搜索过滤..."
            class="cf-filter"
          />
          <div class="cf-checklist">
            <template v-if="filteredPrimeSkillOptions.length">
              <div class="cf-group-header cf-group-prime">Prime Skills</div>
              <div
                v-for="opt in filteredPrimeSkillOptions"
                :key="`prime:${opt.value}`"
                class="cf-row cf-row-prime"
                :class="{ 'cf-row-selected': selectedKey === `skill:${opt.value}` }"
                :title="opt.description"
                @click="selectSkill(opt.value)"
              >
                <div class="cf-seg" @click.stop>
                  <button
                    type="button"
                    class="cf-seg-btn cf-seg-l1"
                    :class="{ on: form.level1_skills.includes(opt.value) }"
                    @click.stop="toggleSkillL1(opt.value)"
                    title="一级: 仅注入 name+desc"
                  >L1</button>
                  <button
                    type="button"
                    class="cf-seg-btn cf-seg-l2"
                    :class="{ on: form.preload_skills.includes(opt.value) }"
                    @click.stop="toggleSkillL2(opt.value)"
                    title="二级: 全量预加载"
                  >L2</button>
                </div>
                <div class="cf-row-name-block">
                  <span class="cf-row-name">{{ opt.label }}</span>
                </div>
              </div>
            </template>

            <template v-if="filteredBuiltinSkillOptions.length">
              <div class="cf-group-header cf-group-builtin">Classic Skills</div>
              <div
                v-for="opt in filteredBuiltinSkillOptions"
                :key="`builtin:${opt.value}`"
                class="cf-row cf-row-builtin"
                :class="{ 'cf-row-selected': selectedKey === `skill:${opt.value}` }"
                :title="opt.description"
                @click="selectSkill(opt.value)"
              >
                <div class="cf-seg" @click.stop>
                  <button
                    type="button"
                    class="cf-seg-btn cf-seg-l1"
                    :class="{ on: form.level1_skills.includes(opt.value) }"
                    @click.stop="toggleSkillL1(opt.value)"
                    title="一级: 仅注入 name+desc"
                  >L1</button>
                  <button
                    type="button"
                    class="cf-seg-btn cf-seg-l2"
                    :class="{ on: form.preload_skills.includes(opt.value) }"
                    @click.stop="toggleSkillL2(opt.value)"
                    title="二级: 全量预加载"
                  >L2</button>
                </div>
                <div class="cf-row-name-block">
                  <span class="cf-row-name">{{ opt.label }}</span>
                </div>
              </div>
            </template>

            <div v-if="!filteredPrimeSkillOptions.length && !filteredBuiltinSkillOptions.length" class="cf-hint">
              {{ (primeSkillOptions.length || skillOptions.length) ? '无匹配项' : (skillLoadError || '加载 skill 列表中...') }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="cf-footer">
      <NButton
        size="small"
        type="primary"
        :loading="creating"
        :disabled="!presetLocked && !form.project_dir?.trim()"
        @click="onCreate"
      >
        Create
      </NButton>
      <div v-if="createError" class="am-error">{{ createError }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NButton, NInput, NInputNumber, NAutoComplete, NSwitch,
} from 'naive-ui'
import type { CreateAgentParams, PresetRow } from '../composables/useAgents'

const props = defineProps<{
  httpBase: string
  projectSuggestions: string[]
  createAgent: (params: CreateAgentParams) => Promise<string | null>
  creating: boolean
  presets: PresetRow[]
  copyPreset: (from: string, newId: string, name?: string) => Promise<string | null>
  deletePreset: (id: string) => Promise<string | null>
}>()

const emit = defineEmits<{
  (e: 'create', params: CreateAgentParams, newAgentId: string): void
  (e: 'error', msg: string): void
}>()

const showAdvanced = ref(false)
const createError = ref('')

const form = reactive({
  preset: 'prime',
  project_dir: '' as string,
  model: '' as string | null,
  plan_mode: false,
  max_turns: null as number | null,
  extra_instructions: '' as string,
  preload_skills: [] as string[],
  level1_skills: [] as string[],
})

// ---- preset copy (copy-only 创作) ----
const showCopy = ref(false)
const copyId = ref('')
const copyName = ref('')
const copying = ref(false)

async function onCopyPreset() {
  createError.value = ''
  const id = copyId.value.trim()
  if (!id) return
  copying.value = true
  try {
    const err = await props.copyPreset(form.preset, id, copyName.value.trim() || undefined)
    if (err) {
      createError.value = err
      emit('error', err)
    } else {
      form.preset = id
      showCopy.value = false
      copyId.value = ''
      copyName.value = ''
    }
  } finally {
    copying.value = false
  }
}

async function onDeletePreset(id: string) {
  const err = await props.deletePreset(id)
  if (err) {
    createError.value = err
    emit('error', err)
  } else if (form.preset === id) {
    form.preset = 'prime'
  }
}

// model 选项: 从 /models 拉, 按 provider 分组
interface ModelOption { label: string; value: string; short: string; provider: string }
const allModels = ref<ModelOption[]>([])
const selectedProvider = ref('')

const providers = computed<string[]>(() => {
  const set = new Set<string>()
  for (const m of allModels.value) set.add(m.provider)
  return [...set].sort()
})

function modelsForProvider(p: string): ModelOption[] {
  if (!p) return []
  return allModels.value.filter(m => m.provider === p)
}

async function loadModels() {
  try {
    const res = await fetch(`${props.httpBase}/models`)
    if (!res.ok) return
    const data = await res.json()
    const models = (data.models || []) as Array<{
      key: string; provider: string; name: string
    }>
    allModels.value = models.map(m => ({
      label: `${m.key}  (${m.name})`,
      value: m.key,
      short: m.name,
      provider: m.provider,
    }))
    // 默认选第一个 provider
    if (providers.value.length && !selectedProvider.value) {
      selectedProvider.value = providers.value[0]
    }
  } catch {
    allModels.value = []
  }
}

const selectedKey = ref<string>('')

const selectedPreset = computed(() => props.presets.find(p => p.id === form.preset))
// 完整预设: 声明了 skills (L1/L2 任一) 即视为组装已定死, 创建表单不再提供
// skill/model 覆盖 (要改能力 copy preset 改 yaml, 这是 copy-only 哲学).
const presetLocked = computed(() =>
  !!(selectedPreset.value?.preload_skills?.length || selectedPreset.value?.level1_skills?.length),
)

function selectPreset(id: string) {
  form.preset = id
  selectedKey.value = `preset:${id}`
}

function selectSkill(name: string) {
  selectedKey.value = `skill:${name}`
}

function toggleSkillL1(name: string) {
  if (form.level1_skills.includes(name)) {
    form.level1_skills = form.level1_skills.filter(n => n !== name)
  } else {
    form.preload_skills = form.preload_skills.filter(n => n !== name)
    form.level1_skills = [...form.level1_skills, name]
  }
}

function toggleSkillL2(name: string) {
  if (form.preload_skills.includes(name)) {
    form.preload_skills = form.preload_skills.filter(n => n !== name)
  } else {
    form.level1_skills = form.level1_skills.filter(n => n !== name)
    form.preload_skills = [...form.preload_skills, name]
  }
}

interface SkillOption { label: string; value: string; description: string }
const skillOptions = ref<SkillOption[]>([])         // classic (builtin)
const primeSkillOptions = ref<SkillOption[]>([])    // prime 自带
const skillLoadError = ref('')
const skillFilter = ref('')

function applyFilter(list: SkillOption[]): SkillOption[] {
  const q = skillFilter.value.trim().toLowerCase()
  if (!q) return list
  return list.filter(o =>
    o.value.toLowerCase().includes(q) || o.description.toLowerCase().includes(q),
  )
}

const filteredBuiltinSkillOptions = computed<SkillOption[]>(() => applyFilter(skillOptions.value))
const filteredPrimeSkillOptions = computed<SkillOption[]>(() => applyFilter(primeSkillOptions.value))

async function loadSkills() {
  skillLoadError.value = ''
  try {
    const url = `${props.httpBase}/builtin_skills`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    if (!data.ok) throw new Error(data.error || 'load failed')
    const skills = (data.skills || []) as Array<{ name: string; description: string; version?: string }>
    const primeSkills = (data.prime_skills || []) as Array<{ name: string; description: string; version?: string }>
    skillOptions.value = skills.map(s => ({
      label: s.name,
      value: s.name,
      description: s.description,
    }))
    primeSkillOptions.value = primeSkills.map(s => ({
      label: s.name,
      value: s.name,
      description: s.description,
    }))
  } catch (e) {
    skillLoadError.value = `加载失败: ${(e as Error).message}`
    skillOptions.value = []
    primeSkillOptions.value = []
  }
}

const selectedDesc = computed<string>(() => {
  if (!selectedKey.value) return ''
  const idx = selectedKey.value.indexOf(':')
  const kind = selectedKey.value.slice(0, idx)
  const name = selectedKey.value.slice(idx + 1)
  if (kind === 'preset') {
    const p = props.presets.find(x => x.id === name)
    if (!p) return ''
    const head = `[preset] ${p.id} (${p.source})${p.description ? ' -- ' + p.description : ''}`
    return p.extra_instructions ? `${head}\n\n${p.extra_instructions}` : head
  }
  if (kind === 'skill') {
    const s = skillOptions.value.find(o => o.value === name)
      || primeSkillOptions.value.find(o => o.value === name)
    if (!s) return ''
    return `[skill] ${s.value}${s.description ? ': ' + s.description : ' (无描述)'}`
  }
  return ''
})

async function onCreate() {
  createError.value = ''
  try {
    const params: CreateAgentParams = presetLocked.value
      // 锁定预设: 一键创建, 只传 preset, 其余全以 preset.yaml 为准
      ? { kind: 'prime', preset: form.preset }
      : {
          kind: 'prime',
          preset: form.preset,
          project_dir: form.project_dir.trim() || undefined,
          model: form.model || undefined,
          plan_mode: form.plan_mode,
          max_turns: form.max_turns ?? undefined,
          extra_instructions: form.extra_instructions.trim() || undefined,
          preload_skills: form.preload_skills.length ? form.preload_skills.slice() : undefined,
          level1_skills: form.level1_skills.length ? form.level1_skills.slice() : undefined,
        }
    const id = await props.createAgent(params)
    if (id) {
      emit('create', params, id)
      form.model = null
      form.plan_mode = false
      form.max_turns = null
      form.extra_instructions = ''
      form.preload_skills = []
      form.level1_skills = []
      showAdvanced.value = false
    }
  } catch (e) {
    createError.value = (e as Error).message
    emit('error', (e as Error).message)
  }
}

onMounted(() => {
  loadSkills()
  loadModels()
})
</script>

<style scoped>
.cf-form { display: flex; flex-direction: column; gap: 16px; height: 100%; }

.cf-preset-row {
  display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
}
.cf-preset-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 10px;
  font-size: 11px; font-family: ui-monospace, 'Consolas', monospace;
  color: #94a3b8;
  background: #1a1d27;
  border: 1px solid #2d3148; border-radius: 999px;
  cursor: pointer; user-select: none;
  transition: all .12s;
}
.cf-preset-chip:hover { border-color: #4f46e5; color: #cbd5e1; }
.cf-preset-chip.on {
  background: #1f2235; border-color: #6366f1; color: #c7d2fe;
}
.cf-preset-chip.user { border-style: dashed; }
.cf-preset-del {
  color: #6b7280; font-size: 13px; line-height: 1;
  padding: 0 1px; border-radius: 50%;
}
.cf-preset-del:hover { color: #f87171; }
.cf-preset-add { padding: 2px 12px; color: #6b7280; }
.cf-preset-copy {
  display: flex; gap: 6px; margin-top: 6px; align-items: center;
}
.cf-preset-copy .n-input { flex: 1; }

.cf-cols {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(320px, 1.3fr);
  gap: 24px;
  flex: 1; min-height: 0;
}
.cf-col { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.cf-col-right { gap: 14px; }

.am-field { display: flex; flex-direction: column; gap: 3px; }
.am-row { flex-direction: row; align-items: center; gap: 8px; }
.am-label { font-size: 11px; color: #818cf8; }
.am-advanced-toggle {
  font-size: 11px; color: #6b7280; cursor: pointer; user-select: none;
  margin-top: 4px;
}
.am-advanced { display: flex; flex-direction: column; gap: 8px; padding: 8px 0; }
.am-error { font-size: 11px; color: #f87171; }

.cf-model-clear {
  float: right; cursor: pointer; color: #6b7280;
  font-size: 14px; line-height: 1; padding: 0 2px;
}
.cf-model-clear:hover { color: #f87171; }
.cf-model-picker {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 0;
  border: 1px solid #2d3148;
  border-radius: 6px;
  overflow: hidden;
  background: #1a1d27;
  max-height: 160px;
}
.cf-model-provs {
  overflow-y: auto;
  border-right: 1px solid #2d3148;
  background: #15171f;
}
.cf-model-prov {
  padding: 4px 10px;
  font-size: 11px;
  font-family: ui-monospace, 'Consolas', monospace;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
  border-left: 2px solid transparent;
  transition: background .12s, color .12s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cf-model-prov:hover { background: #252835; color: #cbd5e1; }
.cf-model-prov.on {
  background: #1f2235;
  color: #818cf8;
  border-left-color: #818cf8;
}
.cf-model-list {
  overflow-y: auto;
}
.cf-model-item {
  padding: 4px 10px;
  font-size: 11px;
  font-family: ui-monospace, 'Consolas', monospace;
  color: #cbd5e1;
  cursor: pointer;
  user-select: none;
  border-left: 2px solid transparent;
  transition: background .12s;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cf-model-item:hover { background: #252835; }
.cf-model-item.on {
  background: #1f2235;
  color: #818cf8;
  border-left-color: #818cf8;
}
.cf-model-empty {
  padding: 8px 10px;
  font-size: 11px;
  color: #4b5563;
  text-align: center;
}

.cf-filter { margin-bottom: 4px; }

/* 锁定预设: 组装只读概览 */
.cf-locked-list { opacity: .85; }
.cf-row-locked { cursor: default; }
.cf-row-locked:hover { background: transparent; }
.cf-checklist {
  max-height: 260px; overflow-y: auto;
  border: 1px solid #2d3148; border-radius: 6px;
  background: #1a1d27;
}
.cf-group-header {
  position: sticky; top: 0; z-index: 1;
  font-size: 10px; font-weight: 700;
  letter-spacing: 0.5px; text-transform: uppercase;
  padding: 4px 10px;
  border-bottom: 1px solid #1f2230;
  background: #15171f;
  color: #94a3b8;
  user-select: none;
}
.cf-group-prime { color: #c084fc; border-left: 2px solid #a855f7; }
.cf-group-builtin { color: #60a5fa; border-left: 2px solid #3b82f6; }
.cf-row-prime.cf-row-selected { border-left-color: #a855f7; }
.cf-row-builtin.cf-row-selected { border-left-color: #3b82f6; }
.cf-row {
  display: flex; align-items: center;
  gap: 6px; padding: 5px 10px;
  border-bottom: 1px solid #1f2230;
  cursor: pointer; user-select: none;
  transition: background .12s;
  border-left: 2px solid transparent;
}
.cf-row:last-child { border-bottom: none; }
.cf-row:hover { background: #252835; }
.cf-row-selected {
  border-left-color: #818cf8;
  background: #1f2235;
}
.cf-row-selected:hover { background: #252a45; }

.cf-seg {
  display: inline-flex;
  border: 1px solid #2d3148;
  border-radius: 999px;
  overflow: hidden;
  flex-shrink: 0;
  background: #0f1117;
}
.cf-seg-btn {
  border: none;
  background: transparent;
  color: #6b7280;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  cursor: pointer;
  font-family: ui-monospace, 'Consolas', monospace;
  line-height: 1.4;
  transition: all .12s;
}
.cf-seg-btn:hover { color: #cbd5e1; }
.cf-seg-l1.on { background: #2563eb; color: #fff; }
.cf-seg-l2.on { background: #16a34a; color: #fff; }

.cf-row-name {
  flex: 1; min-width: 0;
  font-size: 12px; color: #e2e8f0;
  font-family: ui-monospace, 'Consolas', monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cf-row-name-block {
  flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px;
}
.cf-hint { font-size: 10px; color: #6b7280; margin-top: 2px; padding: 4px 12px; }

.cf-desc-bar {
  padding: 10px 14px;
  font-size: 12px; color: #cbd5e1;
  background: #0f1117; border: 1px solid #2d3148; border-radius: 6px;
  height: 200px; line-height: 1.6;
  font-family: 'Segoe UI', system-ui, sans-serif;
  word-break: break-word; white-space: pre-wrap;
  flex-shrink: 0;
  overflow-y: auto;
}

.cf-footer {
  display: flex; align-items: center; gap: 12px;
  padding-top: 12px; border-top: 1px solid #2d3148;
}
</style>
