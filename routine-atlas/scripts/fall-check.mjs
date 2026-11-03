/* 落叶诊断：node scripts/fall-check.mjs [seeds]
   把 LifeTree 里落叶用的两条曲线放在一起算：
   ① 现在用的「一阶惯性 + 终端速度」 y(s) = vt·(s − τ(1 − e^(−s/τ)))
   ② 物理引擎只加重力时的刚体          y(s) = ½ g s²
   用数字看两者差多远，免得「引个物理引擎就免费得到落叶」只是听起来对。
   单位换算按主干 300 单位 ≈ 12 m，即 1 单位 ≈ 4 cm。 */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'fall-'))
const out = join(dir, 'tree.mjs')
await build({
  entryPoints: ['src/lib/tree.ts'],
  bundle: true,
  format: 'esm',
  outfile: out,
  logLevel: 'error'
})
const { growTree } = await import(pathToFileURL(out).href)

const M_PER_UNIT = 0.04
const G = 9.8
const hash1 = n => {
  const v = Math.sin(n * 12.9898) * 43758.5453
  return v - Math.floor(v)
}
const lerp = (a, b, k) => a + (b - a) * k

/* 与 LifeTree.vue 的 drifters 一致：每三团取两片 */
function drifters(seed) {
  const m = growTree({ seed })
  const out = []
  m.clusters.forEach((c, i) => {
    if (i % 3 !== 0) return
    c.leaves.slice(0, 2).forEach((l, j) => {
      const k = Math.floor(i / 3)
      const h = hash1(c.x * 0.31 + c.y * 0.17 + j * 7.3 + k * 3.9)
      const h2 = hash1(h * 91.7 + 5.1)
      const h3 = hash1(h2 * 55.3 + 2.7)
      const t0 = 0.72 + (0.171 + (1 - c.level) * 0.42) * 0.22 + h * 0.012
      let vt = lerp(96, 62, Math.min(1, Math.max(0, (l.len - 12) / 10))) * (0.85 + h * 0.3)
      const tau = 0.35 + h2 * 0.3
      const drop = Math.max(4, -l.y - 1.5 - h3 * 3)
      const budget = (0.985 - t0) * 24 - 0.5
      if (drop / vt + tau > budget) vt = drop / Math.max(0.4, budget - tau)
      let s = drop / vt + tau
      for (let n = 0; n < 4; n++) s = drop / vt + tau * (1 - Math.exp(-s / tau))
      out.push({ drop, vt, tau, sLand: s, sway: 3.5 + h2 * 6, drift: 4 + h3 * 7, len: l.len, t0 })
    })
  })
  return out
}

const seeds = (process.argv[2] ?? '9,23,41').split(',').map(Number)
console.log('seed  片数  本式落时(s)  纯重力(s)  倍数  横移(单位)  终速(单位/s)  收尾(整轮)')
for (const seed of seeds) {
  const d = drifters(seed)
  const sLand = d.reduce((a, x) => a + x.sLand, 0) / d.length
  /* 只加重力：s = sqrt(2h/g)，h 换算成米 */
  const sG = d.reduce((a, x) => a + Math.sqrt((2 * x.drop * M_PER_UNIT) / G), 0) / d.length
  const side = d.reduce((a, x) => a + x.drift * x.sLand + 2 * x.sway, 0) / d.length
  const vt = d.reduce((a, x) => a + x.vt, 0) / d.length
  /* 收尾时刻换算回整轮坐标：t0 是 0–1，sLand 是秒，一轮 24 秒 */
  const end = Math.max(...d.map(x => x.t0 + (x.sLand + 0.5) / 24))
  console.log(
    `${String(seed).padStart(4)}  ${String(d.length).padStart(4)}  ` +
      `${sLand.toFixed(2).padStart(10)}  ${sG.toFixed(2).padStart(9)}  ` +
      `${(sLand / sG).toFixed(2).padStart(4)}  ${side.toFixed(1).padStart(10)}  ` +
      `${vt.toFixed(0).padStart(11)}  ${end.toFixed(3).padStart(8)}`
  )
}
/* 同一批叶子只加重力时，横向位移恒为 0（没有阻力、没有风），
   而真叶落地前会飘出 0.3–1 m —— 这部分必须自己写，引不引入引擎都要写。 */
console.log('\n参照：一片叶终端速度实测约 1 m/s（= 25 单位/s）、飘落 3–8 s；')
console.log('      纯重力从 12 m 落下 1.6 s 到底，且全程垂直。')
