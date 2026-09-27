/** pi 引擎 ModelRuntime 适配层 ---- 中立模型配置 (py models hub) 的 TS 消费方.
 *
 * 事实源是 py 侧 models.yaml: 经 kernel call models/get bridge routine (内部
 * req passive 常驻的 models hub) 拿全量 dump (含 api_key), 适配成 pi
 * ProviderConfigInput 后运行时注册进 ModelRuntime (registerProvider) ----
 * 不再读写 ~/.pi/agent/models.json (该文件留给 pi CLI 独立使用).
 *
 * 缓存进程级: 配置 hash 变化 (save/delete 写 yaml) 才重建 runtime;
 * 并发调用复用同一个 in-flight 重建.
 */
import { ModelRuntime } from '@earendil-works/pi-coding-agent'

/** routine 侧调用能力 (Routine 结构兼容, 传 this 即可). */
export interface CallCap {
  call(routineName: string, kwargs?: Record<string, any>): Promise<any>
}

/** 协议矩阵 {协议: {能力声明}} (provider 级事实). 恒存在 (未声明 = {}),
 *  未声明即隐式 openai-responses. 能力字段如 previous_response_id: false,
 *  base_url (协议级端点覆盖). */
export type ApiMatrix = Record<string, Record<string, unknown>>

/** model 级配置 (models/get 出口; 键 = 实际 API id). 'model' 是出口合成
 *  键 (恒等于配置键, 即实际 API id); 其余字段可选 (配置层只声明事实). */
export interface NeutralModelCfg {
  /** 实际 API id (出口合成, 恒等于该条目的键). */
  model: string
  /** 显示别名 (纯展示, 缺省用键). */
  name?: string
  max_context?: number
  max_tokens?: number
  /** 支持图片输入 (缺省 false; text 恒支持). */
  image?: boolean
  /** thinking 能力声明: effort = 默认档位, can_disable = 支持关,
   *  off = off 档发给 API 的值 (如 none). 未声明 = 非思考模型. */
  reasoning?: { effort?: string; can_disable?: boolean; off?: string }
  /** 纯 API 请求体透传字段, 原样合并进请求. */
  extra?: Record<string, unknown>
  /** 原生 API tools 字段项 (如 {type: web_search}). */
  tools?: Array<Record<string, unknown>>
}

/** 中立配置 (models/get 输出) 的形状. */
export interface NeutralConfig {
  default: string
  providers: Record<string, {
    api_key: string
    base_url: string
    apis: ApiMatrix
    models: Record<string, NeutralModelCfg>
  }>
}

export interface SharedModels {
  runtime: ModelRuntime
  config: NeutralConfig
}

/** pi 模型对象的形状子集 (避免跨包类型导入). */
export interface ModelLike {
  id: string
  provider: string
  name?: string
  contextWindow: number
}

// ProviderConfigInput 未从包导出, 经 registerProvider 参数推导
type PiProviderConfig = Parameters<ModelRuntime['registerProvider']>[1]

const ZERO_COST = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 }

/** 已知协议 (pi 引擎支持的 api 类型). 矩阵只能声明这些名字. */
const KNOWN_PROTOCOLS = ['openai-responses', 'openai-completions', 'anthropic-messages'] as const

type ProtocolId = NonNullable<PiProviderConfig['api']>

function isKnownProtocol(id: string): id is ProtocolId {
  return (KNOWN_PROTOCOLS as readonly string[]).includes(id)
}

/** 协议推导 (消费方缺省策略): 矩阵按固定优先级选第一个; 空矩阵 = 隐式
 * openai-responses. 与 py llm_provider 同规则. */
function pickApi(apis: ApiMatrix): ProtocolId {
  if (!apis || !Object.keys(apis).length) return 'openai-responses'
  for (const id of KNOWN_PROTOCOLS) {
    if (id in apis) return id
  }
  throw new Error(`apis matrix declares no known protocol: ${Object.keys(apis)}`)
}

/** 协议级 base_url 覆盖 (协议特例端点); 缺省沿用 provider 级. */
function pickBase(apis: ApiMatrix, api: string, fallback: string): string {
  const proto = (apis[api] as { base_url?: string } | undefined)?.base_url
  return (proto && proto.trim()) || fallback
}

/** 中立 provider 条目 -> pi ProviderConfigInput (绑定指定协议). */
function toPiProvider(cfg: NeutralConfig['providers'][string], api: ProtocolId): PiProviderConfig {
  const models = Object.entries(cfg.models).map(([local, m]) => {
    const reasoning = m.reasoning
    // text 恒支持; image 是唯一额外模态 (中立配置的 image bool)
    const input: ('text' | 'image')[] = m.image ? ['text', 'image'] : ['text']
    return {
      // 键 = 实际 API id; name 是显示别名 (纯展示, 缺省用键)
      id: String(m.model || local),
      name: String(m.name || local),
      reasoning: Boolean(reasoning?.effort),
      thinkingLevelMap: reasoning?.can_disable
        ? { off: String(reasoning.off ?? 'none') }
        : undefined,
      input,
      cost: ZERO_COST,
      // 缺省对齐 pi 内置模型默认 (128k ctx / 16k out); 传 0 会走 pi 的
      // clamp 坏分支 (contextWindow<=0 时 maxTokens 被夹成 1, 请求截断)
      contextWindow: Number(m.max_context ?? 128000),
      maxTokens: Number(m.max_tokens ?? 16384),
      samplingParams: m.extra,
    }
  })
  return {
    baseUrl: pickBase(cfg.apis, api, cfg.base_url),
    apiKey: cfg.api_key,
    api,
    models,
  }
}

/** 显式协议 -> 变体 provider 键: 每个已声明协议单独注册一份 (pi 的
 * registerProvider 按 providerId 存配置, 流式按 model.api 分派,
 * 协议变体只能以独立 provider 键共存). 键不进 UI 展示面
 * (agent 侧上报 model_key 用配置键). */
function variantKey(provider: string, protocol: string): string {
  return `${provider}@${protocol}`
}

let _shared: SharedModels | null = null
let _hash = ''
let _building: Promise<SharedModels> | null = null

/** 拉中立配置 + 适配注册 (hash 未变时直接复用缓存). */
export async function getSharedModels(cap: CallCap): Promise<SharedModels> {
  const config: NeutralConfig = await cap.call('models/get')
  const hash = JSON.stringify(config) // py 侧序列化顺序稳定
  if (_shared && hash === _hash) return _shared
  _building ??= (async () => {
    const runtime = await ModelRuntime.create({
      modelsPath: null, // 跳过 ~/.pi/agent/models.json
      refreshOnCreate: false, // 静态注册即可, 跳过 catalog/auth 网络刷新
    })
    for (const [providerId, providerCfg] of Object.entries(config.providers)) {
      // 基础注册: 矩阵推导协议 (缺省策略); 显式协议走下面的变体键.
      runtime.registerProvider(providerId, toPiProvider(providerCfg, pickApi(providerCfg.apis)))
      // 协议变体: 每个已声明的已知协议单独注册 (resolveModelRef 显式
      // 协议按变体键解析). 矩阵里的未知协议名跳过 ---- 显式选中它时由
      // resolveModelRef 报错, 不让坏键拖垮整个注册.
      for (const proto of Object.keys(providerCfg.apis ?? {})) {
        if (isKnownProtocol(proto)) {
          runtime.registerProvider(variantKey(providerId, proto), toPiProvider(providerCfg, proto))
        }
      }
    }
    _shared = { runtime, config }
    _hash = hash
    return _shared
  })().finally(() => { _building = null })
  return _building
}

/** 'provider/model_id' (model 部分 = 实际 API id, 即配置键) -> pi Model.
 * protocol 显式给定时走变体键 (必须已在矩阵声明, 同 py 规则: 事实先声明,
 * 怎么用是 agent 策略). 缺省 = 基础注册 (矩阵推导). */
export function resolveModelRef(
  m: SharedModels, ref: string, protocol?: string,
): ModelLike | undefined {
  const slash = ref.indexOf('/')
  if (slash <= 0) return undefined
  const provider = ref.slice(0, slash)
  const local = ref.slice(slash + 1)
  const providerCfg = m.config.providers[provider]
  const entry = providerCfg?.models?.[local]
  if (!entry) return undefined
  const modelId = String(entry.model || local)
  if (protocol !== undefined) {
    const apis = providerCfg.apis
    if (!apis || !(protocol in apis)) {
      const declared = Object.keys(apis ?? {})
      throw new Error(
        `model '${ref}': protocol '${protocol}' not in apis matrix `
        + `(declared: ${JSON.stringify(declared)}); how to use a protocol is `
        + `agent policy, but the fact must be declared first`,
      )
    }
    if (!isKnownProtocol(protocol)) {
      throw new Error(`unknown protocol '${protocol}' in apis matrix of '${ref}'`)
    }
    return m.runtime.getModel(variantKey(provider, protocol), modelId) as ModelLike | undefined
  }
  return m.runtime.getModel(provider, modelId) as ModelLike | undefined
}
