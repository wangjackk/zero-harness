/* 树形诊断：node scripts/tree-stats.mjs [seeds]
   把 src/lib/tree.ts 打包后在 node 里直接算，量化「左右平衡 / 光杆枝 / 树冠分布」，
   免得只靠眼睛看截图判断「歪不歪」。 */
import { build } from 'esbuild'
import { writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'

const dir = mkdtempSync(join(tmpdir(), 'tree-'))
const out = join(dir, 'tree.mjs')
await build({
  entryPoints: ['src/lib/tree.ts'],
  bundle: true,
  format: 'esm',
  outfile: out,
  logLevel: 'error'
})
const { growTree, DEFAULT_TREE } = await import(pathToFileURL(out).href)

const seeds = (process.argv[2] ?? '7,9,23,41,77').split(',').map(Number)
const rows = []
for (const seed of seeds) {
  const m = growTree({ seed })
  /* 光杆枝：自己和整棵子树都没有叶 */
  const hasLeaf = new Set(m.clusters.map(c => c.owner))
  for (const b of m.branches) {
    let p = b.parent
    while (p >= 0) {
      hasLeaf.add(p)
      p = m.branches[p].parent
    }
  }
  const bare = m.branches.filter(b => !hasLeaf.has(b.id)).length

  const lx = m.leaves.filter(l => l.x < 0)
  const rx = m.leaves.filter(l => l.x >= 0)
  const cx = m.leaves.reduce((s, l) => s + l.x, 0) / Math.max(1, m.leaves.length)
  const total = m.leaves.length || 1
  const imb = Math.abs(lx.length - rx.length) / total

  /* 树冠分 8 层看叶量，中空会露出来 */
  const H = 300
  const bands = new Array(8).fill(0)
  for (const l of m.leaves) {
    const k = Math.min(7, Math.max(0, Math.floor((-l.y / H) * 8)))
    bands[k]++
  }

  const tips = m.branches.map(b => b.tip)
  const maxAbs = Math.max(...tips.map(p => Math.abs(p.x)))
  const spread = maxAbs / H

  /* 叶向：叶柄到团心的方向（= 该朝外）与叶尖方向的平均余弦。
     1 是全朝外，负数就是反着插的 —— rot 漏加枝方向那一项时这一列会掉到 0.05。 */
  let face = 0
  let fn = 0
  for (const c of m.clusters) {
    for (const l of c.leaves) {
      const ox = l.x - c.x
      const oy = l.y - c.y
      const om = Math.hypot(ox, oy) || 1
      const tx = Math.sin((l.rot * Math.PI) / 180)
      const ty = -Math.cos((l.rot * Math.PI) / 180)
      face += (ox / om) * tx + (oy / om) * ty
      fn++
    }
  }

  rows.push({
    seed,
    branch: m.branches.length,
    leaf: m.leaves.length,
    cluster: m.clusters.length,
    bare,
    imb: imb.toFixed(2),
    cx: cx.toFixed(1),
    spread: spread.toFixed(2),
    face: (face / Math.max(1, fn)).toFixed(2),
    bands: bands.map(v => String(v).padStart(3)).join(' ')
  })
}
console.log('默认参数：  h=' + DEFAULT_TREE.height + ' lv=' + DEFAULT_TREE.levels + ' ang=' + DEFAULT_TREE.angle.join('-'))
console.log('seed  枝  叶  团 光杆 失衡  重心x 幅宽 叶向  1  2  3  4  5  6  7  8（由低到高）')
for (const r of rows) {
  console.log(
    String(r.seed).padStart(4),
    String(r.branch).padStart(3),
    String(r.leaf).padStart(3),
    String(r.cluster).padStart(3),
    String(r.bare).padStart(4),
    String(r.imb).padStart(4),
    String(r.cx).padStart(6),
    String(r.spread).padStart(5),
    String(r.face).padStart(4),
    ' ',
    r.bands
  )
}
