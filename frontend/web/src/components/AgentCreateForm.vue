<template>
  <div class="af-form">
    <!-- 预制体: 常用配置模板 (~/.zero/presets), 点击预填三板块 -->
    <section v-if="presets.length" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">预制体</span>
        <span class="af-sec-hint">一键预填, 可再微调</span>
      </div>
      <div class="af-presets">
        <span v-for="p in presets" :key="p.id" class="af-preset-wrap">
          <!-- 内联删除确认态 (无原生 confirm) -->
          <span v-if="confirmDel === p.id" class="af-preset af-preset-danger">
            删除 {{ p.label }}?
            <span class="af-preset-ok" title="确认删除" @click.stop="doDelete(p)"><Icon :comp="Check" :size="11" /></span>
            <span class="af-preset-no" title="取消" @click.stop="confirmDel = null"><Icon :comp="X" :size="11" /></span>
          </span>
          <template v-else>
            <button
              class="af-preset"
              :title="p.description"
              @click="applyPreset(p)"
            ><span class="af-preset-ic"><Icon :comp="Stars" :size="12" /></span>{{ p.label }}</button>
            <span class="af-preset-del" title="删除预制体" @click.stop="confirmDel = p.id"><Icon :comp="X" :size="9" /></span>
          </template>
        </span>
      </div>
    </section>

    <!-- 类型清单: agent_hub/types 动态拉取, 无硬编码类型 -->
    <section class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">类型</span>
        <span class="af-sec-hint">由各类型 manager 注册</span>
      </div>
      <div v-if="types.length" class="af-types">
        <div
          v-for="t in types"
          :key="t.kind"
          class="af-type"
          :class="{ on: selectedKind === t.kind }"
          @click="selectKind(t.kind)"
        >
          <div class="af-type-top">
            <span class="af-type-label">{{ t.label }}</span>
            <code class="af-type-kind">{{ t.kind }}</code>
          </div>
          <div class="af-type-desc">{{ t.description }}</div>
        </div>
      </div>
      <div v-else-if="!loadingTypes" class="af-empty">agent_hub/types 无可用类型</div>
    </section>

    <!-- 动态字段: 按 manifest create_fields 渲染 -->
    <section v-if="manifest && fields.length" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">自身配置</span>
      </div>
      <div class="af-fields">
        <div
          v-for="f in fields"
          :key="f.name"
          class="af-field"
          :class="{ 'af-field-wide': f.type === 'textarea' }"
        >
          <label class="af-label">
            {{ f.label }}<span v-if="f.required" class="af-req" />
            <!-- textarea 有内容时渲染态/编辑态切换 -->
            <span
              v-if="f.type === 'textarea' && form[f.name]"
              class="af-ta-toggle"
              @click="editingField = editingField === f.name ? null : f.name"
            >{{ editingField === f.name ? '完成' : '编辑' }}</span>
          </label>

          <!-- text: autocomplete=project_dir 时挂项目历史建议 -->
          <NAutoComplete
            v-if="f.type === 'text' && f.autocomplete === 'project_dir'"
            v-model:value="form[f.name]"
            :options="projectSuggestions"
            filterable
            size="small"
            :placeholder="f.placeholder || ''"
          />
          <NInput
            v-else-if="f.type === 'text'"
            v-model:value="form[f.name]"
            size="small"
            :placeholder="f.placeholder || ''"
          />
          <!-- textarea: 长文指令 (附加指令/AGENTS.md). 有内容默认 md 渲染
               预览 (点「编辑」切源码); 空值直接编辑态 -->
          <template v-else-if="f.type === 'textarea'">
            <div
              v-if="form[f.name] && editingField !== f.name"
              class="af-ta-md"
              v-html="renderMarkdown(form[f.name])"
            />
            <NInput
              v-else
              v-model:value="form[f.name]"
              class="af-textarea"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 24 }"
              size="small"
              :placeholder="f.placeholder || ''"
            />
          </template>
        </div>
      </div>
    </section>

    <!-- 模型: 平台统一选择器, 仅声明 uses_model 的类型渲染
         (model 值由各 agent 自行处理; 自有模型体系/无模型 agent 不显示) -->
    <section v-if="manifest?.uses_model" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">模型</span>
        <span v-if="model" class="af-sec-clear" @click="model = ''">清除</span>
      </div>
      <ModelPicker v-model="model" :http-base="httpBase" />
    </section>

    <!-- 协议: 所选模型的 provider 在 apis 矩阵声明了 >1 个协议时才出现
         (单协议/未声明 = 矩阵推导结果唯一, 无可选). 不选 = 缺省推导. -->
    <section v-if="manifest?.uses_model && protocolOptions.length > 1" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">协议</span>
        <span class="af-sec-hint">不选 = 按矩阵推导</span>
      </div>
      <div class="af-protos">
        <button
          class="af-proto" :class="{ on: !protocol }" @click="protocol = ''"
        >自动</button>
        <button
          v-for="p in protocolOptions" :key="p"
          class="af-proto" :class="{ on: protocol === p }" @click="protocol = p"
        >{{ p }}</button>
      </div>
    </section>

    <!-- 初始 skill: 平台统一选择器, 仅声明 uses_skills 的类型渲染
         (共享库名单, 各 agent manager 拿到后自行预载) -->
    <section v-if="manifest?.uses_skills" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">初始 Skill</span>
        <span class="af-sec-hint">点徽标切换 摘要 / 全文</span>
        <span v-if="skills.length" class="af-sec-clear" @click="skills = []">清除</span>
      </div>
      <SkillPicker v-model="skills" :http-base="httpBase" />
    </section>

    <!-- Routine 白名单: 平台统一选择器, 仅声明 uses_routines 的类型渲染
         (选中作 prompt_routines 参数, 各 agent manager 拿到后自行处理) -->
    <section v-if="manifest?.uses_routines" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">Routine 白名单</span>
        <span class="af-sec-hint">进 system prompt 的接口表</span>
        <span v-if="promptRoutines.length" class="af-sec-clear" @click="promptRoutines = []">清除</span>
      </div>
      <RoutinePicker v-model="promptRoutines" :http-base="httpBase" />
    </section>

    <!-- Routine 别名: 仅声明 uses_aliases 的类型渲染.
         {全名: 标签} 行编辑, 提交作 routine_aliases 参数 ---- 含 '/' 的
         白名单 routine 无显式标签不进接口表 (对齐后端渲染规则). -->
    <section v-if="manifest?.uses_aliases" class="af-sec">
      <div class="af-sec-head">
        <span class="af-sec-mark" />
        <span class="af-sec-title">Routine 别名</span>
        <span class="af-sec-hint">含 '/' 的 routine 需显式标签</span>
        <span v-if="aliasRows.length" class="af-sec-clear" @click="aliasRows = []">清除</span>
      </div>
      <div class="af-alias-rows">
        <div v-for="(row, i) in aliasRows" :key="i" class="af-alias-row">
          <NInput v-model:value="row.full" size="small" placeholder="routine 全名 (如 car/shake)" />
          <span class="af-alias-arrow">→</span>
          <NInput v-model:value="row.tag" size="small" placeholder="标签 (如 shake)" />
          <span class="af-alias-del" title="删除" @click="aliasRows.splice(i, 1)"><Icon :comp="X" :size="11" /></span>
        </div>
        <button class="af-alias-add" @click="aliasRows.push({ full: '', tag: '' })">+ 添加别名</button>
      </div>
    </section>

    <div class="af-footer">
      <NButton
        size="large"
        type="primary"
        round
        block
        :disabled="!manifest"
        :loading="creating"
        @click="onCreate"
      >{{ creating ? '创建中…' : '创建 Agent' }}</NButton>
      <NButton size="small" quaternary :disabled="!manifest" @click="openSaveModal">存为预制体</NButton>
    </div>
    <div v-if="createError" class="af-error">{{ createError }}</div>

    <!-- 存为预制体: 应用内命名对话框 (slug 已存在时提示覆盖) -->
    <NModal v-model:show="saveModal" preset="card" size="small" :title="saveTitle"
      style="max-width:380px" :mask-closable="true">
      <div class="af-save-body">
        <NInput v-model:value="saveLabel" size="small" placeholder="预制体名称"
          @keydown.enter="savePreset" />
        <div v-if="saveExists" class="af-save-warn">已存在同名预制体 "{{ saveExists.label }}", 保存将覆盖</div>
        <div class="af-save-actions">
          <NButton size="small" @click="saveModal = false">取消</NButton>
          <NButton size="small" type="primary" @click="savePreset">{{ saveExists ? '覆盖保存' : '保存' }}</NButton>
        </div>
      </div>
    </NModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { NButton, NInput, NAutoComplete, NModal } from 'naive-ui'
import Icon from './Icon.vue'
import { Stars, Check, X } from '@vicons/tabler'
import ModelPicker from './ModelPicker.vue'
import SkillPicker from './SkillPicker.vue'
import RoutinePicker from './RoutinePicker.vue'
import { renderMarkdown } from '../utils/markdown'
import type { AgentManifest, AgentManifestField, CreateAgentParams, SkillPick } from '../composables/useAgents'

const props = defineProps<{
  httpBase: string
  projectSuggestions: string[]
  createAgent: (kind: string, params: CreateAgentParams) => Promise<string | null>
  creating: boolean
}>()

const emit = defineEmits<{
  (e: 'create', kind: string, params: CreateAgentParams, newAgentId: string): void
  (e: 'error', msg: string): void
}>()

const types = ref<AgentManifest[]>([])
const loadingTypes = ref(false)
const selectedKind = ref('')
const createError = ref('')
/** 平台统一模型选择器的选中值 (所有 agent 类型共用, 提交时作 model 参数). */
const model = ref('')
/** 协议选择 (agent 策略): '' = 缺省按 provider apis 矩阵推导. */
const protocol = ref('')
/** 当前处于「编辑源码」态的 textarea 字段名 (null = 全部渲染态预览). */
const editingField = ref<string | null>(null)
/** model key -> 可选协议列表 (models/list 的 protocols, provider 级事实). */
const modelProtocols = ref<Record<string, string[]>>({})
/** 当前所选模型的可选协议 (未选模型 / 未声明矩阵 -> []). */
const protocolOptions = computed<string[]>(() =>
  model.value ? modelProtocols.value[model.value] ?? [] : [])

/** 换模型即清协议选择 (新 provider 的矩阵未必声明旧协议).
 *  sync: 与 applyPreset 的赋值顺序确定 (先 model 后 protocol, 不被
 *  deferred watcher 回头清掉). */
watch(model, () => { protocol.value = '' }, { flush: 'sync' })
/** 平台统一 skill 选择器的选中值 (提交时作 skills 数组参数, 带加载等级). */
const skills = ref<SkillPick[]>([])
/** 平台统一 routine 白名单选择器的选中值 (提交时作 prompt_routines 参数). */
const promptRoutines = ref<string[]>([])
/** Routine 别名行编辑 (提交时作 routine_aliases 参数): {全名: 标签} 映射,
 *  含 '/' 的白名单 routine 无显式标签不进接口表. */
const aliasRows = ref<{ full: string; tag: string }[]>([])
/** 预制体清单 (~/.zero/presets, 预填创建参数模板). */
interface Preset { id: string; label: string; description: string; kind: string; params: Record<string, unknown> }
const presets = ref<Preset[]>([])
/** 内联删除确认态: 待删除预制体 id (null = 无). */
const confirmDel = ref<string | null>(null)
/** 存为预制体 modal 状态. */
const saveModal = ref(false)
const saveLabel = ref('')
const saveExists = computed(() => {
  const id = slugify(saveLabel.value)
  return id ? presets.value.find(p => p.id === id) ?? null : null
})
const saveTitle = computed(() =>
  saveExists.value ? '覆盖预制体' : '存为预制体')

/** 字段值表 (kind 切换时重建). */
const form = reactive<Record<string, string>>({})

const manifest = computed(() => types.value.find(t => t.kind === selectedKind.value) ?? null)
const fields = computed<AgentManifestField[]>(() => manifest.value?.create_fields ?? [])

function selectKind(kind: string) {
  selectedKind.value = kind
  createError.value = ''
  for (const k of Object.keys(form)) delete form[k]
  model.value = ''
  skills.value = []
  promptRoutines.value = []
  editingField.value = null
}

/** 收集当前表单的创建参数 (Create 提交 / 存为预制体共用). */
function buildParams(): CreateAgentParams {
  const params: CreateAgentParams = {}
  if (model.value) params.model = model.value
  if (protocol.value) params.protocol = protocol.value
  if (skills.value.length) params.skills = skills.value
  if (promptRoutines.value.length) params.prompt_routines = promptRoutines.value
  const aliases: Record<string, string> = {}
  for (const r of aliasRows.value) {
    const full = r.full.trim()
    const tag = r.tag.trim()
    if (full && tag) aliases[full] = tag
  }
  if (Object.keys(aliases).length) params.routine_aliases = aliases
  for (const f of fields.value) {
    const v = (form[f.name] ?? '').trim()
    if (v) params[f.name] = v
  }
  return params
}

/** 预填: 按 preset 的 kind 切类型, 再回填各板块 (可微调后 Create). */
function applyPreset(p: Preset) {
  confirmDel.value = null
  if (!types.value.some(t => t.kind === p.kind)) {
    createError.value = `preset "${p.label}" references unknown kind: ${p.kind}`
    return
  }
  selectKind(p.kind)
  const params = p.params ?? {}
  if (typeof params.model === 'string') model.value = params.model
  if (typeof params.protocol === 'string') protocol.value = params.protocol
  if (Array.isArray(params.skills)) {
    skills.value = (params.skills as SkillPick[]).filter(s => s && typeof s.name === 'string')
  }
  if (Array.isArray(params.prompt_routines)) {
    promptRoutines.value = (params.prompt_routines as unknown[]).filter(s => typeof s === 'string') as string[]
  }
  if (params.routine_aliases && typeof params.routine_aliases === 'object') {
    aliasRows.value = Object.entries(params.routine_aliases as Record<string, unknown>)
      .filter(([, v]) => typeof v === 'string')
      .map(([full, tag]) => ({ full, tag: tag as string }))
  } else {
    aliasRows.value = []
  }
  for (const f of fields.value) {
    const v = params[f.name]
    if (typeof v === 'string' && v) form[f.name] = v
  }
}

function slugify(label: string): string {
  return label.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

/** 打开存为预制体 modal (预填默认名). */
function openSaveModal() {
  const m = manifest.value
  if (!m) return
  saveLabel.value = `${m.label} 配置`
  saveModal.value = true
}

/** 把当前表单值存为预制体 (modal 内命名, id 由 label slug 化; 同名覆盖已提示). */
async function savePreset() {
  const m = manifest.value
  const label = saveLabel.value.trim()
  if (!m || !label) return
  const id = slugify(label) || `preset_${Date.now()}`
  createError.value = ''
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'agent_hub/preset/save', kwargs: { id, label, kind: m.kind, params: buildParams() } }),
    })
    const data = await res.json()
    if (!data?.ok) throw new Error(data?.result?.error || data?.error || 'save preset failed')
    saveModal.value = false
    confirmDel.value = null
    await loadPresets()
  } catch (e) {
    createError.value = (e as Error).message
  }
}

/** 删除预制体 (内联二次确认后执行, 调 agent_hub/preset/delete). */
async function doDelete(p: Preset) {
  createError.value = ''
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'agent_hub/preset/delete', kwargs: { id: p.id } }),
    })
    const data = await res.json()
    if (!data?.ok) throw new Error(data?.result?.error || data?.error || 'delete preset failed')
    if (confirmDel.value === p.id) confirmDel.value = null
    await loadPresets()
  } catch (e) {
    createError.value = (e as Error).message
  }
}

async function loadPresets(): Promise<void> {
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'agent_hub/preset/list', kwargs: {} }),
    })
    if (!res.ok) return
    const data = await res.json()
    presets.value = (data?.result?.presets ?? []) as Preset[]
  } catch {
    presets.value = []
  }
}

async function onCreate() {
  const m = manifest.value
  if (!m) return
  createError.value = ''
  for (const f of m.create_fields) {
    if (f.required && !form[f.name]?.trim()) {
      createError.value = `${f.label} is required`
      return
    }
  }
  const params = buildParams()
  try {
    const id = await props.createAgent(m.kind, params)
    if (id) {
      emit('create', m.kind, params, id)
      for (const k of Object.keys(form)) form[k] = ''
      model.value = ''
      skills.value = []
      promptRoutines.value = []
      aliasRows.value = []
    }
  } catch (e) {
    createError.value = (e as Error).message
    emit('error', (e as Error).message)
  }
}

/** 模型可选协议表 (models/list 的 protocols 字段, provider 级事实). */
async function loadModelProtocols(): Promise<void> {
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'models/list', kwargs: {} }),
    })
    if (!res.ok) return
    const data = await res.json()
    const models = (data?.result?.models || []) as Array<{ key: string; protocols?: string[] }>
    const map: Record<string, string[]> = {}
    for (const m of models) map[m.key] = m.protocols ?? []
    modelProtocols.value = map
  } catch {
    modelProtocols.value = {}
  }
}

onMounted(async () => {
  loadPresets()
  loadModelProtocols()
  loadingTypes.value = true
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'agent_hub/types', kwargs: {} }),
    })
    if (res.ok) {
      const data = await res.json()
      types.value = (data?.result?.types ?? []) as AgentManifest[]
      if (types.value.length) selectKind(types.value[0].kind)
    }
  } finally {
    loadingTypes.value = false
  }
})
</script>

<style scoped>
/* ---- 分区骨架: 统一 section 标题带 (indigo 竖标 + 标题 + 右侧提示) ---- */
.af-form {
  display: flex; flex-direction: column; gap: 22px;
  max-width: 1100px; margin: 0 auto; padding-bottom: 8px;
}
.af-sec { display: flex; flex-direction: column; gap: 10px; }
.af-sec-head {
  display: flex; align-items: center; gap: 8px;
}
.af-sec-mark {
  width: 3px; height: 12px; border-radius: 2px;
  background: linear-gradient(180deg, #818cf8, #6366f1);
}
.af-sec-title {
  font-size: 11px; font-weight: 700; letter-spacing: .12em;
  color: #94a3b8;
}
.af-sec-hint {
  margin-left: auto; font-size: 11px; color: #4b5563;
}
.af-sec-clear {
  margin-left: auto; font-size: 11px; color: #64748b;
  cursor: pointer; padding: 1px 8px; border-radius: 999px;
  border: 1px solid #2d3148; transition: color .15s, border-color .15s;
}
.af-sec-clear:hover { color: #f87171; border-color: #7f3a3a; }
.af-sec-hint + .af-sec-clear { margin-left: 8px; }

/* ---- Routine 别名行编辑 ---- */
.af-alias-rows { display: flex; flex-direction: column; gap: 6px; }
.af-alias-row { display: flex; align-items: center; gap: 8px; }
.af-alias-row .n-input { flex: 1; }
.af-alias-row .n-input:first-child { flex: 3; }
.af-alias-arrow { color: #64748b; font-size: 12px; flex: none; }
.af-alias-del {
  flex: none; display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; border-radius: 6px; cursor: pointer;
  color: #64748b; transition: color .15s, background .15s;
}
.af-alias-del:hover { color: #f87171; background: rgba(248, 113, 113, .1); }
.af-alias-add {
  align-self: flex-start; font-size: 11px; color: #94a3b8; cursor: pointer;
  padding: 3px 10px; border-radius: 999px; border: 1px dashed #2d3148;
  background: none; transition: color .15s, border-color .15s;
}
.af-alias-add:hover { color: #38bdf8; border-color: #1e3a5f; }

/* ---- 预制体 chips (悬停浮现删除 ✕) ---- */
.af-presets { display: flex; flex-wrap: wrap; gap: 8px; }
.af-preset-wrap { position: relative; display: inline-flex; }
.af-preset-del {
  position: absolute; top: -5px; right: -5px;
  width: 15px; height: 15px; line-height: 14px;
  border-radius: 50%; text-align: center;
  font-size: 9px; color: #cbd5e1;
  background: #3b4160; border: 1px solid #262c40;
  cursor: pointer; opacity: 0;
  transition: opacity .15s, background .15s, color .15s;
}
.af-preset-wrap:hover .af-preset-del { opacity: 1; }
.af-preset-del:hover { background: #7f3a3a; color: #fecaca; }
/* 内联删除确认态: chip 变红色警示胶囊 */
.af-preset-danger {
  border-color: #7f3a3a; background: #2a1618; color: #fca5a5;
  cursor: default; gap: 8px;
}
.af-preset-ok, .af-preset-no {
  display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 50%;
  font-size: 9px; cursor: pointer; user-select: none;
  transition: background .15s, color .15s;
}
.af-preset-ok { background: #7f3a3a; color: #fecaca; }
.af-preset-ok:hover { background: #dc2626; color: #fff; }
.af-preset-no { background: #3b4160; color: #cbd5e1; }
.af-preset-no:hover { background: #4c5474; color: #fff; }
/* 存为预制体 modal */
.af-save-body { display: flex; flex-direction: column; gap: 10px; }
.af-save-warn {
  font-size: 11px; color: #fbbf24; padding: 6px 10px;
  background: #2a2214; border: 1px solid #5c4a1a; border-radius: 6px;
}
.af-save-actions { display: flex; justify-content: flex-end; gap: 8px; }
.af-preset {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 14px;
  border: 1px solid #2d3148; border-radius: 999px;
  background: #161a25; color: #a5aec4;
  font-size: 12px; font-family: inherit; cursor: pointer; user-select: none;
  transition: border-color .15s, color .15s, background .15s, box-shadow .15s;
}
.af-preset:hover {
  border-color: #6366f1; color: #c7cdff;
  background: #1b1f33; box-shadow: 0 0 0 3px rgba(99, 102, 241, .12);
}
.af-preset:active { transform: translateY(1px); }
.af-preset-ic { font-size: 10px; color: #818cf8; }

/* ---- 类型卡片 (自适应多列网格) ---- */
.af-types {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px;
}
.af-type {
  display: flex; flex-direction: column; gap: 6px;
  padding: 12px 14px;
  border: 1px solid #262c40; border-radius: 10px;
  background: #161a25;
  cursor: pointer; text-align: left;
  transition: border-color .15s, background .15s, box-shadow .15s, transform .15s;
}
.af-type:hover {
  border-color: #3a4160; background: #1a1f2e; transform: translateY(-1px);
}
.af-type.on {
  border-color: #6366f1; background: #1b1f33;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, .14);
}
.af-type-top {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}
.af-type-label {
  font-size: 13px; font-weight: 600; color: #e2e8f0;
}
.af-type.on .af-type-label { color: #c7cdff; }
.af-type-kind {
  font-family: Consolas, monospace; font-size: 10px;
  color: #64748b; background: #0f1117;
  border: 1px solid #2d3148; border-radius: 999px;
  padding: 1px 8px;
}
.af-type.on .af-type-kind {
  color: #818cf8; border-color: #4c4372;
}
.af-type-desc {
  font-size: 11px; line-height: 1.5; color: #64748b;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.af-empty {
  padding: 18px; font-size: 12px; color: #4b5563; text-align: center;
  border: 1px dashed #2d3148; border-radius: 10px;
}

/* ---- 协议 chips (apis 矩阵声明的可选协议 + 自动) ---- */
.af-protos { display: flex; flex-wrap: wrap; gap: 8px; }
.af-proto {
  padding: 5px 14px;
  border: 1px solid #2d3148; border-radius: 999px;
  background: #161a25; color: #a5aec4;
  font-size: 12px; font-family: Consolas, monospace; cursor: pointer;
  user-select: none;
  transition: border-color .15s, color .15s, background .15s, box-shadow .15s;
}
.af-proto:hover {
  border-color: #6366f1; color: #c7cdff;
  background: #1b1f33; box-shadow: 0 0 0 3px rgba(99, 102, 241, .12);
}
.af-proto.on {
  border-color: #6366f1; background: #1b1f33; color: #c7cdff;
  box-shadow: 0 0 0 3px rgba(99, 102, 241, .14);
}

/* ---- 动态字段 (自适应两列起铺) ---- */
.af-fields {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px 18px;
}
.af-field { display: flex; flex-direction: column; gap: 6px; }
/* textarea 字段 (长文指令): 跨满两列整行 */
.af-field-wide { grid-column: 1 / -1; }
.af-ta-toggle {
  margin-left: auto;
  cursor: pointer;
  font-size: 11px;
  color: #818cf8;
}
.af-ta-toggle:hover { color: #a5b4fc; }
/* md 渲染预览 (v-html -> :deep), 样式与 AgentPanel .md-body 同源 */
.af-ta-md {
  font-size: 12px;
  color: #b6bccf;
  line-height: 1.7;
  padding: 10px 12px;
  border: 1px solid #2d3148;
  border-radius: 6px;
  background: #131622;
  max-height: 340px;
  overflow-y: auto;
}
.af-ta-md :deep(p) { margin: 4px 0; }
.af-ta-md :deep(p:first-child) { margin-top: 0; }
.af-ta-md :deep(p:last-child) { margin-bottom: 0; }
.af-ta-md :deep(h1),
.af-ta-md :deep(h2),
.af-ta-md :deep(h3),
.af-ta-md :deep(h4),
.af-ta-md :deep(h5),
.af-ta-md :deep(h6) {
  color: #f1f5f9;
  font-weight: 600;
  margin: 12px 0 4px;
  line-height: 1.3;
}
.af-ta-md :deep(h1) { font-size: 16px; }
.af-ta-md :deep(h2) { font-size: 15px; }
.af-ta-md :deep(h3),
.af-ta-md :deep(h4),
.af-ta-md :deep(h5),
.af-ta-md :deep(h6) { font-size: 13px; }
.af-ta-md :deep(ul),
.af-ta-md :deep(ol) { padding-left: 22px; margin: 4px 0; }
.af-ta-md :deep(li) { margin: 2px 0; }
.af-ta-md :deep(code) {
  background: #0f1117;
  padding: 1px 5px;
  border-radius: 3px;
  font-family: 'Consolas', ui-monospace, monospace;
  font-size: 11px;
  color: #a5b4fc;
}
.af-ta-md :deep(pre) {
  background: #0f1117;
  border: 1px solid #2d3148;
  padding: 10px 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}
.af-ta-md :deep(pre code) { background: none; padding: 0; color: #e2e8f0; }
.af-ta-md :deep(strong) { color: #f1f5f9; }
.af-ta-md :deep(a) { color: #818cf8; }
.af-ta-md :deep(blockquote) {
  border-left: 3px solid #4a4e7a;
  margin: 6px 0;
  padding: 2px 12px;
  color: #94a3b8;
}
.af-textarea {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
  line-height: 1.65;
}
.af-label {
  display: flex; align-items: center; gap: 5px;
  font-size: 12px; color: #a5aec4;
}
.af-req {
  width: 4px; height: 4px; border-radius: 50%; background: #f87171;
}

/* ---- footer + 错误 ---- */
.af-footer {
  position: sticky; bottom: 0;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 14px 0 4px;
  background: linear-gradient(180deg, transparent, #0f1117 30%);
}
.af-error {
  margin-top: 2px; padding: 8px 14px;
  font-size: 12px; color: #fca5a5;
  border: 1px solid #7f3a3a; border-radius: 8px;
  background: #2a1618;
}
</style>
