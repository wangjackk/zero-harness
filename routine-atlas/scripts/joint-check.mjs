/* 检查每一处父子衔接：子枝根端边的采样点有多少落在父枝轮廓之外。
   父枝轮廓按「中心线 + 该处半径」近似 —— ribbon 就是这么生成的。
   判定量是「点到父枝中心线的最近距离」减去「父枝在该处的半径」，>0 就是露出背景。 */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'jc-'))
const out = join(dir, 't.mjs')
await build({ entryPoints: ['src/lib/tree.ts'], bundle: true, format: 'esm', outfile: out, logLevel: 'error' })
const { growTree } = await import(pathToFileURL(out).href)

const seeds = process.argv[2] ? process.argv[2].split(',').map(Number) : [7, 9, 23, 41]

function nearest(pts, p) {
  let best = { d: Infinity, i: 0, t: 0 }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1]
    const vx = b.x - a.x, vy = b.y - a.y
    const L2 = vx * vx + vy * vy || 1
    let t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / L2
    t = t < 0 ? 0 : t > 1 ? 1 : t
    const d = Math.hypot(p.x - (a.x + vx * t), p.y - (a.y + vy * t))
    if (d < best.d) best = { d, i, t }
  }
  return best
}

for (const seed of seeds) {
  const m = growTree({ seed })
  let bad = 0, worstAll = 0
  const rows = []
  for (const b of m.branches) {
    if (b.parent < 0) continue
    const par = m.branches[b.parent]
    const p0 = b.pts[0], p1 = b.pts[1]
    const dx = p1.x - p0.x, dy = p1.y - p0.y
    const L = Math.hypot(dx, dy) || 1
    const nx = -dy / L, ny = dx / L
    const r0 = b.radii[0]
    let worst = -Infinity
    for (let k = 0; k <= 10; k++) {
      const s = -1 + k * 0.2
      const q = { x: p0.x + nx * r0 * s, y: p0.y + ny * r0 * s }
      const nb = nearest(par.pts, q)
      const pr = par.radii[nb.i] + (par.radii[nb.i + 1] - par.radii[nb.i]) * nb.t
      const o = nb.d - pr
      if (o > worst) worst = o
    }
    if (worst > 0.05) {
      bad++
      if (worst > worstAll) worstAll = worst
      rows.push({ b, worst, par })
    }
  }
  rows.sort((a, b) => b.worst - a.worst)
  console.log(`seed ${seed}: 枝 ${m.branches.length}，根端露出父枝的 ${bad} 处，最深 ${worstAll.toFixed(2)}`)
  for (const r of rows.slice(0, 6)) {
    console.log(
      `   #${String(r.b.id).padStart(2)} L${r.b.level} → 父 #${r.par.id} L${r.par.level}`,
      `露出 ${r.worst.toFixed(2)}`,
      `根半径 ${r.b.radii[0].toFixed(2)}`,
      `父枝落点半径 ${r.par.radii[Math.min(r.par.radii.length - 1, 1)].toFixed(2)}`,
      `长 ${r.b.length.toFixed(0)}`
    )
  }
}
