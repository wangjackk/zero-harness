import { ref, computed } from 'vue'
import type { WsMessage } from './useWS'

// ── 数据类型 ─────────────────────────────────────────────────────────────────

export interface DagMeta {
  name: string
  description: string
  filename: string
}

export interface DagInputSpec {
  name: string
  type: 'str' | 'int' | 'float' | 'bool'
  desc: string
  default: unknown
  required: boolean
}

export interface DagNodeSpec {
  id: string
  routine: string
  inputs: Record<string, unknown>
  depends_on: string[]
  trigger_rule: string
  when: string | null
  retry: { max_attempts: number; delay_ms: number; on_error: string } | null
}

export interface DagSpec {
  name: string
  description: string
  inputs: DagInputSpec[]
  nodes: DagNodeSpec[]
  outputs: Record<string, string>
}

export type NodeState = 'pending' | 'running' | 'retrying' | 'completed' | 'failed' | 'skipped'

export interface NodeRunState {
  state: NodeState
  output: unknown
  outputFormat: string   // text | json | md | yaml
  error: string | null
  attempt: number
  startedAt: number | null
  doneAt: number | null
}

export interface RunInfo {
  runId: string
  dag: string
  startedAt: number
  doneAt: number | null
  success: boolean | null
  error: string | null
}

// ── composable ───────────────────────────────────────────────────────────────

// 清单/spec/运行全部经 web_server 的 run_routine; dag_event 实时事件仍由 WS 推送.
export type RunRoutineFn = (
  name: string, kwargs: Record<string, unknown>, timeoutMs?: number,
) => Promise<any>
type OnFn = (type: string, fn: (msg: WsMessage) => void) => () => void

/** WS run_routine 回包是 {ok, result}; HTTP 版本已拆包. 两种都归一化. */
function unwrapRoutine(data: any): any {
  if (data && typeof data === 'object' && 'ok' in data) {
    if (!data.ok) throw new Error(String(data.error || 'routine failed'))
    return data.result
  }
  return data
}

export function useDag(runRoutine: RunRoutineFn, on: OnFn) {
  const dagList = ref<DagMeta[]>([])
  const currentSpec = ref<DagSpec | null>(null)
  const nodeStates = ref<Map<string, NodeRunState>>(new Map())
  const runInfo = ref<RunInfo | null>(null)
  const isRunning = ref(false)
  const loadingSpec = ref(false)
  const loadingList = ref(false)
  const runError = ref<string | null>(null)

  // 完成节点数 / 总节点数
  const progressText = computed(() => {
    if (!currentSpec.value || !runInfo.value) return ''
    const total = currentSpec.value.nodes.length
    const done = [...nodeStates.value.values()].filter(
      s => s.state === 'completed' || s.state === 'failed' || s.state === 'skipped',
    ).length
    return `${done} / ${total} 节点`
  })

  // 监听实时事件(由 DagPanel 在 onMounted 时注册)
  function setupListeners() {
    on('dag_run_started', (msg) => {
      nodeStates.value = new Map()
      runInfo.value = {
        runId: msg.run_id as string,
        dag: msg.dag as string,
        startedAt: Date.now(),
        doneAt: null,
        success: null,
        error: null,
      }
    })

    on('dag_node_event', (msg) => {
      const nodeId = msg.node_id as string
      const state = msg.state as NodeState
      const prev = nodeStates.value.get(nodeId) ?? {
        state: 'pending' as NodeState,
        output: null,
        outputFormat: 'text',
        error: null,
        attempt: 1,
        startedAt: null,
        doneAt: null,
      }
      const isTerminal = state === 'completed' || state === 'failed' || state === 'skipped'
      nodeStates.value.set(nodeId, {
        state,
        output: msg.output !== undefined ? msg.output : prev.output,
        outputFormat: (msg.output_format as string | undefined) ?? prev.outputFormat,
        error: (msg.error as string | null) ?? prev.error,
        attempt: (msg.attempt as number | undefined) ?? prev.attempt,
        startedAt: state === 'running' ? Date.now() : prev.startedAt,
        doneAt: isTerminal ? Date.now() : prev.doneAt,
      })
      // 触发响应式更新(Map 不自动追踪)
      nodeStates.value = new Map(nodeStates.value)
    })

    on('dag_run_done', (msg) => {
      if (runInfo.value) {
        runInfo.value = {
          ...runInfo.value,
          doneAt: Date.now(),
          success: msg.success as boolean,
          error: (msg.error as string | null) ?? null,
        }
      }
      isRunning.value = false
    })
  }

  async function fetchList() {
    loadingList.value = true
    try {
      const data = unwrapRoutine(await runRoutine('dag/list', {}))
      if (data?.error) throw new Error(String(data.error))
      dagList.value = (data?.dags as DagMeta[]) ?? []
    } catch (e) {
      dagList.value = []
    } finally {
      loadingList.value = false
    }
  }

  async function fetchSpec(dagName: string) {
    loadingSpec.value = true
    currentSpec.value = null
    nodeStates.value = new Map()
    runInfo.value = null
    try {
      const data = unwrapRoutine(await runRoutine('dag/spec', { dag: dagName }))
      if (data?.error) throw new Error(String(data.error))
      currentSpec.value = data.spec as DagSpec
    } catch (e) {
      currentSpec.value = null
    } finally {
      loadingSpec.value = false
    }
  }

  async function runDag(dagName: string, inputs: Record<string, unknown>) {
    if (isRunning.value) return
    isRunning.value = true
    runError.value = null
    nodeStates.value = new Map()
    runInfo.value = null
    try {
      // run_dag.inputs 契约为 JSON 字符串; timeout=0 = 等待整条工作流结束.
      unwrapRoutine(await runRoutine('run_dag', {
        dag: dagName,
        inputs: JSON.stringify(inputs),
      }, 0))
    } catch (e) {
      runError.value = (e as Error).message
    } finally {
      isRunning.value = false
    }
  }

  return {
    dagList,
    currentSpec,
    nodeStates,
    runInfo,
    isRunning,
    loadingSpec,
    loadingList,
    runError,
    progressText,
    setupListeners,
    fetchList,
    fetchSpec,
    runDag,
  }
}
