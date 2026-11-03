/* 页面层的落叶轨迹。输入是离枝那一帧的真实叶位姿，不是骨架里的静态蓝图位置。
   纯函数：同一个离枝状态 + 同一个时刻，永远得到同一个落点，便于逐帧验收。 */
import type { GStem, LeafFrame } from './growth'

export interface LeafFall {
  key: string
  stem: GStem
  origin: LeafFrame
  t0: number
  hold: number
  vt: number
  tau: number
  sway: number
  omega: number
  phase: number
  spin: number
  drift: number
  drop: number
  sLand: number
}

export interface LeafFallPose {
  x: number
  y: number
  rot: number
  scale: number
  opacity: number
  /** 已经离枝多少秒；hold 期间为 0 */
  released: number
}

function hashOf(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 100000) / 100000
}

/** 解一次不动点：s = D/vt + τ(1 − e^(−s/τ)) */
function landTime(drop: number, vt: number, tau: number): number {
  let s = drop / vt + tau
  for (let i = 0; i < 4; i++) s = drop / vt + tau * (1 - Math.exp(-s / tau))
  return s
}

/** 把运行时保存的离枝位姿变成一条完整的落体轨迹。
 *  origin 同时决定起点、叶片大小、初始透明度和落地距离；
 *  这四个量不能在开始掉落时改回 blade 的静态值。 */
export function createLeafFall(
  key: string,
  stem: GStem,
  origin: LeafFrame,
  t0: number
): LeafFall {
  const h = hashOf(stem.id)
  const h2 = hashOf(stem.id + 'b')
  const drop = Math.max(6, -origin.y)
  const vt = drop / (0.95 + h * 1.0)
  const tau = 0.16 + h * 0.12
  return {
    key,
    stem,
    origin,
    t0,
    hold: 0.02 + h2 * 0.4,
    vt,
    tau,
    sway: 12 + h2 * 16,
    omega: 2.4 + h * 1.8,
    phase: h2 * Math.PI * 2,
    spin: (h > 0.5 ? 1 : -1) * (44 + h2 * 96),
    drift: (h2 > 0.5 ? 1 : -1) * (10 + h2 * 22),
    drop,
    sLand: landTime(drop, vt, tau),
  }
}

/** 某一时刻的叶片位姿。rel=0 必须精确等于 origin：
 *  横向摆动要减去自己的初值，否则 sin(phase) 会让叶片离枝时先横跳一下。 */
export function leafFallAt(f: LeafFall, t: number): LeafFallPose {
  const blade = f.stem.blade
  const sec = Math.max(0, (t - f.t0) / 1000)
  const rel = Math.max(0, sec - f.hold)
  const falling = Math.min(rel, f.sLand)
  const y = f.vt * (falling - f.tau * (1 - Math.exp(-falling / f.tau)))
  const k = Math.min(1, y / Math.max(1, f.drop))
  const sway = f.sway *
    (Math.sin(f.omega * falling + f.phase) - Math.sin(f.phase)) *
    (1 - k * k * k)
  const landed = rel - f.sLand
  return {
    x: f.origin.x + sway + f.drift * falling,
    y: f.origin.y + y,
    rot: (blade?.rot ?? 0) + f.spin * falling,
    scale: f.origin.scale,
    opacity: f.origin.opacity * (landed > 0 ? Math.max(0, 1 - landed / 0.45) : 1),
    released: rel,
  }
}
