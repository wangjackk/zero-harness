<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BANDS, BAND_MAP, type BandId, type Impl } from '../data/bands'
import { EDGES, GHOSTS, KIND, type EdgeKind } from '../data/edges'
import RoutineDrawer, { type DrawerData, type Relation } from '../components/RoutineDrawer.vue'

/* ------------------------------ 画布常量 ------------------------------ */
interface GNode {
  id: string
  label: string
  band: BandId | 'ghost'
  color: string
  x: number; y: number; vx: number; vy: number
  r: number
  pill?: { w: number; h: number }
  ghost?: boolean
  pin?: boolean
  d?: string; w?: string; f?: string; cls?: string
  cnt?: number
  note?: string
  sub?: string
  impl?: Impl
  /* 一个注册名，干的却是 hub 的活：画成双环，否则它混在普通点里认不出来 */
  hubRole?: boolean
}

interface GEdge { id: string; f: string; t: string; k: EdgeKind; label?: string; both?: boolean; soft?: boolean }

/* 排字时用的矩形（画布坐标，左上右下） */
type Box = { x1: number; y1: number; x2: number; y2: number }

/* ------------------------------ 建图 ------------------------------ */
const nodes = ref<GNode[]>([])
const edges = ref<GEdge[]>([])

for (const b of BANDS) {
  for (const c of b.chips) {
    nodes.value.push({
      id: c.n, label: c.n, band: b.id, color: b.accent,
      x: 0, y: 0, vx: 0, vy: 0, r: 7.5 + Math.min(3, Math.log2((c.x || 1) + 1)),
      d: c.d, w: c.w, f: c.f, cnt: c.x, impl: c.impl,
      hubRole: c.n === 'agent_hub'
    })
  }
}
for (const [id, g] of Object.entries(GHOSTS)) {
  nodes.value.push({
    id, label: g.title, band: 'ghost', color: '#8A94A6',
    x: 0, y: 0, vx: 0, vy: 0, r: 13,
    pill: { w: 'w' in g ? (g.w as number) : 132, h: 36 }, ghost: true, pin: true,
    note: g.note, sub: g.sub
  })
}
const byId = computed(() => Object.fromEntries(nodes.value.map(n => [n.id, n])) as Record<string, GNode>)

/* 一条边属于哪个地盘。kernel 算 py —— 它跟 py 同进程，不是第四片地盘。 */
function zoneOf(n: GNode) {
  if (n.id === '@kernel') return 'py'
  return n.ghost ? n.id : hubOf(n)
}

for (const e of EDGES) {
  const a = byId.value[e.f]
  const b = byId.value[e.t]
  if (!a || !b) continue
  /* 旁支有两种：hub 注册到 hub（要绕出自己那片地才看得见），和跨区的 call
     （pi/agent 调 py 的 ipython —— 它必须直穿 py 区，躲不开）。
     两种都画成细虚线，压在主干下面；不然读者先看见“有根线横穿了整片”。 */
  const soft = e.k === 'hub' ? e.t !== '@kernel' : zoneOf(a) !== zoneOf(b)
  edges.value.push({ id: `${e.f}->${e.t}`, f: e.f, t: e.t, k: e.k, label: e.label, soft })
}

const neighbours = computed(() => {
  const m: Record<string, Set<string>> = {}
  for (const n of nodes.value) m[n.id] = new Set()
  for (const e of edges.value) {
    m[e.f]?.add(e.t)
    m[e.t]?.add(e.f)
  }
  return m
})

/* ------------------------------ 布局 ------------------------------

   这张图只讲一件事：**三个 hub 各自带着自己那批名字，拨进同一个 kernel。**

   所以 hub 不是节点 —— 画成节点，它跟名字就成了并列的两样东西，
   读者看到的自然是「一堆 routine，旁边站着几个 hub」。它是一块**区域**：
   包含自己带进来的那些点，标签贴在区域底边的正中（就是 fieldset 的 legend）。
   框的边缘才是它的接口 —— 名字经这个区注册进表，线就从区的边上出去。

   py 那个框最大（四十来处），因为它跟 kernel 同进程、它带的最多；
   ts / rs 各三处。框的大小是事实，不是排版：一个 hub 只有把手里的东西
   真的放上来，画面上才有那么大一块地方。

   区内按证据带分簇：外框回答「谁带进来的」，簇标题回答「这是哪一类」。
   同一类能力可以被两个 hub 分担 —— ts 带的是 agent 能力里属于 pi 的那三块，
   rs 带的是身体与表达里属于声音的那三块，所以 ② 和 ⑤ 各出现两次。
   那不是重复，那是这张表的真实样子。 */
const W = 1480
const H = 960

interface Zone { id: string; x: number; y: number; w: number; h: number }

/* py 在上、ts 与 rs 在下两角，表落在三者正中。

   这是唯一能让「三个入口平级」和「表在中心」同时成立的摆法。表若偏在一边，
   三条注册线里就有两条要横穿整张图去追它 —— 那两条线会抢掉全部注意力，
   读者先看见的是“线绕得真远”，而不是“名字汇进同一张表”。

   py 那片比两侧宽三倍多、也高出一截，因为重心必须落在它身上：一片宽、
   两片窄，中间那片的位置就是视觉中心。反过来把 py 摆在边上，表就得跟着歪。
   高度差也是事实 —— py 里塞了七个带、两行，ts / rs 各只有一个三块的小簇，
   给它们一样高只会让中间那片看起来被压扁。

   底下两片贴着表面左右分开，是为了让三条线都变成直线：一条竖直落下来、
   两条水平插进去。只要有一片挪开，那条线就得拐弯，而弯一长，读起来
   就成了“容器边框”。 */
const ZONE_GEO: Record<string, Zone> = {
  '@hub-py': { id: '@hub-py', x: 290, y: 96, w: 860, h: 575 },
  '@hub-ts': { id: '@hub-ts', x: 140, y: 700, w: 240, h: 258 },
  '@hub-rs': { id: '@hub-rs', x: 1060, y: 700, w: 240, h: 258 }
}

/* 表坐在三片正中的空地上（横向 720 是整张图的中心），
   左右两片的簇心与它同高 —— 于是两条注册线是水平的。 */
const KERNEL_POS = { x: 720, y: 820 }
const zones = ref<Zone[]>([])
const zoneMap = computed(() => Object.fromEntries(zones.value.map(z => [z.id, z])))

/* 一个 hub 一种底色。跟七个证据带的色系刻意错开 —— 带回答“这是哪一类能力”，
   云回答“这批名字是谁带进来的”，两件事用同一套颜色就分不出来了。 */
function zoneAccent(id: string) {
  return GHOSTS[id]?.accent ?? '#8A94A6'
}

/* 面积补偿：同样的浓度，八倍大的那片会从“地盘”变成“背景色”。
   py 压到 0.18，另外两片给到 0.27 —— 读起来是三块地，不是一个底。 */
function zoneAlpha(id: string) {
  return id === '@hub-py' ? 0.18 : 0.27
}

/* 三条注册线在行簇之间穿行的高度。py 区两行簇的包络在这儿刚好留空 —— 
   上面那行最底 366，下面那行最顶 448，走廊取正中间。 */
const CORRIDOR_Y = 402

/* py 区内七个带的锚点，4 列 × 2 行。手排，不用公式：七个带大小差一倍
   （agent 10 块、prompt 3 块），等分必然有两个叠在一起。改一次就固定。
   列距 205 / 250 是在 860 宽里能容下最大一簇（半径 96）的极限，再挤就会
   让 agent 那团压到 lang 上。第一行压到 270 是为了给左上角那个 pill 腾地方 ——
   它要占 78~114 这一段。 */
const PY_SLOT: Record<BandId, { x: number; y: number }> = {
  meta:   { x: 402, y: 270 },
  agent:  { x: 608, y: 270 },
  lang:   { x: 812, y: 270 },
  tools:  { x: 1018, y: 270 },
  body:   { x: 460, y: 530 },
  sense:  { x: 710, y: 530 },
  prompt: { x: 960, y: 530 }
}

/* 这一块是谁带进来的。缺省 py —— 也就是 kernel 所在的那个进程。 */
function hubOf(n: GNode) {
  return n.impl ?? 'py'
}

/* 向日葵螺旋：确定性排布，缩放到任何尺寸都不跳。返回这一团的半径。
   spread 是“这三个点该撑多开”：ts / rs 各只有三块，按数量算出来的半径
   放在 240 宽的地盘里会缩成一小撮；同一条规则加上系数，才和地盘相称。 */
function pack(ns: GNode[], cx: number, cy: number, seed: number, spread = 1) {
  const packR = (30 + 21 * Math.sqrt(ns.length)) * spread
  ns.forEach((n, j) => {
    const rr = packR * Math.sqrt((j + 0.5) / ns.length)
    const th = j * 2.39996 + seed * 0.7
    n.x = cx + Math.cos(th) * rr
    n.y = cy + Math.sin(th) * rr
    n.vx = n.vy = 0
  })
  return packR
}

/* ts / rs 区里的那一簇叫什么：用带的号和名，后面缀上实现 —— 
   光写「Agent」会让读者以为 ts 也带了一整套 agent。 */
const hubTitles = ref<{ id: string; text: string; x: number; y: number; accent: string }[]>([])

function layoutStructure() {
  zones.value = []
  hubTitles.value = []

  BANDS.forEach((b, i) => {
    const ns = nodes.value.filter(n => n.band === b.id && hubOf(n) === 'py')
    if (!ns.length) return
    const s = PY_SLOT[b.id]
    pack(ns, s.x, s.y, i)
  })

  const OTHER: [string, 'ts' | 'rs'][] = [['@hub-ts', 'ts'], ['@hub-rs', 'rs']]
  for (const [hid, impl] of OTHER) {
    const ns = nodes.value.filter(n => !n.ghost && hubOf(n) === impl)
    const z = ZONE_GEO[hid]
    /* 簇心与表同高 —— 三条注册线才是水平的；也是这张图唯一“天然对齐”的地方 */
    pack(ns, z.x + z.w / 2, KERNEL_POS.y - 2, 1, 1.25)
    zones.value.push(z)
    const b = BAND_MAP[(ns[0]?.band ?? 'agent') as BandId]
    /* 标题压在这一片的顶边内侧当图注 —— 底边让给 pill 了（它就骑在底边上） */
    hubTitles.value.push({
      id: hid, text: `${b.no} ${b.title.split(' · ')[0]} · ${impl}`,
      x: z.x + z.w / 2, y: z.y + 20, accent: b.accent
    })
  }
  zones.value.unshift(ZONE_GEO['@hub-py'])

  /* pill 就是区框的图例：水平正中、底边压在框的下边框上（不是骑过去，
     也不是留一条缝）。三片区的宽度差四倍，只有“贴同一条边”才让三个标签
     看起来是同一个位置；差半个像素都读成没对齐。 */
  for (const z of zones.value) {
    const n = byId.value[z.id]
    Object.assign(n, { x: z.x + z.w / 2, y: z.y + z.h - (n.pill?.h ?? 36) / 2 })
  }

  /* 表在三片底边之下、整张图的正中 —— 左中右三条注册线各走最近的一面进来 */
  Object.assign(byId.value['@kernel'], KERNEL_POS)
}

function applyLayout() {
  layoutStructure()
}

/* ------------------------------ 视图变换 ------------------------------ */
const stage = ref<HTMLDivElement | null>(null)
const k = ref(1)
const tx = ref(0)
const ty = ref(0)

/* 内容包围盒：节点 + 标签 + 云团半径。视野贴着内容走，
   否则 1400×1000 的虚拟画布会让图只占屏幕中间一小块。 */
const box = computed(() => {
  let x1 = Infinity
  let y1 = Infinity
  let x2 = -Infinity
  let y2 = -Infinity
  for (const n of nodes.value) {
    const hw = n.pill ? n.pill.w / 2 : Math.max(n.r + 8, Math.min(84, (n.label.length * 6.2) / 2))
    const hh = n.pill ? n.pill.h / 2 : n.r + 26
    x1 = Math.min(x1, n.x - hw - 26)
    x2 = Math.max(x2, n.x + hw + 26)
    y1 = Math.min(y1, n.y - hh - 26)
    y2 = Math.max(y2, n.y + hh + 26)
  }
  /* 表底下还有两行数字，也是要看见的东西 */
  const kn = byId.value['@kernel']
  if (kn) y2 = Math.max(y2, kn.y + 92)
  return { x1, y1, x2, y2, w: x2 - x1, h: y2 - y1 }
})

function fit(padding = 0.9) {
  const el = stage.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const b = box.value
  if (!isFinite(b.w) || b.w <= 0) return
  const kk = Math.min(r.width / b.w, r.height / b.h) * padding
  k.value = Math.max(0.45, Math.min(1.7, kk))
  tx.value = (r.width - b.w * k.value) / 2 - b.x1 * k.value
  ty.value = (r.height - b.h * k.value) / 2 - b.y1 * k.value

  /* 顶部工具条 / 底部 HUD 的安全区 */
  const top = b.y1 * k.value + ty.value
  if (top < 74) ty.value += 74 - top
  const bottom = (b.y1 + b.h) * k.value + ty.value
  if (bottom > r.height - 30) ty.value -= bottom - (r.height - 30)
}

function zoomBy(f: number, cx?: number, cy?: number) {
  const el = stage.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const px = cx ?? r.width / 2
  const py = cy ?? r.height / 2
  const k2 = Math.max(0.42, Math.min(2.8, k.value * f))
  tx.value = px - ((px - tx.value) * k2) / k.value
  ty.value = py - ((py - ty.value) * k2) / k.value
  k.value = k2
}

function onWheel(e: WheelEvent) {
  e.preventDefault()
  const r = stage.value?.getBoundingClientRect()
  if (!r) return
  zoomBy(Math.exp(-e.deltaY * 0.0014), e.clientX - r.left, e.clientY - r.top)
}

let drag: { x: number; y: number; tx: number; ty: number; moved: boolean } | null = null
let dragged = false

function onDown(e: PointerEvent) {
  if (e.button !== 0) return
  dragged = false
  drag = { x: e.clientX, y: e.clientY, tx: tx.value, ty: ty.value, moved: false }
}
function onMove(e: PointerEvent) {
  if (!drag) return
  const dx = e.clientX - drag.x
  const dy = e.clientY - drag.y
  if (Math.abs(dx) + Math.abs(dy) > 3) {
    drag.moved = true
    dragged = true
  }
  tx.value = drag.tx + dx
  ty.value = drag.ty + dy
}
function onUp() {
  drag = null
}

/* ------------------------------ 交互态 ------------------------------ */
const hover = ref<string | null>(null)
const selected = ref<GNode | null>(null)
const drawer = ref(false)
const q = ref('')
const offBands = ref<Set<BandId>>(new Set())
const offKinds = ref<Set<EdgeKind>>(new Set())
const focusBand = ref<BandId | null>(null)
const router = useRouter()
const route = useRoute()

const hot = computed(() => hover.value || selected.value?.id || null)
const hotSet = computed(() => {
  const id = hot.value
  if (!id) return new Set<string>()
  const s = new Set<string>([id])
  neighbours.value[id]?.forEach(x => s.add(x))
  return s
})

const matched = computed(() => {
  const s = q.value.trim().toLowerCase()
  if (!s) return null
  const set = new Set<string>()
  for (const n of nodes.value) {
    if (n.id === s || n.label.toLowerCase().includes(s) || (n.d || '').toLowerCase().includes(s)) set.add(n.id)
  }
  return set
})

const kindCount = computed(() => {
  const m: Record<string, number> = {}
  for (const e of edges.value) m[e.k] = (m[e.k] || 0) + 1
  return m
})

function nodeDim(n: GNode) {
  if (offBands.value.has(n.band as BandId)) return true
  if (focusBand.value && n.band !== focusBand.value) return true
  if (matched.value && !matched.value.has(n.id)) return true
  if (hot.value && !hotSet.value.has(n.id)) return true
  return false
}

function edgeDim(e: GEdge) {
  if (offKinds.value.has(e.k)) return true
  if (offBands.value.has(byId.value[e.f]?.band as BandId) || offBands.value.has(byId.value[e.t]?.band as BandId)) return true
  if (focusBand.value && byId.value[e.f]?.band !== focusBand.value && byId.value[e.t]?.band !== focusBand.value) return true
  if (hot.value && e.f !== hot.value && e.t !== hot.value) return true
  return false
}

const isHotEdge = (e: GEdge) => !!hot.value && (e.f === hot.value || e.t === hot.value)

/* 每条带的“云团”：质心 + 半径。只在 py 区里用 —— ts / rs 区里那一簇自己有
   区框兜着，再叠一层云团会让「谁的边界」变得说不清。
   +26 是贴着簇外沿：早先给到 +58，相邻两团会互相渗色，读起来像糊在一起。 */
const hulls = computed(() =>
  BANDS.map(b => {
    const ns = nodes.value.filter(n => n.band === b.id && hubOf(n) === 'py')
    if (!ns.length) return null
    const cx = ns.reduce((s, n) => s + n.x, 0) / ns.length
    const cy = ns.reduce((s, n) => s + n.y, 0) / ns.length
    const r = Math.max(...ns.map(n => Math.hypot(n.x - cx, n.y - cy))) + 26
    return { id: b.id, no: b.no, title: b.title, accent: b.accent, cx, cy, r }
  }).filter((h): h is { id: BandId; no: string; title: string; accent: string; cx: number; cy: number; r: number } => !!h)
)

/* 云团上方那一行名字。没有它，读者得回侧栏数字号才知道哪团是什么 ——
   图上七个带是七种证据，名字必须长在图上。 */
const bandTitles = computed(() =>
  BANDS.map(b => {
    const ns = nodes.value.filter(n => n.band === b.id && hubOf(n) === 'py')
    if (!ns.length) return null
    return {
      id: b.id, no: b.no, accent: b.accent,
      /* 区里已经有 hub 的框在说“谁带进来的”，这里只留带自己的名字：
         一条带上写「① 元能力 · 系统自身」，七条并排在框里会读成七个句子。 */
      text: `${b.no} ${b.title.split(' · ')[0]}`,
      x: ns.reduce((s, n) => s + n.x, 0) / ns.length,
      /* -16 不是 -30：行间那条走廊要从簇顶上方穿过去，标题抬高就会跟它打架 */
      y: Math.min(...ns.map(n => n.y)) - 16
    }
  }).filter((h): h is { id: BandId; no: string; text: string; accent: string; x: number; y: number } => !!h)
)

/* 标签避让：两遍。
   第一遍照常渲染，量出**每串字真实的长度**（`getComputedTextLength`）—— 按字数估
   一定会偏：等宽字体在不同机器上落到的实际字体不一样，偏 10% 就足以让两个字压上。
   第二遍拿真值按 y 顺序贪心放置：六个候选位置都撞就收起来（悬停时仍显示）。

   **别的点是硬障碍**：只避标签不避点，字就会压到旁边那颗亮球上，
   读起来像被裁掉半截（`routine/check_na…`）。 */
const labelPlan = ref<Record<string, { dy: number; show: boolean }>>({})

function placeLabels() {
  const el = stage.value
  if (!el) return
  const list = nodes.value.filter(n => !n.ghost).slice().sort((a, b) => a.y - b.y || a.x - b.x)
  const dots = list.map(n => ({ id: n.id, x1: n.x - n.r - 5, y1: n.y - n.r - 5, x2: n.x + n.r + 5, y2: n.y + n.r + 5 }))
  const taken: Box[] = []
  /* 2px 余量：贴着放也算撞，否则两个字会连成一句 */
  const hit = (a: Box, b: Box) => !(a.x2 + 2 < b.x1 || a.x1 - 2 > b.x2 || a.y2 + 2 < b.y1 || a.y1 - 2 > b.y2)
  const plan: Record<string, { dy: number; show: boolean }> = {}

  for (const n of list) {
    const t = el.querySelector<SVGTextElement>(`g.nd[data-id="${n.id}"] text.lbl`)
    /* getComputedTextLength 是画布用户单位，跟 n.x / n.y 同一套坐标 */
    const w = (t ? t.getComputedTextLength() : n.label.length * 5.7) + 6
    let done = false
    /* 候选位置按“离自己那颗点多近”排序：下面是常规位置，被占了才往上/远处挪 */
    for (const dy of [15, -9, 28, -22, 40, -34]) {
      const box: Box = { x1: n.x - w / 2, y1: n.y + dy - 11, x2: n.x + w / 2, y2: n.y + dy + 3 }
      if (taken.some(b => hit(b, box))) continue
      if (dots.some(d => d.id !== n.id && hit(d, box))) continue
      taken.push(box)
      plan[n.id] = { dy, show: true }
      done = true
      break
    }
    if (!done) plan[n.id] = { dy: 15, show: false }
  }
  labelPlan.value = plan
}

/* 图上有多少处（不是多少个名字 —— 一个折叠族只占一处）。
   它现在只在筛选反馈里出现，不再是页面上的一行铭牌。 */
const chipCount = computed(() => nodes.value.filter(n => !n.ghost).length)
const hubNodes = computed(() => nodes.value.filter(n => n.ghost && n.id !== '@kernel'))

const usedKinds = computed(() => Object.entries(KIND).filter(([kk]) => (kindCount.value[kk] || 0) > 0))

/* ------------------------------ 边几何 ------------------------------ */
function exitPoint(a: GNode, b: GNode) {
  const dx = b.x - a.x
  const dy = b.y - a.y
  /* 首帧两边都还叠在 (0,0)：直接回原点。否则 pill 分支会得到 Infinity，
     再乘 0 就是 NaN，SVG 会当场抛 "Expected number, MNaN,NaN"。 */
  if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return { x: a.x, y: a.y }

  /* hub 那几条不走这里：它的出口是整片区的底边，在 edgePath 里按区的边算 ——
     从 pill 出发会斜穿自己那个区。 */
  if (a.pill) {
    const sx = Math.abs(dx) > 0.001 ? a.pill.w / 2 / Math.abs(dx) : Infinity
    const sy = Math.abs(dy) > 0.001 ? a.pill.h / 2 / Math.abs(dy) : Infinity
    const t = Math.min(sx, sy)
    return { x: a.x + dx * t, y: a.y + dy * t }
  }
  const d = Math.hypot(dx, dy) || 1
  return { x: a.x + (dx / d) * (a.r + 2), y: a.y + (dy / d) * (a.r + 2) }
}

function edgePath(e: GEdge) {
  const a = byId.value[e.f]
  const b = byId.value[e.t]
  if (!a || !b) return ''

  const za = zoneMap.value[a.id]

  /* 区 → 表。三条线都从自己那片区的**内侧边**出发，全是直线 ——
     表坐在三片正中，左右两片的簇心与它同高，上面那片的底边就在它头顶，
     所以每条线都只需要走“自己到表”这段最近的路，不穿任何区、不拐任何弯。

     早先表偏在右下，为了不让线斜穿 py 区，绕成了三段直角：两条各带
     五百多像素的水平段，在底部并排横着 —— 那是管道工程图，读者先看见
     “线绕得真远”。弯一收成直线，“汇进同一张表”才自己浮出来。 */
  if (za && b.id === '@kernel') {
    const half = (b.pill?.w ?? 132) / 2
    if (a.id === '@hub-py') {
      /* 从 pill 的下沿出发，也就是从框的底边出发：pill 正压着底边中点，
         从底边画出去会被那块牌子截掉上半截，看起来像线从标签里长出来的。 */
      const x0 = za.x + za.w / 2
      return `M${x0},${za.y + za.h} L${x0},${b.y - 18}`
    }
    const left = a.id === '@hub-ts'
    const x0 = left ? za.x + za.w : za.x
    const x1 = b.x + (left ? -half : half)
    return `M${x0},${b.y} L${x1},${b.y}`
  }

  /* 区 → 别的区里的某个名字（现在只有 ts 的 pi manager 注册到 py 的 agent_hub）。
     从区顶边出去，贴着两片区之间的空隙上行，拐进两行簇之间的走廊，
     再从下面插进那个点 —— 它本来就是“钻进别的地盘”，只能走通道。
     出发点避开左上角那个 pill（它占掉区宽的前半截），否则线像从标签里长出来的。 */
  if (za) {
    const x0 = za.x + za.w - 70
    return `M${x0},${za.y} L${x0},${CORRIDOR_Y} L${b.x},${CORRIDOR_Y} L${b.x},${b.y + b.r + 2}`
  }

  const p1 = exitPoint(a, b)
  const p2 = exitPoint(b, a)
  // 兜底：算不出有限坐标就干脆不画，别把 NaN 交给渲染器
  if (![p1.x, p1.y, p2.x, p2.y].every(Number.isFinite)) return ''

  /* 区内这几十条边如果全是直的，叠在一起就是一片斜着交叉的网 ——
     读者一条也数不出来，只看见“这里很乱”。给它们一点弧度：长短不同的边
     弯得不一样，交叉处的夹角变大，眼睛才拆得开这是几束流。
     弯量跟长度成正比、上限 40，短边基本看不出来 —— 同一个簇里的两条
     本来就该是一根短线，不该为了好看去绕。 */
  const dx = p2.x - p1.x
  const dy = p2.y - p1.y
  const len = Math.hypot(dx, dy)
  if (len < 70) {
    return `M${p1.x.toFixed(1)},${p1.y.toFixed(1)} L${p2.x.toFixed(1)},${p2.y.toFixed(1)}`
  }
  const bend = Math.min(40, len * 0.14)
  const cx = (p1.x + p2.x) / 2 + (-dy / len) * bend
  const cy = (p1.y + p2.y) / 2 + (dx / len) * bend
  return `M${p1.x.toFixed(1)},${p1.y.toFixed(1)} Q${cx.toFixed(1)},${cy.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`
}

function edgeLabelAt(e: GEdge) {
  const a = byId.value[e.f]
  const b = byId.value[e.t]
  if (!a || !b) return { x: 0, y: 0 }
  /* hub 那三条的起点是区的边、不是 pill：按 pill 求中点会飘到区外面去 */
  const za = zoneMap.value[a.id]
  if (za && b.id === '@kernel') {
    if (a.id === '@hub-py') return { x: b.x + 12, y: (za.y + za.h + b.y) / 2 }
    const left = a.id === '@hub-ts'
    const x0 = left ? za.x + za.w : za.x
    return { x: (x0 + b.x) / 2, y: b.y - 9 }
  }
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 4 }
}

/* ------------------------------ 选中 / 抽屉 ------------------------------ */
function relationsOf(id: string): Relation[] {
  /* ghost 的 id 是 @hub-py 这种写法，直接显示会跟页面上那个 pill 对不上 ——
     显示用 label（hub · py），跳转仍然用 id。 */
  const nm = (x: string) => byId.value[x]?.label ?? x
  const out: Relation[] = []
  for (const e of edges.value) {
    if (e.f === id) out.push({ dir: 'out', name: e.t, disp: nm(e.t), kind: e.k, label: e.label })
    if (e.t === id && !(e.both)) out.push({ dir: 'in', name: e.f, disp: nm(e.f), kind: e.k, label: e.label })
  }
  return out
}

const drawerData = computed<DrawerData | null>(() => {
  const n = selected.value
  if (!n) return null
  if (n.ghost) {
    return {
      name: n.label,
      band: n.id === '@kernel' ? '非单元 · 注册表' : '非单元 · 注册入口',
      accent: '#8A94A6',
      ghost: true,
      desc: n.note,
      facts: [{ k: '类型', v: n.sub || '' }],
      relations: relationsOf(n.id)
    }
  }
  const b = BAND_MAP[n.band as BandId]
  const implName = n.impl && n.impl !== 'py'
    ? ({ ts: 'TypeScript · pi', rs: 'Rust' })[n.impl]
    : ''
  return {
    name: n.id,
    band: b ? `${b.no} ${b.title}` : '单元',
    accent: n.color,
    desc: n.d,
    why: n.w,
    file: n.f,
    facts: [
      { k: '注册项', v: n.cnt ? `${n.cnt} 个同前缀注册名` : '单个注册名' },
      { k: '实现', v: implName },
      { k: '类型', v: b ? b.sub : '' }
    ].filter(f => f.v),
    relations: relationsOf(n.id)
  }
})

function pick(n: GNode) {
  if (dragged) return
  selected.value = n
  drawer.value = true
}

function pickByName(name: string) {
  const n = byId.value[name]
  if (!n) return
  selected.value = n
  drawer.value = true
}

function closeDrawer() {
  drawer.value = false
  selected.value = null
}

/* ------------------------------ 侧栏 ------------------------------ */
const sideOpen = ref(false)

function toggleBand(id: BandId) {
  const s = new Set(offBands.value)
  s.has(id) ? s.delete(id) : s.add(id)
  offBands.value = s
}
function toggleKind(kd: EdgeKind) {
  const s = new Set(offKinds.value)
  s.has(kd) ? s.delete(kd) : s.add(kd)
  offKinds.value = s
}
function resetFilters() {
  offBands.value = new Set()
  offKinds.value = new Set()
  focusBand.value = null
  q.value = ''
}

const visibleCount = computed(
  () => nodes.value.filter(n => !n.ghost && !offBands.value.has(n.band as BandId)).length
)

/* 右下角那行只在“图被筛过”的时候出现。它是反馈，不是铭牌 ——
   平时挂一串数字在那儿，读者第一眼要解的不是图，是那串数字。 */
const filtered = computed(() => offBands.value.size > 0 || offKinds.value.size > 0 || !!focusBand.value)

/* ------------------------------ 生命周期 ------------------------------ */
function onKey(e: KeyboardEvent) {
  if (e.target instanceof HTMLInputElement) {
    if (e.key === 'Escape') (e.target as HTMLInputElement).blur()
    return
  }
  if (e.key === 'Escape') closeDrawer()
  if (e.key === '0') fit()
}

let ro: ResizeObserver | null = null

onMounted(async () => {
  applyLayout()
  /* 量标签必须等这一版 DOM 出来：位置是 applyLayout 写进去的，字是渲染才有的 */
  await nextTick()
  placeLabels()
  requestAnimationFrame(() => fit())
  ro = new ResizeObserver(() => fit())
  if (stage.value) ro.observe(stage.value)
  window.addEventListener('keydown', onKey)
  const b = String(route.query.band || '') as BandId
  if (b && BAND_MAP[b]) focusBand.value = b
})

onBeforeUnmount(() => {
  ro?.disconnect()
  window.removeEventListener('keydown', onKey)
})

watch(
  () => route.query.band,
  v => {
    const b = String(v || '') as BandId
    focusBand.value = b && BAND_MAP[b] ? b : null
  }
)
</script>

<template>
  <div class="tp">
    <!-- 整页是一张画布，没有可见大标题；文档大纲仍然要有 -->
    <h1 class="sr-only">拓扑 · 三个 hub 向同一注册表注册单元，{{ edges.length }} 条关系</h1>
    <!-- ============================ 侧栏 ============================ -->
    <aside class="side" :class="{ open: sideOpen }">
      <div class="side-head">
        <span class="eyebrow">拓扑</span>
        <button class="side-x" @click="sideOpen = false" aria-label="收起">✕</button>
      </div>

      <div class="side-sec">
        <span class="tag">入口 · 注册来源</span>
        <div class="filters">
          <button
            v-for="h in hubNodes" :key="h.id"
            class="flt hubrow"
            :class="{ sel: selected?.id === h.id }"
            @click="pick(h)"
          >
            <i class="flt-d hub" />
            <span class="flt-t">{{ h.label }}</span>
          </button>
        </div>
        <p class="side-note">
          图中仅显示向注册表注册单元的 hub。go 提供 SDK，但未注册单元，
          因此不在图中显示。
        </p>
      </div>

      <div class="side-sec">
        <span class="tag">证据类别 · 点击切换显示</span>
        <div class="filters">
          <button
            v-for="b in BANDS"
            :key="b.id"
            class="flt"
            :class="{ off: offBands.has(b.id), focus: focusBand === b.id }"
            :style="{ '--a': b.accent }"
            @click="toggleBand(b.id)"
          >
            <i class="flt-d" />
            <span class="flt-t">{{ b.no }} {{ b.title }}</span>
          </button>
        </div>
        <p class="side-note">
          每个能力节点对应一个注册名或一组同前缀注册名，如 <code>routine/list</code>、<code>ipython</code>。
          <span class="mono">×N</span> 表示该节点折叠了 N 个注册名，如 <code>chan/*</code> 包含 5 个。
          空间不足的标签默认隐藏，悬停节点时显示。
        </p>
      </div>

      <div class="side-sec">
        <span class="tag">关系 · 点击切换显示</span>
        <div class="kinds">
          <button
            v-for="[kk, v] in usedKinds"
            :key="kk"
            class="knd"
            :class="{ off: offKinds.has(kk as EdgeKind) }"
            @click="toggleKind(kk as EdgeKind)"
          >
            <span class="knd-l" :style="{ background: v.color }" :class="{ dash: !!v.dash }" />
            <span class="knd-t">{{ v.label }}</span>
            <span class="knd-n mono">{{ kindCount[kk] || 0 }}</span>
          </button>
        </div>
      </div>

      <div class="side-sec hints">
        <span class="tag">操作</span>
        <ul>
          <li><kbd>滚轮</kbd> 以指针为中心缩放</li>
          <li><kbd>拖动</kbd> 平移空白区域</li>
          <li><kbd>点击</kbd> 打开详情</li>
          <li><kbd>0</kbd> 适应画布</li>
        </ul>
      </div>

      <div class="side-foot">
        <button class="btn btn-quiet" @click="resetFilters">重置筛选</button>
        <button class="btn" @click="fit()">适应画布</button>
      </div>
    </aside>

    <!-- ============================ 画布 ============================ -->
    <div class="stage" ref="stage" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp">
      <svg class="g" :class="{ 'has-hot': !!hot }" @wheel="onWheel">
        <defs>
          <marker
            v-for="(v, kk) in KIND"
            :id="`arw-${kk}`"
            :key="kk"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0,1 L7,4 L0,7 z" :fill="v.color" />
          </marker>
          <radialGradient v-for="b in BANDS" :id="`hull-${b.id}`" :key="'g' + b.id">
            <stop offset="0%" :stop-color="b.accent" stop-opacity=".17" />
            <stop offset="62%" :stop-color="b.accent" stop-opacity=".06" />
            <stop offset="100%" :stop-color="b.accent" stop-opacity="0" />
          </radialGradient>
          <radialGradient id="core">
            <stop offset="0%" stop-color="#A98BFA" stop-opacity=".18" />
            <stop offset="100%" stop-color="#A98BFA" stop-opacity="0" />
          </radialGradient>
          <!-- 区的边不是线，是一团化开的墨：模糊半径 18，等于把整块地盘的边缘揉软。
               再大就会越过两片地盘之间那道三十来像素的缝，三块糊成一片。
               缩放时它跟着一起放大，所以看起来永远是“气”而不是“描边”。 -->
          <filter id="hub-blur" x="-25%" y="-25%" width="150%" height="150%">
            <feGaussianBlur stdDeviation="18" />
          </filter>
        </defs>

        <g :transform="`translate(${tx},${ty}) scale(${k})`">
          <!-- 三个 hub 的区。不是“一个框”，是**这个 hub 的地盘**：它带进来的名字全站在里面。
               画成一片朦胧的色团（模糊过的圆角块）而不是直角框 —— 框说的是“这里被圈住了”，
               云说的是“这里归它管”，后者才是真的：名字是可以走的，区不是一堵墙。
               三种颜色跟七个带的色系刻意错开，免得把“哪个 hub”和“哪类能力”看成一回事。 -->
          <g class="zones">
            <rect
              v-for="z in zones" :key="'z' + z.id"
              :x="z.x" :y="z.y" :width="z.w" :height="z.h" :rx="Math.min(92, z.w * 0.3)"
              :data-z="z.id"
              class="zone-glow" :style="{ '--a': zoneAccent(z.id), '--o': zoneAlpha(z.id) }"
              filter="url(#hub-blur)"
            />
            <rect
              v-for="z in zones" :key="'zl' + z.id"
              :x="z.x" :y="z.y" :width="z.w" :height="z.h" :rx="Math.min(92, z.w * 0.3)"
              class="zone-line" :style="{ '--a': zoneAccent(z.id) }"
            />
          </g>

          <!-- kernel 底下那两行统计撤了：读者要解的是图，不是“54 和 138 哪个是哪个”。
               真要知道规模，页脚有数、侧栏逐条都数得出来。 -->

          <g class="hulls">
            <template v-for="h in hulls" :key="'h' + h.id">
              <circle :cx="h.cx" :cy="h.cy" :r="h.r" :fill="`url(#hull-${h.id})`" />
            </template>
          </g>

          <g class="btitles">
            <text
              v-for="t in bandTitles" :key="'bt' + t.id"
              :x="t.x" :y="t.y" text-anchor="middle" :fill="t.accent"
            >{{ t.text }}</text>
            <text
              v-for="t in hubTitles" :key="'ht' + t.id"
              :x="t.x" :y="t.y" text-anchor="middle" :fill="t.accent"
            >{{ t.text }}</text>
          </g>

          <!-- 边 -->
          <g class="edges">
            <path
              v-for="e in edges"
              :key="e.id"
              :d="edgePath(e)"
              class="eg"
              :class="['k-' + e.k, { dim: edgeDim(e), hot: isHotEdge(e), soft: e.soft }]"
              :style="{ '--c': KIND[e.k].color }"
              :stroke-dasharray="e.soft ? '4 5' : (KIND[e.k].dash || undefined)"
              :marker-end="`url(#arw-${e.k})`"
            />
          </g>

          <!-- 边标注：只在热节点上出现 -->
          <g class="elabels">
            <template v-for="e in edges" :key="'el' + e.id">
              <text
                v-if="isHotEdge(e) && e.label"
                :x="edgeLabelAt(e).x"
                :y="edgeLabelAt(e).y"
                text-anchor="middle"
                class="elbl"
              >{{ e.label }}</text>
            </template>
          </g>

          <!-- 节点 -->
          <g
            v-for="n in nodes"
            :key="n.id"
            class="nd"
            :data-id="n.id"
            :class="{ dim: nodeDim(n), sel: selected?.id === n.id, ghost: n.ghost, hot: hot === n.id, near: hotSet.has(n.id) && hot !== n.id }"
            :transform="`translate(${n.x.toFixed(1)},${n.y.toFixed(1)})`"
            @pointerenter="hover = n.id"
            @pointerleave="hover = null"
            @click.stop="pick(n)"
          >
            <template v-if="n.pill">
              <rect
                :x="-n.pill.w / 2"
                :y="-n.pill.h / 2"
                :width="n.pill.w"
                :height="n.pill.h"
                :rx="n.pill.h / 2"
                class="hub-tag"
                :class="{ dashed: n.ghost && !zoneMap[n.id] }"
              />
              <!-- 垂直居中不靠手调 dy：mono 字体的 ascent/descent 一改，写死的数就偏。
                   交给 dominant-baseline，基线自动落在胶囊中线上。 -->
              <text text-anchor="middle" class="hub-tag-t">{{ n.label }}</text>
              <!-- 副标题：区的 pill 骑在底边上，上方才是框内；表不在区里，仍挂下方
                   （上方是三条注册线的落点，字压上去就被线穿了） -->
              <text
                text-anchor="middle" class="hub-tag-s"
                :dy="zoneMap[n.id] ? -n.pill.h / 2 - 5 : n.pill.h / 2 + 15"
              >{{ n.sub }}</text>
            </template>
            <template v-else>
              <circle class="halo" :r="n.r + 10" :fill="n.color" />
              <circle class="dot" :r="n.r" :fill="n.color" />
              <circle class="ring" :r="n.r + 5" />
              <!-- 不是 py 注册的：常显一圈细环。语言是实现细节，但“它从哪个 hub 来”不是 -->
              <circle v-if="n.impl && n.impl !== 'py'" class="far-ring" :r="n.r + 8" :stroke="n.color" />
              <!-- 一个注册名干着 hub 的活（agent_hub）：虚线外环 -->
              <circle v-if="n.hubRole" class="hub-ring" :r="n.r + 13" />
              <text
                text-anchor="middle"
                :y="labelPlan[n.id]?.dy ?? 15"
                class="lbl"
                :style="labelPlan[n.id] ? null : { opacity: 0 }"
                :class="{ off: labelPlan[n.id] && !labelPlan[n.id].show && hot !== n.id }"
              >{{ n.label
                }}<tspan v-if="n.cnt" class="lbl-x" dx="4">×{{ n.cnt }}</tspan></text>
            </template>
          </g>
        </g>
      </svg>

      <!-- 顶部工具条 -->
      <div class="bar" @pointerdown.stop @wheel.stop>
        <button class="bar-burger" @click="sideOpen = !sideOpen" aria-label="筛选">☰</button>
        <div class="searchbox">
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <circle cx="7" cy="7" r="4.6" stroke="currentColor" stroke-width="1.4" />
            <path d="M10.6 10.6 14 14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
          </svg>
          <input v-model="q" type="text" placeholder="搜索节点…" spellcheck="false" />
          <button v-if="q" class="sb-x" @click="q = ''">✕</button>
        </div>
        <div class="zoomer">
          <button @click="zoomBy(0.86)" aria-label="缩小">−</button>
          <span class="mono">{{ Math.round(k * 100) }}%</span>
          <button @click="zoomBy(1.16)" aria-label="放大">+</button>
        </div>
        <button class="bar-txt" @click="fit()">适应画布</button>
      </div>

      <!-- 聚焦提示 -->
      <button v-if="focusBand" class="focus-chip" :style="{ '--a': BAND_MAP[focusBand].accent }" @click="focusBand = null">
        <i />
        筛选 {{ BAND_MAP[focusBand].no }} {{ BAND_MAP[focusBand].title }}
        <span>✕</span>
      </button>

      <!-- 统计：只在筛过之后出现 —— 平时它是一串要解的数字，不是一个数 -->
      <div v-if="filtered" class="hud mono">
        显示 {{ visibleCount }} / {{ chipCount }} 个节点 · {{ Object.keys(kindCount).length }} 类关系
      </div>
    </div>

    <RoutineDrawer :open="drawer" :data="drawerData" @close="closeDrawer" @pick="pickByName" />
  </div>
</template>

<style scoped>
.tp {
  display: grid;
  grid-template-columns: 268px 1fr;
  height: calc(100vh - var(--nav-h));
  overflow: hidden;
}

/* ------------------------------ 侧栏 ------------------------------ */
.side {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding: 22px 18px 16px;
  border-right: 1px solid var(--line);
  overflow: auto;
  background: linear-gradient(180deg, color-mix(in oklab, var(--bg) 60%, transparent), transparent);
}
.side-head { display: flex; align-items: center; justify-content: space-between; }
.side-x { display: none; color: var(--ink-3); }
.side-sec { display: grid; gap: 10px; }
.tag { font-size: 9.5px; letter-spacing: 0.16em; }

.filters { display: grid; gap: 1px; }
.flt {
  display: grid;
  grid-template-columns: 14px 1fr auto;
  align-items: center;
  gap: 8px;
  padding: 7px 6px;
  border-radius: 6px;
  text-align: left;
  transition: background 0.2s var(--ease), opacity 0.2s var(--ease);
}
.flt:hover { background: var(--surface); }
.flt-d { width: 6px; height: 6px; border-radius: 99px; background: var(--a); box-shadow: 0 0 8px color-mix(in oklab, var(--a) 60%, transparent); }
.flt-t { font-size: 12px; color: var(--ink-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.flt-n { font-size: 9.5px; color: var(--ink-4); }
.flt.off { opacity: 0.34; }
.flt.off .flt-d { box-shadow: none; background: var(--ink-4); }
.flt.focus { background: color-mix(in oklab, var(--a) 12%, transparent); }
.flt.focus .flt-t { color: var(--ink); }

/* 入口那一节：hub 不是过滤器，是能点开的对象 */
.hubrow.sel { background: var(--surface-2); }
.hubrow .flt-n { font-size: 8.5px; letter-spacing: 0.02em; opacity: 0.7; }
.flt-d.hub { border-radius: 2px; background: none; border: 1px solid var(--ink-3); box-shadow: none; }

.side-note { font-size: 10.5px; line-height: 1.7; color: var(--ink-4); margin: 0; }

.kinds { display: grid; gap: 1px; }
.knd { display: grid; grid-template-columns: 18px 1fr auto; align-items: center; gap: 8px; padding: 6px; border-radius: 6px; text-align: left; }
.knd:hover { background: var(--surface); }
.knd.off { opacity: 0.3; }
.knd-l { height: 1px; background: var(--c); position: relative; }
.knd-l.dash { background: repeating-linear-gradient(90deg, var(--c) 0 4px, transparent 4px 7px); }
.knd-t { font-size: 11.5px; color: var(--ink-2); }
.knd-n { font-size: 9.5px; color: var(--ink-4); }

.hints ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.hints li { font-size: 11px; color: var(--ink-3); display: flex; gap: 6px; align-items: baseline; }
.hints kbd {
  font-size: 9px; padding: 1px 4px; border-radius: 3px;
  border: 1px solid var(--line-2); color: var(--ink-3); flex-shrink: 0;
}
.side-foot { margin-top: auto; display: flex; gap: 6px; }
.side-foot .btn { flex: 1; height: 32px; font-size: 11.5px; justify-content: center; }

/* ------------------------------ 画布 ------------------------------ */
.stage {
  position: relative;
  overflow: hidden;
  touch-action: none;
  user-select: none;
  background:
    radial-gradient(60% 60% at 50% 40%, color-mix(in oklab, var(--accent) 7%, transparent), transparent 70%);
}
html[data-theme='light'] .stage { background: radial-gradient(60% 60% at 50% 40%, rgba(107, 78, 216, 0.06), transparent 70%); }
.g { display: block; width: 100%; height: 100%; cursor: grab; }
.g:active { cursor: grabbing; }

.edges .eg {
  fill: none;
  stroke: var(--c);
  stroke-width: 1.25;
  opacity: 0.62;
  transition: opacity 0.25s var(--ease), stroke-width 0.25s var(--ease);
}
.edges .eg.dim { opacity: 0.08; }
.edges .eg.hot { opacity: 0.95; stroke-width: 1.8; }
/* 三条注册线是这张图的骨架：名字经它们进表。默认就得比 call 显眼 */
.edges .eg.k-hub { opacity: 0.82; stroke-width: 1.5; }
/* 跨区登记是旁支，不是骨架：压在主干下面，只在追它的时候才浮出来 */
.edges .eg.soft { opacity: 0.34; stroke-width: 1.1; }
.edges .eg.soft.hot { opacity: 0.8; }
/* 自举链整条都在 py 区里：它讲的是“谁把名字装载进来”，不是主干，
   压低一档 —— 但别压成旁支（旁支是“另一个区的人摸进来”，它不是） */
.edges .eg.k-boot { opacity: 0.52; }
.edges .eg.k-boot.hot { opacity: 0.9; }
/* 浅色底下同一根灰线的对比度高得多：不压下来，区里会糊成一团乱麻，
   而这张要读的恰恰是“哪条线连着谁”。骨架那三条照旧。 */
html[data-theme='light'] .edges .eg:not(.k-hub) { opacity: 0.4; }
html[data-theme='light'] .edges .eg.soft { opacity: 0.24; }

.elbl {
  font-family: var(--font-mono);
  font-size: 9.5px;
  fill: var(--ink-2);
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3.5px;
  stroke-linejoin: round;
}

.nd { cursor: pointer; }
.nd .dot { transition: opacity 0.25s var(--ease), r 0.2s var(--ease); }
.nd .halo { opacity: 0.1; transition: opacity 0.25s var(--ease); }
.nd .ring { fill: none; stroke: var(--ink); stroke-opacity: 0; stroke-width: 1; transition: stroke-opacity 0.25s var(--ease); }
.nd .far-ring { fill: none; stroke-width: 1; stroke-opacity: 0.42; }
.nd .hub-ring {
  fill: none; stroke: var(--ink-3); stroke-opacity: 0.5;
  stroke-width: 1; stroke-dasharray: 2 3;
}
.nd .lbl {
  font-family: var(--font-mono);
  font-size: 10.8px;
  letter-spacing: -0.01em;
  fill: var(--ink-2);
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
  stroke-linejoin: round;
  transition: fill 0.25s var(--ease), opacity 0.2s var(--ease);
  pointer-events: none;
}
.nd .lbl.off { opacity: 0; }
/* 折叠族的角标：一块 = N 个注册名。不写出来，就没人能对上「54 块怎么装下 138 个名字」。 */
.nd .lbl-x { font-size: 8.6px; fill: var(--ink-4); letter-spacing: 0; }
/* 指到某个点：它自己那串字浮上来，其余的退到后面。
   被收起来的名字就是在这一刻出来的 —— 不把邻居压暗，它出来也是叠着的。
   （`:not(:hover)` 是为了压过下面 `.nd.near .lbl` 那条） */
.g.has-hot .nd:not(.hot):not(:hover) .lbl { opacity: 0.26; fill: var(--ink-3); }
.g.has-hot .nd.hot .lbl { opacity: 1; fill: var(--ink); }
.nd:hover .lbl, .nd.near .lbl { fill: var(--ink); opacity: 1; }
.nd:hover .halo { opacity: 0.3; }
.nd:hover .dot { r: 10; }
.nd.dim { opacity: 0.16; }
.nd.sel .ring { stroke-opacity: 0.75; }
.nd.sel .halo { opacity: 0.34; }
.nd.sel .lbl { fill: var(--ink); }

/* 类名不能叫 .pill —— 全局样式表里已经有一个给 HTML 用的 .pill，
   它带 `height: 24px`。SVG2 里 height 是可以用 CSS 设的**几何属性**，
   于是这枚 36 高的胶囊被压成 24，而 y 还是 -18：整块往上缩、字往下掉，
   表现出来就是“字偏下”。图上所有 SVG 类名都带 nd-/hub- 前缀避开这套。 */
.nd .hub-tag {
  /* 近乎不透明：pill 底下的边不许透出来，否则标签像被划掉 */
  fill: color-mix(in oklab, var(--bg) 96%, transparent);
  stroke: var(--line-3);
  stroke-width: 1;
}
.nd .hub-tag.dashed { stroke-dasharray: 5 4; }
.nd .hub-tag-t {
  font-family: var(--font-mono);
  font-size: 11px;
  fill: var(--ink-2);
  letter-spacing: 0.01em;
  /* 基线放到字母的中线上 —— 不写这一个词，字就整体往下坐 1~2px，
     胶囊上留白多、下留白少，看起来永远“偏下”。 */
  dominant-baseline: central;
}
.nd .hub-tag-s {
  font-family: var(--font-mono);
  font-size: 8.5px;
  fill: var(--ink-4);
  letter-spacing: 0.1em;
  /* 副标题在 pill 外，直接压在边上 —— 和正文标签用同一套描边兜住 */
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
  stroke-linejoin: round;
}
.nd.ghost:hover .hub-tag { stroke: var(--ink-2); }
.nd.ghost:hover .hub-tag-t { fill: var(--ink); }

/* hub 的地盘。两层：一层是化开的色（模糊过，像一片气氛），一层是几乎看不见的轮廓。
   只要色就分不出边界（尤其 py 那片和背景灰度接近），只要线就变成硬框 ——
   两个都留 12% 以下，读起来才是“这里归它管”而不是“这里被框住了”。 */
.zones { pointer-events: none; }
.zone-glow { fill: var(--a); fill-opacity: var(--o); stroke: none; }
/* 同一份浓度，白底上读成“色块”，黑底上才读成“气氛” —— 浅色主题要减半；
   py 那片面积是另外两片的八倍，减半还不够，得再压一道，否则整页泛绿。 */
/* 浅色稿要单独配一次：同一个 fill-opacity，白底读成“色块”、黑底才读成“气氛”。
   但不能压到看不见 —— pill 是贴在这条边上的图例，框自己糊掉，标签就悬空了。
   系数 0.55 / py 再 ×0.55：py 那片面积是另外两片的八倍，同浓度会铺成整页底色。 */
html[data-theme='light'] .zone-glow { fill-opacity: calc(var(--o) * 0.55); }
html[data-theme='light'] .zone-glow[data-z='@hub-py'] { fill-opacity: calc(var(--o) * 0.55 * 0.55); }
.zone-line {
  fill: none;
  stroke: var(--a);
  stroke-opacity: 0.14;
  stroke-width: 1;
  stroke-dasharray: 1 0;
}

.hulls { pointer-events: none; }
.btitles text {
  font-family: var(--font-mono);
  font-size: 11px;
  letter-spacing: 0.08em;
  opacity: 0.78;
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 4px;
  stroke-linejoin: round;
}

/* ------------------------------ HUD ------------------------------ */
.bar {
  position: absolute;
  top: 14px;
  left: 14px;
  right: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
  pointer-events: none;
}
.bar > * { pointer-events: auto; }
.bar-burger {
  display: none;
  width: 34px; height: 34px; border-radius: 9px;
  border: 1px solid var(--line-2);
  background: color-mix(in oklab, var(--surface-solid) 80%, transparent);
  backdrop-filter: blur(12px);
  color: var(--ink-2);
}
.searchbox {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 11px;
  border-radius: 9px;
  border: 1px solid var(--line-2);
  background: color-mix(in oklab, var(--surface-solid) 78%, transparent);
  backdrop-filter: blur(12px);
  color: var(--ink-3);
  width: 210px;
}
.searchbox input {
  border: 0; background: none; outline: none; color: var(--ink);
  font-size: 12px; width: 100%;
}
.sb-x { color: var(--ink-3); font-size: 10px; }
.zoomer {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 2px;
  height: 34px;
  padding: 0 4px;
  border-radius: 9px;
  border: 1px solid var(--line-2);
  background: color-mix(in oklab, var(--surface-solid) 78%, transparent);
  backdrop-filter: blur(12px);
}
.zoomer button { width: 26px; height: 26px; border-radius: 6px; color: var(--ink-2); font-size: 14px; }
.zoomer button:hover { background: var(--surface-2); color: var(--ink); }
.zoomer span { font-size: 10px; color: var(--ink-3); min-width: 38px; text-align: center; }
.bar-txt {
  height: 34px; padding: 0 12px; border-radius: 9px; font-size: 11.5px; color: var(--ink-2);
  border: 1px solid var(--line-2);
  background: color-mix(in oklab, var(--surface-solid) 78%, transparent);
  backdrop-filter: blur(12px);
}

.focus-chip {
  position: absolute;
  top: 60px;
  left: 14px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 28px;
  padding: 0 10px;
  border-radius: 99px;
  border: 1px solid color-mix(in oklab, var(--a) 40%, transparent);
  background: color-mix(in oklab, var(--a) 12%, var(--bg));
  font-size: 11px;
  color: var(--ink);
  backdrop-filter: blur(10px);
}
.focus-chip i { width: 5px; height: 5px; border-radius: 99px; background: var(--a); }
.focus-chip span { color: var(--ink-3); font-size: 9px; }

.hud {
  position: absolute;
  right: 14px;
  bottom: 12px;
  font-size: 9.5px;
  letter-spacing: 0.06em;
  color: var(--ink-4);
  pointer-events: none;
}

/* ------------------------------ 响应式 ------------------------------ */
@media (max-width: 1080px) {
  .tp { grid-template-columns: 1fr; }
  .side {
    position: absolute;
    top: calc(var(--nav-h));
    bottom: 0;
    left: 0;
    width: 280px;
    z-index: 120;
    background: color-mix(in oklab, var(--bg) 96%, transparent);
    backdrop-filter: blur(18px);
    transform: translateX(-102%);
    transition: transform 0.34s var(--ease);
  }
  .side.open { transform: none; box-shadow: 40px 0 80px -40px rgba(0, 0, 0, 0.8); }
  .side-x { display: block; }
  .bar-burger { display: grid; place-items: center; }
  .searchbox { width: 150px; }
  .stage { height: calc(100vh - var(--nav-h)); }
}
@media (max-width: 620px) {
  .bar { gap: 6px; }
  .searchbox { width: 120px; }
  .hud { display: none; }
  .bar-txt { padding: 0 9px; }
}
</style>
