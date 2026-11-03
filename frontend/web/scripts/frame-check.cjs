/* 取景的数值检查（不进构建）。
   判据只有一条：**画框边长只能变大，不能变小**。

   早先是两条规则同时在跑（来新节点扩、静 1.2s 缩），而树的规模在运行中
   本来就在增删之间来回 —— 于是「扩 → 缩 → 扩」循环，整棵树在呼吸。
   用户看到的是「一会变大一会变小」。这条曲线只能靠脚本量，眼睛看不过来。

   用法：node scripts/frame-check.cjs */
const path = require('path')
const { buildSync } = require('esbuild')

const load = entry => {
  const out = buildSync({
    entryPoints: [path.join(__dirname, entry)],
    bundle: true,
    format: 'cjs',
    platform: 'node',
    write: false,
    logLevel: 'warning',
  })
  const mod = { exports: {} }
  new Function('module', 'exports', 'require', out.outputFiles[0].text)(mod, mod.exports, require)
  return mod.exports
}
const { buildGrowth } = load('../src/lib/growth.ts')
const { frameOf, grown, sideFor } = load('../src/lib/frame.ts')

function makeTree(target, seed = 7) {
  let s = seed
  const rnd = () => ((s = (s * 1103515245 + 12345) % 2147483648) / 2147483648)
  const nodes = [{ id: '1', parent_id: '', name: 'root', state: 'running' }]
  const open = ['1']
  let id = 1
  while (nodes.length < target && open.length) {
    const par = open.shift()
    const k = 2 + Math.round(rnd() * 2)
    for (let i = 0; i < k && nodes.length < target; i++) {
      const nid = String(++id)
      nodes.push({ id: nid, parent_id: par, name: 'r' + nid, state: 'running' })
      if (rnd() > 0.3) open.push(nid)
    }
  }
  return nodes
}

/* 一条真实的增删时间线：长到 120 → 砍回 20 → 再长到 200。
   每一步都调一次 grown，记录画框边长 —— 它必须单调不减 */
const full = makeTree(200)
const steps = []
steps.push(...full.slice(1, 120).map((_, i) => full.slice(0, i + 2)))
for (let k = 120; k > 20; k -= 4) steps.push(full.slice(0, k))
for (let k = 20; k <= 200; k += 2) steps.push(full.slice(0, k))

let frame = frameOf(192)
let shrinks = 0
let changes = 0
let worst = 1
let contentOvers = 0
const trace = [frame.w]
for (const nodes of steps) {
  const m = buildGrowth(nodes)
  const next = grown(frame, m.box)
  if (next) {
    if (next.w < frame.w) shrinks++
    worst = Math.max(worst, next.w / frame.w)
    changes++
    frame = next
    trace.push(frame.w)
  }
  /* 取景之后必须真的装得下，否则树会被画到框外 */
  if (sideFor(m.box) > frame.w) contentOvers++
}

/* 顺带看一眼「一直不缩」的代价：一条会话里树最大过之后，画框会一直那么大。
   这里量的是「树最小的时候占画框多少」——太低说明画框收不回来是真问题 */
const small = buildGrowth(full.slice(0, 20))
const fill = sideFor(small.box) / frame.w

console.log(
  `画框 · ${steps.length} 次重算 · 变更 ${changes} 次 · 缩小 ${shrinks} 次` +
    ` · 单次最大放大 ${(worst * 100 - 100).toFixed(0)}%` +
    ` · 出框 ${contentOvers}` +
    ` · 192 → ${frame.w}` +
    ` · 20 节点时占框 ${(fill * 100).toFixed(0)}%`
)
if (shrinks || contentOvers) {
  console.log('失败：画框缩回过，或有树被画到框外')
  process.exit(1)
}
