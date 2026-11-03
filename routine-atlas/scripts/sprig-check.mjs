/* 检查新梢的落点在画面上干不干净。
   叶实例长成枝以后，原位不该再有叶：真被顶掉的是「锚点那一团」，
   但邻团的叶仍可能压在落点上。这里按 LifeTree 的挑法复现三个落点，
   统计未让位的团有多少片叶落在落点附近，并检查落点是否落在自己那根枝的枝身里。 */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'sc-'))
const out = join(dir, 't.mjs')
await build({ entryPoints: ['src/lib/tree.ts'], bundle: true, format: 'esm', outfile: out, logLevel: 'error' })
const { growTree } = await import(pathToFileURL(out).href)

const seeds = process.argv[2] ? process.argv[2].split(',').map(Number) : [7, 9, 23, 41]
const UP = -Math.PI / 2
/** 落点周围多大范围算「那里」—— 新梢根半径 2、叶长约 12，取 16 */
const R = 16
const D2R = Math.PI / 180

/** 点到折线的最近距离与那里的半径 */
function nearest(pts, radii, p) {
  let best = { d: Infinity, r: radii[0] }
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i]
    const b = pts[i + 1]
    const vx = b.x - a.x
    const vy = b.y - a.y
    const L2 = vx * vx + vy * vy || 1
    let t = ((p.x - a.x) * vx + (p.y - a.y) * vy) / L2
    t = t < 0 ? 0 : t > 1 ? 1 : t
    const d = Math.hypot(p.x - (a.x + vx * t), p.y - (a.y + vy * t))
    if (d < best.d) best = { d, r: radii[i] + (radii[i + 1] - radii[i]) * t }
  }
  return best
}

for (const seed of seeds) {
  const m = growTree({ seed })
  const baseOf = c => {
    const dir = c.dir * D2R
    const d2 = dir + (UP - dir) * 0.3
    const rot = d2 / D2R + 90
    const rad = rot * D2R
    return { ax: c.x - Math.cos(dir) * 6, ay: c.y - Math.sin(dir) * 6, rot, cos: Math.cos(rad), sin: Math.sin(rad) }
  }
  const strayAt = (c, base) => {
    let n = 0
    for (const o of m.clusters) {
      if (o === c) continue
      for (const l of o.leaves) if (Math.hypot(l.x - base.ax, l.y - base.ay) < R) n++
    }
    return n
  }
  const pool = m.clusters
    .filter(c => c.level > 0.5 && c.level < 0.84)
    .sort((a, b) => Math.abs(b.x) - Math.abs(a.x))
    .map(c => ({ c, base: baseOf(c) }))
  for (const o of pool) o.stray = strayAt(o.c, o.base)
  const outer = Math.max(...pool.map(o => Math.abs(o.c.x)))
  const rank = s => (s === 0 ? 0 : s <= 2 ? 1 : 2)
  const cand = pool.sort(
    (a, b) =>
      rank(a.stray) - rank(b.stray) ||
      Number(Math.abs(b.c.x) >= outer - 28) - Number(Math.abs(a.c.x) >= outer - 28) ||
      Math.abs(b.c.x) - Math.abs(a.c.x)
  )

  const picked = []
  for (const o of cand) {
    if (picked.length >= 3) break
    if (picked.some(p => Math.hypot(p.base.ax - o.base.ax, p.base.ay - o.base.ay) < 62)) continue
    picked.push(o)
  }
  console.log(`seed ${seed}: ${picked.length} 个新梢，可选落点 ${pool.length}（干净 ${pool.filter(o => o.stray === 0).length}）`)
  for (const o of picked) {
    const b = m.branches[o.c.owner]
    const nb = nearest(b.pts, b.radii, { x: o.base.ax, y: o.base.ay })
    console.log(
      `   落点(${o.base.ax.toFixed(0)},${o.base.ay.toFixed(0)}) 距本枝中心线 ${nb.d.toFixed(1)} / 宿主半径 ${nb.r.toFixed(2)}`,
      `· 16 内邻团叶 ${o.stray}`
    )
  }
}
