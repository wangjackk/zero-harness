/* 骨架 + 映射的**定格**验收页（不进构建，不参与产品）。

   上半：同一棵 131 节点的树，按 6 个时刻各画一帧，所有帧共用同一个画框 ——
         画框若跟着包围盒变，看起来会是「镜头在推」，看不出是枝在长。
   中段：**叶变枝**的四个时刻 —— 同一棵树的同一个节点，
         它长出第一个子实例之后，线从同一个着生点接着往外长。
         这是本次改动的重点：叶和枝不是两个东西，是同一条线的两种长度。
   下半：同一个节点在「它的子实例结束之后」的两种画法 ——
         只按当前 kids 判形态会缩回一片叶，带上「长出过子实例」的历史则留成一条空枝。

   这里只核对时序与形态判据，配色以 RoutineTreeHud.vue 为准，
   别拿这份当第二套实现去改。

   用法：node scripts/growth-frames.cjs  → 生成 growth-frames.html */
const fs = require('fs')
const path = require('path')
const { buildSync } = require('esbuild')

const out = buildSync({
  entryPoints: [path.join(__dirname, '../src/lib/growth.ts')],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  write: false,
  logLevel: 'warning',
})
const mod = { exports: {} }
new Function('module', 'exports', 'require', out.outputFiles[0].text)(mod, mod.exports, require)
const { buildGrowth, leafAt, LEAF_UNIT, bornTimes, finishedAt, stemP, leafFrame, BEAT } = mod.exports

/* 与 growth-check.cjs 同一棵模拟树 */
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

const GREEN = ['#244328', '#305a2e', '#417136']
const BARK = '#4b4237'
const BARK_NEW = '#3f4a48'
const tone = s => (s > 0.72 ? 2 : s > 0.4 ? 1 : 0)
/* 枝 = 还没挂叶片的线；叶 = 挂着叶片的线。同一个类型，只是筛一遍 */
const barksOf = m => m.stems.filter(s => !s.blade)
const leavesOf = m => m.stems.filter(s => !!s.blade)

const PAD = 18
/* 场景：一份拓扑 → 骨架 + 起点 + 一个装得下它的方画框。
   起点与进度都直接问 sdk，这份页面不另写一条映射 ——
   自己再实现一遍的话，验收的就不是产品，而是这份脚本的副本 */
function scene(nodes, branched, seen) {
  const m = buildGrowth(nodes, branched ? { branched } : {})
  const bs = barksOf(m)
  const leaves = leavesOf(m)
  const born = bornTimes(m, seen || (() => 0))
  const total = finishedAt(m, born)
  const half = Math.max(Math.abs(m.box.x), Math.abs(m.box.x + m.box.w))
  const side = Math.ceil((Math.max(half * 2, m.box.h) + PAD) / 10) * 10
  return { m, bs, leaves, born, total, side }
}
const vbOf = side => `${(-side / 2).toFixed(0)} ${(12 - side).toFixed(0)} ${side} ${side}`

/** 一帧。`t` 是绝对时刻（毫秒）；`mark` 是给对照帧加的虚线圈 */
function frame(sc, t, i, vbs, w, mark) {
  const parts = []
  parts.push(`<rect x="${-sc.side / 2}" y="${12 - sc.side}" width="${sc.side}" height="${sc.side}" fill="#12140f"/>`)

  /* 遮罩：正在长的线用圆头 —— 那是芽尖；未出生/已长满保持平头 */
  const masks = sc.bs
    .map(b => {
      const g = stemP(b, sc.born, t)
      return (
        `<path d="${b.spine}" fill="none" stroke="#fff" stroke-width="${Math.max(2.4, b.r0 * 2.3)}" ` +
        `stroke-linecap="${g > 0 && g < 1 ? 'round' : 'butt'}" pathLength="1" stroke-dasharray="1" ` +
        `stroke-dashoffset="${(1 - g).toFixed(4)}"/>`
      )
    })
    .join('')
  parts.push(
    `<defs><mask id="m${i}" maskUnits="userSpaceOnUse" x="${-sc.side}" y="${-sc.side * 2}" ` +
      `width="${sc.side * 3}" height="${sc.side * 3}">${masks}</mask></defs>`
  )

  const bark = sc.bs
    .map(b => `<path d="${b.ribbon}" fill="${b.state === 'created' ? BARK_NEW : BARK}"/>`)
    .join('')
  parts.push(`<g mask="url(#m${i})">${bark}</g>`)

  parts.push(
    sc.leaves
      .map(l => {
        /* 直接读产品的叶帧：叶柄伸长、叶片随柄端移动并从 6% 展开 */
        const v = leafFrame(l, sc.born, t)
        if (!v || v.opacity <= 0) return ''
        const posed = { ...l, blade: { ...l.blade, x: v.x, y: v.y } }
        return (
          `<line x1="${l.mesh.x.toFixed(1)}" y1="${l.mesh.y.toFixed(1)}" ` +
          `x2="${v.x.toFixed(1)}" y2="${v.y.toFixed(1)}" ` +
          `stroke="#2f4a2c" stroke-width="0.7" opacity="${v.opacity.toFixed(2)}"/>` +
          `<path d="${LEAF_UNIT}" transform="${leafAt(posed, v.scale)}" fill="${GREEN[tone(l.blade.shade)]}" opacity="${v.opacity.toFixed(2)}"/>`
        )
      })
      .join('')
  )
  if (mark) {
    parts.push(
      `<circle cx="${mark.x.toFixed(1)}" cy="${mark.y.toFixed(1)}" r="16" fill="none" ` +
        `stroke="#c9a35c" stroke-width="0.9" stroke-dasharray="3 3" opacity="0.6"/>`
    )
  }
  return `<svg viewBox="${vbs}" width="${w}" height="${w}">${parts.join('')}</svg>`
}

/* ---- 上半：生长时序 ---- */
const main = scene(makeTree(131))
const ts = [0.3, 0.6, 0.9, 1.2, 1.8].map(v => v * 1000).concat(main.total + BEAT)
const cells = ts
  .map((t, i) => `<figure><figcaption>t = ${(t / 1000).toFixed(1)}s</figcaption>${frame(main, t, i, vbOf(main.side), 300)}</figure>`)
  .join('')

/* ---- 中段：叶变枝 = 同一条线在同一个着生点继续长 ----
   取一片挂在「一级枝」上的叶 L。
   - 第 1 帧：它还是叶（拓扑里没有子实例），叶片张开在位。
   - 第 2–4 帧：给它一个子实例之后。它的 `blade` 没了、`len` 变成一条枝的长度，
     而 `mesh`/`at` 与第 1 帧**完全相同** —— 线没有挪窝，只是继续往外抽。
   过渡那一刻在页面里是 `born` 被重置为当下（叶早就长满了），
   这里用 `seen` 把 L 的出生时刻固定到 T0，等价于同一件事。 */
const nodes = makeTree(131)
const leafSc = scene(nodes)
const L = leavesOf(leafSc.m).find(l => (leafSc.m.byId.get(l.parent)?.depth ?? 0) === 1) || leavesOf(leafSc.m)[0]
const kid = { id: 'K' + L.id, parent_id: L.id, name: 'ipython', state: 'running' }
/* 四帧共用一个时刻基准：整棵树在那一刻已经长满，
   所以除了 L 这一条线，其余画面必须逐像素相同 —— 否则比的不是形态变化 */
const leafEnd = leafSc.total + BEAT
const T0 = leafEnd
const grownSc = scene([...nodes, kid], null, s => (s.id === L.id ? T0 : 0))
const dL = BEAT * grownSc.m.byId.get(L.id).dur
/* 两段必须同一个画框：第 1 帧是「没有 kid」的拓扑，包围盒小一点 */
const pairSide = Math.max(leafSc.side, grownSc.side)
leafSc.side = grownSc.side = pairSide
const turnVb = vbOf(pairSide)
const grow4 = [
  { sc: leafSc, t: T0, cap: '① 叶 · 还没有子实例' },
  { sc: grownSc, t: T0 + dL * 0.12, cap: '② 长出子实例 · 线从这一点起头' },
  { sc: grownSc, t: T0 + dL * 0.5, cap: '③ 抽到一半' },
  { sc: grownSc, t: T0 + dL, cap: '④ 长满' },
]
const turnCells = grow4
  .map(
    (f, i) =>
      `<figure><figcaption>${f.cap}</figcaption>` +
      `${frame(f.sc, f.t, 'g' + i, turnVb, 300, f.sc.m.byId.get(L.id)?.mesh ?? L.mesh)}</figure>`
  )
  .join('')

/* ---- 下半：子实例结束之后 ----
   挑同一个 L（用在无 kid 的拓扑上），把 kid 结束掉 —— 真实系统里这一步就是子 routine 跑完。

   旧：只看当前 kids → L 的 kids 为空 → 它退回成叶，枝没了，原地出现一片叶。
   新：带上「L 长出过子实例」这条历史 → L 仍是枝，只是这条线上没有叶了。 */
const oldSc = scene(nodes)
const newSc = scene(nodes, new Set([L.id]))
/* 顺手核一嘴：加上 kid 的那一刻，L 确实是一条枝（否则这个对照根本没发生过） */
const withKid = buildGrowth([...nodes, kid])
const wasBranch = !withKid.byId.get(L.id).blade
const side = Math.max(oldSc.side, newSc.side)
oldSc.side = newSc.side = side
const vbs = vbOf(side)
const end = Math.max(oldSc.total, newSc.total) + BEAT

const pair =
  `<figure><figcaption>旧 · 只看当前 kids → 缩回一片叶（虚线圈内）</figcaption>` +
  `${frame(oldSc, end, 'o', vbs, 300, oldSc.m.byId.get(L.id)?.blade)}</figure>` +
  `<figure><figcaption>新 · 记得长出过子实例 → 留成空枝（虚线圈内，线还在、叶没了）</figcaption>` +
  `${frame(newSc, end, 'n', vbs, 300, newSc.m.byId.get(L.id)?.mesh)}</figure>`

const html = `<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>growth frames</title>
<style>
  body{margin:0;padding:24px;background:#0c0d0b;color:#b8b5aa;
       font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}
  h1{font-size:14px;font-weight:500;color:#e6e3d8;margin:0 0 4px}
  h2{font-size:13px;font-weight:500;color:#cfccbf;margin:30px 0 6px}
  p{margin:0 0 18px;color:#8a877c}
  .grid{display:grid;grid-template-columns:repeat(3,300px);gap:20px}
  .four{display:grid;grid-template-columns:repeat(4,300px);gap:20px}
  .pair{display:grid;grid-template-columns:repeat(2,300px);gap:20px}
  figure{margin:0}
  figcaption{color:#7d8a7d;margin:0 0 6px}
  svg{display:block;border-radius:10px}
</style>
</head>
<body>
<h1>131 节点 · 六个时刻</h1>
<p>同一画框、同一份骨架；每条线按 len 等比抽长，叶片在它自己的线上张开</p>
<div class="grid">${cells}</div>

<h2>叶变枝 · 同一个节点 ${L.id}（${L.name}）</h2>
<p>虚线是它的着生点。四帧里这一点<b>一个像素都没动</b>：叶片收掉、线接着往外抽 —— 不是「换了一种东西」，是同一条线继续长</p>
<div class="four">${turnCells}</div>

<h2>子实例结束之后 · 同一个节点</h2>
<p>两帧都是「${kid.name} 已经结束」的静止状态，同一画框。差别只在骨架有没有收到「它长出过子实例」这条历史</p>
<div class="pair">${pair}</div>
</body>
</html>`

fs.writeFileSync(path.join(__dirname, '../growth-frames.html'), html)
console.log(
  `ok · 131 节点 / ${main.bs.length} 枝 / ${main.leaves.length} 叶 · 全长 ${(main.total / 1000).toFixed(2)}s` +
    ` · 对照节点 ${L.id}：叶→枝 ${(dL / 1000).toFixed(2)}s（${dL.toFixed(0)}ms）` +
    ` · 着生点 (${L.mesh.x.toFixed(1)}, ${L.mesh.y.toFixed(1)}) → ` +
    `(${grownSc.m.byId.get(L.id).mesh.x.toFixed(1)}, ${grownSc.m.byId.get(L.id).mesh.y.toFixed(1)})` +
    ` · 加 kid 时是枝 ${wasBranch}` +
    ` · kid 结束后：旧 ${oldSc.bs.length} 枝 ${oldSc.leaves.length} 叶 / 新 ${newSc.bs.length} 枝 ${newSc.leaves.length} 叶`
)
