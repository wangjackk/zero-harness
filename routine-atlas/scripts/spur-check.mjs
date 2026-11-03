/* 末级小分叉不该是「刺」。
   刺是三件事叠出来的，缺一件都还不至于：
   ① 枝短到撑不起自己的梢头团 —— 团心退进梢内 0.22·size，团却往外盖 1.2·size，
      枝整个被叶盖住，露在团外的只有一小截；
   ② 梢端收得太尖 —— ribbon 的最后一点乘 0.32，十几像素的枝就是一根针；
   ③ 极短的小分叉本就不该存在 —— 早先 childLen 用 Math.max(7, …) 兜底，
      把被包络压到 3–6 的长度抬到 7，生出一批十几像素的枝。
   现在①②按枝长分别处理，③直接不分叉。这个脚本守这三条。

   用法：node scripts/spur-check.mjs [seed …] */
import { build } from 'esbuild'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dir = mkdtempSync(join(tmpdir(), 'spur-'))
const out = join(dir, 't.mjs')
await build({ entryPoints: ['src/lib/tree.ts'], bundle: true, format: 'esm', outfile: out, logLevel: 'error' })
const { growTree, DEFAULT_TREE } = await import(pathToFileURL(out).href)

const seeds = process.argv.slice(2).map(Number).filter(n => n)
const list = seeds.length ? seeds : [7, 9, 23, 41, 101]
/* 枝长 / 梢头团半径 的下限：低于它就说明团比枝还大，枝被叶吞掉 */
const MIN_RATIO = 0.6
/* 末梢半径下限：再细就成了一条飘着的线。
   只对短枝判 —— 长枝末梢收成 0.32 是对的（真树梢部也细），
   而且它梢头有团盖着；短枝末梢露在团外，收尖就只剩一根针。 */
const MIN_TIP_R = 0.45

let bad = 0
for (const seed of list) {
  const m = growTree({ seed })
  const tipOf = new Map(m.clusters.map(c => [c.owner, c]))
  const short = []
  const lean = []
  const sharp = []
  let minRatio = Infinity
  for (const b of m.branches) {
    const c = tipOf.get(b.id)
    const size = c ? c.size : 0
    if (b.length < DEFAULT_TREE.clusterSize * 0.75) short.push(b)
    if (c) {
      const k = b.length / size
      if (k < minRatio) minRatio = k
      if (k < MIN_RATIO) lean.push({ b, c, k })
    }
    const tipR = b.radii[b.radii.length - 1]
    if (b.length < DEFAULT_TREE.clusterSize && tipR < MIN_TIP_R) sharp.push({ b, tipR })
  }
  const lens = m.branches.map(b => b.length).sort((a, b) => a - b)
  const med = lens[Math.floor(lens.length / 2)]
  const minLen = lens[0]
  const worst = lean.sort((a, b) => a.k - b.k)[0]
  const sharpest = sharp.sort((a, b) => a.tipR - b.tipR)[0]
  const line =
    `seed ${seed}: 枝 ${m.branches.length} 叶 ${m.leafCount} · 最短枝 ${minLen.toFixed(1)} 中位 ${med.toFixed(1)}` +
    ` | 撑不起团 ${lean.length}（最小比 ${minRatio.toFixed(2)}${worst ? ` #${worst.b.id}` : ''}）` +
    ` | 短枝针尖 ${sharp.length}${sharpest ? `（最细 #${sharpest.b.id} ${sharpest.tipR.toFixed(2)}）` : ''}` +
    ` | 极短 ${short.length}`
  console.log(line)
  if (short.length || lean.length || sharp.length) bad++
}

/* 树冠不能因为砍掉小分叉而露空：叶数与包围盒要和砍之前同量级 */
console.log('---')
for (const seed of list) {
  const m = growTree({ seed })
  console.log(`seed ${seed} 包围盒 ${m.box.w.toFixed(0)}×${m.box.h.toFixed(0)}  叶 ${m.leafCount}  团 ${m.clusters.length}`)
}
console.log(bad ? `\n不合格：${bad} 个种子` : '\n全部合格')
process.exit(bad ? 1 : 0)
