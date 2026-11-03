/* 通用 2D 树生长算法。
   只有几何，没有时间：给一组参数和种子，吐出一棵确定的树（枝干中心线、锥形轮廓、叶簇）。
   生长与枯萎的时序由调用方按 birth / dur 推进，所以同一棵树可以被反复重播，也可以停在任意时刻。

   形态取合轴分枝的落叶乔木：
   - 主干一路向上，侧枝从主干不同高度斜出；
   - 每条枝沿长度做方向插值：梢头朝外张，末端再向上翘（向光），所以枝是弧线不是直线；
   - 叶子不沿枝散撒，而是聚成团挂在末梢，团与团之间留空，远看才有树冠的体块感。 */

import { clamp, lerp } from './anim'

export interface Pt {
  x: number
  y: number
}

export interface TreeOptions {
  /** 同一个种子必得同一棵树 */
  seed: number
  /** 主干长度，px */
  height: number
  /** 主干基部宽度，px */
  trunkWidth: number
  /** 分叉层数：0 只有主干 */
  levels: number
  /** 子枝相对父枝的分叉角，度 */
  angle: [number, number]
  /** 每条枝生几个孩子 */
  children: [number, number]
  /** 子枝长度相对父枝的衰减 */
  lengthDecay: [number, number]
  /** 每层宽度相对父枝的衰减 */
  widthDecay: number
  /** 梢头向光上翘的比例：0 一直朝外，1 完全竖直 */
  bend: number
  /** 沿枝的中低频摆动，让它不笔直 */
  wobble: number
  /** 主干细分段数 */
  segments: number
  /** 从第几层开始挂满尺寸的团（更浅的层也挂，只是小一号的顶芽） */
  leafFrom: number
  /** 每个叶团里放几片 */
  cluster: [number, number]
  /** 叶片长度，px */
  leafSize: [number, number]
  /** 叶片长宽比 */
  leafAspect: number
  /** 叶团半径，px */
  clusterSize: number
  /** 枝数上限。调参台上参数可以被拖到极端（5 层 × 5 个孩子），
      不设上限会一次生成几万条枝；正常生成用不到，所以留空表示不限 */
  maxBranches?: number
}

export const DEFAULT_TREE: TreeOptions = {
  seed: 7,
  height: 300,
  trunkWidth: 14,
  levels: 3,
  angle: [38, 70],
  children: [3, 4],
  lengthDecay: [0.66, 0.8],
  widthDecay: 0.58,
  bend: 0.26,
  wobble: 0.3,
  segments: 9,
  leafFrom: 2,
  /* 一片叶就是一个运行中的实例，所以叶要少到能数、大到看得出是单独一片：
     每个团三五片，整树一百来片。早先每团三五十片，看着是树冠，数不清是实例。 */
  cluster: [2, 3],
  leafSize: [14, 20],
  leafAspect: 2.2,
  clusterSize: 22
}

export interface LeafNode {
  x: number
  y: number
  /** 度 */
  rot: number
  len: number
  wid: number
  /** 明暗档：0 背光 … 1 受光 */
  shade: number
  /** 出现时间 0–1 */
  birth: number
  /** 属于哪条枝 */
  owner: number
}

export interface BranchNode {
  id: number
  level: number
  parent: number
  /** 中心线采样点，首点在基部，末点在梢头 */
  pts: Pt[]
  /** 每个采样点的半径 */
  radii: number[]
  /** 中心线 path，用于描边与生长遮罩 */
  center: string
  /** 左右边界围成的锥形，用于填充 */
  ribbon: string
  /** 填充轮廓实际用的半径（主干根部外扩成根盘）。
      生长中枝还要继续变粗，调用方按比例缩放这份半径重算轮廓，不用自己再处理根盘 */
  drawR: number[]
  /** 该枝占用的时间片 */
  birth: number
  dur: number
  length: number
  tip: Pt
  leaves: LeafNode[]
}

/** 一个叶团：同一簇叶子一起出现、一起枯萎，所以整团当一片来推进时间 */
export interface Cluster {
  x: number
  y: number
  size: number
  /** 团所在枝梢的方向，度。叶实例要再抽梢时从这里接出去 */
  dir: number
  /** 团所在的高度（0 底 1 顶），枯萎时高处先变色 */
  level: number
  /** 出现时间 0–1（相对整棵树的生长窗口） */
  birth: number
  owner: number
  /** 团心在所属枝上的参数位置：0 基部、1 梢头。
      芽里就揉着叶，枝往哪长团就跟到哪 —— 调用方按它算出「此刻团心在枝上的何处」，
      而不是等枝长到终点再把一团叶摆上去。中段团因此也要记下自己在枝上的位置 */
  u: number
  leaves: LeafNode[]
}

export interface TreeModel {
  branches: BranchNode[]
  leaves: LeafNode[]
  clusters: Cluster[]
  branchCount: number
  leafCount: number
  /** 树冠包围盒，供布局用 */
  box: { x: number; y: number; w: number; h: number }
}

/* 定长随机流：同一个 seed 永远同一棵树 */
function mulberry32(seed: number) {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const TAU = Math.PI * 2
const UP = -Math.PI / 2
const D2R = Math.PI / 180
/** 0–1 之间的平滑过渡，超出范围夹住 */
const smooth01 = (x: number) => {
  const c = clamp(x, 0, 1)
  return c * c * (3 - 2 * c)
}

/** 折线转平滑 path：二次贝塞尔穿过各点中点。
    对外也用它画枝的中心线（遮罩描边与点击热区都走这条路径）。 */
export function smooth(pts: Pt[]): string {
  if (pts.length < 2) return ''
  let d = `M${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`
  for (let i = 1; i < pts.length - 1; i++) {
    const mx = (pts[i].x + pts[i + 1].x) / 2
    const my = (pts[i].y + pts[i + 1].y) / 2
    d += `Q${pts[i].x.toFixed(2)} ${pts[i].y.toFixed(2)} ${mx.toFixed(2)} ${my.toFixed(2)}`
  }
  const last = pts[pts.length - 1]
  d += `L${last.x.toFixed(2)} ${last.y.toFixed(2)}`
  return d
}

/** 沿中心线按法线向两侧偏移，围成有锥度的闭合形状 */
export function ribbonPath(pts: Pt[], radii: number[]): string {
  const left: Pt[] = []
  const right: Pt[] = []
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i]
    const a = pts[Math.max(0, i - 1)]
    const b = pts[Math.min(pts.length - 1, i + 1)]
    let dx = b.x - a.x
    let dy = b.y - a.y
    const m = Math.hypot(dx, dy) || 1
    dx /= m
    dy /= m
    const r = radii[i]
    left.push({ x: p.x - dy * r, y: p.y + dx * r })
    right.push({ x: p.x + dy * r, y: p.y - dx * r })
  }
  const head = smooth(left)
  const back = right.reverse()
  const tail = smooth(back).replace(/^M/, 'L')
  return `${head} ${tail} Z`
}

/** 叶片轮廓：两端收尖的卵形，原点在叶柄，叶尖朝 -y */
export function leafPath(len: number, wid: number): string {
  const l = len
  const w = wid / 2
  return (
    `M0 0C${(w * 0.5).toFixed(2)} ${(-l * 0.2).toFixed(2)} ${(w * 1.05).toFixed(2)} ${(-l * 0.58).toFixed(2)} 0 ${(-l).toFixed(2)}` +
    `C${(-w * 1.05).toFixed(2)} ${(-l * 0.58).toFixed(2)} ${(-w * 0.5).toFixed(2)} ${(-l * 0.2).toFixed(2)} 0 0Z`
  )
}

export function growTree(over: Partial<TreeOptions> = {}): TreeModel {
  const o: TreeOptions = { ...DEFAULT_TREE, ...over }
  const rand = mulberry32(o.seed)
  const between = (r: [number, number]) => lerp(r[0], r[1], rand())

  const branches: BranchNode[] = []
  const leaves: LeafNode[] = []
  const clusters: Cluster[] = []

  /* 树冠包络：按高度的宽度剖面，不是椭圆。
     椭圆在底部是切点，从那里往水平方向走立刻出界，树冠下面永远填不满；
     剖面底部有 0.6 的宽度、中部最宽、顶部收口，才是树的冠形。 */
  const CROWN_BOTTOM = 0.28
  const CROWN_RX = o.height * 0.42
  const CROWN_TOP = 1.02
  function crownProfile(y: number): number {
    const t = clamp((y - CROWN_BOTTOM) / (1 - CROWN_BOTTOM), 0, 1)
    /* 指数 1 而不是 0.6：0.6 把剖面底部撑得很宽，低处那几根枝能一路走到包络边缘，
       于是一棵树上最长最平的枝长在最下面，看着像一条裙边。真实的树恰好相反 ——
       下层的枝被上层遮住，最短；最宽的一圈在树冠中下部。 */
    return Math.sin(Math.PI * (0.15 + 0.85 * t))
  }
  function insideCrown(p: Pt): boolean {
    const y = -p.y / o.height
    if (y < CROWN_BOTTOM || y > CROWN_TOP) return false
    return Math.abs(p.x) <= CROWN_RX * crownProfile(y)
  }
  /** 从 p 沿 dir 走到出界为止的距离 */
  function room(p: Pt, dir: number): number {
    const dx = Math.cos(dir)
    const dy = Math.sin(dir)
    const step = 3
    for (let t = step; t < CROWN_RX * 2.4; t += step) {
      if (!insideCrown({ x: p.x + dx * t, y: p.y + dy * t })) return t - step
    }
    return CROWN_RX * 2.4
  }
  /* 每一级允许走到剩余空间的几分之几：主枝几乎铺到边，越往外越短，
     所以树冠外缘由密集的小枝和叶团收口 */
  const ROOM_FILL = [1, 0.82, 0.66, 0.56, 0.5, 0.45]

  /* 一根枝短到这个份上就不再分出来：长度还不如自己的顶芽大，
     分出来是一根插在树冠边缘的针，不是一个梢。空间不够时该发生的事是
     「这里没长枝」，叶留给父枝自己的梢头团。 */
  const MIN_SPUR = o.clusterSize * 0.75

  /* 叶团：叶柄抠在团心附近，叶身朝外伸。
     早先把叶柄按整个团的半径散布，叶根离枝几十个单位，整片树冠看着是浮在枝外的绿雾；
     叶柄集中在枝梢上、叶尖才探出去，一簇叶才真的长在这根枝上。
     散布半径只有 size 的两成：梢尖处枝的半径原本就不到一个单位，
     叶柄再往外撒，整团叶就会离开梢头飘在空中 —— 主干顶只有这一团遮不住，最显眼。 */
  function clusterAt(center: Pt, birth: number, owner: number, size: number, dir: number, at = 1) {
    const n = Math.round(lerp(o.cluster[0], o.cluster[1], rand()))
    const ca = Math.cos(dir)
    const sa = Math.sin(dir)
    const first = leaves.length
    for (let i = 0; i < n; i++) {
      const ang = rand() * TAU
      /* 两成叶子故意探得远一点，树冠边缘才有毛刺，不像一颗圆球 */
      const spill = rand() < 0.2 ? 1.2 + rand() * 0.35 : 1
      const foot = size * 0.2 * Math.pow(rand(), 0.7)
      const u = Math.cos(ang) * foot * spill
      const v = Math.sin(ang) * foot * spill * 0.6
      const x = center.x + ca * u - sa * v
      const y = center.y + sa * u + ca * v
      /* 叶尖朝团外，再加抖动。
         ang 是团内的局部角，rot 却是绝对角度 —— 得先把这根枝自己的方向加上。
         漏了这一项时，叶尖方向整体差一个 dir：实测枝方向 -180° 的那组余弦 -0.90、
         -135° 的 -0.63、0° 的 +0.97，误差正好等于枝的方向角，
         于是竖直枝（主干）上的叶全是横着插在枝上的。 */
      const rot = dir / D2R + ang / D2R + 90 + (rand() - 0.5) * 44
      const len = between(o.leafSize) * (0.9 + rand() * 0.3)
      /* 叶柄散布收到 0.2 size 之后，仍要按「离团心多远」分档，所以分母跟着收，
         否则整片树冠的 shade 都压在最低档、右上方那层受光就没了 */
      const outward = clamp(Math.hypot(u, v) / Math.max(1, size * 0.31), 0, 1)
      /* 明暗不是随机撒的：光从右上来，所以右侧、外侧的叶亮，左侧、内膛的叶暗。
         早先 shade 只按叶在团里离心的距离算，跟受光无关，整片树冠是一层平均的绿，
         看上去是贴纸而不是有厚度的树冠。 */
      leaves.push({
        x,
        y,
        rot,
        len,
        wid: len / o.leafAspect,
        shade: clamp(
          0.18 + (center.x / (o.height * 0.45) + 1) * 0.23 + outward * 0.4 + (rand() - 0.5) * 0.2,
          0,
          1
        ),
        /* 这里不能 clamp 到 1：此时的时间还是未归一化的绝对刻度，能到 2 以上；
           提前夹住会让所有靠后的叶团挤到同一刻一起冒出来 */
        birth: birth + rand() * 0.06,
        owner
      })
    }
    clusters.push({
      x: center.x,
      y: center.y,
      size,
      dir: dir / D2R,
      level: clamp(-center.y / o.height, 0, 1),
      birth,
      owner,
      u: at,
      leaves: leaves.slice(first)
    })
  }

  function grow(
    base: Pt,
    angle: number,
    len: number,
    radius: number,
    level: number,
    parent: number,
    birth: number,
    dur: number,
    cont: number,
    /** 根部半径：分叉处要和父枝一样宽，衔接才是 Y 形而不是细枝插进粗枝 */
    rootR?: number,
    /** 父枝在落点处的方向：子枝起步先顺着它走一小段，再转到自己的分叉角 */
    entry?: number
  ) {
    if (o.maxBranches && branches.length >= o.maxBranches) return
    const segs = Math.max(4, Math.round(o.segments * (1 - level * 0.12)))
    const pts: Pt[] = [base]
    const radii: number[] = [rootR ?? radius]
    /* 梢部不能细到看不见：模型 1 个单位在这棵树上只有两三像素，
       半径掉到 0.4 就成了一条飘着的线，看着像没接到枝上 */
    const tipRadius = Math.max(1, radius * (level === 0 ? 0.5 : 0.45))

    /* 方向插值：整条枝基本保持外张，只有梢头稍稍向光上翘。
       bend 若直接作用在整条枝上，树冠会收成一束，所以只取一小部分。 */
    const a1 = level === 0 ? angle : angle + (UP - angle) * o.bend * (0.45 + (1 - clamp(-base.y / o.height, 0, 1)) * 0.75) + (rand() - 0.5) * 0.2
    const wob = (rand() - 0.5) * o.wobble * (level === 0 ? 0.45 : 1)
    const phase = rand() * TAU

    let cur = base
    const step = len / segs
    for (let i = 1; i <= segs; i++) {
      const t = i / segs
      const e = t * t * (3 - 2 * t)
      /* 主干不该明显摆，侧枝要摆 */
      const a = (entry == null ? angle : lerp(entry, angle, smooth01(t / 0.28)))
        + (a1 - angle) * e
        + Math.sin(t * Math.PI * 1.7 + phase) * wob
      cur = { x: cur.x + Math.cos(a) * step, y: cur.y + Math.sin(a) * step }
      pts.push(cur)
      /* 半径带一点噪声，边缘才不像塑料管。
         最后一点再收一次：ribbon 是两端平头的闭合形状，
         梢部不收尖就是一根被切平的木头。
         但收多紧要看这条枝有多长 —— 长枝末梢本来就细，收成尖是对的；
         十几厘米的小分叉也收成 0.32，剩下的就是一根针。短枝留钝头，
         配上它梢头那一小簇叶，读起来是芽，不是刺。 */
      const tipK = i === segs ? (len < o.clusterSize ? 0.62 : 0.32) : 1
      let r = lerp(radius, tipRadius, Math.pow(t, 0.8)) * (1 + (rand() - 0.5) * 0.1) * tipK
      /* 喇叭口只在前两点：根部和父枝同宽，一两个点上收到本色，
         之后与普通枝无异。不做这一步，子枝根部只有父枝的一半宽，
         两条边界从父枝里面钻出来，与父枝表面夹出一个锐角缺口。 */
      if (rootR != null && i === 1) r = lerp(rootR, r, 0.82)
      else if (rootR != null && i === 2) r = lerp(rootR, r, 0.94)
      radii.push(r)
    }

    /* 主干在贴地处外扩一点，像树根撑住地面；其余枝的轮廓就用 radii。
       遮罩按 drawR 算宽度，调用方按同一个系数重算轮廓，根盘才不会丢。 */
    const drawRadii = (): number[] => {
      if (level !== 0) return radii
      const r = radii.slice()
      r[0] *= 1.16
      return r
    }
    const drawR = drawRadii()

    const node: BranchNode = {
      id: branches.length,
      level,
      parent,
      pts,
      radii,
      center: smooth(pts),
      ribbon: ribbonPath(pts, drawR),
      drawR,
      birth,
      dur,
      length: len,
      tip: cur,
      leaves: []
    }
    const self = node.id
    const before = leaves.length
    branches.push(node)

    /* 叶子：每根枝的梢头都结团 —— 一根枝没有顶芽是不成立的，
       早先只有第 leafFrom 层以上才挂团，lower 层一旦分叉被空间判据砍掉，
       整条主枝就成了一根伸出去的光杆，树冠中段因此是一片空的。
       尺寸按层收：主干顶那一撮最小，末级枝最满。leafFrom 以上才算「满团」，
       中段的小团也只从这一层开始挂。 */
    {
      const top = level >= o.levels
      const full = level >= o.leafFrom
      const size = o.clusterSize * (full ? (top ? 1 : 0.75) : level === 0 ? 0.85 : 0.68)
      const n1 = pts.length - 1
      const tipDir = Math.atan2(pts[n1].y - pts[n1 - 1].y, pts[n1].x - pts[n1 - 1].x)
      /* 团心落在梢尖稍内一点：叶柄若挂在梢尖之外，叶就整团浮在枝头上方。
         早先放在梢外 0.42 size，叶柄又按团心散布，主干顶那一撮离枝尖十几像素，
         生长过程中看着就是「枝还没长到，叶先飘在那儿」。退进梢内 0.22 size，
         叶柄才落在枝身上，叶尖朝外张开，一簇叶是从枝头长出来的。 */
      const out = -size * 0.22
      clusterAt(
        { x: cur.x + Math.cos(tipDir) * out, y: cur.y + Math.sin(tipDir) * out },
        birth + dur * 0.98, self, size, tipDir
      )
      /* 中段团：叶不能全长在梢头，否则整棵树的叶都被枝的斜度推到顶上，树冠成了倒三角。
         枝中段也挂一撮，叶的高度分布才会往下铺。 */
      if (level >= 1 && !top && rand() < 0.45) {
        const mi = clamp(Math.round((pts.length - 1) * 0.55), 1, pts.length - 2)
        const midDir = Math.atan2(pts[mi].y - pts[mi - 1].y, pts[mi].x - pts[mi - 1].x)
        clusterAt(pts[mi], birth + dur * (mi / n1), self, size * 0.6, midDir, mi / n1)
      }
    }
    node.leaves = leaves.slice(before)

    if (level >= o.levels) return

    /* 分叉：成对出枝，同高、左右反向；主干分成 children 对，往下每级只留一对。
       两条旧路都试过，都不行：
       ① 每枝生 k 个孩子、左右交替 —— 落点谁在下面谁的分叉角最大（低处取 angle[1]），
          于是总有一侧甩出一根近水平的长枝把整棵树的重量拖过去，另一侧空着，
          五个种子实测左右叶片数差 25%–49%、重心 x 全偏出 25–34 单位；
       ② 每级都成对 —— 层数一深枝数就爆（4 层 × 2 对 = 100+ 枝），树冠变成一团。
       现在主干 2–3 对（children），主枝上各 1 对侧枝，层级再深也只是一分为二。 */
    const want = Math.round(between(o.children))
    const pairs = level === 0 ? want : 1
    /* 真正长出子枝的落点，循环结束后在这里鼓一圈枝瘤 */
    const forkAt = new Set<number>()
    for (let j = 0; j < pairs; j++) {
      /* 落点避开梢头：末两点的半径已经收到接近梢尖，在那里分叉的话
         父枝只剩一小截尖尾巴，子枝从尖上接出去，接缝看着就是「一条枝被切断」 */
      const at = pairs === 1 ? 0.5 : 0.38 + (j / (pairs - 1)) * 0.36 + (rand() - 0.5) * 0.04
      const idx = clamp(Math.round(at * (pts.length - 1)), 1, pts.length - 2)
      const p = pts[idx]
      /* 太细的地方不长枝，只在末梢挂叶 */
      if (radii[idx] < 1.05) continue
      const parentDir = Math.atan2(pts[idx + 1].y - pts[idx].y, pts[idx + 1].x - pts[idx].x)
      /* 分叉角随高度收：树冠下部的枝几乎水平，上部越来越立，
         否则所有枝都斜着往上跑，树冠只剩一个平顶，下面空一大截 */
      const upRatio = clamp(-p.y / o.height, 0, 1)
      const atReal = idx / (pts.length - 1)
      /* 同一对的两根各抽一次角度：差的能到三成，才不会左右对称得像剪出来的 */
      for (const side of [1, -1]) {
        const spreadDeg = lerp(o.angle[1], o.angle[0], upRatio) * (0.82 + rand() * 0.36)
        const spread = spreadDeg * D2R * (1 - level * 0.08)
        /* 越低的枝越要往下探一点：完全水平的长枝像插了根横杆，
           带一点下垂才是树冠底缘的样子 */
        const droop = (1 - upRatio) * 0.36
        /* 侧枝不允许下垂太多：方向锁在竖直往上 ±80° 之间 */
        const childAngle = clamp(parentDir + side * (spread + droop), UP - 1.4, UP + 1.4)
        const ideal = len * between(o.lengthDecay)
        /* 最下面那一对要短：它的落点处包络已经很宽，不压的话它会把整个冠幅吃下来，
           树冠变成「底部一对大枝 + 顶上一小团」的 V 形。真实树最低的枝最弱。 */
        const lowK = j === 0 && pairs > 1 ? 0.7 : 1
        const fill = ROOM_FILL[Math.min(ROOM_FILL.length - 1, level + 1)] * lowK
        const space = room(p, childAngle)
        /* 只有真的贴到边界（或还在包络外的树干下部）才不分叉。
           阈值放太高会让整段子树被随机砍掉，同样的参数不同种子疏密差一倍。 */
        if (space < 4) continue
        const childLen = Math.min(ideal, space * fill * (0.9 + rand() * 0.16))
        /* 这里原先是 Math.max(7, …)：被包络压到 3–6 的长度硬抬到 7，
           于是末级多出一批十几像素的小分叉 —— 梢头收尖、再顶一团叶，
           远看是一圈刺。抬高长度是在撒谎（空间本来就没那么大），
           正确的事是不分叉。实测砍掉 8% 的枝，叶只少 3%（父枝梢头团还在）。 */
        if (childLen < MIN_SPUR) continue
        /* 同一对里两根的粗细也各抽一次，交点上不会左右一样粗。
           但要封顶在父枝半径的 0.92：末尾几下分叉落在 1.1 左右的细枝上，
           原来 min 1.15 的兜底会让子枝比父枝还粗，根端就从父枝侧面鼓出来 */
        const childR = Math.min(
          radii[idx] * 0.92,
          Math.max(1.15, radii[idx] * o.widthDecay * (0.88 + rand() * 0.28))
        )
        /* 孩子必须在父枝长过这个落点之后才出生。
           早先按 at 折算成 0.32+at*0.26，落点靠上的孩子反而先长，父枝还没伸到它就悬在空中。 */
        /* 根部往父枝里退：子枝的根端是垂直于自己方向的平头截面，
           要整条埋进父枝内部，接缝才在枝里、不露出背景。
           退的方向必须是**沿父枝往回**，不能沿子枝方向 ——
           早先按子枝方向退 0.9r，起点相对父枝中心线横移了 0.9r·sinθ，
           根端边的半宽又有 childR≈0.58r，外侧角点就跑到父枝表面外 0.4 个单位，
           两条枝的交界处露出一道 V 形缺口，看着是两根枝条拼起来的。
           沿父枝退时横移恒为 0，只要 childR ≤ 父枝半径，根端边就整个在父枝里。 */
        const back = clamp(radii[idx] * 0.9, 0.9, 3.5)
        grow(
          { x: p.x - Math.cos(parentDir) * back, y: p.y - Math.sin(parentDir) * back },
          childAngle,
          childLen,
          childR,
          level + 1,
          self,
          birth + dur * Math.min(0.98, atReal + 0.02),
          Math.max(0.08, dur * 0.7),
          0,
          Math.max(childR, radii[idx] * 0.95),
          parentDir
        )
        forkAt.add(idx)
      }
    }

    /* 枝杈处鼓一圈。轮廓是用二次贝塞尔穿过中点画的，实际填充比「中心线 ± 半径」略窄，
       子枝根部嵌在落点上时父枝包不住，腋部就露出一个尖缺口 —— 单独看像两根枝条拼起来。
       加粗只做在落点及左右邻点，形状上就是枝瘤：真实分叉处本来就是鼓的。 */
    if (forkAt.size) {
      for (const i of forkAt) {
        radii[i] *= 1.22
        if (i > 1) radii[i - 1] = Math.max(radii[i - 1], radii[i] * 0.94)
        if (i < radii.length - 2) radii[i + 1] = Math.max(radii[i + 1], radii[i] * 0.9)
      }
      node.drawR = drawRadii()
      node.ribbon = ribbonPath(pts, node.drawR)
    }

    /* 合轴：主干再抽一节，层数不变，让中上部仍由主干串起来 */
    if (level === 0 && cont > 0) {
      const top = pts[pts.length - 1]
      const dir = Math.atan2(top.y - pts[pts.length - 2].y, top.x - pts[pts.length - 2].x)
      grow(top, dir + (rand() - 0.5) * 0.18, len * 0.44, radii[radii.length - 1] * 0.92,
        0, self, birth + dur * 0.5, dur * 0.5, cont - 1)
    }
  }

  grow({ x: o.height * 0.02, y: 0 }, UP, o.height, o.trunkWidth / 2, 0, -1, 0, 1, 0)

  /* 归一化时间：整棵树 0→1 */
  const span = branches.reduce((m, b) => Math.max(m, b.birth + b.dur), 0) || 1
  for (const b of branches) {
    b.birth /= span
    b.dur /= span
  }
  for (const l of leaves) l.birth = clamp(l.birth / span, 0, 1)
  /* 叶团的出生时间也必须一起归一化，否则 clamp 过的 1 会全挤到最后 */
  for (const c of clusters) c.birth = clamp(c.birth / span, 0, 1)

  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity
  for (const b of branches) {
    for (const p of b.pts) {
      if (p.x < minX) minX = p.x
      if (p.x > maxX) maxX = p.x
      if (p.y < minY) minY = p.y
      if (p.y > maxY) maxY = p.y
    }
  }
  /* 叶比枝探得更远，包围盒要把叶尖算进来，否则调参台分列时树冠会互相压住 */
  for (const l of leaves) {
    for (const [x, y] of [[l.x, l.y], [l.x + Math.cos((l.rot - 90) / D2R) * l.len, l.y + Math.sin((l.rot - 90) / D2R) * l.len]]) {
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    }
  }

  return {
    branches,
    leaves,
    clusters,
    branchCount: branches.length,
    leafCount: leaves.length,
    box: { x: minX, y: minY, w: maxX - minX, h: maxY - minY }
  }
}

/* 二次生长：从一个已经展开的叶团里再抽一簇新梢。
   运行中的实例再调一个 routine，多出来的一层不是预先排进整棵树的，
   所以这簇枝不能由 growTree 一次算完 —— 它要在调用发生的那一刻才开始长。
   这里只给几何：基点在原点，-y 向上，时间仍是相对的 0–1，由调用方接到自己的时钟上。
   形态规则和主树一致（弧线、锥度、末梢结团），只是尺寸小一号、层数浅。 */

export interface SproutOptions {
  seed: number
  /** 主梢长度，px */
  length: number
  /** 基部半径，px */
  radius: number
  levels: number
  angle: [number, number]
  children: [number, number]
  lengthDecay: [number, number]
  widthDecay: number
  cluster: [number, number]
  clusterSize: number
  leafSize: [number, number]
  leafAspect: number
  /** 根部半径：新梢从别人的枝身上长出来，根端要和宿主在那一点的宽度齐平，
      否则平头截面比宿主宽一圈、从枝身上鼓出一道台阶。
      不给就按 radius 起（自己从空处长出来时用） */
  rootR?: number
  /** 起步方向（弧度）：先顺着宿主那一段的方向走，再转出去 */
  entry?: number
}

export const DEFAULT_SPROUT: SproutOptions = {
  seed: 3,
  length: 74,
  radius: 2.2,
  levels: 2,
  angle: [32, 62],
  children: [1, 2],
  lengthDecay: [0.6, 0.74],
  widthDecay: 0.54,
  cluster: [2, 4],
  clusterSize: 9,
  leafSize: [9, 13],
  leafAspect: 2.1
}

export interface Sprout {
  branches: BranchNode[]
  clusters: Cluster[]
  leaves: LeafNode[]
  tip: Pt
}

export function growSprout(over: Partial<SproutOptions> = {}): Sprout {
  const o: SproutOptions = { ...DEFAULT_SPROUT, ...over }
  const rand = mulberry32(o.seed)
  const between = (r: [number, number]) => lerp(r[0], r[1], rand())

  const branches: BranchNode[] = []
  const leaves: LeafNode[] = []
  const clusters: Cluster[] = []

  function clusterAt(center: Pt, birth: number, owner: number, size: number, dir: number) {
    const n = Math.round(lerp(o.cluster[0], o.cluster[1], rand()))
    const ca = Math.cos(dir)
    const sa = Math.sin(dir)
    const first = leaves.length
    for (let i = 0; i < n; i++) {
      const ang = rand() * TAU
      const spill = rand() < 0.2 ? 1.25 + rand() * 0.4 : 1
      const rad = size * Math.pow(rand(), 0.55) * spill
      const u = Math.cos(ang) * rad * 1.35
      const v = Math.sin(ang) * rad * 0.72
      const x = center.x + ca * u - sa * v
      const y = center.y + sa * u + ca * v
      const rot = Math.atan2(v, u) / D2R + 90 + (rand() - 0.5) * 55
      const len = between(o.leafSize) * (0.85 + rand() * 0.3)
      const outward = clamp(rad / Math.max(1, size), 0, 1)
      leaves.push({
        x,
        y,
        rot,
        len,
        wid: len / o.leafAspect,
        shade: clamp(0.2 + outward * 0.62 + (rand() - 0.5) * 0.22, 0, 1),
        birth: birth + rand() * 0.06,
        owner
      })
    }
    clusters.push({
      x: center.x,
      y: center.y,
      size,
      dir: dir / D2R,
      /* 局部高度：调用方换算成整棵树的绝对高度后再定枯萎次序 */
      level: clamp(-center.y / Math.max(1, o.length), 0, 1),
      birth,
      owner,
      /* 新梢的团都在梢头 */
      u: 1,
      leaves: leaves.slice(first)
    })
  }

  function grow(
    base: Pt,
    angle: number,
    len: number,
    radius: number,
    level: number,
    parent: number,
    birth: number,
    dur: number,
    rootR?: number,
    entry?: number
  ) {
    const segs = Math.max(4, 6 - level)
    const pts: Pt[] = [base]
    const radii: number[] = [rootR ?? radius]
    const tipRadius = Math.max(0.65, radius * 0.45)
    const a1 = angle + (UP - angle) * 0.5 + (rand() - 0.5) * 0.24
    const wob = (rand() - 0.5) * 0.22
    const phase = rand() * TAU

    let cur = base
    const step = len / segs
    for (let i = 1; i <= segs; i++) {
      const t = i / segs
      const e = t * t * (3 - 2 * t)
      const a = (entry == null ? angle : lerp(entry, angle, smooth01(t / 0.28)))
        + (a1 - angle) * e
        + Math.sin(t * Math.PI * 1.7 + phase) * wob
      cur = { x: cur.x + Math.cos(a) * step, y: cur.y + Math.sin(a) * step }
      pts.push(cur)
      let r = lerp(radius, tipRadius, Math.pow(t, 0.8)) * (1 + (rand() - 0.5) * 0.1) * (i === segs ? 0.32 : 1)
      if (rootR != null && i === 1) r = lerp(rootR, r, 0.82)
      else if (rootR != null && i === 2) r = lerp(rootR, r, 0.94)
      radii.push(r)
    }

    const node: BranchNode = {
      id: branches.length,
      level,
      parent,
      pts,
      radii,
      center: smooth(pts),
      ribbon: ribbonPath(pts, radii),
      drawR: radii,
      birth,
      dur,
      length: len,
      tip: cur,
      leaves: []
    }
    const self = node.id
    const before = leaves.length
    branches.push(node)

    /* 新梢的每根枝梢都结团 —— 短梢的意义就在末端那一撮，留一截裸枝立刻像铁丝 */
    const n1 = pts.length - 1
    const tipDir = Math.atan2(pts[n1].y - pts[n1 - 1].y, pts[n1].x - pts[n1 - 1].x)
    clusterAt(cur, birth + dur * 0.98, self, level === 0 ? o.clusterSize * 0.78 : o.clusterSize, tipDir)
    node.leaves = leaves.slice(before)

    if (level >= o.levels) return

    const kids = Math.round(between(o.children))
    for (let k = 0; k < kids; k++) {
      const at = kids === 1 ? 0.66 : 0.42 + (k / (kids - 1)) * 0.44
      const idx = clamp(Math.round(at * (pts.length - 1)), 1, pts.length - 2)
      const p = pts[idx]
      const parentDir = Math.atan2(pts[idx + 1].y - pts[idx].y, pts[idx + 1].x - pts[idx].x)
      const side = (k % 2 === 0 ? 1 : -1) * (0.85 + rand() * 0.2)
      const spread = lerp(o.angle[1], o.angle[0], rand()) * D2R
      const childAngle = clamp(parentDir + side * spread, UP - 1.3, UP + 1.3)
      const childLen = Math.max(5, len * between(o.lengthDecay))
      const atReal = idx / (pts.length - 1)
      /* 同上：沿父枝方向退，不沿子枝方向退 */
      const back = Math.min(2, radii[idx] * 0.9)
      const cr = Math.min(
        radii[idx] * 0.92,
        Math.max(0.7, radii[idx] * o.widthDecay * (1.25 - k * 0.1))
      )
      grow(
        { x: p.x - Math.cos(parentDir) * back, y: p.y - Math.sin(parentDir) * back },
        childAngle,
        childLen,
        cr,
        level + 1,
        self,
        birth + dur * Math.min(0.98, atReal + 0.02),
        Math.max(0.1, dur * 0.66),
        Math.max(cr, radii[idx] * 0.95),
        parentDir
      )
    }
  }

  grow({ x: 0, y: 0 }, UP, o.length, o.radius, 0, -1, 0, 1, o.rootR, o.entry)

  const span = branches.reduce((m, b) => Math.max(m, b.birth + b.dur), 0) || 1
  for (const b of branches) {
    b.birth /= span
    b.dur /= span
  }
  for (const l of leaves) l.birth = clamp(l.birth / span, 0, 1)
  for (const c of clusters) c.birth = clamp(c.birth / span, 0, 1)

  return { branches, clusters, leaves, tip: branches[0].tip }
}
