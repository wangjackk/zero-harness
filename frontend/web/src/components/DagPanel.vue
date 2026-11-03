<template>
  <div class="dag-panel">
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <div class="brand-mark">
          <span class="brand-icon">⬡</span>
          <span class="brand-text">DAG Runner</span>
        </div>
        <div class="divider-v" />
        <NSelect
          v-model:value="selectedDagName"
          :options="dagOptions"
          :loading="loadingList"
          placeholder="选择工作流..."
          size="small"
          style="width: 210px"
          @update:value="onDagSelect"
        />
        <template v-if="currentSpec">
          <div class="divider-v" />
          <template v-for="inp in currentSpec.inputs" :key="inp.name">
            <div class="input-wrap">
              <span class="input-label">{{ inp.name }}</span>
              <NSwitch
                v-if="inp.type === 'bool'"
                v-model:value="(inputValues[inp.name] as boolean)"
                size="small"
              />
              <NInputNumber
                v-else-if="inp.type === 'int' || inp.type === 'float'"
                v-model:value="(inputValues[inp.name] as number)"
                size="small"
                style="width: 88px"
                :show-button="false"
              />
              <NInput
                v-else
                v-model:value="(inputValues[inp.name] as string)"
                :placeholder="inp.desc || inp.name"
                size="small"
                style="width: 120px"
              />
            </div>
          </template>
        </template>
      </div>

      <div class="toolbar-right">
        <span v-if="currentSpec" class="node-count">{{ currentSpec.nodes.length }} nodes</span>
        <button
          class="run-btn"
          :class="{ running: isRunning, disabled: !selectedDagName || loadingSpec }"
          :disabled="!selectedDagName || loadingSpec"
          @click="handleRun"
        >
          <span class="run-icon">{{ isRunning ? '⏸' : '▶' }}</span>
          {{ isRunning ? 'Running...' : 'Run' }}
        </button>
      </div>
    </div>

    <!-- 画布区 -->
    <div class="canvas-wrap">
      <div class="flow-wrap">
        <transition name="fade">
          <div v-if="loadingSpec" class="empty-state">
            <NSpin size="small" /><span>Loading...</span>
          </div>
          <div v-else-if="!currentSpec" class="empty-state">
            <span class="empty-icon">⬡</span>
            <span class="empty-title">No workflow selected</span>
            <span class="empty-sub">Select a DAG from the dropdown above</span>
          </div>
        </transition>

        <VueFlow
          v-if="currentSpec"
          :nodes="flowNodes"
          :edges="flowEdges"
          fit-view-on-init
          :zoom-on-double-click="false"
          :nodes-draggable="false"
          :nodes-connectable="false"
          :elements-selectable="false"
          class="flow-canvas"
          @node-click="onNodeClick"
        >
          <template #node-dagNode="{ data }">
            <DagNodeBox :data="data" />
          </template>
          <Background variant="dots" pattern-color="#313335" :gap="22" :size="1.2" />
          <Controls :show-interactive="false" />
        </VueFlow>
      </div>

      <!-- 节点详情面板 -->
      <transition name="nd-slide">
        <div v-if="selectedNodeData" class="node-detail">
          <div class="nd-header">
            <div class="nd-title-row">
              <span class="nd-id">{{ selectedNodeData.spec.id }}</span>
              <span class="nd-routine">{{ selectedNodeData.spec.routine }}</span>
            </div>
            <button class="nd-close" @click="selectedNodeData = null">✕</button>
          </div>
          <div class="nd-body">
            <template v-if="selectedNodeData.output != null">
              <div class="nd-label">
                Output
                <span class="nd-fmt-badge">{{ selectedNodeData.outputFormat }}</span>
              </div>
              <pre class="nd-content nd-ok" :class="`nd-fmt-${selectedNodeData.outputFormat}`">{{ formatNodeValue(selectedNodeData.output, selectedNodeData.outputFormat) }}</pre>
            </template>
            <template v-if="selectedNodeData.error">
              <div class="nd-label">Error</div>
              <pre class="nd-content nd-err">{{ selectedNodeData.error }}</pre>
            </template>
            <div v-if="selectedNodeData.output == null && !selectedNodeData.error" class="nd-empty">
              {{ selectedNodeData.state === 'pending' ? '未运行' : selectedNodeData.state }}
            </div>
          </div>
        </div>
      </transition>
    </div>

    <!-- 底部状态栏 -->
    <div class="foot-bar">
      <template v-if="runInfo">
        <div class="foot-run-id">
          <span class="foot-label">run</span>
          <code class="foot-id">{{ runInfo.runId }}</code>
        </div>
        <div class="foot-sep" />
        <span class="foot-chip" :class="`fc-${runInfo.success === true ? 'ok' : runInfo.success === false ? 'fail' : 'run'}`">
          <span class="fc-dot" />{{ statusLabel }}
        </span>
        <span v-if="progressText" class="foot-meta">{{ progressText }}</span>
        <span v-if="elapsed" class="foot-elapsed">{{ elapsed }}</span>
        <span v-if="runError" class="foot-err" :title="runError">{{ runError }}</span>
      </template>
      <template v-else-if="currentSpec">
        <span class="foot-meta">{{ currentSpec.description || (currentSpec.nodes.length + ' nodes') }}</span>
      </template>
      <template v-else>
        <span class="foot-meta">Workflow Visualizer</span>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import { ref, computed, watch, onMounted } from 'vue'
import { VueFlow } from '@vue-flow/core'
import type { NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import type { Node as FlowNode, Edge as FlowEdge } from '@vue-flow/core'
import { MarkerType } from '@vue-flow/core'
import Dagre from '@dagrejs/dagre'
import { NSelect, NInput, NInputNumber, NSwitch, NSpin } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import DagNodeBox from './DagNodeBox.vue'
import { useDag } from '../composables/useDag'
import type { DagSpec, DagNodeSpec, RunRoutineFn } from '../composables/useDag'

const props = defineProps<{
  runRoutine: RunRoutineFn
  on: (type: string, fn: (msg: Record<string, unknown>) => void) => () => void
  connected: boolean
}>()

const {
  dagList, currentSpec, nodeStates, runInfo, isRunning,
  loadingSpec, loadingList, runError, progressText,
  setupListeners, fetchList, fetchSpec, runDag,
} = useDag(props.runRoutine, props.on as never)

const selectedDagName = ref<string | null>(null)
const inputValues = ref<Record<string, unknown>>({})
const selectedNodeData = ref<null | {
  spec: { id: string; routine: string }
  state: string
  output: unknown
  outputFormat: string
  error: string | null
}>(null)

function onNodeClick({ node }: NodeMouseEvent) {
  selectedNodeData.value = node.data
}

function formatNodeValue(val: unknown, fmt = 'text'): string {
  if (fmt === 'json') {
    try {
      const parsed = typeof val === 'string' ? JSON.parse(val) : val
      return JSON.stringify(parsed, null, 2)
    } catch { /* fall through */ }
  }
  return typeof val === 'string' ? val : JSON.stringify(val, null, 2)
}

const dagOptions = computed<SelectOption[]>(() =>
  dagList.value.map(d => ({ label: d.name, value: d.filename, title: d.description })),
)

async function onDagSelect(name: string) { await fetchSpec(name) }

watch(currentSpec, (spec) => {
  if (!spec) return
  const vals: Record<string, unknown> = {}
  for (const inp of spec.inputs) {
    if (inp.default !== null && inp.default !== undefined) vals[inp.name] = inp.default
    else if (inp.type === 'bool') vals[inp.name] = false
    else if (inp.type === 'int' || inp.type === 'float') vals[inp.name] = null
    else vals[inp.name] = ''
  }
  inputValues.value = vals
})

// ── 槽位解析 ───────────────────────────────────────────────────────────────

export interface InputPort {
  id: string          // handle id:参数名 | '__trigger__' | '__cond_<nodeId>__'
  type: 'data' | 'cond' | 'trigger' | 'wi' | 'literal'
  param?: string      // 参数名(data/wi/literal 有)
  sourceNodes?: string[] // 来源节点 id 列表(data 有,可多个)
  sourceNode?: string    // 来源节点 id(cond 有,单个)
  ref?: string           // 原始表达式(data 有)
  condExpr?: string      // when 表达式(cond 有)
  wiKey?: string         // WI 键名(wi 有)
  value?: string         // 字面值(literal 有)
  sources?: string[]     // trigger-only 的依赖节点列表
}

// 无 ^ 锚点 + 全局标志:能匹配字符串中任意位置,任意数量的 $nodeId.output 引用
const NODE_REF_GLOBAL_RE = /\$([a-zA-Z_][a-zA-Z0-9_-]*)\.output(?:\.([a-zA-Z_][a-zA-Z0-9_]*))?/g
const WI_REF_RE          = /\$WI\.([a-zA-Z_][a-zA-Z0-9_]*)/

function getInputPorts(node: DagNodeSpec): InputPort[] {
  const ports: InputPort[] = []
  const coveredDeps = new Set<string>()

  // 1. inputs 里的 $nodeId.output 引用 → data 端口(支持嵌入字符串 & 多引用)
  for (const [param, val] of Object.entries(node.inputs ?? {})) {
    if (typeof val === 'string') {
      // matchAll 找出字符串中所有 $nodeId.output,过滤出属于 depends_on 的节点并去重
      const nodeRefs = [...val.matchAll(new RegExp(NODE_REF_GLOBAL_RE))]
        .map(m => m[1])
        .filter(nodeId => node.depends_on.includes(nodeId))
        .filter((v, i, a) => a.indexOf(v) === i)

      if (nodeRefs.length > 0) {
        for (const nodeId of nodeRefs) coveredDeps.add(nodeId)
        ports.push({ id: param, type: 'data', param, sourceNodes: nodeRefs, ref: val })
        continue
      }
      const wiM = val.match(WI_REF_RE)
      if (wiM) {
        ports.push({ id: `wi__${param}`, type: 'wi', param, wiKey: wiM[1] })
        continue
      }
      ports.push({ id: `lit__${param}`, type: 'literal', param, value: String(val) })
    } else if (val !== null && val !== undefined) {
      ports.push({ id: `lit__${param}`, type: 'literal', param, value: JSON.stringify(val) })
    }
  }

  // 2. when 里的 $nodeId.output 引用 → cond 端口(条件边,单个 sourceNode)
  if (node.when) {
    const re = /\$([a-zA-Z_][a-zA-Z0-9_-]*)\.output/g
    let m: RegExpExecArray | null
    while ((m = re.exec(node.when)) !== null) {
      const nodeId = m[1]
      if (node.depends_on.includes(nodeId) && !coveredDeps.has(nodeId)) {
        coveredDeps.add(nodeId)
        ports.push({
          id: `__cond_${nodeId}__`,
          type: 'cond',
          sourceNode: nodeId,
          condExpr: node.when,
        })
      }
    }
  }

  // 3. 剩余纯 trigger 依赖(既无 data 也无 cond)
  const triggerDeps = node.depends_on.filter(dep => !coveredDeps.has(dep))
  if (triggerDeps.length > 0) {
    ports.push({ id: '__trigger__', type: 'trigger', sources: triggerDeps })
  }

  return ports
}

// ── dagre 布局 ──────────────────────────────────────────────────────────────

import { NODE_W, HEADER_H, ROW_H, SEC_PAD_T, CHIP_H } from './dagConstants'

function nodeHeight(ports: InputPort[]): number {
  const rowCount = ports.length  // trigger 也占一行
  const inputsH = rowCount > 0 ? 1 + SEC_PAD_T + rowCount * ROW_H + 3 : 0
  return HEADER_H + inputsH + 1 + CHIP_H + 4
}

function buildLayout(spec: DagSpec): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const g = new Dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 50, ranksep: 110 })

  for (const n of spec.nodes) {
    const ports = getInputPorts(n)
    const h = nodeHeight(ports)
    g.setNode(n.id, { width: NODE_W, height: h })
  }
  for (const n of spec.nodes)
    for (const dep of n.depends_on) g.setEdge(dep, n.id)
  Dagre.layout(g)

  const nodes: FlowNode[] = spec.nodes.map(n => {
    const pos = g.node(n.id)
    const ports = getInputPorts(n)
    const h = nodeHeight(ports)
    const rs = nodeStates.value.get(n.id)
    return {
      id: n.id,
      type: 'dagNode',
      position: { x: pos.x - NODE_W / 2, y: pos.y - h / 2 },
      data: {
        spec: n,
        inputPorts: ports,
        state: rs?.state ?? 'pending',
        output: rs?.output ?? null,
        outputFormat: rs?.outputFormat ?? 'text',
        error: rs?.error ?? null,
        attempt: rs?.attempt ?? 1,
        startedAt: rs?.startedAt ?? null,
        doneAt: rs?.doneAt ?? null,
      },
    }
  })

  const edges: FlowEdge[] = []
  for (const n of spec.nodes) {
    const ports = getInputPorts(n)
    const state   = nodeStates.value.get(n.id)?.state
    const running = state === 'running'
    const doneOk  = state === 'completed'
    const failed  = state === 'failed'
    const dataColor    = running ? '#4f84c4' : doneOk ? '#57965c' : failed ? '#c75450' : '#454749'
    const condColor    = running ? '#9876aa' : doneOk ? '#57965c' : failed ? '#c75450' : '#4a3060'
    const triggerColor = running ? '#4f84c4' : '#3a3e45'

    // dep → { handleId, type } 映射(一个 dep 可能是多个 data port 的 source,但每条边独立)
    for (const p of ports) {
      if (p.type === 'data' && p.sourceNodes) {
        // 多个 source node 都指向同一个 handle(例:data: "北方=$north.output 南方=$south.output")
        for (const srcId of p.sourceNodes) {
          edges.push({
            id: `${srcId}→${n.id}.${p.id}`,
            source: srcId,  sourceHandle: 'output',
            target: n.id,   targetHandle: p.id,
            type: 'bezier',
            animated: running,
            style: { stroke: dataColor, strokeWidth: 1.5 },
            markerEnd: { type: MarkerType.ArrowClosed, color: dataColor, width: 12, height: 12 },
          })
        }
      } else if (p.type === 'cond' && p.sourceNode) {
        edges.push({
          id: `${p.sourceNode}→${n.id}.${p.id}`,
          source: p.sourceNode,  sourceHandle: 'output',
          target: n.id,          targetHandle: p.id,
          type: 'bezier',
          animated: running,
          style: { stroke: condColor, strokeWidth: 1.5, strokeDasharray: '6 3' },
          markerEnd: { type: MarkerType.ArrowClosed, color: condColor, width: 12, height: 12 },
        })
      } else if (p.type === 'trigger' && p.sources) {
        for (const srcId of p.sources) {
          edges.push({
            id: `${srcId}→${n.id}.__trigger__`,
            source: srcId,  sourceHandle: 'output',
            target: n.id,   targetHandle: '__trigger__',
            type: 'bezier',
            animated: running,
            style: { stroke: triggerColor, strokeWidth: 1.2, strokeDasharray: '4 4' },
            markerEnd: { type: MarkerType.ArrowClosed, color: triggerColor, width: 10, height: 10 },
          })
        }
      }
    }
  }
  return { nodes, edges }
}

const flowNodes = computed<FlowNode[]>(() => currentSpec.value ? buildLayout(currentSpec.value).nodes : [])
const flowEdges = computed<FlowEdge[]>(() => currentSpec.value ? buildLayout(currentSpec.value).edges : [])

function handleRun() {
  if (!selectedDagName.value) return
  runDag(selectedDagName.value, inputValues.value)
}

const statusLabel = computed(() => {
  if (!runInfo.value) return ''
  return runInfo.value.success === true ? 'Done' : runInfo.value.success === false ? 'Failed' : 'Running'
})

const elapsed = computed(() => {
  if (!runInfo.value) return ''
  const end = runInfo.value.doneAt ?? (isRunning.value ? Date.now() : null)
  if (!end) return ''
  const ms = end - runInfo.value.startedAt
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
})

onMounted(() => {
  setupListeners()
})

watch(() => props.connected, (v) => {
  if (v) fetchList()
}, { immediate: true })
</script>

<style>
.dag-panel .vue-flow__edge-path { stroke: #454749; stroke-width: 1.6px; }
.dag-panel .vue-flow__edge.animated .vue-flow__edge-path { stroke: #4f84c4; }
.dag-panel .vue-flow__controls {
  background: #2b2b2b;
  border: 1px solid #454749;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0,0,0,.4);
  overflow: hidden;
}
.dag-panel .vue-flow__controls-button {
  background: transparent; border: none; color: #6e7176; transition: background .15s;
}
.dag-panel .vue-flow__controls-button:hover { background: #3c3f41; color: #bababa; }
.dag-panel .vue-flow__controls-button + .vue-flow__controls-button { border-top: 1px solid #3a3c3f; }
</style>

<style scoped>
.dag-panel {
  display: flex; flex-direction: column; height: 100%;
  background: #1e1f22; border-radius: 10px; overflow: hidden;
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', ui-monospace, monospace;
  color: #bababa;
}
.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 14px; height: 48px; background: #2b2b2b;
  border-bottom: 1px solid #3a3c3f; flex-shrink: 0; gap: 10px;
}
.toolbar-left  { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-width: 0; }
.toolbar-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.brand-mark { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.brand-icon { font-size: 16px; color: #4f84c4; line-height: 1; }
.brand-text { font-size: 13px; font-weight: 600; color: #bababa; white-space: nowrap; }
.divider-v  { width: 1px; height: 18px; background: #3a3c3f; flex-shrink: 0; }
.input-wrap { display: flex; align-items: center; gap: 5px; }
.input-label { font-size: 11px; color: #6e7176; white-space: nowrap; }
.node-count  { font-size: 11px; color: #5c6065; }
.run-btn {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 14px; border-radius: 5px;
  border: 1px solid #3a5c8a; background: #1e3a5a;
  color: #7ab3e0; font-size: 12px; font-weight: 500;
  cursor: pointer; font-family: inherit; white-space: nowrap;
  transition: background .15s, border-color .15s, transform .1s;
}
.run-btn:hover:not(.disabled) { background: #244a74; border-color: #4f84c4; color: #9ecaf0; transform: translateY(-1px); }
.run-btn:active:not(.disabled) { transform: translateY(0); }
.run-btn.running { background: #1a2a40; border-color: #2d4a6a; color: #5c8ab8; cursor: default; animation: btn-pulse 2s ease-in-out infinite; }
.run-btn.disabled { background: #252628; border-color: #3a3c3f; color: #4a4d52; cursor: not-allowed; }
.run-icon { font-size: 10px; }
@keyframes btn-pulse { 0%,100%{opacity:1} 50%{opacity:.7} }
.canvas-wrap { flex: 1; display: flex; overflow: hidden; background: #1e1f22; }
.flow-wrap { flex: 1; position: relative; overflow: hidden; }
.flow-canvas { width: 100%; height: 100%; background: transparent; }

/* 节点详情面板 */
.node-detail {
  width: 340px; flex-shrink: 0;
  display: flex; flex-direction: column;
  background: #25272b; border-left: 1px solid #3a3c3f;
  overflow: hidden;
}
.nd-header {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 10px 12px 8px; border-bottom: 1px solid #3a3c3f; flex-shrink: 0; gap: 8px;
}
.nd-title-row { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.nd-id { font-size: 13px; font-weight: 700; color: #d4d6db; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nd-routine { font-size: 10px; color: #5c6065; }
.nd-close {
  background: none; border: none; color: #5c6065; font-size: 12px;
  cursor: pointer; padding: 2px 4px; border-radius: 3px; flex-shrink: 0;
  transition: background .15s, color .15s;
}
.nd-close:hover { background: #3a3c3f; color: #bababa; }
.nd-body { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
.nd-label { font-size: 10px; font-weight: 600; color: #5c6065; text-transform: uppercase; letter-spacing: .06em; display: flex; align-items: center; gap: 5px; }
.nd-fmt-badge { font-size: 9px; font-weight: 500; background: #2d3a4a; color: #4f84c4; border-radius: 3px; padding: 1px 5px; text-transform: lowercase; letter-spacing: 0; }
.nd-fmt-json { color: #9ecaf0; }
.nd-fmt-md   { color: #c8c080; }
.nd-fmt-yaml { color: #a0c080; }
.nd-content {
  font-family: inherit; font-size: 11px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-all;
  background: #1e1f22; border-radius: 4px; padding: 8px 10px;
  border: 1px solid #3a3c3f; margin: 0;
}
.nd-ok  { color: #7ab87a; border-color: #1e3a1e; }
.nd-err { color: #c48080; border-color: #3a1e1e; }
.nd-empty { font-size: 12px; color: #454749; text-align: center; padding: 24px 0; }

.nd-slide-enter-active { transition: width .2s ease, opacity .2s ease; }
.nd-slide-leave-active { transition: width .15s ease, opacity .15s ease; }
.nd-slide-enter-from, .nd-slide-leave-to { width: 0; opacity: 0; }
.empty-state {
  position: absolute; inset: 0; z-index: 10; pointer-events: none;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px;
}
.empty-icon  { font-size: 36px; color: #3a3c3f; }
.empty-title { font-size: 15px; font-weight: 600; color: #5c6065; }
.empty-sub   { font-size: 12px; color: #454749; }
.fade-enter-active,.fade-leave-active{transition:opacity .2s}
.fade-enter-from,.fade-leave-to{opacity:0}
.foot-bar {
  display: flex; align-items: center; gap: 9px; padding: 0 14px;
  height: 32px; background: #2b2b2b; border-top: 1px solid #3a3c3f;
  flex-shrink: 0; font-size: 11px; overflow: hidden;
}
.foot-run-id { display: flex; align-items: center; gap: 5px; }
.foot-label  { font-size: 10px; font-weight: 600; color: #5c6065; text-transform: uppercase; letter-spacing: .06em; }
.foot-id     { font-family: inherit; font-size: 11px; color: #4f84c4; background: #1a2b3d; padding: 1px 6px; border-radius: 3px; border: 1px solid #2a4060; }
.foot-sep    { width: 1px; height: 12px; background: #3a3c3f; }
.foot-chip   { display: inline-flex; align-items: center; gap: 4px; padding: 1px 7px; border-radius: 3px; font-size: 10px; font-weight: 600; background: #2d2f31; color: #5c6065; text-transform: uppercase; letter-spacing: .03em; }
.fc-dot      { width: 5px; height: 5px; border-radius: 50%; background: #454749; flex-shrink: 0; }
.fc-run      { color: #4f84c4; background: #1a2b3d; }
.fc-run  .fc-dot { background: #4f84c4; animation: blink 1s infinite; }
.fc-ok       { color: #57965c; background: #1a2b1a; }
.fc-ok   .fc-dot { background: #57965c; }
.fc-fail     { color: #c75450; background: #2b1a1a; }
.fc-fail .fc-dot { background: #c75450; }
.foot-meta    { color: #5c6065; }
.foot-elapsed { font-size: 10px; color: #5c6065; background: #252628; padding: 1px 5px; border-radius: 3px; }
.foot-err     { color: #c75450; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.2} }
</style>
