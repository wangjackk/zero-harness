/* 问题② **生长映射**：给定一个时刻，每条线露出全长的几分之几。
   输入骨架（skeleton.ts）+ 每个实例的出生时刻，输出一组 0–1 的进度。
   一句 `clamp((t − 起点) / 时长, 0, 1)`，没有别的规则。

   这一层纯到可以直接算，所以它是可验的：scripts 里那两份验收脚本
   跑的就是这里的函数，不是照抄一份。早先映射写在 HUD 组件里，
   验收脚本只能自己再实现一遍 ease / since / open —— 测的不是产品。

   ## 只有一条进度

   叶张开、枝抽长，早先是两个函数：`leafOpen` 和 `branchP`。
   分开是因为叶**没有自己的出生时刻**（它们不在 `born` 表里），
   只能临时拿「父枝长过 `u` 了没有」现算一遍。

   现在每个 GStem —— 包括叶 —— 都有自己的 `born`，而且
   `birthTime` 本来就把「父线长到 `at`」写进去了：
   `born = max(被看见的时刻, born(父) + BEAT·父.dur·父.at)`。
   于是叶的张开就是它自己的进度，`stemP` 一个函数管到底。
   `leafOpen` / `leafShown` 一并删掉：它们只是这条规则的另一种写法。

   ## 这一层负责什么

   - 一拍多长（`BEAT`）：骨架给的是「走几拍」，这里给「一拍多少毫秒」
   - 起点规则：子线最早在「父线长到自己落点」那一刻出现（`birthTime`）
   - 进度：`stemP` 露出几成、`sincePast` 父线长过某点没有

   ## 这一层不负责什么

   - 拓扑从哪来（ws 事件、轮询、mock）—— 数据，不是规则
   - 画框取景、配色、hover、层序、降级 —— 呈现，不是生长
   - 落叶与断枝的**落体轨迹**：那是颗粒的位移（重力、阻力、横风），
     画在页面里；这里只回答「它露过面没有」（`rawP > 0`），
     免得凭空掉下一片黄叶。

   骨架里没有绝对时间，这一层也没有：`born` 是数据（谁什么时候被看见），
   函数本身只读它。所以同一棵树可以在任意一帧停下，也可以倒放。 */

import type { GStem, Skeleton } from './skeleton'

/* 一拍的基础毫秒数。骨架给的是「跑几拍」（`dur`，主干 1 拍），
   这里只负责一拍有多长 —— 速度是骨架的属性，不是这一层的。 */
export const BEAT = 560

/** 生长曲线：八成线性掺两成 smoothstep。
 *  纯线性走到头是「啪」地停住；纯 smoothstep 起手又像卡了一下（一拍只有半秒多）。
 *  掺两成只把两头磨圆，中段仍然匀速，所以「整树同速」这句还站得住。
 *  **调用前必须已经把 p 夹到 0–1**：smoothstep 过了 1 会拐回来（p=2 → 0.8、p=3 → −3），
 *  拿它当「超了就截断」用，会让长满很久的枝/叶算成负数，画面上是凭空消失。
 *  所以 `stemP` 一律先夹再缓，外部拿不到未夹的值。 */
export function easeGrow(p: number): number {
  const x = p <= 0 ? 0 : p >= 1 ? 1 : p
  return x * 0.8 + x * x * (3 - 2 * x) * 0.2
}

/** 这条线已经出生多久（毫秒）。没有出生时刻 = 这一帧还没有它 */
export function grownFor(s: GStem, born: ReadonlyMap<string, number>, t: number): number {
  const t0 = born.get(s.id)
  return t0 == null ? -Infinity : t - t0
}

/** 已知出生时刻时的线性进度。把「查出生表」与「算进度」分开，
 *  结束队列里的叶已经离开主出生表，也能沿用同一条映射。 */
function rawAt(s: GStem, birth: number, t: number): number {
  const e = t - birth
  if (!(e > 0)) return 0
  const d = BEAT * s.dur
  return e >= d ? 1 : e / d
}

/** 线性进度：0 未出生，1 长满。**不缓动**，给判据用
 *  （「露过面没有」这种阈值不该被曲线挪位置） */
export function rawP(s: GStem, born: ReadonlyMap<string, number>, t: number): number {
  const birth = born.get(s.id)
  return birth == null ? 0 : rawAt(s, birth, t)
}

/** 露出全长的几成 —— 遮罩的 dashoffset 直接用这个值。
 *  叶也用它：叶的「张开」就是这条线走完自己那几拍。
 *  分两个函数是历史：叶早先没有出生时刻，只能拿父线进度现算。 */
export function stemP(s: GStem, born: ReadonlyMap<string, number>, t: number): number {
  return easeGrow(rawP(s, born, t))
}

export interface LeafFrame {
  /** 与枝共用的生长进度 */
  progress: number
  /** 叶片尺度，从接近零连续展开 */
  scale: number
  opacity: number
  /** 当前叶柄末端；叶柄与叶片一起从 mesh 长到这个点 */
  x: number
  y: number
}

const smooth01 = (p: number) => {
  const x = p <= 0 ? 0 : p >= 1 ? 1 : p
  return x * x * (3 - 2 * x)
}

/** 叶片在某一帧的完整位姿。
 *
 * 不是把一片 45% 大小的叶突然放到最终位置再淡入：叶柄先从着生点伸出，
 * 叶片跟着柄端前进，同时从 6% 张到 100%。透明度和尺度都用零端点导数的
 * smoothstep，出现与长满这两下没有速度突变。 */
export function leafFrame(
  s: GStem,
  born: ReadonlyMap<string, number>,
  t: number
): LeafFrame | null {
  const birth = born.get(s.id)
  return leafFrameAt(s, birth ?? Number.POSITIVE_INFINITY, t)
}

/** 结束队列里的叶仍按原出生时刻生长。它不因为外部 stop 而换一条映射，
 *  只是长完后由 DepartedStem 接管离枝与飘落。 */
export function leafFrameAt(s: GStem, birth: number, t: number): LeafFrame | null {
  if (!s.blade) return null
  const progress = easeGrow(rawAt(s, birth, t))
  /* 长满就是终点本身，不再走一遍插值：`mesh + (tip − mesh) × 1` 与 `tip`
     在浮点里会差一个 ULP。肉眼无所谓，但「离枝那一帧是不是精确落在梢头」
     是要拿严格相等去核的，差一个 ULP 就等于在接缝处留了一道说不清的缝。 */
  if (progress >= 1) {
    return { progress: 1, scale: 1, opacity: 1, x: s.tip.x, y: s.tip.y }
  }
  const open = smooth01(progress)
  return {
    progress,
    scale: 0.06 + 0.94 * open,
    opacity: smooth01(progress / 0.55),
    x: s.mesh.x + (s.tip.x - s.mesh.x) * open,
    y: s.mesh.y + (s.tip.y - s.mesh.y) * open,
  }
}

/** 父线长到参数位置 u 之后过了多少毫秒。负值/`-Infinity` 表示还没长到。
 *  只剩落枝的「露过面没有」用它：判断一根枝在画面上出现过没有，
 *  要看断口以下那一截有没有被揭示出来过 */
export function sincePast(
  s: GStem,
  born: ReadonlyMap<string, number>,
  u: number,
  t: number
): number {
  const t0 = born.get(s.id)
  if (t0 == null) return -Infinity
  return t - t0 - BEAT * s.dur * u
}

/** 一条子线的出生时刻。
 *
 *  它自己被看见是一件事，能不能露面是另一件事：还得等父线长到自己这个落点，
 *  否则子线先浮在空中、根部与父线之间留一道缝。父线长到 `at` 用的时间是
 *  它自己的 `dur × at`（骨架给的），不是固定一拍 —— 否则挂在一根短线末端的
 *  子线会白等半拍。
 *
 *  叶也走这一条：叶的「出生」就是「父线长到了它那一点」。
 *  `seen` 是「它被看见的时刻」（created 事件时间；叶变枝时是当下）。
 *  根实例没有父线，直接用 `seen`。 */
export function birthTime(
  s: GStem,
  par: GStem | undefined,
  born: ReadonlyMap<string, number>,
  seen: number
): number {
  if (!par) return seen
  const pb = born.get(par.id)
  if (pb == null) return seen
  return Math.max(seen, pb + BEAT * par.dur * s.at)
}

/** 一次性把整棵树的出生时刻排出来（静态渲染、验收脚本、回放）。
 *  运行中的增量在页面里逐条算 `birthTime` —— 那里的事件是陆续到的。
 *  `stems` 是先序（父一定在子前面），所以一趟就够。 */
export function bornTimes(
  sk: Skeleton,
  seen: (s: GStem) => number
): Map<string, number> {
  const born = new Map<string, number>()
  for (const s of sk.stems) {
    const par = s.parent ? sk.byId.get(s.parent) : undefined
    born.set(s.id, birthTime(s, par, born, seen(s)))
  }
  return born
}

/** 整棵树长满的时刻（最后一条线走完全长） */
export function finishedAt(sk: Skeleton, born: ReadonlyMap<string, number>): number {
  let end = 0
  for (const s of sk.stems) {
    const t0 = born.get(s.id)
    if (t0 == null) continue
    end = Math.max(end, t0 + BEAT * s.dur)
  }
  return end
}

/** 还有线在长：决定这一帧要不要继续跑 rAF。
 *  比「距上次出生不到一拍」准 —— 长线的尾巴可以拖到它自己的 dur 之后 */
export function anyGrowing(
  sk: Skeleton,
  born: ReadonlyMap<string, number>,
  t: number
): boolean {
  return sk.stems.some(s => {
    const t0 = born.get(s.id)
    return t0 == null || t - t0 < BEAT * s.dur
  })
}
