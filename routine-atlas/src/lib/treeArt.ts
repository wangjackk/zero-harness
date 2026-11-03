/* 一片叶就是一个 SVG 节点。
   一片叶对应一个运行中的实例，所以叶的数量本身就是信息：每团两到四片、整树百余片，
   少到可以直接看、以后也能单独挂交互。之前把整团几十片合成一条 path 是为了省 DOM，
   但那种画法默认叶子只是质地，不是单元 —— 现在不需要了。 */

import { leafPath, type LeafNode } from './tree'

/** 单位叶片：长 1（y 从 0 到 −1），宽 1.05（x ±0.525）。用 scale(len, wid / LEAF_W) 还原真实尺寸 */
export const LEAF_UNIT = leafPath(1, 1)
export const LEAF_W = 1.05

const n = (v: number) => String(Math.round(v * 100) / 100)
/* 只依赖叶的形状字段：别的模型（比如按运行时拓扑长出来的树）只要给出
   位置、角度、长宽就能用同一套画法，不必伪造 birth / owner。 */
type LeafLike = Pick<LeafNode, 'x' | 'y' | 'rot' | 'len' | 'wid'>
const scale = (l: LeafLike) => `${n(l.len)} ${(l.wid / LEAF_W).toFixed(2)}`

/** 相对团中心：整团一起缩放、让位时用。
    ex 是单片叶自己的偏移：顶芽张开时叶柄被顶开（dx/dy）、叶身转出去（drot）、
    随老叶落掉再收一点（s）。旋转都绕叶柄根部，所以先平移到叶柄再转。 */
export function leafT(
  l: LeafLike,
  ox: number,
  oy: number,
  ex?: { dx?: number; dy?: number; drot?: number; s?: number }
): string {
  const k = ex?.s ?? 1
  const size = k === 1 ? scale(l) : `${n(l.len * k)} ${((l.wid * k) / LEAF_W).toFixed(2)}`
  const move = ex && (ex.dx || ex.dy) ? `translate(${n(ex.dx ?? 0)} ${n(ex.dy ?? 0)}) ` : ''
  const spin = ex?.drot ? ` rotate(${n(ex.drot)})` : ''
  return `${move}translate(${n(l.x - ox)} ${n(l.y - oy)})${spin} rotate(${n(l.rot)}) scale(${size})`
}

/** 绝对坐标：静态图上直接用 */
export function leafAbs(l: LeafLike): string {
  return `translate(${n(l.x)} ${n(l.y)}) rotate(${n(l.rot)}) scale(${scale(l)})`
}

/** 明暗档 0 背光 / 1 中间 / 2 受光 */
export function tone(shade: number): number {
  return shade > 0.72 ? 2 : shade > 0.4 ? 1 : 0
}
