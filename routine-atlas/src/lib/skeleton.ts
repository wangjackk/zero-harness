/* 问题① **骨架**：谁长在谁身上、从哪儿出去、朝哪、多长、多粗、走完全长要几拍。
   输入一组实例 { id, parent_id, name, state }，输出每个实例**一条线**。
   全部由拓扑与 id 决定，与时间无关，也不能随兄弟增删而变。

   问题②（给定时刻露出几成）不在这里，在 growth.ts。
   两个文件分开是因为它们回答的是两个问题，混在一起就会出现
   「时长写在组件里、几何写在库里」那种半截状态。

   ## 一个实例 = 一条线

   早先这里是两种东西：`GBranch`（有子实例）和 `GLeaf`（没有）。
   两类各有自己的几何（`spine/ribbon/pts/r0` 对 `x/y/rot/len/wid`）、
   自己的时长公式、自己的构造函数 —— 于是「一片叶长出第一个子实例」
   不是同一个东西继续长，而是把甲扔掉、在乙里重建一遍。
   两条构造路径之间只能靠对齐参数维持一致，接口处必然裂开一次
   （挂点差 12.5 单位那件事就是这么来的）。

   现在只有 `GStem`：**从父线上的着生点 `mesh` 往外伸出的一条线**。

   - 有线可延长的，`len` 是它自己的长度，画成锥形枝干；
   - 没有子实例的，`len` 只是叶柄那么长，线头挂一片 `blade`，画成叶。

   两者共用同一个 `mesh`、同一个 `at`、同一个 `dur` 公式、同一个构造函数。
   叶变枝因此不是换类型，是**同一条线从同一个着生点继续长**：
   `blade` 收掉，`len` 从几单位变成几十单位，起点没动过。
   `leaf` 这个概念只是「这条线此刻有没有挂叶片」，不是另一个对象。

   ## 一条 routine 在树上只有一种形态

   有子实例的是枝，没有子实例的是叶。早先写成「一条枝是一条调用边、
   子实例是梢头那片叶」，于是同一个 routine 被画了两次（一条枝 + 一片自己的叶），
   读图的人自然要问「枝、根、叶怎么都是一个 routine」。

   **这一步是单向的**：长出过子实例的实例永远是枝，子实例结束只是这条线
   上暂时没有叶了，线本身还在。判据因此是 `kids || 长出过`，不是 `kids`
   —— 只看当前拓扑的话，子实例一结束，同一个 routine 就从枝缩回叶，
   画面上是一根枝退化成了叶。这条历史由调用方提供（`BuildOptions.branched`），
   因为骨架只看得见当前拓扑。

   ## 稳定性：几何只跟自己的 id 和父线有关

   实例树在运行中是持续增删的。早先按「子树负载」分配扇区与粗细 ——
   每来一个节点，兄弟的扇区宽度、归一化分母、整棵树的粗细全都变，
   于是整棵树每一步都在重排：画面上是树一直在轻微地摇、缩放。
   现在每个节点的挂点、分叉角、长度、半径全部由**自己的 id** 决定，
   父线算完就不会再变；新节点只是多长一条，已有的线一个像素都不动。

   画法（锥形轮廓、叶片形状、叶变换）不在这里实现，见 tree.ts / treeArt.ts ——
   这里只解决一件 tree.ts 不解决的问题：/life 的形态由 growTree 按固定分叉因子
   和种子生成，而运行时拓扑的深度、每层子数、名字都是随机的，只能按真实父子算。

   骨架里没有绝对时间：`dur` 只是「这条线自己要走几拍」，
   起点来自实例被看见的那一刻，由消费者接上真实时钟，
   所以同一份骨架可以被反复重画，也可以在任意一帧停下。 */

import { ribbonPath, smooth, type Pt } from './tree'
import { leafT, LEAF_UNIT, LEAF_W } from './treeArt'

/* 单位叶片的**实际**包围盒宽度。`leafPath` 的注释写着 x ±0.525，
   但那是控制点 —— 贝塞尔曲线走不到，实测左右只到 ±0.30，宽度 0.603。
   `leafT` 里是 `scale(len, wid / LEAF_W)`，第一个参数缩放的是 x（宽）、
   第二个才是 y（长）。照字面填 (长, 宽)，画出来就是一片**比长度还宽**的叶子：
   在 /life 里几百片挤成叶团看不出来，这里一片叶就是一个单元，一眼是块鹅卵石。 */
const LEAF_UNIT_W = 0.603

export type { Pt }

/** 挂在一条线头上的叶片。它是这条线的**视图**，不是另一个节点。
 *  `leafPath` 的原点在叶柄末端、叶尖沿 −y，所以 (x, y) 就是叶柄的另一头。 */
export interface Blade {
  /** 叶片基部 = 叶柄末端。从这儿沿 rot 张开 */
  x: number
  y: number
  /** 叶尖绝对角，单位是度（`leafT` 的 rotate 口径） */
  rot: number
  /** `leafT` 的 x 缩放 = 叶宽 */
  len: number
  /** `leafT` 的 y 缩放 = 叶长 × LEAF_W */
  wid: number
  /** 明暗 0 背光 / 1 受光 */
  shade: number
}

export interface GStem {
  /** 这个实例自己 */
  id: string
  name: string
  state: string
  /** 父实例 id；根为空 */
  parent: string
  depth: number

  /** 挂在父线上的哪个位置（0–1）。子线要等父线长到这儿才出现。
   *  叶和枝取的是**同一个值**（`attachOf`）：叶变枝时那一点不能挪。
   *  取值只在**中段**（约 0.19–0.81）：侧生分叉，父枝在分叉之上继续延伸 */
  at: number
  /** 着生点：父线上的那一点。叶柄的另一头、枝根的锚都是它 */
  mesh: Pt
  /** 线的起点。枝从 `mesh` 沿父方向回缩一点（喇叭口盖住接头），叶就是 `mesh` */
  base: Pt
  /** 线的末端：枝是梢头，叶是叶柄末端（叶片就从这儿张开） */
  tip: Pt
  /** 这条线自己的出射方向，弧度 */
  angle: number
  /** 中心线采样点。枝用它插值出子线的挂点，叶就是 `mesh → 柄端` */
  pts: Pt[]
  /** 中心线各点的半径：子线挂点的粗细从这里插值，所以必须留着 */
  radii: number[]
  /** 线长。枝 = 它自己的长度；叶 = 叶柄长度（叶片另有 `blade`） */
  len: number
  /** 基部半径 */
  r0: number
  /** 把这条线从 0 抽到全长要多久，单位是「拍」。
   *  **一条公式管到底**：`clamp(可见尺寸 / 主干长, 0.65, 1.8)`。
   *  与长度成正比 → 整棵树上任何一条线的抽长速度都一样；
   *  0.65 拍是短线的可辨认下限：约 364ms，避免叶片和深层枝一闪而过。 */
  dur: number

  /** 中心线 path，两用：遮罩描边与点击热区 */
  spine: string
  /** 锥形轮廓，可见枝干 */
  ribbon: string

  /** 此刻的子实例个数 */
  kids: number
  /** 长在这条线上的子线，先序 */
  stems: GStem[]
  /** 这条线上挂着的叶片。没有 = 这条线已经分叉，只画枝干 */
  blade: Blade | null
  /** 标签锚点 */
  label: Pt
}

export interface Skeleton {
  /** 所有实例，先序（父一定在子前面）。叶和枝都在里面 —— 它们本来就是同一个类型 */
  stems: GStem[]
  /** 一个 id 一次查到：枝、叶、根都不必再分两张表 */
  byId: Map<string, GStem>
  box: { x: number; y: number; w: number; h: number }
}

export interface GrowthNode {
  id: string
  parent_id: string
  name: string
  state: string
}

export interface BuildOptions extends Partial<GrowthOptions> {
  /** 长出过子实例的 id。
   *
   *  形态只有一种判定口径：「有子实例的是枝，没有的是叶」。而子实例是会结束的，
   *  结束之后这条线的 `kids` 就空了 —— 只看当前拓扑，它就得变回叶。
   *  画面上是「一根枝缩成一片叶」，而它明明已经调用过了。
   *
   *  所以骨架的输入不是「当前拓扑」，是「当前拓扑 + 谁长出过子实例」：
   *  这条历史不是组件的私事，它是形态的判据之一，和 kids 一起决定枝还是叶。
   *  只要这一项在，子实例走光之后留下的是一条**空枝**（还有线、暂时没叶）。 */
  branched?: ReadonlySet<string>
}

export interface GrowthOptions {
  /** 主干长度 */
  height: number
  /** 主干基部半径 */
  width: number
  /** 每层长度衰减 */
  decay: number
  /** 子枝分叉角的基准值，弧度 */
  spread: number
  /* 下面三项跟 tree.ts 的 DEFAULT_TREE 同口径：叶怎么画由树那边定，
     这里只是把它搬到运行时拓扑上，不要另立一套尺寸 */
  /** 叶长区间 */
  leafSize: [number, number]
  /** 叶长宽比 */
  leafAspect: number
  /** 叶团直径：叶绕梢头散开的范围由它派生 */
  clusterSize: number
}

export const DEFAULT_GROWTH: GrowthOptions = {
  /* 主轴基准，不是整棵树的高度：主干取它的 0.2（= 84 单位），
     一级枝比主干还长一点，成树一百五十上下，正好装进 HUD 那个方画框。
     曾经按 300 给 —— 整棵树才一百多单位，一片叶占了树高的十分之一，
     叶怎么调都是贴上去的大块。 */
  height: 420,
  /* 主干粗细照 tree.ts 的 trunkWidth / widthDecay 等比压下来。
     2.9 的基部半径配 84 的主干已经够「木」，再粗就是一截电线杆 */
  width: 2.9,
  /* 每层收七成：三层之后梢枝剩三分之一，树冠才有内外层次。
     给到 0.82 时每一级都跟父枝差不多长，画出来是一把往外伸的扫帚 */
  decay: 0.7,
  spread: 0.82,
  leafSize: [9, 13],
  leafAspect: 2.2,
  clusterSize: 22,
}

const UP = -Math.PI / 2
const D2R = Math.PI / 180
const TAU = Math.PI * 2

const clamp = (v: number, a: number, b: number) => (v < a ? a : v > b ? b : v)
const lerp = (a: number, b: number, t: number) => a + (b - a) * t
/** 角差折到 (−π, π]：直接相减会顺着「绕远的那一边」转过去 */
const wrapPi = (d: number) => {
  let x = d % TAU
  if (x > Math.PI) x -= TAU
  if (x <= -Math.PI) x += TAU
  return x
}
const smooth01 = (t: number) => {
  const x = clamp(t, 0, 1)
  return x * x * (3 - 2 * x)
}

/** id → [0,1)，给挂点、角度、叶位用。同一个 id 每次都一样，不随兄弟增删跳动 */
function hash(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 100000) / 100000
}

/* 挂点：一条线只认**自己的 id**。
   一片叶一旦调用出第一个子实例，它就从叶变成枝 —— 位置必须原地不动，
   否则看着是「叶掉了，别处长出一根线」。
   早先按 i / (k-1) 均分，兄弟多一个就全体挪位；改成 id 哈希之后，
   增删兄弟不再影响任何人，这棵树在运行中才是稳的。

   **侧生，不是顶部分叉。** 真树的枝大多从父枝中段的腋芽出来，
   父枝自己的顶芽继续往前长；只有顶部分叉才是「一根枝走到头，分成两股」。
   早先五档是 0.30 / 0.47 / 0.64 / 0.81 / 0.98：均值 0.64，最高一档贴着梢头，
   挂在那儿的子枝一多，看上去就是每根枝都在末端开叉 —— 那不是侧生。
   现在五档落在 0.22–0.78（含档内散差约 0.19–0.81），均值 0.50：
   分叉集中在**中段**，分叉点之上父枝始终还有自己那一段路。
   这个改动只动分布，不动「由 id 决定」这条规则：同一条线的 at 因此永远不变，
   叶↔枝的接缝才不会又裂开（挂点若按「是不是枝」分别取，叶变枝时就会挪窝）。

   分五档而不是连续值：几个 id 相近的叶落在同一档，就成了一个叶簇
   （见 growBlade），枝也从这一档分出去 —— 叶变成枝时，新线就长在原来那簇叶里。
   连续值时每片叶各占一个高度，只能沿枝排成一列，叠成一串鳞片。

   档内再按 id 散开 ±3.5%：同一档、同一侧的两条线若从同一点出去，
   远看是一根枝分了个叉，其实是两个并列的子实例，根部叠在一起。 */
const TUFT_U = [0.22, 0.36, 0.5, 0.64, 0.78]
function attachOf(id: string): number {
  const u = TUFT_U[Math.min(4, Math.floor(hash(id + 'tf3') * 5))]
  /* 上限 0.84 是保险，正常到不了（最高档 + 散差 = 0.815）：
     宁可让散差吃掉一点，也不允许任何一条子线贴到父线梢头上 */
  return clamp(u + (hash(id + 'aj') - 0.5) * 0.07, 0.16, 0.84)
}

function cmpId(a: string, b: string): number {
  const na = Number(a)
  const nb = Number(b)
  if (Number.isFinite(na) && Number.isFinite(nb)) return na - nb
  return a < b ? -1 : a > b ? 1 : 0
}

/* 往哪边分叉。数字 id（kernel 里就是递增的）按奇偶分左右 ——
   同一层相邻的两个子实例一定分到相反的两边。
   早先只用哈希：一层只有四五个子节点时经常全挤到同一边，
   整棵树歪成一个方向，阴影像被剃掉半边。 */
function sideOf(id: string): number {
  const n = Number(id)
  if (Number.isFinite(n) && String(n) === id.trim()) return n % 2 === 0 ? 1 : -1
  return hash(id + 'sd') > 0.5 ? 1 : -1
}

interface Raw extends GrowthNode {
  kids: Raw[]
  depth: number
}

/** `stemOf` 的全部入参。枝和叶都产出这一个元组 ——
 *  两条构造路径往同一个构造函数里交，没有第二个出口。 */
type StemArgs = [
  Raw, // 节点
  Pt, // mesh 着生点
  Pt, // base
  Pt, // tip
  number, // angle
  Pt[], // pts
  number[], // radii
  number, // r0
  number, // len
  number, // at
  Blade | null, // 叶片
  number, // 计算 dur 用的可见尺寸
]

/**
 * 按拓扑算一棵树。
 * 每个节点的挂点、角度、长度、半径只由自己的 id 与父线决定 ——
 * 增删节点时已有的线不动，只有新线在原地长出来。
 */
export function buildGrowth(nodes: GrowthNode[], over: BuildOptions = {}): Skeleton {
  const { branched, ...shape } = over
  const o: GrowthOptions = { ...DEFAULT_GROWTH, ...shape }
  /* 曾经长出过子实例的，一律算枝。`kids` 只说明「此刻有没有子实例」，
     不说明「是不是枝」—— 子实例会结束，枝不会因此退回成叶。
     落在骨架里而不是组件里，是因为它跟 kids 一起决定形态，
     少了它骨架就不是拓扑的函数，而是「拓扑 + 一次运气」。 */
  const wasBranch = (id: string) => !!branched && branched.has(id)
  const known = new Set(nodes.map(n => n.id))
  const raw = new Map<string, Raw>()
  for (const n of nodes) raw.set(n.id, { ...n, kids: [], depth: 0 })

  const roots: Raw[] = []
  for (const n of raw.values()) {
    const pid = n.parent_id
    if (!pid || pid === '0' || pid === '-1') {
      roots.push(n)
      continue
    }
    const p = raw.get(pid)
    /* 父已经结束：这棵子树跟着不在（父停子停），不单独画。
       当根处理会让它在根部凭空长出一条，整棵树错位。 */
    if (p) p.kids.push(n)
  }
  if (!known.size || !roots.length) {
    return { stems: [], byId: new Map(), box: { x: 0, y: 0, w: 1, h: 1 } }
  }
  roots.sort((a, b) => cmpId(a.id, b.id))
  const walkW = (n: Raw, d: number) => {
    n.depth = d
    n.kids.sort((a, b) => cmpId(a.id, b.id))
    for (const k of n.kids) walkW(k, d + 1)
  }
  roots.forEach(r => walkW(r, 0))

  const stems: GStem[] = []
  const byId = new Map<string, GStem>()

  /* 一条线的时长只由**可见尺寸**决定：主干长走一拍。
     枝和叶共用 0.65 拍下限（≈364ms）：再短的线也经过一段可读的生长，
     不以「已经很小」为理由直接闪现。 */
  const durOf = (size: number) => clamp(size / (o.height * 0.2), 0.65, 1.8)

  /** 唯一一处构造 `GStem`：枝和叶都在这里落地 */
  function stemOf(a: StemArgs): GStem {
    const [n, mesh, base, tip, angle, pts, radii, r0, len, at, blade, size] = a
    const n1 = pts.length - 1
    /* 标签：叶贴在自己的叶尖外侧，枝贴在中段侧面 —— 兄弟枝的梢头本来就挨得近，
       名字全堆在那儿会连成一团字。 */
    let label: Pt
    if (blade) {
      const dir = angle
      label = { x: blade.x + Math.cos(dir) * 7, y: blade.y + Math.sin(dir) * 7 }
    } else {
      const side = hash(n.id + 's') > 0.5 ? 1 : -1
      const li = clamp(Math.round(n1 * 0.7), 1, n1)
      const lp = pts[li]
      const ldir = Math.atan2(
        pts[Math.min(n1, li + 1)].y - pts[Math.max(0, li - 1)].y,
        pts[Math.min(n1, li + 1)].x - pts[Math.max(0, li - 1)].x
      )
      const loff = r0 + 8
      label = {
        x: lp.x + Math.cos(ldir + Math.PI / 2) * loff * side,
        y: lp.y + Math.sin(ldir + Math.PI / 2) * loff * side,
      }
    }

    const s: GStem = {
      id: n.id,
      name: n.name,
      state: n.state,
      parent: n.parent_id,
      depth: n.depth,
      at,
      mesh,
      base,
      tip,
      angle,
      pts,
      radii,
      len,
      r0,
      dur: durOf(size),
      spine: smooth(pts),
      ribbon: ribbonPath(pts, radii),
      kids: n.kids.length,
      stems: [],
      blade,
      label,
    }
    stems.push(s)
    byId.set(s.id, s)
    for (const c of n.kids) stemOf(placement(s, c))
    return s
  }

  /* 子线长在父线的 `at` 处。叶与枝走的是**同一个** `placement`：
     着生点、父方向、此处半径都只算一遍，所以「叶变枝时那一点不挪」
     不是靠两条路径对齐，是没有第二个值。 */
  function placement(par: GStem, c: Raw): StemArgs {
    const at = attachOf(c.id)
    const pts = par.pts
    const n1 = pts.length - 1
    /* 挂点取父线中心线上 `at` 处的那一点 —— **插值，不取最近的采样点**。
       取整的话，档内 ±3.5% 的散差会被采样网格吞掉（网格比它粗一个量级），
       同档同侧的两条线根部还是叠在一起；而且叶变枝那一下会整个跳一格，
       实测最远 12.5 单位（母线才 40 长），读起来就是「叶掉了，旁边长出一根线」。 */
    const i0 = Math.min(n1 - 1, Math.max(0, Math.floor(at * n1)))
    const f = clamp(at * n1 - i0, 0, 1)
    const a0 = pts[i0]
    const a1 = pts[i0 + 1]
    const mesh: Pt = { x: a0.x + (a1.x - a0.x) * f, y: a0.y + (a1.y - a0.y) * f }
    const parentDir = Math.atan2(a1.y - a0.y, a1.x - a0.x)
    /* 半径在同一处插值：取整会让叶与枝的「粗细口径」差半格，
       基部半径不一致时，新线的喇叭口与父线的接触面看着错位 */
    const rHere = par.radii[i0] + (par.radii[i0 + 1] - par.radii[i0]) * f

    /* 没有子实例、也从来没长出过子实例的，才是叶。长出过的那几个
       此时只是「子实例都结束了」，仍按枝画（见 BuildOptions.branched）。 */
    if (!c.kids.length && !wasBranch(c.id)) {
      return growBlade(c, mesh, at, parentDir, par.len, rHere)
    }

    /* 分叉朝外：右半边往右、左半边往左，整棵树才张开成树冠而不是扭成绳。
       主干上（x≈0）按 id 奇偶分左右；三成故意朝内，免得树冠空心 */
    const out = mesh.x > 4 ? 1 : mesh.x < -4 ? -1 : 0
    const side2 = out === 0 ? sideOf(c.id) : hash(c.id + 'in') < 0.3 ? -out : out
    const spread = o.spread * (0.74 + 0.52 * hash(c.id + 'sp')) * (par.depth === 0 ? 0.9 : 1)
    const ang = parentDir + side2 * spread
    /* 长度按挂点高度走一条驼峰：中段最长，靠近根部的短一点。
       早先是 `1.08 - 0.36·at`，挂在**最低处**的枝最长 —— 最长最外的两臂
       长在树冠根部，轮廓就成了下宽上尖的 /|\ 。
       反过来把最长的一段抬到中段，树才是向上张开的 \|/ 。
       挂点改成侧生（0.22–0.78）之后，驼峰的峰正好落在分叉最密集的中段：
       这也是真树的样子 —— 中段的主侧枝最壮，越往梢头新枝越短。 */
    const vigour = 0.84 + 0.26 * Math.sin(Math.PI * clamp(at, 0, 1))
    /* 长度从父线递推，一级一级收，不改特殊基准：给一级枝单独放大之后，
       一米长的两臂把整棵树撑成一把横着张开的伞，主干显得更短 */
    const len = Math.max(
      14,
      par.len * (par.depth === 0 ? 1.3 : o.decay) * vigour * (0.84 + 0.32 * hash(c.id + 'ln'))
    )
    /* 粗细按父线收，也是一个 id 决定一次，不随全树节点数变 */
    const nextR = Math.max(0.5, par.r0 * (0.58 + 0.12 * hash(c.id + 'rd')))
    return growAxis(c, mesh, parentDir, ang, len, Math.min(rHere * 0.8, nextR), rHere, at)
  }

  /* 叶：一条很短的线（叶柄）+ 线头上一片叶片。
     叶柄的方向由「叶片基部落在团里哪」反推，叶片自己另有朝向
     （叶尖朝团外再折回竖直）—— 叶柄与叶脉不同向本来就是常事。 */
  function growBlade(
    c: Raw,
    mesh: Pt,
    at: number,
    parentDir: number,
    hostLen: number,
    hostR: number
  ): StemArgs {
    const h2 = hash(c.id + 'lv')
    const h3 = hash(c.id + 'ag')
    const h4 = hash(c.id + 'sh')
    /* 叶成「簇」而不是沿枝排成一列。
       同一档挂点上的几片叶共享一个团心，各自朝团外张开 —— 这是 tree.ts
       `clusterAt` 的做法，也是这棵树唯一能读成树冠的排法：
       沿枝一列排开时，叶只能贴着枝平铺，一簇叶子互相压住，
       远看是一条长满鳞片的绿绳。
       团内的位置角 `ang` 同时决定叶尖方向（叶尖朝团外），
       所以叶不会全都朝同一边，也不会有两片完全重叠。 */
    const ang = hash(c.id + 'ta') * TAU
    const spill = hash(c.id + 'ts') < 0.2 ? 1.2 + 0.35 * hash(c.id + 'tp') : 1
    /* 团开多大看宿主线有多长：一根二十来单位的末梢线顶着同样的 22，
       叶比线还宽，梢头就成了插在枝上的大绿片。短线小团、长线满团，
       树冠才有内外层次 —— 跟 tree.ts 按层级收团是同一件事，
       只是运行时没有「层」这个稳定的东西，用长度更直接。 */
    const tuft = o.clusterSize * clamp(hostLen / 70, 0.5, 1.05)
    const foot = tuft * 0.2 * Math.pow(hash(c.id + 'tc'), 0.7) * spill
    const lu = Math.cos(ang) * foot
    /* 团心上的叶不能落进枝身里，否则一半叶子被枝挡住：沿法线推到枝面外 */
    const lim = hostR + 0.8
    let lv = Math.sin(ang) * foot * 0.6
    if (Math.abs(lv) < lim) lv = lv < 0 ? -lim : lim
    /* 叶径照 tree.ts：leafSize 9–13、宽 = 长 / leafAspect。
       早先按枝长给（clamp(len*0.62, 16, 58)），主干每一级都顶到上限，
       整棵树挂满「一片叶占树高两成」的大叶 —— 那不是把叶画对了，
       是把画布上的叶放大了。 */
    const l = lerp(o.leafSize[0], o.leafSize[1], h2) * (0.78 + h4 * 0.36)
    const nx = Math.cos(parentDir + Math.PI / 2)
    const ny = Math.sin(parentDir + Math.PI / 2)
    const tip: Pt = {
      x: mesh.x + Math.cos(parentDir) * lu + nx * lv,
      y: mesh.y + Math.sin(parentDir) * lu + ny * lv,
    }
    /* 叶尖朝团外（dir + ang），再往竖直方向偏一成：整簇朝光张开，
       但不会像 `clusterAt` 那样有一半叶子朝下 */
    const a = parentDir + ang + wrapPi(UP - (parentDir + ang)) * 0.12 + (h3 - 0.5) * 0.26
    const rot = a / D2R + 90 + (h3 - 0.5) * 16
    /* 叶尖沿 −y，所以 `leafT` 的第二个参数是**长度**、第一个是宽度。
       填反了就是把叶子横过来画：长 13 的叶会变成宽 13、长 5.4 的一颗豆 */
    const blade: Blade = {
      x: tip.x,
      y: tip.y,
      rot,
      len: l / o.leafAspect / LEAF_UNIT_W,
      wid: l * LEAF_W,
      /* 光从右上来：右侧亮、高处亮，内膛暗。与受光无关的平均绿看着像贴纸。
         一片叶自己的随机量给到 ±0.34：同一簇里相邻两片常常差一档，
         树冠才有明暗斑驳；早先只差 ±0.14，一簇叶是一个平均色的绿块。

         分母按**这棵树**的尺寸算，不是 `o.height`：o.height 只是主轴基准，
         成树只有一百多单位，`-y / o.height` 永远接近零 —— 位置项白算，
         shade 退化成「基准 ± 随机」，三档叶按均匀分布撒，
         四成落进最暗一档，在深色底上等于没有叶子。 */
      shade: clamp(
        0.56 + (tip.x / (o.height * 0.2)) * 0.16 + (-tip.y / (o.height * 0.31)) * 0.2 +
          (h4 - 0.5) * 0.68,
        0,
        1
      ),
    }
    const dir = Math.atan2(tip.y - mesh.y, tip.x - mesh.x)
    const len = Math.hypot(tip.x - mesh.x, tip.y - mesh.y)
    /* 叶柄半径按宿主线的粗细给一点，不然细枝上挂着一根粗柄 */
    const r0 = clamp(hostR * 0.35, 0.35, 0.7)
    /* 三个采样点（而不是两个）：`smooth` 要的是折线，两点会退化成退化曲线。
       中间那点是连线中点，所以叶柄是直的 —— 它是一个柄，不是一段枝 */
    const mid: Pt = { x: (mesh.x + tip.x) / 2, y: (mesh.y + tip.y) / 2 }
    return [
      c,
      mesh,
      mesh,
      tip,
      dir,
      [mesh, mid, tip],
      [r0, r0, r0 * 0.8],
      r0,
      len,
      at,
      blade,
      /* 叶的可见尺寸是叶片长度，不把叶柄误当成整片叶。
         这个量级的线都会落到 0.65 拍下限：优先保证出现过程可读。 */
      l,
    ]
  }

  /* 枝：从着生点沿 `angle` 抽出去，根部回缩进父线盖住接头，
     一路有向光牵引、下垂、抖动，梢头收细。 */
  function growAxis(
    c: Raw,
    mesh: Pt,
    parentDir: number,
    angle: number,
    len: number,
    r0: number,
    rHere: number,
    at: number
  ): StemArgs {
    const segs = clamp(10 - c.depth, 5, 10)
    const rnd = hash(c.id)
    const phase = rnd * TAU
    const wob = 0.036 + (rnd - 0.5) * 0.02
    const pts: Pt[] = []
    /* 根部一圈略放大：像扎进土里，不像插了根管子。
       梢头收得比基部细得多，否则整根枝看着像一根等粗的棍。 */
    const r0r = c.depth === 0 ? r0 * 1.22 : r0
    const tipR = Math.max(0.55, r0 * 0.4)
    /* 水平枝垂一点、朝上的枝不怎么垂：真树枝条都不是从父枝上一路直着长出去的 */
    const droop = clamp(0.15 + Math.sin(angle) * 0.13, 0.03, 0.24)
    /* 往竖直方向的牵引：真树枝条都有向光性，一根枝越往外走越抬头。
       没有这一项，每一级分叉都在父线方向的基础上再偏一个 spread，
       三代之后枝已经走到水平线以下 —— 树冠摊成一把低垂的伞，也是 /|\ 。
       但也不能像早先那样按 0.44 一路拉到底：那样每根枝走完都几乎竖直，
       一树的枝全往上收，冠收成一个尖。取三分之一，且留一条硬约束：枝不朝下长。 */
    const flare = (UP - angle) * 0.34
    /* 离竖直最多 64°。超出的部分夹住 —— 光靠牵引挡不住「一代一代越偏越多」，
       而不夹的时候最外一层会全挤在水平线附近，摊成一片扁扇 */
    const MAXDEV = 1.12

    /* 根部回缩：让喇叭口盖住与父线的接头。它属于「长在枝上」，
       不属于「挪窝」—— 着生点 `mesh` 一个字没动（`at` 与 `mesh` 由 `placement`
       统一给出，叶和枝拿的是同一个值） */
    const back = Math.min(2.2, rHere * 0.8)
    const base: Pt = {
      x: mesh.x - Math.cos(parentDir) * back,
      y: mesh.y - Math.sin(parentDir) * back,
    }
    pts.push(base)
    const radii: number[] = [r0r]
    let cur = base
    const step = len / segs
    for (let i = 1; i <= segs; i++) {
      const t = i / segs
      const a = clamp(
        /* 前段顺着父线方向转出去 */
        lerp(parentDir, angle, smooth01(t / 0.22)) +
          flare * smooth01(t) -
          droop * 0.16 * Math.sin(Math.PI * clamp(t * 0.9, 0, 1)) +
          Math.sin(t * Math.PI * 1.6 + phase) * wob,
        UP - MAXDEV,
        UP + MAXDEV
      )
      cur = { x: cur.x + Math.cos(a) * step, y: cur.y + Math.sin(a) * step }
      pts.push(cur)
      /* 梢头收细但不归零：末级线的 r0 本来就只有一像素，
         再乘一个系数会细成一根针，从叶簇里戳出来 */
      const rr = Math.max(0.5, lerp(r0, tipR, Math.pow(t, 0.7)) * (i === segs ? 0.6 : 1))
      radii.push(rr)
    }
    return [c, mesh, base, cur, angle, pts, radii, r0, len, at, null, len]
  }

  /* 根层：多根时并排站着，各自略微朝外。都从 (0,0) 长出去会叠成一束 */
  const rn = roots.length
  roots.forEach((r, i) => {
    const off = rn > 1 ? (i - (rn - 1) / 2) * 46 : 0
    const ang = UP + (rn > 1 ? (i - (rn - 1) / 2) * 0.17 : 0)
    /* 主干只当一段茎，不占满高度。早先主干比一级枝还长，画出来就是
       一根杆子顶着两根枝 —— 那是 /|\ 的一竖。现在一级枝比主干长三成，
       主干大约是整棵树高的四成，轮廓才是「一段竖直的茎 + 往上张开的枝」= \|/ 。
       也不能太短：叶（没有子的子实例）大量长在主干上，主干短了它们会挤成一簇。 */
    const len = o.height * 0.2
    const mesh: Pt = { x: off, y: 0 }
    const leaf = !r.kids.length && !wasBranch(r.id)
    /* 系统里只有一个单元、它还没调用任何东西：土里一片叶就是全部。
       它也是同一条线 —— 只是这条线上没有子线，线头挂着一片叶 */
    const args = leaf
      ? growBlade(r, mesh, 0, ang, len, 0)
      : growAxis(r, mesh, ang, ang, len, o.width * (rn > 1 ? 0.82 : 1), o.width * 0.8, 0)
    stemOf(args)
  })

  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const s of stems) {
    for (const p of [s.base, s.tip]) {
      if (p.x < x0) x0 = p.x
      if (p.x > x1) x1 = p.x
      if (p.y < y0) y0 = p.y
      if (p.y > y1) y1 = p.y
    }
    if (!s.blade) continue
    /* 叶的包围盒按 0.7 倍宽算：叶是斜的，用全长四周各撑一圈会把画面放大一截，
       树就缩在中间 */
    const e = s.blade.len * 0.7
    const bx = s.blade.x
    const by = s.blade.y
    if (bx - e < x0) x0 = bx - e
    if (bx + e > x1) x1 = bx + e
    if (by - e < y0) y0 = by - e
    if (by + e > y1) y1 = by + e
  }
  if (!Number.isFinite(x0)) {
    x0 = y0 = 0
    x1 = y1 = 1
  }

  return {
    stems,
    byId,
    box: { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) },
  }
}

/* 单位叶片的 path 与叶变换都在 treeArt.ts；这里只给一个不带原点的写法，
   因为本骨架的叶坐标已经是绝对的。
   `s` 是绕**叶柄**的缩放（treeArt 的 `leafT` 的 ex.s 就是这个），
   叶展开时用它从柄端张开，而不是原地淡入。 */
export { LEAF_UNIT, LEAF_W }
export function leafAt(s: GStem, k = 1): string {
  const b = s.blade
  if (!b) return ''
  return k === 1 ? leafT(b, 0, 0) : leafT(b, 0, 0, { s: k })
}
