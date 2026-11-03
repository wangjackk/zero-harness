<script setup lang="ts">
/* 一棵树的一生：种子 → 破土 → 苗 → 成树 → 转黄 → 落叶 → 枯枝，自动循环。
   同一份几何由 growTree 生成，时间轴只决定「此刻长到哪」，所以任意时刻都能停住，不会有中间态穿帮。 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { growTree, growSprout, ribbonPath, type Cluster, type LeafNode, type Pt } from '../lib/tree'
import { LEAF_UNIT, LEAF_W, leafT, tone } from '../lib/treeArt'

const props = withDefaults(defineProps<{ duration?: number; seed?: number; at?: number }>(), {
  duration: 24,
  seed: 9
})
const emit = defineEmits<{ tick: [t: number]; stats: [s: { leaf: number; branch: number; cluster: number }] }>()

const model = growTree({ seed: props.seed })
/* 地面到树梢的高度，用来把新梢上的局部高度换算成整棵树的绝对高度。
   写死 300 的话，改 DEFAULT_TREE.height 之后枯萎次序就错位了 */
const H = Math.max(1, -model.box.y)
const c01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x)
const lerp = (a: number, b: number, k: number) => a + (b - a) * k
const arts = model.clusters

/* 叶实例再调 routine —— 树上多出来的这一层不在整树里预算好，
   要等运行到那一刻才长。所以它单独算一簇新梢，挂在原来那团叶的团心上：
   那团叶就是这个梢的顶芽，梢从团心穿出来，叶被顶开、随后落掉，
   末梢再结新的团。新梢的叶团按换算后的绝对高度参与枯萎，梢头永远最先转黄。 */
const UP = -Math.PI / 2

interface SprigArt {
  key: number
  ax: number
  ay: number
  /** SVG rotate，度 */
  rot: number
  /** 这条梢挂在哪根枝上：它是那根枝的孩子，长出来之后宿主得跟着变粗 */
  host: number
  branches: ReturnType<typeof growSprout>['branches']
  /** b 是团长所在的那根枝 —— 叶团不是钉在终点，它挂在枝上，枝到哪它在哪 */
  arts: { c: Cluster; level: number; b: BarkLike }[]
}

const sprigs: SprigArt[] = []
const anchorOf = new Map<Cluster, number>()
/** 枝身在某点附近的半径：按最近的线段插值 */
function radiusNear(b: { pts: Pt[]; radii: number[] }, p: Pt) {
  let best = { d: Infinity, r: b.radii[0] }
  for (let i = 0; i < b.pts.length - 1; i++) {
    const a = b.pts[i]
    const e = b.pts[i + 1]
    const vx = e.x - a.x
    const vy = e.y - a.y
    const L2 = vx * vx + vy * vy || 1
    let t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / L2
    t = t < 0 ? 0 : t > 1 ? 1 : t
    const d = Math.hypot(p.x - (a.x + vx * t), p.y - (a.y + vy * t))
    if (d < best.d) best = { d, r: b.radii[i] + (b.radii[i + 1] - b.radii[i]) * t }
  }
  return best.r
}
/* 新梢的落点：局部 +y 是枝梢背后那侧，基点沿**枝梢方向**退进去 6 个单位，
   根埋在枝干里，露出来的只有末端那一小撮。
   早先按新梢方向退 8 个单位，新梢和枝梢差二三十度，基点就横着离开了枝身；
   那段空白原先被叶团盖着看不出来，叶一退掉，根就悬在梢头旁边。 */
function sprigBase(c: Cluster) {
  const dir = (c.dir * Math.PI) / 180
  const d2 = dir + (UP - dir) * 0.3
  const rot = d2 / (Math.PI / 180) + 90
  const rad = (rot * Math.PI) / 180
  const cos = Math.cos(rad)
  const sin = Math.sin(rad)
  const ax = c.x - Math.cos(dir) * 6
  const ay = c.y - Math.sin(dir) * 6
  /* 宿主（这根枝）在落点处有多粗：新梢的根端要和它齐平。
     枝梢自己只有 0.3 宽，新梢若按 2 起，平头截面比枝梢宽一倍，
     叶一退掉就在枝身上鼓出一道明显的台阶 —— 原先全靠叶团盖着。
     局部坐标里 UP 对应世界里的 d2，所以宿主方向换算过来是 UP + (dir - d2)。 */
  const hostR = radiusNear(model.branches[c.owner], { x: ax, y: ay })
  return {
    ax,
    ay,
    rot,
    cos,
    sin,
    rootR: Math.max(0.6, hostR * 0.98),
    radius: Math.min(2, Math.max(1.05, hostR * 1.45)),
    entry: UP + (dir - d2)
  }
}
{
  /* 落点要干净：这一处马上要多一条枝，周围不能再压着别的团的叶。
     早先只按「外缘、中上部」挑，邻团的叶正好落在团心旁边 ——
     叶退掉之后那几片还挂在枝根上，同一处又有枝又有叶。
     先挑一片叶都不挨着的，实在没有才按压得最少的兜底。 */
  const strayAt = (c: Cluster, base: { ax: number; ay: number }) => {
    let n = 0
    for (const o of model.clusters) {
      if (o === c) continue
      for (const l of o.leaves) if (Math.hypot(l.x - base.ax, l.y - base.ay) < 16) n++
    }
    return n
  }
  /* 挑树冠外缘、中上部的团：太低会被内部枝叶埋住，最高处的团再往外抽就顶出树冠，只剩一根铁丝 */
  const pool = model.clusters
    .filter(c => c.level > 0.5 && c.level < 0.84)
    .sort((a, b) => Math.abs(b.x) - Math.abs(a.x))
    .map(c => ({ c, base: sprigBase(c), stray: 0 }))
  for (const o of pool) o.stray = strayAt(o.c, o.base)
  /* 次序：干净的优先，干净里面再挑靠外缘的。
     只按干净挑会挑到内膛的团 —— 新梢长进树冠内部，多出来的一层就看不见了；
     只按外缘挑又回到「叶退掉了、邻团的叶还压在枝根上」。
     「外缘」用最外侧那个团往里 28 单位算，不写死坐标，换种子也成立。 */
  const outer = Math.max(...pool.map(o => Math.abs(o.c.x)))
  const rank = (stray: number) => (stray === 0 ? 0 : stray <= 2 ? 1 : 2)
  const cand = pool.sort(
    (a, b) =>
      rank(a.stray) - rank(b.stray) ||
      Number(Math.abs(b.c.x) >= outer - 28) - Number(Math.abs(a.c.x) >= outer - 28) ||
      Math.abs(b.c.x) - Math.abs(a.c.x)
  )
  for (const { c, base } of cand) {
    if (sprigs.length >= 3) break
    /* 团与团之间留开，否则几簇新梢挤成一片，看不出是各自抽的 */
    if (sprigs.some(s => Math.hypot(s.ax - base.ax, s.ay - base.ay) < 62)) continue
    const k = sprigs.length
    /* 新梢是这一跳追加出来的，长度只够结一层小团，不可能铺满剩下的树冠 */
    const m = growSprout({
      seed: props.seed * 131 + k * 17,
      length: 46 - k * 2,
      radius: base.radius,
      rootR: base.rootR,
      entry: base.entry,
      clusterSize: 13,
      cluster: [2, 3]
    })
    const items = m.clusters.map(cc => {
      const wy = base.ay + (cc.x * base.sin + cc.y * base.cos)
      /* 这个团长在哪根枝上，也一并记下来：团心要跟着那根枝的枝尖走 */
      return { c: cc, level: Math.min(1, Math.max(0, -wy / H)), b: m.branches[cc.owner] }
    })
    sprigs.push({
      key: k,
      ax: base.ax,
      ay: base.ay,
      rot: base.rot,
      host: c.owner,
      branches: m.branches,
      arts: items
    })
    anchorOf.set(c, k)
  }
}
/* 落叶不是抛物线。叶子一离枝几乎立刻到终速，之后是飘 ——
   所以这里不用物理引擎，用一阶惯性 + 终端速度的解析式：
   y(s) = vt·(s − τ(1 − e^(−s/τ)))。
   物理引擎给的是刚体：一片叶只加重力会像硬币一样直落，横向一动不动；
   要飘就得自己写阻力、翻滚、横向风。「免费得到」的那一半恰好是看上去最假的。
   而解析式只是 t 的函数：任意时刻都能定格（?at= 才成立），也不用存状态。 */
interface Drifter {
  key: string
  /** 从哪片叶离的枝：团里那一片要同时消失，否则同一片叶会同时挂在枝上又在半空 */
  leaf: LeafNode
  x0: number
  y0: number
  len: number
  rot0: number
  tone: number
  /** 终端速度，单位/秒 */
  vt: number
  /** 到终速的时间常数，秒 */
  tau: number
  /** 左右摆的振幅（单位）与角频率（rad/s） */
  sway: number
  omega: number
  phase: number
  /** 翻滚角速度，度/秒 */
  spin: number
  /** 横向风，单位/秒 */
  drift: number
  /** 离枝时刻（整轮 0–1） */
  t0: number
  /** 落到地面要下降的距离 */
  drop: number
  /** 落地所用时间，秒 */
  sLand: number
}
const hash1 = (n: number) => {
  const v = Math.sin(n * 12.9898) * 43758.5453
  return v - Math.floor(v)
}
/* 落地之后留半秒淡掉：叶不该在半空消失 */
const LITTER_FADE = 0.5
const detachOf = new Map<LeafNode, Drifter>()
const drifters: Drifter[] = model.clusters
  .filter((_, i) => i % 3 === 0)
  .flatMap((c, k) => {
    /* 每三团取两片：取的是团里真实的那两片，位置和朝向都对得上，
       离枝那一刻不用重新摆姿势 */
    const picks = c.leaves.length > 1 ? [0, Math.floor(c.leaves.length / 2)] : [0]
    return picks.map((pi, j) => {
      const l = c.leaves[pi]
      const h = hash1(c.x * 0.31 + c.y * 0.17 + j * 7.3 + k * 3.9)
      const h2 = hash1(h * 91.7 + 5.1)
      const h3 = hash1(h2 * 55.3 + 2.7)
      /* 高处先离枝：按这一团自己的枯萎进度反推时刻（witherP ≈ 0.45 时脱开），
         于是梢头的叶先飘，下面的叶晚一点，和 stop 从叶梢往下一路收回同向 */
      const t0 = 0.72 + (0.171 + (1 - c.level) * 0.42) * 0.22 + h * 0.012
      /* 大叶慢：面积涨得比质量快，终端速度要按叶长压下来 */
      let vt = lerp(96, 62, Math.min(1, Math.max(0, (l.len - 12) / 10))) * (0.85 + h * 0.3)
      const tau = 0.35 + h2 * 0.3
      const drop = Math.max(4, -l.y - 1.5 - h3 * 3)
      /* 一轮收尾是硬边界：整棵树 1.0 就重来，还在半空的叶子会凭空消失。
         所以按「最晚 0.985 落完」反算这个叶片允许的上限，超了就加快 ——
         被加快的只可能是又大又高、原本最慢的那几片。 */
      const budget = (0.985 - t0) * props.duration - LITTER_FADE
      if (drop / vt + tau > budget) vt = drop / Math.max(0.4, budget - tau)
      /* 落地时刻解一次不动点：s = D/vt + τ(1 − e^(−s/τ)) */
      let s = drop / vt + tau
      for (let n = 0; n < 4; n++) s = drop / vt + tau * (1 - Math.exp(-s / tau))
      const d: Drifter = {
        key: `${k}-${j}-${Math.round(l.x)}`,
        leaf: l,
        x0: l.x,
        y0: l.y,
        len: l.len,
        rot0: l.rot,
        tone: tone(l.shade),
        vt,
        tau,
        sway: 3.5 + h2 * 6,
        omega: 3.4 + h * 2.2,
        phase: h3 * Math.PI * 2,
        spin: (h > 0.5 ? 1 : -1) * (70 + h2 * 110),
        drift: 4 + h3 * 7,
        t0,
        drop,
        sLand: s
      }
      detachOf.set(l, d)
      return d
    })
  })

const t = ref(0)
let raf = 0
let t0 = 0
watch(t, v => emit('tick', v))
onMounted(() => {
  emit('stats', { leaf: model.leaves.length, branch: model.branches.length, cluster: model.clusters.length })
  if (props.at != null) {
    t.value = props.at
    return
  }
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    t.value = 0.68
    return
  }
  const step = (ts: number) => {
    if (!t0) t0 = ts
    t.value = (((ts - t0) / 1000) % props.duration) / props.duration
    raf = requestAnimationFrame(step)
  }
  raf = requestAnimationFrame(step)
})
onUnmounted(() => cancelAnimationFrame(raf))

/* 落叶的位姿：往下落的距离、横向摆、翻滚角，全是 t 的函数。 */
const fallArt = computed(() => {
  const out: { key: string; fill: string; op: number; x: number; y: number; rot: number; sx: number; sy: number }[] = []
  for (const d of drifters) {
    const s = (t.value - d.t0) * props.duration
    if (s <= 0) continue
    const rel = Math.min(s, d.sLand)
    const y = d.vt * (rel - d.tau * (1 - Math.exp(-rel / d.tau)))
    const k = Math.min(1, y / Math.max(1, d.drop))
    /* 越接近地面摆得越小：落地时还在摆，看着像被风推进土里 */
    const sway = d.sway * Math.sin(d.omega * rel + d.phase) * (1 - k * k * k)
    const op =
      Math.min(1, s * 8) * Math.min(1, Math.max(0, (d.sLand + LITTER_FADE - s) / LITTER_FADE)) * 0.9
    if (op <= 0.004) continue
    out.push({
      key: d.key,
      /* 离枝时还是半干，落地时全干：飘的过程也在褪 */
      fill: rgbStr(greenOf(d.tone, 0.45 + 0.45 * k)),
      op,
      x: d.x0 + d.drift * rel + sway,
      y: d.y0 + y,
      rot: d.rot0 + d.spin * rel,
      /* 用同一个单位叶片还原尺寸：老写法是另一条更胖的固定形状按长度缩放，
         落下来的叶比挂在枝上的宽一半，一眼就看得出是两套叶子 */
      sx: d.len,
      sy: d.leaf.wid / LEAF_W
    })
  }
  return out
})
/* 离枝那一片在团里同时消失：叶子飞出去和叶子留在枝上是同一片，
   两边各画一份的话，起飞那半秒会看到同一片叶出现在两处。
   两边用同一条 0.125 秒的斜坡，交接就看不出。 */
function leafDetached(l: LeafNode) {
  const d = detachOf.get(l)
  if (!d) return 0
  return c01((t.value - d.t0) * props.duration * 8)
}

/* 生长窗口：破土之后到成树 */const growP = computed(() => c01((t.value - 0.07) / 0.56))
const branchP = (birth: number, dur: number) =>
  c01((growP.value - birth * 0.94) / Math.max(0.05, dur * 0.94))
/* 叶团跟着它那根枝走：芽里就揉着叶，枝往哪长、梢头那团就跟到哪，
   不是等枝长到终点再凭空摆一团上去。所以团的进度不是自己的窗口，
   直接读它所在那根枝的进度。 */
const clusterP = (c: Cluster) => {
  const b = model.branches[c.owner]
  return b ? branchP(b.birth, b.dur) : 1
}

/* 粗细不是「成树半径 × 一个进度系数」，而是此刻撑住多少截面算出来的。
   树的管道模型：一条枝的横截面积 = 它自己已长出的部分 + 已长出来的子枝截面积之和，
   开方就是半径。所以主干在苗期只是一根细茎 —— 那时它谁也不撑；
   一级枝抽出来，它的截面积才加到主干上去，主干跟着粗一截。
   成树时子树每一项都长满，面积和恰好回到蓝图，形态不变。 */
const capOwn = model.branches.map(b => b.drawR[0] * b.drawR[0])
/* 每根枝子树的总截面积：成树时它该撑住的全部 */
const capAll = capOwn.slice()
for (let i = model.branches.length - 1; i >= 0; i--) {
  const b = model.branches[i]
  if (b.parent >= 0) capAll[b.parent] += capAll[i]
}
interface BarkLike {
  id: number
  parent: number
  birth: number
  dur: number
  pts: Pt[]
  drawR: number[]
}
/* 父先子后，倒序一遍：每个后代已把自己的面积加到父上，轮到父时再加自己那一段 */
const areaAll = computed(() => {
  const a = new Array<number>(model.branches.length).fill(0)
  for (let i = model.branches.length - 1; i >= 0; i--) {
    const b = model.branches[i]
    a[b.id] += capOwn[b.id] * branchP(b.birth, b.dur)
    if (b.parent >= 0) a[b.parent] += a[b.id]
  }
  return a
})
/* 子枝的窗口比父枝短，长起来更快，中途会反超父枝 —— 一旦反超，
   子枝根部就比父枝在落点处还粗，腋部又露出缺口。所以封顶在父枝的当前粗细。 */
/* 二次生长这一层不走面积账：新梢是主树长完之后由叶实例调出来的，蓝图里没有它，
   把它同时算进分子和分母会几乎自动抵消（成树又回到 1.0，中途也看不出）。
   这里按「一圈年轮」处理：形成层包着整棵树，新梢一抽，从宿主到主干各长一层薄薄的木质。
   按比例给（而不是按固定厚度给），细枝上看得见、主干上只是一点点 ——
   真树是固定厚度，但那样主干几乎不动，而这条路径要讲的正是「子长出来、父跟着粗」。
   梢断掉之后不回缩：长成的是木质，不是撑着的东西。 */
const SPRIG_BOOST = 0.075
function onPathToRoot(id: number, host: number) {
  let p = host
  for (let hop = 0; hop < 64 && p >= 0; hop++) {
    if (p === id) return true
    p = model.branches[p].parent
  }
  return false
}
function sprigBoost(id: number) {
  let k = 0
  for (const s of sprigs) {
    if (!onPathToRoot(id, s.host)) continue
    const g = sprigGrow(s.key)
    if (g > k) k = g
  }
  return 1 + SPRIG_BOOST * k
}
const girthAll = computed(() => {
  const a = areaAll.value
  const g = new Array<number>(model.branches.length)
  for (const b of model.branches) {
    const own = Math.sqrt(Math.max(0, a[b.id]) / Math.max(1e-9, capAll[b.id]))
    g[b.id] = b.parent < 0 ? own : Math.min(own, g[b.parent])
  }
  /* 同一条路径乘同一个系数，父子关系不变；兄弟枝不受影响，也不会出现子比父粗 */
  for (const b of model.branches) g[b.id] *= sprigBoost(b.id)
  return g
})
function girth(b: { id: number }) {
  return girthAll.value[b.id] ?? 1
}
/* 枝的填充轮廓按当前粗细重算。只缩遮罩宽度是不够的 ——
   那样枝本身永远是成年宽度，只是「揭示出来多少」，看着从头到尾一样粗。
   drawR 已经含了主干根盘的外扩，这里只乘一个系数。 */
const barkCache = new Map<number, { g: number; d: string }>()
function ribbonOf(b: BarkLike) {
  const g = Math.round(girth(b) * 64) / 64
  const hit = barkCache.get(b.id)
  if (hit && hit.g === g) return hit.d
  const d = ribbonPath(b.pts, b.drawR.map(r => r * g))
  barkCache.set(b.id, { g, d })
  return d
}

/* 枯萎窗口：高处先枯，和 stop 从叶梢往根部传同向 */
const witherAll = computed(() => c01((t.value - 0.72) / 0.22))
const witherP = (level: number) =>
  c01((witherAll.value - (0.1 + (1 - level) * 0.42)) / 0.38)

const GREEN = [
  [0x24, 0x43, 0x28],
  [0x30, 0x5a, 0x2e],
  [0x41, 0x71, 0x36]
]
const DRY = [
  [0x54, 0x45, 0x2b],
  [0x6b, 0x57, 0x33],
  [0x82, 0x6a, 0x3c]
]
const rgbStr = (c: number[]) => `rgb(${Math.round(c[0])} ${Math.round(c[1])} ${Math.round(c[2])})`
/* 老叶先是转黄再落，不是直接淡到没有：叶落下来总有个理由 */
function greenOf(tone: number, w: number) {
  const g = GREEN[tone]
  const d = DRY[tone]
  return [lerp(g[0], d[0], w), lerp(g[1], d[1], w), lerp(g[2], d[2], w)]
}
const SENESCE = [0x9a, 0x86, 0x33]

/* 二次生长的叶比老叶浅：这一簇是刚抽的，和周围的分得开才看得出「又长了一层」 */
const FRESH = [
  [0x3c, 0x6e, 0x33],
  [0x4c, 0x8a, 0x3c],
  [0x61, 0xa4, 0x48]
]
function freshFill(tone: number, w: number) {
  const f = FRESH[tone]
  const d = DRY[tone]
  return rgbStr([lerp(f[0], d[0], w), lerp(f[1], d[1], w), lerp(f[2], d[2], w)])
}

/* 种子与苗 */
const seedIn = computed(() => c01(t.value / 0.03))
const seedCrack = computed(() => c01((t.value - 0.042) / 0.03))
const seedOut = computed(() => c01((t.value - 0.072) / 0.038))
/* 苗期不是另画一根茎：主干自己就细，靠 girth 随年龄变粗，所以「苗」和「树」之间没有换模型的跳变 */
/* 子叶在主干长到顶之前就褪：主干 t≈0.30 已九成高，顶芽的叶团 0.34 接上，
   两片横向的子叶若拖到 0.43，会在树顶多挂一对暗色的宽叶 */
const seedLeaf = computed(() => c01((t.value - 0.1) / 0.05) * (1 - c01((t.value - 0.28) / 0.06)))
const mainP = computed(() => branchP(model.branches[0].birth, model.branches[0].dur))/* 子叶长在主干顶端，要取主干此刻**实际**伸到哪。
   早先按 -H*mainP 估，H 是含叶尖的整树高度、比主干长一截，
   于是子叶一路浮在梢头上方几十个单位，直至褪色。
   采样点是等步长的，所以下标比例就是遮罩揭示的比例，直接按进度插值。 */
const trunkPts = model.branches[0].pts
const stem = computed(() => {
  const n = trunkPts.length - 1
  const f = Math.min(n, Math.max(0, mainP.value * n))
  const i = Math.min(n - 1, Math.floor(f))
  const k = f - i
  const a = trunkPts[i]
  const b = trunkPts[i + 1]
  return {
    x: a.x + (b.x - a.x) * k,
    y: a.y + (b.y - a.y) * k,
    /* 子叶跟着主干的方向歪，不总是垂直朝上 */
    rot: Math.atan2(b.y - a.y, b.x - a.x) / (Math.PI / 180) + 90
  }
})
/* 枝干是 create/start 形成的父子关系，属于实例树这一层。
   整棵子树结算完，这棵树就不在了，所以枝也要跟着散掉 ——
   注册表（name → 类）不在这一层，它不变。
   次序仍然从细枝开始、主干最后结算：越深的枝越先落。
   层级先算好：level 0 是主干。 */
const LV = Math.max(1, ...model.branches.map(b => b.level))
/* 枝的停比叶晚半步：先落叶，叶落光了才看得出枝在变 */
const barkWitherAll = computed(() => c01((t.value - 0.8) / 0.17))
function barkP(level: number) {
  const deep = 1 - Math.min(1, level / LV)
  return c01((barkWitherAll.value - (0.05 + deep * 0.36)) / 0.4)
}
/* 同一层里也要错开断：只按 level 排，两边层级不同的枝会整片同时走，
   看着是半边被剃掉，不是枝一根根断。seed 用枝 id 是稳定的，重渲染次序不变 */
const staggerOf = (seed: number) => ((Math.sin(seed * 127.1) * 43758.5453) % 1 + 1) % 1
const BARK_LIVE = [0x45, 0x37, 0x29]
const BARK_DEAD = [0x40, 0x41, 0x3f]
/* 枝干只有一种材质色。新梢原先单独染成偏绿的褐（rgb 82 84 49），
   可它全长在树冠外缘，于是同一棵树上两侧远端是新色、近处是老色，看着像两套材质。
   新梢的「新」由形态和叶色认出来：更细更短，叶用 FRESH 那一档更亮的绿。 */
function barkFillOf(level: number) {
  const k = barkP(level)
  const f = BARK_LIVE.map((v, i) => Math.round(lerp(v, BARK_DEAD[i], k)))
  return `rgb(${f[0]} ${f[1]} ${f[2]})`
}
/* 停掉的枝先退一层（灰比褐亮，只换色会反而抢眼）。
   之后它不淡掉 —— 断落另有其人，见下面的 FallBark */
function barkOpacityOf(level: number) {
  return 1 - 0.35 * barkP(level)
}

/* 枝也要落。枝是 create/start 出来的子实例，stop 之后就不在实例树上了 ——
   画面上只改成「慢慢淡掉」是透明度的事，读不出「这一条已经被收回」。
   所以它从父枝的接点断开、垂下去、落到地面，和落叶用同一套解析式：
   位姿只是 t 的函数，?at= 才定格得住，也不用存状态。
   主干不走这条：它是根实例自己的身体，下面没有父枝可以断开，所以只灰掉、再淡掉。 */
interface FallBark {
  key: string
  level: number
  /** 已经算好的轮廓（世界坐标）：整簇新梢要当成一个刚体一起动 */
  ds: string[]
  /** 绕哪一点转：枝腰，梢头往下扫 */
  px: number
  py: number
  /** 翻多少度（带方向）：每根枝自己的一段，不是统一的「往挂直转」 */
  spin: number
  drop: number
  vt: number
  tau: number
  drift: number
  /** 断开的时刻（整轮 0–1） */
  t0: number
  sLand: number
  /** 新梢末端那一团叶：叶挂在枝上，枝走它就一起走，不能留在原处悬着 */
  sp?: {
    k: number
    ax: number
    ay: number
    rot: number
    arts: { c: Cluster; level: number; b: BarkLike }[]
  }
}
const BARK_FADE = 0.32
/* 断开那一下两份几何要接得上：旧枝还在遮罩里，新枝是不带遮罩的一份，
   两边共用同一条斜坡，和不透明度恒为基值。否则断口会闪一下 */
const BARK_HAND = 9.5
const fallPool: FallBark[] = []
const barkFall: (FallBark | null)[] = new Array(model.branches.length).fill(null)
const sprigFall: (FallBark | null)[] = sprigs.map(() => null)
function addFall(key: string, lines: { pts: Pt[]; drawR: number[] }[], level: number, idseed: number, at?: number) {
  const all = lines.flatMap(l => l.pts)
  const deep = 1 - Math.min(1, level / LV)
  const h = hash1(idseed * 3.7 + 1.3)
  const h2 = hash1(h * 57.3 + 3.1)
  /* 翻转不定向：每根枝在空中各翻各的一段，转多少也只跟自己的哈希有关。
     早先让所有枝都往「挂直」转 —— 一圈枝同时垂下来，整棵树像在收伞，
     一秒里看着像一棵枞树，不像一根根掉下来的枝。 */
  const spin = (h2 > 0.5 ? 1 : -1) * (28 + hash1(h2 * 13.7) * 40)
  /* 越细越先断：停是从梢往根结算的，枝也一样。
     起点比最后一片叶离枝略晚一点 —— 叶还挂在枝上、枝先走，就会看到叶凭空悬着 */
  let t0 = at ?? 0.874 + deep * 0.055 + staggerOf(idseed) * 0.012
  const mid = lines[0].pts[Math.floor(lines[0].pts.length / 2)]
  /* 落多远要看**翻到底**时最低的那一点，不是原位的最低点。
     绕枝腰转会把梢头扫下去几十个单位，按原位算 drop 的话，
     枝落地那一刻梢头已经插进土里 —— 早先就是这样，地面上摊着一圈枝梢 */
  const ra = (spin * Math.PI) / 180
  const rc = Math.cos(ra)
  const rs = Math.sin(ra)
  const maxYEnd = Math.max(...all.map(p => mid.y + (p.x - mid.x) * rs + (p.y - mid.y) * rc))
  const drop = Math.max(8, -maxYEnd)
  /* 枝比叶重：终速高、到终速快，接近自由落体但不会一路加速 ——
     叶是飘，枝是掉，同一条曲线取不同的 vt 和 τ 就分得开 */
  let vt = 205 + h * 60
  const tau = 0.13 + h * 0.05
  /* 收尾是硬边界：一轮 1.0 就重来，还在半空的枝会凭空消失 */
  const budget = (0.985 - t0) * props.duration - BARK_FADE
  if (drop / vt + tau > budget) vt = drop / Math.max(0.35, budget - tau)
  if (budget <= 0.35) t0 = 0.985 - (BARK_FADE + 0.35) / props.duration
  let s = drop / vt + tau
  for (let n = 0; n < 4; n++) s = drop / vt + tau * (1 - Math.exp(-s / tau))
  const fall: FallBark = {
    key,
    level,
    ds: lines.map(l => ribbonPath(l.pts, l.drawR)),
    px: mid.x,
    py: mid.y,
    spin,
    drop,
    vt,
    tau,
    /* 往外偏一点：原地直落会贴着树身下去，一段段从别的枝中间穿过，
       看着像还挂在上面。外圈的枝偏得多，内圈的少 —— 横移按它离主干多远给 */
    drift: (mid.x >= 0 ? 1 : -1) * (10 + Math.abs(mid.x) * 0.08 + h * 8),
    t0,
    sLand: s
  }
  fallPool.push(fall)
  return fall
}
for (const b of model.branches) {
  if (b.parent < 0) continue
  barkFall[b.id] = addFall(`b${b.id}`, [{ pts: b.pts, drawR: b.drawR }], b.level, b.id + 1)
}
/* 新梢自己的点都在局部系里，先换到世界系再算落 —— 它的 rot 是纯旋转，
   所以换过去之后就与主枝同一套坐标，落法不用另写一份 */
sprigs.forEach((s, k) => {
  const rad = (s.rot * Math.PI) / 180
  const c = Math.cos(rad)
  const sn = Math.sin(rad)
  const lines = s.branches.map(b => ({
    pts: b.pts.map(p => ({ x: s.ax + p.x * c - p.y * sn, y: s.ay + p.x * sn + p.y * c })),
    drawR: b.drawR
  }))
  /* 新梢是这一跳最新的一层，但它末端的叶团要到 0.9 才转黄 ——
     枝先走、叶留在原处悬着，就会看到一小簇叶浮在半空。
     所以它稍晚断，而且叶团跟着这一枝一起落（叶本来是挂在这一枝上的实例）。 */
  const f = addFall(`s${k}`, lines, LV + 1, 40 + k, 0.898 + hash1(k * 7.9 + 2.3) * 0.01)
  f.sp = { k, ax: s.ax, ay: s.ay, rot: s.rot, arts: s.arts }
  sprigFall[k] = f
})
function barkRamp(f: FallBark | null) {
  if (!f) return 0
  return c01((t.value - f.t0) * props.duration * BARK_HAND)
}
/* 主干最后结算：它没有父枝可断，只剩灰掉、淡掉。
   窗口压在大枝还在一根根掉的那一段里 —— 枝全断完只剩一根秃杆，
   顶上还是锥尖，单独放着读成一根针，不是「这棵树不在了」 */
const trunkFade = computed(() => c01((t.value - 0.902) / 0.048))
/* 落下来的枝：下坠距离、转角、横移同样是 t 的函数。
   转是绕枝腰转 —— 梢头往下扫，读起来是「掉」，整段平移只会读成「往下沉」 */
interface FallLeaf {
  t: string
  fill: string
  op: number
}
const barkArt = computed(() => {
  const out: {
    key: string; ds: string[]; fill: string; base: number; op: number
    dx: number; dy: number; rot: number; px: number; py: number
    spx?: string; leaves?: FallLeaf[]
  }[] = []
  for (const f of fallPool) {
    const s = (t.value - f.t0) * props.duration
    if (s <= 0) continue
    const rel = Math.min(s, f.sLand)
    const y = f.vt * (rel - f.tau * (1 - Math.exp(-rel / f.tau)))
    const land = Math.min(1, Math.max(0, (f.sLand + BARK_FADE - s) / BARK_FADE))
    /* 断开与落地两段合在整组的不透明度上；枝本身的基值放在 path 的 fill-opacity，
       这样跟枝一起落的那团叶不会被枝的基值压暗两次 */
    const op = Math.min(1, s * BARK_HAND) * land
    if (op <= 0.004) continue
    let leaves: FallLeaf[] | undefined
    let spx: string | undefined
    if (f.sp) {
      spx = `translate(${f.sp.ax.toFixed(2)} ${f.sp.ay.toFixed(2)}) rotate(${f.sp.rot.toFixed(2)})`
      leaves = []
      for (const a of f.sp.arts) {
        /* 断落这一刻梢早已长完（0.898 之后），团心就在最终位置，不必再按枝尖插值 */
        const sc = sprigLeafScale(f.sp.k, a.level, a.b)
        const cop = sprigLeafOpacity(f.sp.k, a.level, a.b)
        if (cop <= 0.004) continue
        for (const l of a.c.leaves) {
          leaves.push({
            t: `translate(${a.c.x.toFixed(2)} ${a.c.y.toFixed(2)}) scale(${sc.toFixed(3)}) ` +
              leafT(l, a.c.x, a.c.y),
            fill: freshFill(tone(l.shade), witherP(a.level)),
            op: cop
          })
        }
      }
    }
    out.push({
      key: f.key,
      ds: f.ds,
      fill: barkFillOf(f.level),
      base: barkOpacityOf(f.level),
      op,
      dx: f.drift * rel,
      dy: y,
      /* 翻转在落的过程里做完，不翻跟头：转角到自己的 spin 就停 */
      rot: f.spin * (1 - Math.exp(-rel / 0.4)),
      px: f.px,
      py: f.py,
      spx,
      leaves
    })
  }
  return out
})
function clusterOpacity(c: Cluster) {
  return clusterP(c) * (1 - c01((witherP(c.level) - 0.55) / 0.45))
}
function clusterScale(c: Cluster) {
  const grow = 0.25 + 0.75 * clusterP(c)
  return grow * (1 - 0.3 * c01((witherP(c.level) - 0.5) / 0.5))
}
/* 顶芽：叶团不是「消失」，它就是这一梢的芽。
   梢从团心穿出来，叶被顶开、向两侧张，随后老叶转黄落掉 ——
   所以这一刻不是两份几何交叉，是一个动作走四拍：鼓、开、抽、落。
   早先是整团淡到 0、再让新梢长出来，读起来是「一个东西没了、另一个东西出现」，
   可这一处本来是一个实例在往下长，叶柄、梢、老叶都还连着同一个点。 */
const BUD_SWELL = 0.024
const BUD_OPEN = 0.055
const BUD_SHED = 0.045
function budU(c: Cluster) {
  const k = anchorOf.get(c)
  if (k == null) return null
  return t.value - SPROUT_FROM - k * 0.016
}
/* 涨一下再张：鼓得比梢早半拍，叶子才像是被里面那根梢顶开的，不是自己散的 */
function budSwell(c: Cluster) {
  const u = budU(c)
  return u == null ? 0 : Math.sin(Math.PI * c01(u / BUD_SWELL))
}
function budOpen(c: Cluster) {
  const u = budU(c)
  return u == null ? 0 : c01(u / BUD_OPEN)
}
/* 老叶逐片落：同一团里差开工，否则几片同时熄，又变回整团淡出 */
function budShed(c: Cluster, j: number) {
  const u = budU(c)
  if (u == null) return 0
  return c01((u - 0.042 - j * 0.009) / BUD_SHED)
}
/* 团的不透明度只剩「生」那一档：这一团是被一片片落掉的，不能整团淡 */
function clusterVisible(c: Cluster) {
  return clusterOpacity(c)
}
/* 团心此刻在枝上的哪一处。
   枝还短的时候团心在枝的基部附近，枝往外伸团心跟着往外挪，p=1 时回到最终位置。
   团心相对枝上那一点有固定偏移（梢头团往内退 0.22·size，中段团就在那个采样点上），
   所以只算「枝上此刻那一点」与「枝上最终那一点」之差即可。 */
function clusterXform(c: Cluster) {
  const s = clusterScale(c) * (1 + 0.1 * budSwell(c))
  const b = model.branches[c.owner]
  let x = c.x
  let y = c.y
  if (b) {
    const p = branchP(b.birth, b.dur)
    if (p < 1) {
      const fin = branchPointAt(b, c.u)
      const now = branchPointAt(b, c.u * p)
      x = c.x + (now.x - fin.x)
      y = c.y + (now.y - fin.y)
    }
  }
  return `translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${s.toFixed(3)})`
}
/* 单片的张开：叶柄不动、叶身转出去 —— 叶柄是长在枝上的，跟着团心平移
   就变成几片叶子在枝上滑，不是被梢顶开。转的方向按叶在团心的左右分开，
   于是两边的叶各自往外张，中间让出一条给梢。落的时候才真的往下沉。 */
function budLeafT(c: Cluster, l: LeafNode, j: number) {
  const open = budOpen(c)
  if (open <= 0) return leafT(l, c.x, c.y)
  const shed = budShed(c, j)
  let rx = l.x - c.x
  let ry = l.y - c.y
  const m = Math.hypot(rx, ry) || 1
  rx /= m
  ry /= m
  const side = rx >= 0 ? 1 : -1
  /* 只挪两三个单位就够：张开的位移几乎全在转上，叶柄留在原处 */
  const dx = rx * 3 * open
  const dy = (ry < 0 ? ry * 0.4 : ry) * 3 * open + 2 * open + 8 * shed * shed
  const drot = side * 42 * open + side * 20 * shed
  return leafT(l, c.x, c.y, { dx, dy, drot, s: 1 - 0.14 * shed })
}
function budLeafOpacity(c: Cluster, j: number) {
  return 1 - budShed(c, j)
}
/* 逐片转黄再落：团里那几片是真叶，被新梢顶开之后先老去，
   所以颜色比整树的枯萎早走一步，落的时候已经是黄褐 */
function budLeafFill(c: Cluster, l: LeafNode, j: number) {
  const rgb = greenOf(tone(l.shade), witherP(c.level))
  const k = 0.78 * budShed(c, j)
  return rgbStr(rgb.map((v, i) => lerp(v, SENESCE[i], k)))
}

/* 新梢的时钟：调用发生后才起算，逐簇错开一点。
   比芽晚半拍起 —— 芽先鼓、再张，梢才从张开的缝里出来，
   三件事同时开始的话看到的是「一根梢和一团叶一起动」，不是梢把芽顶开 */
const SPROUT_FROM = 0.52
const SPROUT_LAG = 0.012
const sprigGrow = (k: number) => c01((t.value - SPROUT_FROM - SPROUT_LAG - k * 0.016) / 0.1)
const sprigP = (k: number, birth: number, dur: number) =>
  c01((sprigGrow(k) - birth * 0.92) / Math.max(0.06, dur * 0.92))
/* 新梢同理：一出生就全粗像一根插上去的枝条，也从五成开始长 */
function sprigGirth(k: number, b: { birth: number; dur: number }) {
  const start = b.birth * 0.92
  const age = c01((sprigGrow(k) - start) / Math.max(0.1, b.dur * 0.92 + 0.24))
  return 0.5 + 0.5 * Math.pow(age, 0.75)
}
const sprigCache = new Map<string, { g: number; d: string }>()
function sprigRibbon(k: number, b: BarkLike) {
  const g = Math.round(sprigGirth(k, b) * 64) / 64
  const key = `${k}:${b.id}`
  const hit = sprigCache.get(key)
  if (hit && hit.g === g) return hit.d
  const d = ribbonPath(b.pts, b.drawR.map(r => r * g))
  sprigCache.set(key, { g, d })
  return d
}
/* 叶不是等梢长到终点才凭空出现在那儿的：芽里揉着的幼叶是跟着梢一起抽出来的。
   所以不透明度跟枝的进度走（略晚一线，梢先探头），大小从四分之一片长到整片；
   同时团心也不钉在最终梢头 —— 见 sprigLeafXform。 */
const sprigLeafP = (k: number, b: { birth: number; dur: number }) =>
  c01((sprigGrow(k) - b.birth * 0.92 - 0.02) / Math.max(0.08, b.dur * 0.92))
function sprigLeafOpacity(k: number, level: number, b: { birth: number; dur: number }) {
  return sprigLeafP(k, b) * (1 - c01((witherP(level) - 0.55) / 0.45))
}
function sprigLeafScale(k: number, level: number, b: { birth: number; dur: number }) {
  const g = 0.25 + 0.75 * sprigLeafP(k, b)
  return g * (1 - 0.3 * c01((witherP(level) - 0.5) / 0.5))
}
/* 枝上当前长到哪一点：pts 是等距采样，所以按段数插值就是按弧长插值 */
function branchPointAt(b: BarkLike, p: number) {
  const n = b.pts.length - 1
  const u = c01(p) * n
  const i = Math.min(n - 1, Math.floor(u))
  const f = u - i
  const a = b.pts[i]
  const d = b.pts[i + 1]
  return { x: a.x + (d.x - a.x) * f, y: a.y + (d.y - a.y) * f }
}
/* 团心跟着枝尖走。
   早先团心钉在最终梢头、只把不透明度往后拖，于是梢身已经抽出四成、梢头还是光杆，
   那截裸枝远看就是一根刺，要等梢到位叶才出现 —— 读起来是「先插一根枝、再挂一团叶」。
   团心相对梢尖只退进 0.22·size（几个单位），所以
   当前团心 = 枝上当前点 + (最终团心 − 最终梢尖)，p=1 时正好回到最终位置。 */
function sprigLeafXform(s: SprigArt, a: { c: Cluster; level: number; b: BarkLike }) {
  const p = sprigP(s.key, a.b.birth, a.b.dur)
  const sc = sprigLeafScale(s.key, a.level, a.b)
  let x = a.c.x
  let y = a.c.y
  if (p < 1) {
    const q = branchPointAt(a.b, p)
    const tip = a.b.pts[a.b.pts.length - 1]
    x = q.x + (a.c.x - tip.x)
    y = q.y + (a.c.y - tip.y)
  }
  return `translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${sc.toFixed(3)})`
}
</script>

<template>
  <svg class="life" viewBox="0 0 720 660" role="img" aria-label="一棵树从种子长成再枯萎">
    <defs>
      <mask id="life-grow" maskUnits="userSpaceOnUse" x="-380" y="-660" width="760" height="720">
        <rect x="-380" y="-660" width="760" height="720" fill="#000" />
        <g fill="none" stroke="#fff" stroke-linecap="round">
          <path
            v-for="b in model.branches" :key="b.id" :d="b.center"
            :stroke-width="Math.max(2.2, b.drawR[0] * 2.2 * girth(b))"
            pathLength="1" stroke-dasharray="1 1"
            :stroke-dashoffset="1 - branchP(b.birth, b.dur)"
          />
        </g>
      </mask>
      <!-- 每簇新梢一根自己的生长遮罩：它不在主树的时间轴里 -->
      <mask
        v-for="s in sprigs" :id="`life-sprout-${s.key}`" :key="s.key"
        maskUnits="userSpaceOnUse" x="-380" y="-660" width="760" height="720"
      >
        <rect x="-380" y="-660" width="760" height="720" fill="#000" />
        <g fill="none" stroke="#fff" stroke-linecap="round">
          <path
            v-for="b in s.branches" :key="b.id" :d="b.center"
            :stroke-width="Math.max(2, b.drawR[0] * 2.2 * sprigGirth(s.key, b))"
            pathLength="1" stroke-dasharray="1 1"
            :stroke-dashoffset="1 - sprigP(s.key, b.birth, b.dur)"
          />
        </g>
      </mask>
    </defs>

    <g transform="translate(360 604) scale(1.72)">
      <path d="M-160 0H160" class="soil" />

      <!-- 种子：入土、裂开、消失 -->
      <g v-if="seedOut < 1" :opacity="seedIn * (1 - seedOut)">
        <ellipse cx="0" cy="-3" rx="6.2" ry="4.6" class="seed" />
        <ellipse
          v-if="seedCrack > 0" cx="0" cy="-3" rx="6.2" ry="4.6"
          class="seed" :transform="`translate(${seedCrack * 7} ${-seedCrack * 2}) rotate(${seedCrack * 16})`"
        />
        <ellipse
          v-if="seedCrack > 0" cx="0" cy="-3" rx="6.2" ry="4.6"
          class="seed split" :transform="`translate(${-seedCrack * 7} ${-seedCrack * 2}) rotate(${-seedCrack * 16})`"
        />
      </g>

      <!-- 子叶：苗期长在主干顶端，真叶出来后枯萎脱落 -->
      <g
        :opacity="seedLeaf"
        :transform="`translate(${stem.x.toFixed(1)} ${stem.y.toFixed(1)}) rotate(${stem.rot.toFixed(1)})`"
        class="cotyle"
      >
        <path d="M0 0C-7 -8 -15 -8 -18 -3C-12 1.4 -4 2 0 0Z" />
        <path d="M0 0C7 -8 15 -8 18 -3C12 1.4 4 2 0 0Z" />
      </g>

      <!-- 枝干：由下往上逐段长出来。
           停的时候不是一起变：level 越深越先褪成灰、再断开落地，主干最后结算。
           这里的副本带生长遮罩，只负责「长」和「在树上」；
           断下来的那一段在下一个层里另画一份 —— 遮罩是沿旧枝轮廓开的，
           物体一离开原位就会被它切掉，所以落的那一份不能留在遮罩里 -->
      <g class="bark" mask="url(#life-grow)" :style="{ opacity: c01((t - 0.082) / 0.05) }">
        <path
          v-for="b in model.branches" :key="b.id" :d="ribbonOf(b)"
          :style="{
            fill: barkFillOf(b.level),
            opacity: barkOpacityOf(b.level) * (1 - barkRamp(barkFall[b.id]))
              * (b.parent < 0 ? 1 - trunkFade : 1)
          }"
        />
      </g>

      <!-- 叶：一片叶就是一个运行中的实例。整团一起冒出、一起转黄缩回，
           所以外层按团推进时间，叶子各自是一个节点。
           被选作顶芽的那几团例外：叶是单片被顶开、单片落掉的，所以它们各自转、各自淡 -->
      <g
        v-for="(c, i) in arts" :key="i" class="leaf-cluster"
        :opacity="clusterVisible(c)"
        :transform="clusterXform(c)"
      >
        <path
          v-for="(l, j) in c.leaves" :key="j" :d="LEAF_UNIT"
          :transform="budLeafT(c, l, j)"
          :opacity="budLeafOpacity(c, j) * (1 - leafDetached(l))"
          :fill="budLeafFill(c, l, j)"
        />
      </g>

      <!-- 二次生长：叶实例在运行中又调了一个 routine，于是从叶团里抽出新梢 -->
      <g
        v-for="s in sprigs" :key="'s' + s.key"
        :transform="`translate(${s.ax.toFixed(2)} ${s.ay.toFixed(2)}) rotate(${s.rot.toFixed(2)})`"
      >
        <g
          class="sprout-bark" :mask="`url(#life-sprout-${s.key})`"
          :style="{
            fill: barkFillOf(LV + 1),
            opacity: barkOpacityOf(LV + 1) * (1 - barkRamp(sprigFall[s.key]))
          }"
        >
          <path v-for="b in s.branches" :key="b.id" :d="sprigRibbon(s.key, b)" />
        </g>
        <g
          v-for="(a, i) in s.arts" :key="i" class="leaf-cluster"
          :opacity="sprigLeafOpacity(s.key, a.level, a.b) * (1 - barkRamp(sprigFall[s.key]))"
          :transform="sprigLeafXform(s, a)"
        >
          <path
            v-for="(l, j) in a.c.leaves" :key="j" :d="LEAF_UNIT"
            :transform="leafT(l, a.c.x, a.c.y)"
            :fill="freshFill(tone(l.shade), witherP(a.level))"
          />
        </g>
      </g>

      <!-- 断落的枝：从细枝开始，一根根从父枝上断开、垂下去、落地。
           位置与角度都由 barkArt 按时钟算出来，和落叶同一套做法 -->
      <g class="fall-bark">
        <g
          v-for="f in barkArt" :key="f.key" :opacity="f.op.toFixed(3)"
          :transform="`translate(${f.dx.toFixed(2)} ${f.dy.toFixed(2)}) rotate(${f.rot.toFixed(1)} ${f.px.toFixed(1)} ${f.py.toFixed(1)})`"
        >
          <path v-for="(d, i) in f.ds" :key="i" :d="d" :fill="f.fill" :fill-opacity="f.base.toFixed(3)" />
          <!-- 新梢末端那团叶跟着这一枝一起走：叶本来就挂在这一枝上 -->
          <g v-if="f.spx" :transform="f.spx">
            <path
              v-for="(lf, i) in f.leaves" :key="'l' + i" :d="LEAF_UNIT"
              :transform="lf.t" :fill="lf.fill" :opacity="lf.op.toFixed(3)"
            />
          </g>
        </g>
      </g>

      <!-- 落叶：位置与角度都由 fallArt 按时钟算出来，不用 CSS 关键帧 ——
           关键帧只能整体平移加匀速旋转，飘不起来；而且 scrubbing 全靠负 delay 凑 -->
      <g class="fall">
        <path
          v-for="p in fallArt" :key="p.key"
          :d="LEAF_UNIT"
          :fill="p.fill" :opacity="p.op.toFixed(3)"
          :transform="`translate(${p.x.toFixed(2)} ${p.y.toFixed(2)}) rotate(${p.rot.toFixed(1)}) scale(${p.sx.toFixed(2)} ${p.sy.toFixed(2)})`"
        />
      </g>
    </g>
  </svg>
</template>

<style scoped>
.life { width: 100%; height: 100%; display: block; }
.soil { stroke: #2a2521; stroke-width: 1.2; }
.seed { fill: #4b3a28; }
.seed.split { fill: #3d2f20; }
.cotyle path { fill: #3d6b34; }
.bark path { fill: inherit; }
.sprout-bark path { fill: inherit; }
.fall-bark path { stroke: none; }
.leaf-cluster path { stroke: none; }
</style>
