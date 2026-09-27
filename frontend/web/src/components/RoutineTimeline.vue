<template>
  <div class="tl-card" ref="cardEl">
    <div class="tl-head">
      <span class="tl-title">Timeline</span>
      <span v-if="rootName" class="tl-root" title="点击树节点取消过滤">
        ⌖ {{ rootName }}<span class="tl-root-id">#{{ rootId }}</span>
      </span>
      <span class="tl-count">{{ lanes.length }}</span>
    </div>
    <div class="tl-body">
      <div class="tl-scroll" @mouseleave="hover = null">
        <div class="tl-lanes">
          <div v-for="(lane, li) in lanes" :key="li" class="tl-lane">
            <div class="tl-label" :title="lane.name">
              <span class="tl-dot" :class="lane.state" />{{ lane.name }}<span class="tl-id">×{{ lane.insts.length }}</span>
            </div>
            <div class="tl-track">
              <template v-for="inst in lane.insts" :key="inst.id">
                <div v-if="inst.pre" class="tl-seg pre" :style="seg(inst.pre)"
                     @mouseenter="hoverOn($event, inst)" @mousemove="hoverOn($event, inst)" @mouseleave="hover = null" />
                <div class="tl-seg" :class="inst.state" :style="seg(inst.main)"
                     @mouseenter="hoverOn($event, inst)" @mousemove="hoverOn($event, inst)" @mouseleave="hover = null" />
              </template>
            </div>
          </div>
          <div v-if="!lanes.length" class="tl-empty">no events</div>
        </div>
      </div>
      <div class="tl-axis">
        <div v-for="tk in ticks" :key="tk.t" class="tl-tick" :style="{ left: pct(tk.t) + '%' }">
          <span>{{ tk.label }}</span>
        </div>
      </div>
    </div>
    <!-- 悬浮执行详情 -->
    <div v-if="hover" class="tl-tip" :style="tipStyle">
      <div class="tl-tip-name">{{ hover.inst.name }}<span class="tl-id">#{{ hover.inst.id }}</span></div>
      <div class="tl-tip-row"><span>state</span><b :class="hover.inst.state">{{ hover.inst.state }}</b></div>
      <div class="tl-tip-row"><span>created</span><b>{{ fmt(hover.inst.t0) }}</b></div>
      <div v-if="hover.inst.started != null" class="tl-tip-row"><span>started</span><b>{{ fmt(hover.inst.started) }}</b></div>
      <div v-if="hover.inst.t1 != null" class="tl-tip-row"><span>stopped</span><b>{{ fmt(hover.inst.t1) }}</b></div>
      <div v-if="hover.inst.started != null" class="tl-tip-row">
        <span>duration</span><b>{{ fmtDur((hover.inst.t1 ?? tEnd) - hover.inst.started) }}</b>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

export interface LifecycleEvent {
  t: number
  ev: string // created | started | stopped | destroyed
  id: string
  pid: string
  name: string
}

interface Seg { a: number; b: number }
interface Inst {
  id: string
  name: string
  t0: number
  started: number | null
  t1: number | null
  state: 'running' | 'ended' | 'created'
  pre: Seg | null
  main: Seg
}
interface Lane { name: string; state: string; insts: Inst[] }

const props = defineProps<{ events: LifecycleEvent[]; rootId?: string | null }>()

// 视图右端 = 最后一个事件时刻: 窗口只在事件到达时扩张, 不随真实时间
// 持续右滑(空转时 clip 不缩小, 右侧不留大片空白).
const tEnd = computed(() => {
  let m = 0
  for (const e of filtered.value) if (e.t > m) m = e.t
  return m
})

// 子树过滤: pid 取自事件首帧(节点 stop 后 live tree 会删,历史 pid 仍在).
const filtered = computed<LifecycleEvent[]>(() => {
  const evs = props.events
  const root = props.rootId
  if (!root) return evs
  const pidOf = new Map<string, string>()
  for (const e of evs) if (!pidOf.has(e.id)) pidOf.set(e.id, e.pid)
  const inTree = (id: string): boolean => {
    for (let i = 0, cur = id; i < 64; i++, cur = pidOf.get(cur) ?? '0') {
      if (cur === root) return true
      if (cur === '0') return false
    }
    return false
  }
  return evs.filter(e => inTree(e.id))
})

const rootName = computed(() => {
  const root = props.rootId
  if (!root) return ''
  return props.events.find(e => e.id === root)?.name ?? ''
})

// 同名 routine 复用 lane: 时间片不重叠的实例打包进同一行
// (running 实例延伸到 tEnd, 两个 running 必重叠 → 各占一行).
const lanes = computed<Lane[]>(() => {
  const insts = new Map<string, Inst>()
  for (const e of filtered.value) {
    // 过滤根自身: 级联停止保证子 clip 全嵌套在父 clip 内,
    // 根的条永远横贯全宽, 不携带信息, 跳过.
    if (props.rootId && e.id === props.rootId) continue
    let l = insts.get(e.id)
    if (!l) {
      l = { id: e.id, name: e.name, t0: e.t, started: null, t1: null,
            state: 'running', pre: null, main: { a: e.t, b: tEnd.value } }
      insts.set(e.id, l)
    }
    if (e.ev === 'started') l.started = e.t
    else if (e.ev === 'stopped' || e.ev === 'destroyed') l.t1 = e.t
    l.state = l.t1 != null ? 'ended' : (l.started != null ? 'running' : 'created')
    l.pre = l.started != null && l.started > l.t0 ? { a: l.t0, b: l.started } : null
    l.main = { a: l.started ?? l.t0, b: l.t1 ?? tEnd.value }
  }
  // 按名分组, 首次适配装箱 (实例按 t0 排序, 行内互不重叠)
  const byName = new Map<string, Inst[][]>()
  for (const inst of [...insts.values()].sort((a, b) => a.t0 - b.t0)) {
    const rows = byName.get(inst.name) ?? []
    const end = inst.t1 ?? tEnd.value
    let placed = false
    for (const row of rows) {
      if (row.every(o => {
        const oe = o.t1 ?? tEnd.value
        return !(inst.t0 < oe && o.t0 < end)
      })) {
        row.push(inst)
        placed = true
        break
      }
    }
    if (!placed) rows.push([inst])
    byName.set(inst.name, rows)
  }
  const out: Lane[] = []
  for (const [name, rows] of byName) {
    for (const row of rows) {
      out.push({
        name, insts: row,
        state: row.some(i => i.state === 'running') ? 'running'
             : row.some(i => i.state === 'created') ? 'created' : 'ended',
      })
    }
  }
  return out
})

// 时间窗: 覆盖全部历史 + 最后事件时刻; 最小 5s 免除零除.
// 右端补 3% 余量: tEnd 恰在窗口边缘的话, 最后一条 clip/playhead
// 会被钳到 100% 处贴边裁切, 看起来像"缺一条".
const view = computed(() => {
  let tMin = tEnd.value, tMax = tEnd.value
  for (const l of lanes.value) {
    for (const i of l.insts) {
      if (i.t0 < tMin) tMin = i.t0
      if (i.t1 != null && i.t1 > tMax) tMax = i.t1
    }
  }
  const span = Math.max(tMax - tMin, 5000)
  return { tMin, span: span * 1.03 }
})

const pct = (t: number) => Math.max(0, Math.min(100, ((t - view.value.tMin) / view.value.span) * 100))
const seg = (s: Seg) => ({
  left: pct(s.a) + '%',
  width: Math.max(pct(s.b) - pct(s.a), 0.4) + '%',
})

// 刻度: 从 1s~10min 里选一个 ≥ span/8 的整齐步长
const ticks = computed(() => {
  const { tMin, span } = view.value
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
  const step = steps.find(s => s * 1000 >= span / 8) ?? 600
  const out: Array<{ t: number; label: string }> = []
  for (let t = Math.ceil(tMin / (step * 1000)) * step * 1000; t <= tMin + span; t += step * 1000) {
    const d = new Date(t)
    out.push({ t, label: step < 60
      ? `${d.getMinutes()}:${String(d.getSeconds()).padStart(2, '0')}`
      : `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}` })
  }
  return out
})

// ---- 悬浮详情 ----
const cardEl = ref<HTMLElement | null>(null)
const hover = ref<{ inst: Inst; x: number; y: number } | null>(null)

function hoverOn(e: MouseEvent, inst: Inst) {
  const rect = cardEl.value?.getBoundingClientRect()
  hover.value = {
    inst,
    x: e.clientX - (rect?.left ?? 0),
    y: e.clientY - (rect?.top ?? 0),
  }
}

const tipStyle = computed(() => {
  const h = hover.value
  if (!h) return {}
  const card = cardEl.value?.getBoundingClientRect()
  const w = 200, ht = 110
  let x = h.x + 14
  if (card && x + w > card.width - 8) x = h.x - w - 14
  let y = h.y + 14
  if (card && y + ht > card.height - 8) y = h.y - ht - 10
  return { left: `${x}px`, top: `${y}px` }
})

function fmt(t: number): string {
  const d = new Date(t)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}
function fmtDur(ms: number): string {
  if (ms < 1_000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`
  const m = Math.floor(ms / 60_000), s = Math.round((ms % 60_000) / 1000)
  return `${m}m${String(s).padStart(2, '0')}s`
}
</script>

<style scoped>
.tl-card {
  position: relative;
  flex: 1;
  min-width: 480px;
  display: flex;
  flex-direction: column;
  background: #1e2130;
  border: 1px solid #3d4270;
  border-radius: 10px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.6);
  overflow: hidden;
}

.tl-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid #3d4270;
}
.tl-title { font-size: 13px; color: #e2e8f0; font-weight: 600; }

.tl-root {
  font-size: 11px;
  color: #f59e0b;
  background: #3a3222;
  border-radius: 8px;
  padding: 1px 8px;
  font-family: ui-monospace, Consolas, monospace;
}
.tl-root-id { color: #92703a; margin-left: 2px; }
.tl-count {
  font-size: 11px; color: #7a8199; background: #2a2e44;
  border-radius: 8px; padding: 1px 8px;
}

.tl-body { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.tl-scroll { flex: 1; overflow-y: auto; }

.tl-lanes { position: relative; padding: 6px 0; }

.tl-lane {
  display: flex;
  align-items: center;
  height: 24px;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
}

.tl-label {
  flex: 0 0 148px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding-left: 12px;
  color: #cbd3e1;
  white-space: nowrap;
  overflow: hidden;
}
.tl-dot { flex-shrink: 0; width: 6px; height: 6px; border-radius: 50%; background: #555b70; }
.tl-dot.running { background: #4ade80; box-shadow: 0 0 5px rgba(74, 222, 128, 0.55); }
.tl-dot.ended { background: #555b70; }
.tl-id { color: #565d75; font-size: 11px; }

.tl-track { position: relative; flex: 1; height: 100%; }

.tl-seg {
  position: absolute;
  top: 7px;
  height: 10px;
  border-radius: 3px;
  min-width: 2px;
}
.tl-seg.pre { background: #565d75; }
.tl-seg.created { background: #b8a03c; }
.tl-seg.running {
  background: linear-gradient(90deg, #22c55e, #4ade80);
  box-shadow: 0 0 6px rgba(74, 222, 128, 0.35);
}
.tl-seg.ended { background: #3d4270; }
.tl-seg.pre, .tl-seg.created, .tl-seg.running, .tl-seg.ended { cursor: pointer; }
.tl-seg.pre:hover, .tl-seg.created:hover,
.tl-seg.running:hover, .tl-seg.ended:hover { filter: brightness(1.35); }

.tl-empty { padding: 16px; text-align: center; color: #565d75; font-size: 12px; }

.tl-axis {
  position: relative;
  height: 22px;
  border-top: 1px solid #3d4270;
  flex-shrink: 0;
}
.tl-tick {
  position: absolute;
  top: 0; bottom: 0;
  border-left: 1px solid #2a2e44;
}
.tl-tick span {
  position: absolute;
  left: 4px;
  top: 3px;
  font-size: 10px;
  color: #565d75;
  font-family: ui-monospace, Consolas, monospace;
}

/* 悬浮详情卡片 */
.tl-tip {
  position: absolute;
  width: 200px;
  background: #16182a;
  border: 1px solid #3d4270;
  border-radius: 8px;
  padding: 8px 10px;
  pointer-events: none;
  z-index: 10;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
}
.tl-tip-name {
  font-family: ui-monospace, Consolas, monospace;
  font-size: 12px;
  color: #e2e8f0;
  font-weight: 600;
  margin-bottom: 6px;
}
.tl-tip-row {
  display: flex;
  justify-content: space-between;
  font-family: ui-monospace, Consolas, monospace;
  font-size: 11px;
  color: #7a8199;
  line-height: 1.7;
}
.tl-tip-row b { color: #cbd3e1; font-weight: 500; }
.tl-tip-row b.running { color: #4ade80; }
.tl-tip-row b.ended { color: #7a8199; }
.tl-tip-row b.created { color: #b8a03c; }
</style>
