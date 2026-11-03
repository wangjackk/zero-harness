/* 画框取景。这是呈现、不是生长，所以留在 web（sdk 只管骨架与映射），
   但必须是纯函数 —— 「树一会大一会小」这种事没法只靠眼睛判断，
   要有脚本能量出画框在一条增删时间线上改过几次、有没有缩回去过。

   ## 只扩不缩

   早先有两条规则同时在跑：来新节点就按包围盒扩，静下来 1.2 秒又按包围盒缩。
   树的规模在运行中本来就在增删之间来回（一轮对话长出一整棵子树，结束就没了），
   于是「扩一次 → 1.2s → 缩回来 → 又长大 → 再扩」循环发生，
   画面上就是整棵树在呼吸 —— 用户看到的是「一会变大一会变小」。

   现在只有一个方向：画框只变大。树往上长、画框往下让，
   所以看起来只会越来越小一点，永远不会突然放大。

   ## 两个防抖

   - 按 `Q`（32）取整：包络每多十几单位就换一档，会有一连串 5% 的缩放跳；
     粗一档，一次会话通常只换三四次。
   - 每次至少放大 6%：包围盒贴着当前边界一点点往外挪时不重取景，
     否则临界处会来回跳档。

   代价写在这里：**一次会话里树最大过之后，画框不会收回去**。
   这是有意的 —— 画框的职责是不让树出画，不是每帧把树填满。 */

export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export interface Frame {
  x: number
  y: number
  w: number
  h: number
}

/** 画框四周留白 */
export const PAD = 34
/** 主干根部离画框下沿留一点土，否则基部封口被切掉，像插在画面外 */
export const SOIL = 12
/** 取整档位 */
const Q = 32
/** 一次放大的最小比例 */
const MIN_GAIN = 1.06

/** 装得下这棵树的方框边长：横向与纵向取大的那个，按 Q 取整。
 *  横向以 0 对称（主干根部在 x=0），底边贴 0 —— 于是画框一定是正方形，
 *  SVG 用 `preserveAspectRatio="xMidYMax meet"` 时才不会有一个方向留白。 */
export function sideFor(b: Box): number {
  const halfW = Math.max(Math.abs(b.x), Math.abs(b.x + b.w))
  return Math.ceil((Math.max(halfW * 2, b.h) + PAD) / Q) * Q
}

/** 边长 → 画框（方形、底部对齐、横向居中） */
export function frameOf(w: number): Frame {
  return { x: -w / 2, y: SOIL - w, w, h: w }
}

/** 装不下就给一个新画框，装得下返回 null。
 *  只扩不缩 —— 这是这个函数唯一的约定，别再往里加「缩」的分支。 */
export function grown(cur: Frame, box: Box): Frame | null {
  const side = sideFor(box)
  if (side <= cur.w) return null
  return frameOf(Math.max(side, Math.ceil((cur.w * MIN_GAIN) / Q) * Q))
}
