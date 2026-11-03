/* 极小的动效工具：缓动、插值、路径采样。
   全部是纯函数 —— 时间是外部喂进来的，所以整条动画可以拖、可以暂停、可以跳。 */

export interface Pt {
  x: number
  y: number
}

export const clamp01 = (x: number) => (x < 0 ? 0 : x > 1 ? 1 : x)
export const clamp = (x: number, a: number, b: number) => (x < a ? a : x > b ? b : x)
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t

/** 某一拍内的局部进度：p 落在 [a,b] 之外时夹到 0 / 1 */
export const seg = (p: number, a: number, b: number) => clamp01((p - a) / (b - a))

export const easeInOutCubic = (t: number) =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2

export const easeOutCubic = (t: number) => 1 - Math.pow(1 - t, 3)
export const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t))
export const easeOutBack = (t: number) => {
  const c = 1.70158
  return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2)
}

/** 二次贝塞尔采样成折线 —— 之后用 walk() 沿它走点 */
export function bez(p0: Pt, c: Pt, p1: Pt, n = 24): Pt[] {
  const out: Pt[] = []
  for (let i = 0; i <= n; i++) {
    const t = i / n
    const u = 1 - t
    out.push({
      x: u * u * p0.x + 2 * u * t * c.x + t * t * p1.x,
      y: u * u * p0.y + 2 * u * t * c.y + t * t * p1.y
    })
  }
  return out
}

/** 圆弧采样 */
export function arc(cx: number, cy: number, r: number, a0: number, a1: number, n = 40): Pt[] {
  const out: Pt[] = []
  for (let i = 0; i <= n; i++) {
    const a = ((a0 + (a1 - a0) * (i / n)) * Math.PI) / 180
    out.push({ x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r })
  }
  return out
}

/** 折线上按归一化弧长取点（近似：按采样序号等分） */
export function walk(pts: Pt[], s: number): Pt {
  if (pts.length === 0) return { x: 0, y: 0 }
  if (pts.length === 1) return pts[0]
  const u = clamp01(s) * (pts.length - 1)
  const i = Math.floor(u)
  const f = u - i
  const a = pts[i]
  const b = pts[Math.min(pts.length - 1, i + 1)]
  return { x: lerp(a.x, b.x, f), y: lerp(a.y, b.y, f) }
}

/** #rrggbb → rgba()，给渐变和辉光用 */
export function rgba(hex: string, a: number): string {
  const h = hex.replace('#', '')
  const n = parseInt(h.length === 3 ? h.split('').map(c => c + c).join('') : h, 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}
