<template>
  <div class="models-panel">
    <!-- 顶部工具栏 -->
    <div class="mp-header">
      <span class="mp-title">模型管理</span>
      <span class="mp-default-hint">
        默认: <span class="mp-default-key">{{ defaultKey || '—' }}</span>
      </span>
      <div class="mp-actions">
        <NButton size="small" quaternary :loading="loading" @click="refresh">⟳ 刷新</NButton>
        <NButton size="small" type="primary" :disabled="!!editor" @click="openCreate">+ 新建 Provider</NButton>
      </div>
    </div>

    <!-- 页面级错误区 -->
    <NAlert v-if="error" type="error" :show-icon="false" class="mp-error">
      <pre>{{ error }}</pre>
    </NAlert>

    <!-- 编辑器视图 (编辑 provider / 其下 model 条目; 保存整体替换该 provider) -->
    <div v-if="editor" class="mp-body">
      <NScrollbar style="height:100%">
        <div class="mp-editor">
          <div class="mp-editor-title-row">
            <div class="mp-editor-title">{{ editorTitle }}</div>
            <button
              type="button"
              class="mp-editor-close"
              title="返回上一级 (Esc)"
              @click="closeEditor"
            >
              <Icon :comp="ArrowLeft" :size="17" />
            </button>
          </div>

          <template v-if="editor.mode === 'provider'">
          <div class="mp-grid">
            <div class="mp-field">
              <label class="mp-label">provider_id <span class="mp-req">*</span></label>
              <NInput
                v-model:value="editor.pid"
                size="small"
                :disabled="!editor.isNew"
                placeholder="如 seed"
              />
            </div>
            <div class="mp-field">
              <label class="mp-label">
                api_key
                <span class="mp-hint">(打码显示, 不修改则保持原值)</span>
              </label>
              <NInput
                :value="editor.apiKeyDisplay"
                size="small"
                placeholder="sk-... 或 $VAR 引用"
                @update:value="onApiKeyInput"
              />
            </div>
            <div class="mp-field">
              <label class="mp-label">base_url</label>
              <NInput v-model:value="editor.baseUrl" size="small" placeholder="https://..." />
            </div>
          </div>

          <div
            v-for="p in PROTOCOLS"
            :key="p.id"
            class="mp-proto"
            :class="{ off: !editor.protocols[p.id].enabled }"
          >
            <div class="mp-proto-head">
              <NSwitch
                size="small"
                :value="editor.protocols[p.id].enabled"
                @update:value="toggleProto(p.id, $event)"
              />
              <span class="mp-proto-name">{{ p.label }}</span>
            </div>
            <div v-if="editor.protocols[p.id].enabled" class="mp-proto-body">
              <div class="mp-proto-row">
                <label class="mp-label">请求 URL</label>
                <NInput
                  size="small"
                  v-model:value="editor.protocols[p.id].baseURL"
                  :placeholder="editor.baseUrl.replace(/\/+$/, '') + p.suffix"
                />
              </div>
              <div v-if="p.id === 'openai-responses'" class="mp-proto-row">
                <label class="mp-label">previous_response_id</label>
                <NSwitch
                  size="small"
                  :value="editor.protocols[p.id].prevRid"
                  @update:value="editor.protocols[p.id].prevRid = $event"
                />
              </div>
            </div>
          </div>
          </template>

          <div v-for="m in visibleModels" :key="m.uid" class="mp-model-edit">
            <div class="mp-model-edit-head">
              <span class="mp-model-edit-title" :title="m.id || '未填写 id'">
                {{ m.id || '新模型' }}
              </span>
              <NButton
                size="tiny"
                quaternary
                type="error"
                title="删除此模型条目"
                @click="removeEditableModel(m.uid)"
              >
                <Icon :comp="Trash" :size="13" />
              </NButton>
            </div>

            <div class="mp-grid mp-grid-2">
              <div class="mp-field">
                <label class="mp-label">model id (API) <span class="mp-req">*</span></label>
                <NInput v-model:value="m.id" size="small" placeholder="厂商给的 API model id, 如 glm-5.3" />
              </div>
              <div class="mp-field">
                <label class="mp-label">显示别名 (可选)</label>
                <NInput v-model:value="m.name" size="small" placeholder="键太长时的友好显示名" />
              </div>
            </div>

            <div class="mp-section">能力</div>
            <div class="mp-grid mp-grid-3">
              <div class="mp-field">
                <label class="mp-label">max_context</label>
                <div class="mp-unit-row">
                  <NInputNumber
                    v-model:value="m.max_context"
                    size="small"
                    :show-button="false"
                    style="flex:1"
                    placeholder="1"
                  />
                  <NSelect
                    v-model:value="m.max_context_unit"
                    size="small"
                    :options="UNIT_OPTIONS"
                    style="width:68px"
                  />
                </div>
              </div>
              <div class="mp-field">
                <label class="mp-label">max_tokens</label>
                <div class="mp-unit-row">
                  <NInputNumber
                    v-model:value="m.max_tokens"
                    size="small"
                    :show-button="false"
                    style="flex:1"
                    placeholder="128"
                  />
                  <NSelect
                    v-model:value="m.max_tokens_unit"
                    size="small"
                    :options="UNIT_OPTIONS"
                    style="width:68px"
                  />
                </div>
              </div>
              <div class="mp-field">
                <label class="mp-label">图片输入</label>
                <button
                  type="button"
                  class="mp-icon-toggle"
                  :class="{ on: m.image }"
                  :title="m.image ? '支持图片输入 (点击关掉)' : '不支持图片输入 (点击开启)'"
                  @click="m.image = !m.image"
                >
                  <Icon :comp="Photo" :size="15" />
                </button>
              </div>
            </div>

            <div class="mp-section mp-inline-section">
              <NSwitch v-model:value="m.hasReasoning" size="small" />
              <span class="mp-label">reasoning (思考模型)</span>
            </div>
            <div v-if="m.hasReasoning" class="mp-grid mp-grid-3">
              <div class="mp-field">
                <label class="mp-label">effort (默认档位)</label>
                <NInput v-model:value="m.effort" size="small" placeholder="high" />
              </div>
              <div class="mp-field">
                <label class="mp-label">关思考</label>
                <button
                  type="button"
                  class="mp-icon-toggle"
                  :class="{ on: m.can_disable }"
                  :title="m.can_disable ? '支持关思考 (点击关掉)' : '不支持关思考 (点击开启)'"
                  @click="m.can_disable = !m.can_disable"
                >
                  <Icon :comp="Power" :size="15" />
                </button>
              </div>
              <div v-if="m.can_disable" class="mp-field">
                <label class="mp-label">off 档值</label>
                <NInput v-model:value="m.off" size="small" placeholder="none" />
              </div>
            </div>

            <div class="mp-section">透传 (原样进 API 请求体)</div>
            <div class="mp-grid mp-grid-2">
              <div class="mp-field">
                <label class="mp-label">extra (JSON, 可选)</label>
                <NInput
                  v-model:value="m.extra"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  size="small"
                  placeholder='{ "store": true }'
                />
              </div>
              <div class="mp-field">
                <label class="mp-label">tools (JSON 数组, 可选)</label>
                <NInput
                  v-model:value="m.tools"
                  type="textarea"
                  :autosize="{ minRows: 2, maxRows: 6 }"
                  size="small"
                  placeholder='[{"type": "web_search"}]'
                />
              </div>
            </div>
          </div>

          <div class="mp-editor-footer">
            <div v-if="editorError" class="mp-editor-error">{{ editorError }}</div>
            <div class="mp-editor-btns">
              <NButton size="small" type="primary" :loading="saving" @click="save">保存</NButton>
            </div>
          </div>
        </div>
      </NScrollbar>
    </div>

    <!-- provider 列表视图 -->
    <div v-else class="mp-body">
      <NScrollbar style="height:100%">
        <div class="mp-list">
          <div v-for="card in providerCards" :key="card.pid" class="mp-card">
            <div class="mp-card-head">
              <span class="mp-pid">{{ card.pid }}</span>
              <span class="mp-card-actions">
                <NButton
                  size="tiny"
                  quaternary
                  title="编辑 provider (api_key / base_url / 协议)"
                  @click="openEdit(card.pid)"
                >
                  <Icon :comp="Pencil" :size="15" />
                </NButton>
                <NButton
                  size="tiny"
                  quaternary
                  title="添加模型"
                  @click="addModelTo(card.pid)"
                >
                  <Icon :comp="Plus" :size="15" />
                </NButton>
                <NPopconfirm
                  :show-icon="false"
                  positive-text="删除"
                  negative-text="取消"
                  @positive-click="removeProvider(card.pid)"
                >
                  <template #trigger>
                    <NButton size="tiny" quaternary type="error" title="删除 provider">
                      <Icon :comp="Trash" :size="15" />
                    </NButton>
                  </template>
                  删除 provider "{{ card.pid }}" 及其全部模型?
                </NPopconfirm>
              </span>
            </div>
            <div
              v-for="r in card.rows"
              :key="r.key"
              class="mp-model-row"
              :class="{ 'is-default': r.key === defaultKey }"
            >
              <span class="mp-model-local" :title="r.key">{{ localOf(r) }}</span>
              <span v-if="r.name && r.name !== localOf(r)" class="mp-arrow">=</span>
              <span v-if="r.name && r.name !== localOf(r)" class="mp-model-id">{{ r.name }}</span>
              <NButton
                size="tiny"
                quaternary
                class="mp-flag-btn"
                :class="{ 'is-default': r.key === defaultKey }"
                :title="r.key === defaultKey ? '默认模型' : '标为默认模型'"
                :disabled="r.key === defaultKey"
                @click="setDefault(r.key)"
              >
                <Icon :comp="Flag" :size="13" />
              </NButton>
              <span class="mp-meta">{{ r.max_context ? fmtTokens(r.max_context) : '—' }}</span>
              <NTag v-if="r.has_tools" size="tiny" :bordered="false" class="mp-tools-tag">tools</NTag>
              <span class="mp-row-actions">
                <NButton
                  size="tiny"
                  quaternary
                  title="编辑此模型"
                  @click="openEdit(card.pid, localOf(r))"
                >
                  <Icon :comp="Pencil" :size="13" />
                </NButton>
                <NPopconfirm
                  :show-icon="false"
                  positive-text="删除"
                  negative-text="取消"
                  @positive-click="removeModel(card.pid, localOf(r))"
                >
                  <template #trigger>
                    <NButton size="tiny" quaternary type="error" title="删除此模型">
                      <Icon :comp="Trash" :size="13" />
                    </NButton>
                  </template>
                  删除模型 "{{ r.key }}"?
                </NPopconfirm>
              </span>
            </div>
            <div v-if="!card.rows.length" class="mp-model-none">该 provider 暂无模型</div>
          </div>
          <div v-if="!providerCards.length && !loading" class="mp-empty">
            无 provider 配置, 点击右上角 "+ 新建 Provider" 创建
          </div>
        </div>
      </NScrollbar>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { NButton, NInput, NInputNumber, NSelect, NSwitch, NTag, NAlert, NScrollbar, NPopconfirm } from 'naive-ui'
import { Pencil, Trash, Flag, Plus, Photo, Power, ArrowLeft } from '@vicons/tabler'
import Icon from './Icon.vue'

// ---- 接口形状 (models/list | models/get 返回) ----

/** models/list 展平行 */
interface ModelRow {
  key: string            // 'provider/model_id'
  provider: string
  name: string           // 显示别名 (缺省 = model id)
  max_context: number
  max_tokens: number
  reasoning_effort: string | null
  has_tools: boolean
}

/** models/get 的 model 条目 (键 = 实际 API id) */
interface ModelCfg {
  name?: string
  max_context?: number
  max_tokens?: number
  image?: boolean
  reasoning?: { effort?: string, can_disable?: boolean, off?: string }
  extra?: Record<string, unknown>
  tools?: Array<Record<string, unknown>>
}

/** models/get 的 provider 条目 */
interface ProviderCfg {
  api_key: string
  base_url: string
  apis?: Record<string, Record<string, unknown>>
  models: Record<string, ModelCfg>
}

const props = defineProps<{
  httpBase: string
  active: boolean
}>()

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const editorError = ref('')
const defaultKey = ref('')
const rows = ref<ModelRow[]>([])
const providers = ref<Record<string, ProviderCfg>>({})

// ---- HTTP: 统一 POST /run_routine {name, kwargs} ----> {ok, result | error}

async function callRoutine(name: string, kwargs: Record<string, unknown>): Promise<any> {
  const res = await fetch(`${props.httpBase}/run_routine`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, kwargs }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  if (!data.ok) throw new Error(data.error || `${name} failed`)
  return data.result
}

/** 列表 = models/list (默认 + 展平行) + models/get (provider 卡片详情 + 编辑源). */
async function refresh(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const [listRes, getRes] = await Promise.all([
      callRoutine('models/list', {}),
      callRoutine('models/get', {}),
    ])
    defaultKey.value = String(listRes?.default ?? '')
    rows.value = (listRes?.models ?? []) as ModelRow[]
    providers.value = (getRes?.providers ?? {}) as Record<string, ProviderCfg>
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

const providerCards = computed(() =>
  Object.entries(providers.value).map(([pid, cfg]) => ({
    pid,
    rows: rows.value.filter(r => r.provider === pid),
  })),
)

/** 'provider/model_id' → model id (provider 不含 '/', slice 剩余部分). */
function localOf(r: ModelRow): string {
  return r.key.slice(r.provider.length + 1)
}

/** token 数 → 主流模型卡单位: ≥1M 用 M (一位小数), ≥1K 用 K. */
function fmtTokens(v: number | null | undefined): string {
  if (!v) return '—'
  if (v >= 1_000_000) {
    const m = Math.round((v / 1_000_000) * 10) / 10
    return `${m}M`
  }
  if (v >= 1000) return `${Math.round(v / 1000)}K`
  return String(v)
}

/** token 数 → (数值, 单位): ≥1M 用 M, 否则 K (数值可为小数). */
function splitTokens(v: number | null | undefined): { n: number | null, unit: TokenUnit } {
  if (v == null || !v) return { n: null, unit: 'K' }
  if (v >= 1_000_000) {
    return { n: Math.round((v / 1_000_000) * 1000) / 1000, unit: 'M' }
  }
  return { n: Math.round((v / 1_000) * 1000) / 1000, unit: 'K' }
}

/** (数值, 单位) → token 数. */
function combineTokens(n: number | null, unit: TokenUnit): number {
  return Math.round(n! * (unit === 'M' ? 1_000_000 : 1_000))
}

/** ModelCfg 的 token 数 → 编辑字段 (数值 + 单位下拉初值). */
function splitTokensModel(m: ModelCfg): {
  max_context: number | null
  max_context_unit: TokenUnit
  max_tokens: number | null
  max_tokens_unit: TokenUnit
} {
  const ctx = splitTokens(m.max_context)
  const out = splitTokens(m.max_tokens)
  return {
    max_context: ctx.n,
    max_context_unit: ctx.unit,
    max_tokens: out.n,
    max_tokens_unit: out.unit,
  }
}

// ---- 编辑器 ----

/** 单位下拉: K / M (×1e3 / ×1e6) */
const UNIT_OPTIONS = [
  { label: 'K', value: 'K' },
  { label: 'M', value: 'M' },
]
type TokenUnit = 'K' | 'M'

interface EditableModel {
  uid: number
  id: string
  name: string
  /** 数值 + 单位下拉 (K/M), 保存时乘回 token 数 */
  max_context: number | null
  max_context_unit: TokenUnit
  max_tokens: number | null
  max_tokens_unit: TokenUnit
  hasReasoning: boolean
  effort: string
  can_disable: boolean
  off: string
  image: boolean
  extra: string
  tools: string
}

/** 协议封闭集: 顺序即默认协议 fallback 顺序 */
const PROTOCOLS = [
  { id: 'openai-responses', label: 'OpenAI Responses', suffix: '/responses' },
  { id: 'openai-completions', label: 'OpenAI Chat Completions', suffix: '/chat/completions' },
  { id: 'anthropic-messages', label: 'Anthropic Messages', suffix: '/v1/messages' },
] as const
type ApiId = typeof PROTOCOLS[number]['id']

interface ProtoState {
  enabled: boolean
  prevRid: boolean
  /** 协议级 base_url 覆盖; 空 = 用 provider 级自动拼 (显示 placeholder 预览) */
  baseURL: string
}

interface EditorState {
  isNew: boolean
  /** 编辑器模式: provider 设置 (api_key/base_url/协议) 或单个 model */
  mode: 'provider' | 'model'
  pid: string
  /** models/get 拿到的 api_key 原文 (输入框未改则原样回传) */
  originalApiKey: string
  /** 输入框当前值 (初始为打码显示) */
  apiKeyDisplay: string
  baseUrl: string
  /** 协议章节状态: enabled = apis 矩阵键存在 (空章节 = 不可用).
   *  协议选择归 agent 层, 无"默认协议"概念. */
  protocols: Record<ApiId, ProtoState>
  /** 全量 model 条目 (保存整体替换该 provider 用), UI 只展示 focusUid */
  models: EditableModel[]
  /** model 模式下正在编辑的条目 */
  focusUid: number | null
}

const editor = ref<EditorState | null>(null)

const editorTitle = computed(() => {
  const ed = editor.value
  if (!ed) return ''
  if (ed.mode === 'model') {
    const m = ed.models.find(x => x.uid === ed.focusUid)
    return `编辑模型: ${ed.pid}/${m?.id || '新模型'}`
  }
  return ed.isNew ? '新建 Provider' : `编辑 Provider: ${ed.pid}`
})

/** model 模式只渲染正在编辑的那一条; provider 模式无模型块 */
const visibleModels = computed(() => {
  const ed = editor.value
  if (!ed || ed.mode !== 'model') return []
  return ed.models.filter(m => m.uid === ed.focusUid)
})

/** 载入协议章节: 矩阵键存在 = 启用; 无矩阵 = 仅隐式 openai-responses */
function loadProtocols(cfg: ProviderCfg): Record<ApiId, ProtoState> {
  const apis = cfg.apis ?? {}
  const has = (id: ApiId) => id in apis
  return {
    'openai-responses': {
      enabled: has('openai-responses') || !Object.keys(apis).length,
      prevRid: apis['openai-responses']?.previous_response_id !== false,
      baseURL: String(apis['openai-responses']?.base_url ?? ''),
    },
    'openai-completions': {
      enabled: has('openai-completions'),
      prevRid: false,
      baseURL: String(apis['openai-completions']?.base_url ?? ''),
    },
    'anthropic-messages': {
      enabled: has('anthropic-messages'),
      prevRid: false,
      baseURL: String(apis['anthropic-messages']?.base_url ?? ''),
    },
  }
}

function enabledProtocols(p: Record<ApiId, ProtoState>): ApiId[] {
  return PROTOCOLS.map(x => x.id).filter(id => p[id].enabled)
}

/** 协议章节开关: 不允许禁到 0 个 (至少声明一个可用协议) */
function toggleProto(aid: ApiId, on: boolean): void {
  const ed = editor.value
  if (!ed) return
  if (!on && enabledProtocols(ed.protocols).filter(x => x !== aid).length === 0) {
    return // 至少保留一个启用协议
  }
  ed.protocols[aid].enabled = on
}

let uidSeq = 0

/** 打码: 前4后4中间 *** (短值整体 ****). */
function maskKey(key: string): string {
  if (!key) return ''
  if (key.length <= 8) return '****'
  return key.slice(0, 4) + '***' + key.slice(-4)
}

function toEditable(id: string, m: ModelCfg): EditableModel {
  return {
    uid: ++uidSeq,
    id,
    name: m.name ?? '',
    ...splitTokensModel(m),
    hasReasoning: m.reasoning != null,
    effort: m.reasoning?.effort ?? '',
    can_disable: Boolean(m.reasoning?.can_disable),
    off: m.reasoning?.off ?? '',
    image: Boolean(m.image),
    extra: m.extra != null ? JSON.stringify(m.extra, null, 2) : '',
    tools: m.tools != null ? JSON.stringify(m.tools, null, 2) : '',
  }
}

function openCreate(): void {
  editorError.value = ''
  editor.value = {
    isNew: true,
    mode: 'provider',
    pid: '',
    originalApiKey: '',
    apiKeyDisplay: '',
    baseUrl: '',
    protocols: {
      'openai-responses': { enabled: true, prevRid: true, baseURL: '' },
      'openai-completions': { enabled: false, prevRid: false, baseURL: '' },
      'anthropic-messages': { enabled: false, prevRid: false, baseURL: '' },
    },
    models: [],
    focusUid: null,
  }
}

/** 编辑前现拉该 provider 完整配置 (含 api_key 原文, 存下供未改动时回传).
 *  focusModelId 给定 = model 模式 (只编辑该模型); 缺省 = provider 设置. */
async function openEdit(pid: string, focusModelId?: string): Promise<void> {
  editorError.value = ''
  error.value = ''
  try {
    const res = await callRoutine('models/get', { provider: pid })
    const cfg = (res?.providers ?? {})[pid] as ProviderCfg | undefined
    if (!cfg) throw new Error(`provider "${pid}" 不存在`)
    const models = Object.entries(cfg.models ?? {}).map(([id, m]) => toEditable(id, m))
    let focusUid: number | null = null
    if (focusModelId) {
      const target = models.find(m => m.id === focusModelId)
      if (!target) throw new Error(`model "${pid}/${focusModelId}" 不存在`)
      focusUid = target.uid
    }
    editor.value = {
      isNew: false,
      mode: focusModelId ? 'model' : 'provider',
      pid,
      originalApiKey: String(cfg.api_key ?? ''),
      apiKeyDisplay: maskKey(String(cfg.api_key ?? '')),
      baseUrl: String(cfg.base_url ?? ''),
      protocols: loadProtocols(cfg),
      models,
      focusUid,
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

/** 卡片 "+" 入口: 打开编辑器直达新模型块 (model 模式). */
async function addModelTo(pid: string): Promise<void> {
  await openEdit(pid)
  const ed = editor.value
  if (!ed) return
  ed.mode = 'model'
  const m = addModel()
  ed.focusUid = m.uid
}

function onApiKeyInput(v: string): void {
  if (editor.value) editor.value.apiKeyDisplay = v
}

function addModel(): EditableModel {
  const m: EditableModel = {
    uid: ++uidSeq,
    id: '',
    name: '',
    max_context: null,
    max_context_unit: 'K',
    max_tokens: null,
    max_tokens_unit: 'K',
    hasReasoning: false,
    effort: '',
    can_disable: false,
    off: '',
    image: false,
    extra: '',
    tools: '',
  }
  editor.value?.models.push(m)
  return m
}

function removeEditableModel(uid: number): void {
  if (!editor.value) return
  editor.value.models = editor.value.models.filter(m => m.uid !== uid)
  // model 模式删的是当前唯一展示块, 删完即退出
  if (editor.value.mode === 'model' && editor.value.focusUid === uid) {
    closeEditor()
  }
}

function closeEditor(): void {
  editor.value = null
  editorError.value = ''
}

/** 编辑条目 → models/save 的 model cfg (键 = model id, 空值省略). */
function buildModelPayload(m: EditableModel): Record<string, unknown> {
  const cfg: Record<string, unknown> = {}
  if (m.name.trim()) cfg.name = m.name.trim()
  if (m.max_context != null) cfg.max_context = combineTokens(m.max_context, m.max_context_unit)
  if (m.max_tokens != null) cfg.max_tokens = combineTokens(m.max_tokens, m.max_tokens_unit)
  if (m.image) cfg.image = true
  if (m.hasReasoning) {
    const reasoning: Record<string, unknown> = { can_disable: m.can_disable }
    if (m.effort.trim()) reasoning.effort = m.effort.trim()
    if (m.can_disable && m.off.trim()) reasoning.off = m.off.trim()
    cfg.reasoning = reasoning
  }
  if (m.extra.trim()) {
    try {
      cfg.extra = JSON.parse(m.extra)
    } catch {
      throw new Error(`模型 "${m.id}": extra 不是合法 JSON`)
    }
  }
  if (m.tools.trim()) {
    try {
      const parsed = JSON.parse(m.tools)
      if (!Array.isArray(parsed)) throw new Error('not array')
      cfg.tools = parsed
    } catch {
      throw new Error(`模型 "${m.id}": tools 不是合法 JSON 数组`)
    }
  }
  return cfg
}

async function save(): Promise<void> {
  const ed = editor.value
  if (!ed || saving.value) return
  editorError.value = ''
  const pid = ed.pid.trim()
  if (!pid) { editorError.value = 'provider_id 不能为空'; return }
  if (pid.includes('/')) { editorError.value = 'provider_id 不能包含 "/"'; return }
  if (ed.isNew && providers.value[pid]) {
    editorError.value = `provider "${pid}" 已存在 (会整体替换, 请改用编辑)`
    return
  }
  const modelsPayload: Record<string, Record<string, unknown>> = {}
  const seen = new Set<string>()
  for (const m of ed.models) {
    const id = m.id.trim()
    if (!id) { editorError.value = '存在 model id 为空的模型条目'; return }
    if (seen.has(id)) { editorError.value = `模型 id 重复: "${id}"`; return }
    seen.add(id)
    try {
      modelsPayload[id] = buildModelPayload(m)
    } catch (e) {
      editorError.value = (e as Error).message
      return
    }
  }
  // api_key: 输入框仍是打码值 → 回传 models/get 拿到的原文; 改过 → 用新值
  const apiKey = ed.apiKeyDisplay === maskKey(ed.originalApiKey)
    ? ed.originalApiKey
    : ed.apiKeyDisplay.trim()
  const provider: Record<string, unknown> = { models: modelsPayload }
  if (apiKey) provider.api_key = apiKey
  if (ed.baseUrl.trim()) provider.base_url = ed.baseUrl.trim()
  // 协议章节 → apis 矩阵
  const apisPayload: Record<string, Record<string, unknown>> = {}
  for (const { id } of PROTOCOLS) {
    if (!ed.protocols[id].enabled) continue
    const caps: Record<string, unknown> = {}
    const protoBase = ed.protocols[id].baseURL.trim()
    if (protoBase) caps.base_url = protoBase // 空 = 不覆盖 (后端用 provider 级自动拼)
    if (id === 'openai-responses' && !ed.protocols[id].prevRid) {
      caps.previous_response_id = false // 缺省即生效, 只存显式 false
    }
    apisPayload[id] = caps
  }
  const enabledIds = Object.keys(apisPayload)
  if (!enabledIds.length) { editorError.value = '至少启用一个协议章节'; return }
  provider.apis = apisPayload
  saving.value = true
  try {
    const res = await callRoutine('models/save', { provider_id: pid, provider })
    if (res && res.ok === false) throw new Error(res.error || 'save failed')
    closeEditor()
    await refresh()
  } catch (e) {
    editorError.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

// ---- 删除 / 设默认 ----

async function removeProvider(pid: string): Promise<void> {
  try {
    const res = await callRoutine('models/delete', { provider_id: pid })
    if (res && res.ok === false) throw new Error(res.error || 'delete failed')
    closeEditor()
    await refresh()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function removeModel(pid: string, modelId: string): Promise<void> {
  try {
    const res = await callRoutine('models/delete', { provider_id: pid, model_id: modelId })
    if (res && res.ok === false) throw new Error(res.error || 'delete failed')
    await refresh()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function setDefault(key: string): Promise<void> {
  try {
    const res = await callRoutine('models/set_default', { model: key })
    if (res && res.ok === false) throw new Error(res.error || 'set_default failed')
    await refresh()
  } catch (e) {
    error.value = (e as Error).message
  }
}

// v-show 常驻挂载: 每次打开 (active → true) 拉最新配置
watch(() => props.active, (v) => { if (v) void refresh() })
// Esc 关闭编辑器 (输入框聚焦时也生效: 返回是安全操作, 未保存的修改直接丢弃)
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape' && editor.value) closeEditor()
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  if (props.active) void refresh()
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.models-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #13151f;
  overflow: hidden;
}

.mp-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 12px;
  border-bottom: 1px solid #2d3148;
  flex-shrink: 0;
}

.mp-title {
  font-size: 12px;
  font-weight: 700;
  color: #94a3b8;
  text-transform: uppercase;
  letter-spacing: 0.8px;
}

.mp-default-hint {
  flex: 1;
  font-size: 11px;
  color: #64748b;
  user-select: none;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mp-default-key {
  font-family: ui-monospace, Consolas, monospace;
  color: #818cf8;
}

.mp-actions { display: flex; gap: 6px; }

.mp-error { flex-shrink: 0; margin: 8px 12px 0; }
.mp-error pre {
  margin: 0;
  font-family: monospace;
  font-size: 12px;
  white-space: pre-wrap;
}

.mp-body { flex: 1; min-height: 0; }

/* ---- provider 列表 ---- */
.mp-list {
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mp-card {
  background: #1a1d27;
  border: 1px solid #2d3148;
  border-radius: 8px;
  overflow: hidden;
}

.mp-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  background: #15171f;
  border-bottom: 1px solid #2d3148;
}

.mp-pid {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.3px;
  color: #e2e8f0;
}

/* 行首旗子: 默认模型绿旗是状态位恒显示; 非默认灰旗是动作位, 悬浮行才
   显示 (hover 绿预示点击效果), 占位保留无布局跳动. naive 主题变量是
   内联 style, 直接给 .ic 上色. */
.mp-flag-btn {
  opacity: 0;
  visibility: hidden;
  transition: opacity .15s, visibility .15s;
}
.mp-model-row:hover .mp-flag-btn,
.mp-flag-btn:focus-within { opacity: 1; visibility: visible; }
.mp-flag-btn.is-default { opacity: 1; visibility: visible; }
.mp-flag-btn .ic { color: #565c74; }
.mp-flag-btn:hover .ic { color: #4ade80; }
.mp-flag-btn.is-default .ic { color: #4ade80; }

/* 悬浮 provider 卡片才显示操作按钮 (占位保留, 无布局跳动) */
.mp-card-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
  opacity: 0;
  visibility: hidden;
  transition: opacity .15s, visibility .15s;
}
.mp-card:hover .mp-card-actions,
.mp-card-actions:focus-within { opacity: 1; visibility: visible; }

.mp-model-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px 6px 24px;
  font-size: 12px;
  color: #b6bccf;
}
.mp-model-row + .mp-model-row { border-top: 1px solid #1e2130; }
.mp-model-row.is-default { background: rgba(34, 197, 94, 0.05); }

.mp-model-local {
  font-family: ui-monospace, Consolas, monospace;
  color: #cbd5e1;
  font-weight: 600;
}
.mp-arrow { color: #4b5563; flex-shrink: 0; }
.mp-model-id {
  font-family: ui-monospace, Consolas, monospace;
  color: #8b90a8;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mp-meta { font-size: 11px; color: #64748b; flex-shrink: 0; }
.mp-tools-tag { color: #8b90a8; }

/* 悬浮模型行才显示编辑/删除 (占位保留; 旗子同规则悬浮显示) */
.mp-row-actions {
  display: flex;
  gap: 4px;
  margin-left: auto;
  flex-shrink: 0;
  opacity: 0;
  visibility: hidden;
  transition: opacity .15s, visibility .15s;
}
.mp-model-row:hover .mp-row-actions,
.mp-row-actions:focus-within { opacity: 1; visibility: visible; }

.mp-model-none { padding: 8px 12px 8px 24px; font-size: 11px; color: #4b5563; }

.mp-empty { padding: 40px; text-align: center; color: #4b5280; font-size: 13px; }

/* ---- 编辑器 ---- */
.mp-editor {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px 20px 24px;
}
.mp-editor-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mp-editor-title { font-size: 14px; font-weight: 700; color: #c7d2fe; }
.mp-editor-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #8a93ad;
  cursor: pointer;
}
.mp-editor-close:hover { background: #2d3148; color: #e2e8f0; }

.mp-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 10px;
}
.mp-grid-2 { grid-template-columns: 1fr 1fr; }
.mp-grid-3 { grid-template-columns: 1fr 1fr 1fr; }

/* 模型编辑块内小节标题 */
.mp-section {
  margin-top: 10px;
  font-size: 10px;
  font-weight: 700;
  color: #565c74;
  letter-spacing: 1px;
  text-transform: uppercase;
  user-select: none;
}
.mp-inline-section { display: flex; align-items: center; gap: 8px; }
.mp-inline-section .mp-label { color: #818cf8; }

.mp-field { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
/* 数值 + 单位下拉并排 */
.mp-unit-row { display: flex; gap: 6px; align-items: center; }
.mp-label { font-size: 11px; color: #818cf8; user-select: none; }
.mp-hint { color: #64748b; font-weight: 400; }
.mp-req { color: #fca5a5; }

.mp-model-edit-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.mp-model-edit-title {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  color: #c7d2fe;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 能力图标开关: 灰 = 关, 绿 = 开 (与默认旗子同语义) */
.mp-icon-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 4px;
  border: 1px solid #2d3148;
  background: transparent;
  color: #565c74;
  cursor: pointer;
  transition: color .15s, border-color .15s, background .15s;
}
.mp-icon-toggle:hover { color: #8b90a8; border-color: #3f4460; }
.mp-icon-toggle.on {
  color: #4ade80;
  border-color: rgba(74, 222, 128, .35);
  background: rgba(34, 197, 94, .06);
}

/* 协议章节: 启用开关 + 名称 + 默认旗; off = 空章节 (不可用), 整体降透明 */
.mp-proto {
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid #2d3148;
  border-radius: 6px;
}
.mp-proto.off { opacity: .45; }
.mp-proto-head { display: flex; align-items: center; gap: 8px; }
.mp-proto-name { font-size: 12px; font-weight: 600; color: #c7d2fe; }
.mp-proto-body { margin-top: 8px; padding-left: 2px; display: flex; flex-direction: column; gap: 8px; }
.mp-proto-row { display: flex; align-items: center; gap: 8px; }
.mp-proto-row .mp-label { flex: none; min-width: 130px; margin: 0; }
.mp-proto-row .n-input { flex: 1; }

.mp-editor-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  border-top: 1px solid #2d3148;
  padding-top: 12px;
}
.mp-editor-error {
  margin-right: auto;
  font-size: 11px;
  color: #f87171;
  white-space: pre-wrap;
}
.mp-editor-btns { display: flex; gap: 8px; flex-shrink: 0; }
</style>
