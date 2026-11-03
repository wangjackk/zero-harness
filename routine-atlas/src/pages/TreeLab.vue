<script setup lang="ts">
import { reactive, ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { growTree, DEFAULT_TREE, type TreeOptions, type TreeModel } from '../lib/tree'
import { LEAF_UNIT, leafAbs, tone } from '../lib/treeArt'

/* 调参台：左边手动拖，右边当场重算；同一组参数并排长几棵（不同种子），
   一眼看出这套参数是稳定像树，还是只有某一个种子碰巧好看。
   调好的一组参数会写进链接，可以直接发给别人：#/tree?h=300&ang=40,82&seeds=7,23,41 */

const router = useRouter()
const p = reactive<TreeOptions>({ ...DEFAULT_TREE })

/* 枝数上限：参数拖到 4 层 × 4 个孩子时是 300+ 条枝，每枝两三个团、每团十几片叶，
   一次重算要几百毫秒，拖动就没法看了。默认那棵树只有二三十枝，留了三倍余量 */
const MAX_BRANCHES = 120

/* ---------- 参数表 ---------- */
interface Knob {
  k: keyof TreeOptions
  label: string
  min: number
  max: number
  step: number
  /** 双值参数（区间），给一对滑杆 */
  pair?: boolean
  unit?: string
}
interface Group {
  name: string
  note: string
  knobs: Knob[]
}

const GROUPS: Group[] = [
  {
    name: '骨架',
    note: '主干多长多粗、分几层',
    knobs: [
      { k: 'height', label: '主干长度', min: 160, max: 460, step: 5 },
      { k: 'trunkWidth', label: '基部宽度', min: 6, max: 40, step: 1 },
      { k: 'levels', label: '分叉层数', min: 1, max: 4, step: 1 },
      { k: 'segments', label: '主干细分', min: 4, max: 16, step: 1 }
    ]
  },
  {
    name: '分枝',
    note: '分叉角、主干对数、逐级衰减',
    knobs: [
      { k: 'angle', label: '分叉角', min: 10, max: 95, step: 1, pair: true, unit: '°' },
      { k: 'children', label: '主干分枝对数', min: 1, max: 4, step: 1, pair: true },
      { k: 'lengthDecay', label: '长度衰减', min: 0.35, max: 0.95, step: 0.01, pair: true },
      { k: 'widthDecay', label: '宽度衰减', min: 0.35, max: 0.85, step: 0.01 }
    ]
  },
  {
    name: '姿态',
    note: '枝的弯曲与摆动',
    knobs: [
      { k: 'bend', label: '梢头向光', min: 0, max: 0.8, step: 0.01 },
      { k: 'wobble', label: '枝身摆动', min: 0, max: 0.8, step: 0.01 }
    ]
  },
  {
    name: '叶',
    note: '挂在哪、团多大、叶多长',
    knobs: [
      { k: 'leafFrom', label: '满叶起始层', min: 0, max: 4, step: 1 },
      { k: 'clusterSize', label: '叶团半径', min: 4, max: 30, step: 1 },
      { k: 'cluster', label: '每团叶数', min: 1, max: 10, step: 1, pair: true },
      { k: 'leafSize', label: '叶片长度', min: 4, max: 28, step: 0.5, pair: true },
      { k: 'leafAspect', label: '叶长宽比', min: 1.2, max: 3.2, step: 0.05 }
    ]
  }
]

/* ---------- 链接参数 ---------- */
const SER: { k: keyof TreeOptions; q: string }[] = [
  { k: 'height', q: 'h' },
  { k: 'trunkWidth', q: 'tw' },
  { k: 'levels', q: 'lv' },
  { k: 'segments', q: 'sg' },
  { k: 'angle', q: 'ang' },
  { k: 'children', q: 'kid' },
  { k: 'lengthDecay', q: 'dec' },
  { k: 'widthDecay', q: 'wd' },
  { k: 'bend', q: 'bd' },
  { k: 'wobble', q: 'wb' },
  { k: 'leafFrom', q: 'lf' },
  { k: 'cluster', q: 'cl' },
  { k: 'leafSize', q: 'ls' },
  { k: 'leafAspect', q: 'la' },
  { k: 'clusterSize', q: 'cs' }
]

const hash = location.hash
const qs = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : location.search.slice(1)
const q = new URLSearchParams(qs)
for (const { k, q: key } of SER) {
  const raw = q.get(key)
  if (!raw) continue
  const nums = raw.split(',').map(Number)
  if (nums.some(n => !Number.isFinite(n))) continue
  if (Array.isArray(p[k])) {
    const arr = p[k] as unknown as number[]
    arr[0] = nums[0]
    arr[1] = nums[1] ?? nums[0]
  } else {
    ;(p as unknown as Record<string, unknown>)[k] = nums[0]
  }
}

/* ---------- 种子与列 ---------- */
const seeds = ref<number[]>(
  (q.get('seeds') ?? '7,23,41')
    .split(',')
    .map(Number)
    .filter(n => Number.isFinite(n))
    .slice(0, 4)
)
if (!seeds.value.length) seeds.value = [7, 23, 41]

const roll = () => 1 + Math.floor(Math.random() * 97)

function setCols(n: number) {
  const cur = seeds.value.slice(0, n)
  while (cur.length < n) cur.push(roll())
  seeds.value = cur
}
function reroll(i: number) {
  const next = seeds.value.slice()
  next[i] = roll()
  seeds.value = next
}
function rerollAll() {
  seeds.value = seeds.value.map(() => roll())
}

/* ---------- 生成 ---------- */
/* 拖动时 input 事件比帧还密，用 rAF 合并：一帧最多重算一次 */
const rev = ref(0)
let queued = false
function bump() {
  if (queued) return
  queued = true
  requestAnimationFrame(() => {
    queued = false
    rev.value++
  })
}
watch(p, bump, { deep: true })
watch(seeds, bump, { deep: true })

interface Col {
  seed: number
  tree: TreeModel
  /** 让这棵在自己的列里水平居中 */
  dx: number
}

const layout = computed(() => {
  void rev.value
  const ts = seeds.value.map(s => growTree({ ...p, seed: s, maxBranches: MAX_BRANCHES }))
  /* 列宽不能只用枝的包围盒：叶子会溢出到枝外一圈，按枝宽排列会让三棵的树冠叠在一起 */
  const ext = ts.map(t => {
    let x0 = t.box.x
    let x1 = t.box.x + t.box.w
    for (const c of t.clusters) {
      const r = c.size * 1.85
      if (c.x - r < x0) x0 = c.x - r
      if (c.x + r > x1) x1 = c.x + r
    }
    return { x0, x1, w: x1 - x0 }
  })
  const wMax = Math.max(120, ...ext.map(e => e.w))
  const colW = wMax + 40
  const top = Math.min(0, ...ts.map(t => t.box.y))
  const cols: Col[] = ts.map((t, i) => ({
    seed: seeds.value[i],
    tree: t,
    dx: i * colW + colW / 2 - (ext[i].x0 + ext[i].w / 2)
  }))
  return { cols, colW, top, w: colW * ts.length }
})

const capped = computed(() => layout.value.cols.some(c => c.tree.branchCount >= MAX_BRANCHES - 1))

const viewBox = computed(() => {
  const { top, w } = layout.value
  return `0 ${(top - 26).toFixed(0)} ${w.toFixed(0)} ${(-top + 26 + 64).toFixed(0)}`
})

/* ---------- 读写 ---------- */
const fmt = (v: number) => String(Math.round(v * 1000) / 1000)
/** 单值参数 */
const val = (k: keyof TreeOptions) => p[k] as unknown as number
/** 双值参数的第 i 个 */
const pairVal = (k: keyof TreeOptions, i: 0 | 1) => (p[k] as unknown as number[])[i]

function setOne(k: keyof TreeOptions, v: number) {
  ;(p as unknown as Record<string, unknown>)[k] = v
}
function setTwo(k: keyof TreeOptions, i: 0 | 1, v: number) {
  ;(p[k] as unknown as number[])[i] = v
}
function onOne(k: keyof TreeOptions, e: Event) {
  setOne(k, Number((e.target as HTMLInputElement).value))
}
function onTwo(k: keyof TreeOptions, i: 0 | 1, e: Event) {
  setTwo(k, i, Number((e.target as HTMLInputElement).value))
}

const snap = (v: number, step: number) => Math.round(v / step) * step

function reset() {
  Object.assign(p, DEFAULT_TREE)
}
function randomize() {
  for (const g of GROUPS) {
    for (const d of g.knobs) {
      const pick = () => snap(d.min + Math.random() * (d.max - d.min), d.step)
      if (d.pair) {
        const a = pick()
        const b = pick()
        setTwo(d.k, 0, Math.min(a, b))
        setTwo(d.k, 1, Math.max(a, b))
      } else {
        setOne(d.k, pick())
      }
    }
  }
  /* 随机也得有个上限，不然随手一组就可能爆量 */
  if (p.levels > 4) p.levels = 4
}

const copied = ref(false)
function share() {
  const body = SER.map(({ k, q: key }) => (Array.isArray(p[k]) ? `${key}=${(p[k] as unknown as number[]).map(fmt).join(',')}` : `${key}=${fmt(p[k] as unknown as number)}`)).join('&')
  const url = `${location.origin}${location.pathname}#/tree?${body}&seeds=${seeds.value.join(',')}`
  const done = () => {
    copied.value = true
    setTimeout(() => (copied.value = false), 1500)
  }
  if (navigator.clipboard) navigator.clipboard.writeText(url).then(done).catch(() => {})
  else done()
}

function back() {
  if (history.length > 1) router.back()
  else router.push('/anatomy')
}
</script>

<template>
  <Teleport to="body">
    <div class="lab">
      <aside class="panel">
        <header class="brand">
          <span class="kicker mono">TREE LAB</span>
          <h2>手动调参</h2>
          <p>左边拖动，右边当场重算。同一组参数并排长三棵，专门用来看这套参数是不是换个种子也还像树。</p>
        </header>

        <section class="seeds">
          <div class="row">
            <span class="lab-l">并排</span>
            <div class="seg">
              <button
                v-for="n in [1, 2, 3, 4]" :key="n"
                :class="{ on: seeds.length === n }" @click="setCols(n)"
              >{{ n }}</button>
            </div>
            <button class="mini" @click="rerollAll">换一批</button>
          </div>
          <div class="row seedlist">
            <button
              v-for="(s, i) in seeds" :key="i" class="chip mono"
              :title="`点击换掉第 ${i + 1} 棵的种子`" @click="reroll(i)"
            >#{{ s }}</button>
          </div>
        </section>

        <section v-for="g in GROUPS" :key="g.name" class="group">
          <div class="ghead">
            <span class="gname">{{ g.name }}</span>
            <em class="mono">{{ g.note }}</em>
          </div>

          <div v-for="d in g.knobs" :key="String(d.k)" class="knob">
            <div class="khead">
              <span>{{ d.label }}</span>
              <b class="mono">
                <template v-if="d.pair">{{ fmt(pairVal(d.k, 0)) }} – {{ fmt(pairVal(d.k, 1)) }}{{ d.unit ?? '' }}</template>
                <template v-else>{{ fmt(val(d.k)) }}{{ d.unit ?? '' }}</template>
              </b>
            </div>
            <div class="sliders" :class="{ dual: d.pair }">
              <input
                type="range" :min="d.min" :max="d.max" :step="d.step" :value="d.pair ? pairVal(d.k, 0) : val(d.k)"
                @input="e => (d.pair ? onTwo(d.k, 0, e) : onOne(d.k, e))"
              />
              <input
                v-if="d.pair" type="range" :min="d.min" :max="d.max" :step="d.step" :value="pairVal(d.k, 1)"
                @input="e => onTwo(d.k, 1, e)"
              />
            </div>
          </div>
        </section>

        <footer class="acts">
          <button @click="reset">默认</button>
          <button @click="randomize">随机一组</button>
          <button @click="share">{{ copied ? '已复制' : '复制链接' }}</button>
        </footer>
      </aside>

      <main class="stage">
        <button class="back mono" @click="back">← 返回</button>
        <svg :viewBox="viewBox" class="tree" preserveAspectRatio="xMidYMid meet">
          <line :x1="0" :x2="layout.w" y1="0" y2="0" class="ground" />

          <g v-for="(col, ci) in layout.cols" :key="col.seed + '-' + ci" :transform="`translate(${col.dx.toFixed(2)} 0)`">
            <g class="bark">
              <path v-for="b in col.tree.branches" :key="b.id" :d="b.ribbon" />
            </g>
            <g class="foliage">
              <path
                v-for="(l, i) in col.tree.leaves" :key="i"
                :d="LEAF_UNIT" :transform="leafAbs(l)" :class="'t' + tone(l.shade)"
              />
            </g>
            <text class="tag mono" x="0" y="42" text-anchor="middle">
              #{{ col.seed }} · {{ col.tree.branchCount }} 枝 · {{ col.tree.leafCount }} 叶
            </text>
          </g>
        </svg>

        <p v-if="capped" class="warn mono">
          已达 {{ MAX_BRANCHES }} 枝上限，再往上加层或加孩子会被截断 —— 参数本身没有失效，只是这一页不再画更多。
        </p>
      </main>
    </div>
  </Teleport>
</template>

<style scoped>
.lab {
  position: fixed; inset: 0; z-index: 60; background: #0a0c0e;
  display: grid; grid-template-columns: 288px 1fr;
  font-family: var(--font-sans, system-ui, sans-serif); color: #cfd8dd;
}

/* ---------- 左栏 ---------- */
.panel {
  border-right: 1px solid rgba(255, 255, 255, .07);
  padding: 20px 18px 22px; overflow: auto; overscroll-behavior: contain;
  display: flex; flex-direction: column; gap: 14px;
}
.brand h2 { margin: 6px 0 8px; font-size: 17px; font-weight: 500; letter-spacing: .01em; }
.brand p { margin: 0; font-size: 11.5px; line-height: 1.75; color: #7b8891; }
.kicker { font-size: 9px; letter-spacing: .18em; color: #5e6b74; }

.seeds { display: grid; gap: 8px; padding-bottom: 12px; border-bottom: 1px solid rgba(255, 255, 255, .06); }
.row { display: flex; align-items: center; gap: 8px; }
.lab-l { font-size: 11px; color: #8b98a1; }
.seg { display: flex; border: 1px solid rgba(255, 255, 255, .1); border-radius: 6px; overflow: hidden; }
.seg button {
  width: 26px; height: 22px; border: 0; background: transparent; cursor: pointer;
  font-family: var(--font-mono, monospace); font-size: 11px; color: #6f7c85;
}
.seg button + button { border-left: 1px solid rgba(255, 255, 255, .08); }
.seg button.on { background: rgba(127, 227, 239, .14); color: #d8f4f8; }
.mini {
  margin-left: auto; padding: 3px 8px; border-radius: 5px; cursor: pointer;
  border: 1px solid rgba(255, 255, 255, .1); background: transparent;
  font-size: 10.5px; color: #8b98a1;
}
.mini:hover { color: #d8f4f8; border-color: rgba(127, 227, 239, .4); }
.seedlist { gap: 6px; }
.chip {
  flex: 1; padding: 3px 0; border-radius: 5px; cursor: pointer;
  border: 1px solid rgba(255, 255, 255, .08); background: rgba(255, 255, 255, .02);
  font-size: 10px; color: #7b8891;
}
.chip:hover { color: #d8f4f8; border-color: rgba(127, 227, 239, .35); }

.group { display: grid; gap: 9px; }
.ghead { display: grid; gap: 2px; padding-bottom: 3px; border-bottom: 1px solid rgba(255, 255, 255, .05); }
.gname { font-size: 11.5px; color: #cfe3e8; letter-spacing: .04em; }
.ghead em { font-style: normal; font-size: 9.5px; color: #5e6b74; }

.knob { display: grid; gap: 1px; }
.khead { display: flex; align-items: baseline; gap: 8px; font-size: 11px; color: #8b98a1; }
.khead b { margin-left: auto; font-weight: 400; color: #a8c6cd; letter-spacing: .02em; }

.sliders { display: grid; gap: 0; }
.sliders.dual { grid-template-columns: 1fr 1fr; column-gap: 8px; }

input[type='range'] {
  -webkit-appearance: none; appearance: none;
  width: 100%; height: 13px; margin: 0; background: transparent; cursor: ew-resize;
}
input[type='range']::-webkit-slider-runnable-track {
  height: 2px; border-radius: 2px; background: rgba(255, 255, 255, .13);
}
input[type='range']::-webkit-slider-thumb {
  -webkit-appearance: none; width: 9px; height: 9px; margin-top: -3.5px;
  border: 0; border-radius: 50%; background: #9fd8e0;
  box-shadow: 0 0 0 3px rgba(127, 227, 239, .1);
}
input[type='range']:hover::-webkit-slider-thumb { background: #d8f4f8; }
input[type='range']::-moz-range-track { height: 2px; background: rgba(255, 255, 255, .13); }
input[type='range']::-moz-range-thumb {
  width: 9px; height: 9px; border: 0; border-radius: 50%; background: #9fd8e0;
}

.acts { display: flex; gap: 6px; margin-top: auto; padding-top: 6px; }
.acts button {
  flex: 1; padding: 6px 0; border-radius: 6px; cursor: pointer;
  border: 1px solid rgba(255, 255, 255, .1); background: rgba(255, 255, 255, .02);
  font-size: 11px; color: #8b98a1;
}
.acts button:hover { color: #d8f4f8; border-color: rgba(127, 227, 239, .35); }

/* ---------- 右栏 ---------- */
.stage { position: relative; display: grid; place-items: center; padding: 22px; min-width: 0; }
.tree { width: 100%; height: 100%; }
.back {
  position: absolute; top: 18px; right: 22px; z-index: 2;
  padding: 5px 10px; border-radius: 6px; cursor: pointer;
  font-size: 11px; letter-spacing: .06em; color: #6f7c85;
  border: 1px solid rgba(255, 255, 255, .08); background: rgba(255, 255, 255, .02);
  transition: color .2s, border-color .2s;
}
.back:hover { color: #cfe3e8; border-color: rgba(255, 255, 255, .2); }
.warn {
  position: absolute; left: 22px; bottom: 16px; margin: 0;
  font-size: 10px; color: #b4853f; letter-spacing: .02em;
}

.bark path { fill: #3f3226; }
.foliage path { stroke: none; }
.foliage .t0 { fill: #26452a; }
.foliage .t1 { fill: #315c2f; }
.foliage .t2 { fill: #427237; }
.ground { stroke: #211f1c; stroke-width: 1; }
.tag { fill: #5c6a72; font-size: 11px; letter-spacing: .04em; }

@media (max-width: 860px) {
  .lab { grid-template-columns: 1fr; grid-template-rows: 46vh 1fr; }
  .panel { border-right: 0; border-bottom: 1px solid rgba(255, 255, 255, .07); }
}
</style>
