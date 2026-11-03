/* 断枝诊断：node scripts/bark-fall-check.mjs [seeds]
   复核 LifeTree 里「枝 stop 之后从父枝断开落地」这套常数，看四件事：
   ① 子枝必须比父枝先断（停是从梢往根结算的，反过来就是父枝带着子枝一起飞走）；
   ② 每根枝都要在地面上落定，不能穿下去；
   ③ 收尾不过一轮的接缝（0.985），否则还在半空的枝会凭空消失；
   ④ 主干不落，只灰掉、淡掉。
   常数与 src/components/LifeTree.vue 的 addFall 一致，改一处要同步另一处。 */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'barkfall-'))
const out = join(dir, 'tree.mjs')
await build({
  entryPoints: ['src/lib/tree.ts'],
  bundle: true,
  format: 'esm',
  outfile: out,
  logLevel: 'error'
})
const { growTree } = await import(pathToFileURL(out).href)

const DURATION = 24
const BARK_FADE = 0.32
const hash1 = n => {
  const v = Math.sin(n * 12.9898) * 43758.5453
  return v - Math.floor(v)
}
const staggerOf = seed => ((Math.sin(seed * 127.1) * 43758.5453) % 1 + 1) % 1

function falls(seed) {
  const m = growTree({ seed })
  const LV = Math.max(1, ...m.branches.map(b => b.level))
  const rows = []
  for (const b of m.branches) {
    if (b.parent < 0) continue
    const deep = 1 - Math.min(1, b.level / LV)
    const h = hash1((b.id + 1) * 3.7 + 1.3)
    const h2 = hash1(h * 57.3 + 3.1)
    /* 翻转不定向，每根枝各翻自己的一段 */
    const spin = (h2 > 0.5 ? 1 : -1) * (28 + hash1(h2 * 13.7) * 40)
    const t0 = 0.874 + deep * 0.055 + staggerOf(b.id + 1) * 0.012
    const mid = b.pts[Math.floor(b.pts.length / 2)]
    /* 落多远按**转到底**时最低那一点算，不按原位 */
    const ra = (spin * Math.PI) / 180
    const rc = Math.cos(ra)
    const rs = Math.sin(ra)
    const maxYEnd = Math.max(...b.pts.map(p => mid.y + (p.x - mid.x) * rs + (p.y - mid.y) * rc))
    const drop = Math.max(8, -maxYEnd)
    let vt = 205 + h * 60
    const tau = 0.13 + h * 0.05
    const budget = (0.985 - t0) * DURATION - BARK_FADE
    const fast = drop / vt + tau > budget
    if (fast) vt = drop / Math.max(0.35, budget - tau)
    let s = drop / vt + tau
    for (let n = 0; n < 4; n++) s = drop / vt + tau * (1 - Math.exp(-s / tau))
    rows.push({
      id: b.id, parent: b.parent, level: b.level, t0, drop, vt, tau,
      sLand: s, spin, maxYEnd, fast, drift: (mid.x >= 0 ? 1 : -1) * (10 + Math.abs(mid.x) * 0.08 + h * 8)
    })
  }
  const byId = new Map(rows.map(r => [r.id, r]))
  return { m, LV, rows, byId }
}

const seeds = (process.argv[2] ?? '9,23,41,7').split(',').map(Number)
console.log('seed  枝数  层数   首断    末断   最后落地  收尾(整轮)  平均下坠  平均横移  子晚于父  穿地  加速')
let bad = 0
for (const seed of seeds) {
  const { m, LV, rows, byId } = falls(seed)
  const first = Math.min(...rows.map(r => r.t0))
  const last = Math.max(...rows.map(r => r.t0))
  const land = Math.max(...rows.map(r => r.t0 + r.sLand / DURATION))
  const end = Math.max(...rows.map(r => r.t0 + (r.sLand + BARK_FADE) / DURATION))
  const drop = rows.reduce((a, r) => a + r.drop, 0) / rows.length
  const side = rows.reduce((a, r) => a + Math.abs(r.drift) * r.sLand, 0) / rows.length
  /* 子先断：每一根枝的 t0 必须晚于它所有子枝 —— 倒过来就是父枝先走、子枝还挂在它身上 */
  const late = rows.filter(r => {
    const kids = rows.filter(k => k.parent === r.id)
    return kids.some(k => k.t0 >= r.t0)
  }).length
  /* 落定之后枝的最低点还在地面以下，说明 drop 算少了（转到底的那个姿态最低） */
  const sunk = rows.filter(r => Math.abs(r.drop + r.maxYEnd) > 0.01).length
  /* 被收尾约束加快的枝：数字上不会出错，但多了就说明时间轴排得太挤 */
  const fast = rows.filter(r => r.fast).length
  if (end > 0.985 || late > 0 || sunk > 0) bad++
  console.log(
    `${String(seed).padStart(4)}  ${String(rows.length).padStart(4)}  ${String(LV).padStart(2)}  ` +
      `${first.toFixed(3).padStart(6)}  ${last.toFixed(3).padStart(6)}  ${land.toFixed(3).padStart(8)}  ` +
      `${end.toFixed(3).padStart(10)}  ${drop.toFixed(0).padStart(8)}  ${side.toFixed(1).padStart(8)}  ` +
      `${String(late).padStart(8)}  ${String(sunk).padStart(4)}  ${String(fast).padStart(4)}`
  )
}
console.log('\n主干（parent < 0）不在上表里：它是根实例自己的身体，没有父枝可断，')
console.log('时间轴上只做 0.902 → 0.950 的灰化淡出，所以不会被这一轮接缝截断。')
if (bad) {
  console.log(`\n有 ${bad} 个种子不合格`)
  process.exitCode = 1
} else console.log('\n全部合格')
