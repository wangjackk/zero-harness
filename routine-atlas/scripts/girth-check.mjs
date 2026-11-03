/* 核对「粗细由此刻撑住的截面积算出来」：打印主干与各级代表枝的 girth 曲线。
   公式与 LifeTree.vue 的 areaAll / girthAll 一致：
   girth = sqrt(子树里已长出的截面积 / 子树成树总截面积)，子枝封顶父枝。 */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'gr-'))
const out = join(dir, 't.mjs')
await build({ entryPoints: ['src/lib/tree.ts'], bundle: true, format: 'esm', outfile: out, logLevel: 'error' })
const { growTree } = await import(pathToFileURL(out).href)

const seed = Number(process.argv[2] ?? 9)
const m = growTree({ seed })
const c01 = (x) => (x < 0 ? 0 : x > 1 ? 1 : x)

const growP = (t) => c01((t - 0.07) / 0.56)
const branchP = (b, t) => c01((growP(t) - b.birth * 0.94) / Math.max(0.05, b.dur * 0.94))

const capOwn = m.branches.map(b => b.drawR[0] ** 2)
const capAll = capOwn.slice()
for (let i = m.branches.length - 1; i >= 0; i--) {
  const b = m.branches[i]
  if (b.parent >= 0) capAll[b.parent] += capAll[i]
}
function girthAt(t) {
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

/* 每级挑一根代表枝：该级里最长的那根 */
const rep = []
for (let l = 0; l <= Math.max(...m.branches.map(b => b.level)); l++) {
  const cand = m.branches.filter(b => b.level === l).sort((a, b) => b.length - a.length)
  if (cand[0]) rep.push(cand[0])
}
const ts = [0.12, 0.2, 0.3, 0.42, 0.55, 0.63]
console.log(`seed ${seed}：枝 ${m.branches.length}，层数 ${rep.length}`)
console.log('枝        birth  dur |  各时刻 girth')
console.log('                     ' + ts.map(v => String(v).padStart(5)).join(''))
for (const b of rep) {
  const cache = ts.map(t => girthAt(t)[b.id])
  console.log(
    `#${String(b.id).padStart(2)} L${b.level} ${b.length.toFixed(0).padStart(4)}`,
    b.birth.toFixed(2).padStart(5),
    b.dur.toFixed(2).padStart(4),
    '|', cache.map(v => v.toFixed(2).padStart(5)).join('')
  )
}
console.log(`\n主干根部直径（模型单位）随时间的宽度：`)
for (const t of ts) {
  const g = girthAt(t)[0]
  console.log(`  t=${t}  girth ${g.toFixed(3)}  半径 ${(m.branches[0].drawR[0] * g).toFixed(2)}  直径 ${(2 * m.branches[0].drawR[0] * g).toFixed(2)}`)
}
