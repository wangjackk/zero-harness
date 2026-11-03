/* 与 joint-check 同一判据，但按生长中的粗细（管道模型）逐时刻检查：
   子枝根端边的采样点有没有跑到父枝轮廓之外。
   girth 公式与 LifeTree.vue 的 areaAll / girthAll 保持一致。 */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'jct-'))
const out = join(dir, 't.mjs')
await build({ entryPoints: ['src/lib/tree.ts'], bundle: true, format: 'esm', outfile: out, logLevel: 'error' })
const { growTree } = await import(pathToFileURL(out).href)

const seeds = process.argv[2] ? process.argv[2].split(',').map(Number) : [7, 9, 23, 41]
const c01 = x => (x < 0 ? 0 : x > 1 ? 1 : x)

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
  const growP = t => c01((t - 0.07) / 0.56)
  const branchP = (b, t) => c01((growP(t) - b.birth * 0.94) / Math.max(0.05, b.dur * 0.94))
  const capOwn = m.branches.map(b => b.drawR[0] ** 2)
  const capAll = capOwn.slice()
  for (let i = m.branches.length - 1; i >= 0; i--) {
    const b = m.branches[i]
    if (b.parent >= 0) capAll[b.parent] += capAll[i]
  }
  const girthAt = t => {
    const a = new Array(m.branches.length).fill(0)
    for (let i = m.branches.length - 1; i >= 0; i--) {
      const b = m.branches[i]
      a[b.id] += capOwn[b.id] * branchP(b, t)
      if (b.parent >= 0) a[b.parent] += a[b.id]
    }
    const g = new Array(m.branches.length)
    for (const b of m.branches) {
      const own = Math.sqrt(Math.max(0, a[b.id]) / Math.max(1e-9, capAll[b.id]))
      g[b.id] = b.parent < 0 ? own : Math.min(own, g[b.parent])
    }
    return g
  }
  const times = [0.2, 0.28, 0.36, 0.44, 0.52, 0.63]
  const hits = []
  for (const t of times) {
    const g = girthAt(t)
    for (const b of m.branches) {
      if (b.parent < 0) continue
      const par = m.branches[b.parent]
      const p0 = b.pts[0], p1 = b.pts[1]
      const dx = p1.x - p0.x, dy = p1.y - p0.y
      const L = Math.hypot(dx, dy) || 1
      const nx = -dy / L, ny = dx / L
      const r0 = b.radii[0] * g[b.id]
      /* 还没长出来的枝是零宽的一条线，它的根点贴在父枝表面那一侧时会被判成「露出」，
         但那时它根本没有轮廓。只看已经粗到能看见的枝。 */
      if (r0 < 0.35) continue
      let worst = -Infinity
      for (let k = 0; k <= 10; k++) {
        const s = -1 + k * 0.2
        const q = { x: p0.x + nx * r0 * s, y: p0.y + ny * r0 * s }
        const nb = nearest(par.pts, q)
        const pr = (par.radii[nb.i] + (par.radii[nb.i + 1] - par.radii[nb.i]) * nb.t) * g[par.id]
        const o = nb.d - pr
        if (o > worst) worst = o
      }
      if (worst > 0.05) hits.push({ t, id: b.id, par: par.id, worst })
    }
  }
  const worst = hits.length ? Math.max(...hits.map(h => h.worst)) : 0
  console.log(
    `seed ${seed}: 枝 ${m.branches.length}，${times.length} 个时刻里露出父枝 ${hits.length} 处，最深 ${worst.toFixed(2)}`
  )
  for (const h of hits.sort((a, b) => b.worst - a.worst).slice(0, 6)) {
    console.log(`   t=${h.t} #${h.id} → 父 #${h.par} 露出 ${h.worst.toFixed(2)}`)
  }
}
