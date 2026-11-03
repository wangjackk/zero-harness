/* 表：这张图背后那些注册名。
   名字来自 names.ts（py 探针导出 + ts / rust 手抄，都是能指出注册名的），
   位置是确定性的 —— 同一个下标永远落在同一个点，刷新不跳。 */

import { NAMES, TOTAL_NAMES } from './names'

export { TOTAL_NAMES }

export const W = 1000
export const H = 560
export const CX = W / 2
export const CY = H / 2

/* 三条扁椭圆，铺满整张舞台。装配环在最里面（R=160），表从 372 起，绕开中间 */
export const RINGS = [
  { rx: 372, ry: 226, n: 45 },
  { rx: 406, ry: 247, n: 47 },
  { rx: 440, ry: 266, n: 46 }
]

/* 确定性噪声：同一个下标永远得到同一个偏移，刷新不跳 */
function jit(i: number, salt: number): number {
  const h = Math.sin(i * 12.9898 + salt * 78.233) * 43758.5453
  return h - Math.floor(h)
}

export interface Slot {
  name: string
  ring: number
  x: number
  y: number
}

function buildTable(): Slot[] {
  const names = NAMES.map(r => r.name)
  const out: Slot[] = []
  let k = 0
  RINGS.forEach((ring, ri) => {
    for (let i = 0; i < ring.n; i++) {
      const name = names[k++]
      if (!name) continue
      const a = ((i + (jit(i, ri + 1) - 0.5) * 0.68) * 360) / ring.n
      const rad = (a * Math.PI) / 180
      out.push({
        name,
        ring: ri,
        x: CX + Math.cos(rad) * (ring.rx + (jit(i, ri + 11) - 0.5) * 22),
        y: CY + Math.sin(rad) * (ring.ry + (jit(i, ri + 23) - 0.5) * 16)
      })
    }
  })
  return out
}

export const TABLE: Slot[] = buildTable()
