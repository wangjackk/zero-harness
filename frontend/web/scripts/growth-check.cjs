/* 骨架 + 映射的数值检查（不进构建）。
   一棵树在画面上只有两件事，这里各验一条：

   ① 骨架：`len / dur` 是否恒定 —— 是，则整棵树只有一种抽长速度，
      短枝短、快，长枝长、慢，而不是「所有枝同时长完」。
   ② 映射：跑**产品里的那几个函数**（sdk 的 stemP），
      验端点、单调、以及叶不会早于父线长到自己的落点。

   这两个函数以前在 HUD 组件里，脚本只能照抄一遍（ease / since / open），
   于是脚本测的是它自己的实现 —— 改错了它一起错，照样通过。
   现在映射搬进 sdk，`require` 到这里的就是产品本身。

   用法：node scripts/growth-check.cjs                       */
const path = require('path')
const { buildSync } = require('esbuild')

/* 直接从 TS 源码跑，不留中间产物：改完 skeleton.ts / growth.ts 立刻能验 */
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
const { buildGrowth, bornTimes, finishedAt, stemP, rawP, BEAT } = mod.exports

/* 照真实实例树的形状造一棵：数字 id、父在前、叶子与枝混合 */
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
      /* 三成是叶子（不再往下开），剩下继续 */
      if (rnd() > 0.3) open.push(nid)
    }
  }
  return nodes
}

const ms = v => (v / 1000).toFixed(2) + 's'
/* 枝 = 头上没有叶片的线；叶 = 头上挂着叶片的线。同一个类型，只是筛一遍 */
const barksOf = m => m.stems.filter(s => !s.blade)
const leavesOf = m => m.stems.filter(s => !!s.blade)

for (const n of [17, 21, 23, 131, 300]) {
  const nodes = makeTree(n)
  const m = buildGrowth(nodes)
  const bs = barksOf(m)
  if (!bs.length) continue
  const leaves = leavesOf(m)

  /* ① 速度 = len / dur。只统计没被上下限夹住的那些 */
  const mid = bs.filter(b => b.dur > 0.65 + 1e-9 && b.dur < 1.8 - 1e-9)
  const sp = mid.map(b => b.len / b.dur)
  const avg = sp.reduce((a, b) => a + b, 0) / (sp.length || 1)
  const dev = Math.max(...sp.map(v => Math.abs(v - avg)), 0) / avg
  const clamped = bs.length - mid.length

  /* ② 起点：最坏情况 —— 每个实例一被看见就要等父线长到自己的落点 */
  const born = bornTimes(m, () => 0)
  const total = finishedAt(m, born)

  /* 端点必须精确：出生那一刻一丝不露，走完那一刻刚好长满。
     中间逐步单调。这三条不成立，「露出一半」和「长满」就不是同一套刻度。
     端点用容差比 —— `t0 + dur − t0` 在浮点上不总是恰好等于 `dur`，
     差个 1e-13 不是映射错，是双精度的尾数 */
  let bad = 0
  for (const b of bs) {
    const t0 = born.get(b.id)
    const end = t0 + BEAT * b.dur
    if (Math.abs(stemP(b, born, t0)) > 1e-9) bad++
    if (Math.abs(stemP(b, born, end) - 1) > 1e-6) bad++
    let prev = -1
    for (let k = 0; k <= 40; k++) {
      const p = stemP(b, born, t0 + ((end - t0) * k) / 40)
      if (p < prev - 1e-12) bad++
      prev = p
    }
  }

  /* 叶：不早于父线长到 `at`，且最后必须能满 ——
     早先固定 0.14 的淡入窗口下，梢头的叶（at=0.98）永远只到四成，整棵树发灰。
     现在叶的张开就是它自己的 `stemP`：它的出生时刻已经是「父线长到 at」，
     所以这三条判据与枝完全同一套，不再需要单独一个函数 */
  const tEnd = total + BEAT
  let early = 0
  let notFull = 0
  for (const l of leaves) {
    const par = m.byId.get(l.parent)
    if (!par) continue
    const gate = born.get(par.id) + BEAT * par.dur * l.at
    if (stemP(l, born, gate - 0.001) !== 0) early++
    if (stemP(l, born, tEnd) < 1) notFull++
  }

  /* 叶长出第一个子实例 → 它自己变成枝。这是一次**形态变更**，
     抽得太短就是「闪一下变成枝」，看着像画错了 */
  const deep = leaves.filter(l => (m.byId.get(l.parent)?.depth ?? 0) >= 3).slice(0, 12)
  const turn = deep.map(l => {
    const after = buildGrowth([
      ...nodes,
      { id: 'K' + l.id, parent_id: l.id, name: 'x', state: 'running' },
    ])
    return BEAT * after.byId.get(l.id).dur
  })
  turn.sort((a, b) => a - b)

  /* 正在生长（0<p<1）的枝数随时间怎么变：一起起步一起停会是方波 */
  let peak = 0
  for (let t = 0; t <= total; t += total / 160) {
    const act = bs.filter(b => {
      const p = rawP(b, born, t)
      return p > 0 && p < 1
    }).length
    if (act > peak) peak = act
  }

  /* 叶的时长来自骨架的同一条公式（可见尺寸 / 主干长） */
  const ld = leaves.map(l => l.dur)
  /* 叶团离枝身多远、叶自己多大 —— 拿宿主线长当尺子。
     比值过 1 就是「叶比生它的枝还大」，梢头会读成插了一片大绿片 */
  const bulk = leaves.map(l => {
    const par = m.byId.get(l.parent)
    if (!par || !par.len || !l.blade) return 0
    const spread = Math.hypot(l.tip.x - l.mesh.x, l.tip.y - l.mesh.y)
    return (spread + l.blade.wid / 1.05) / par.len
  })
  /* 同一父上、落点几乎相同的两条线：根部叠在一起 */
  const twin = new Map()
  for (const b of bs) {
    if (!b.parent) continue
    const k = b.parent + '#' + b.at.toFixed(2)
    twin.set(k, (twin.get(k) || 0) + 1)
  }
  let twins = 0
  for (const v of twin.values()) if (v > 1) twins += v - 1

  console.log(
    `${String(n).padStart(3)} 节点 · 枝 ${String(bs.length).padStart(3)} · 叶 ${String(leaves.length).padStart(3)}` +
      ` · dur ${Math.min(...bs.map(b => b.dur)).toFixed(2)}–${Math.max(...bs.map(b => b.dur)).toFixed(2)}` +
      ` · 速度离散 ${(dev * 100).toFixed(2)}%${clamped ? `（${clamped} 条被夹）` : ''}` +
      ` · 全长 ${ms(total)}` +
      ` · 端点/单调 ${bad === 0 ? 'ok' : bad + ' 处不符'}` +
      ` · 叶早于落点 ${early} · 叶未满 ${notFull}` +
      (turn.length
        ? ` · 叶→枝 ${turn[0].toFixed(0)}–${turn[turn.length - 1].toFixed(0)}ms（中位 ${turn[Math.floor(turn.length / 2)].toFixed(0)}）`
        : '') +
      ` · 峰值并发 ${peak}/${bs.length}` +
      ` · 叶 dur ${Math.min(...ld).toFixed(2)}–${Math.max(...ld).toFixed(2)}` +
      ` · 叶/枝 均值 ${(bulk.reduce((a, b) => a + b, 0) / bulk.length).toFixed(2)} 最大 ${Math.max(...bulk).toFixed(2)}` +
      ` · 重根 ${twins}`
  )
}

/* ③ 枝不会退回成叶 --------------------------------------------------------
   一片叶长出第一个子实例，它就从叶变成枝；那个子实例结束之后，它必须**还是枝**，
   只是这条线上没有叶了。子实例结束 = 节点从拓扑里消失，所以只看当前 kids 的话，
   同一个 routine 会在那一刻缩回一片叶 —— 画面上是一根枝倒放成叶。

   历史由调用方提供（sdk 的 BuildOptions.branched）。这一节同时验两件事：
   给了历史就不退化，不给就真的会退化（否则这条断言是空的）。 */
{
  const nodes = makeTree(131)
  const m = buildGrowth(nodes)
  const L = leavesOf(m)[0]
  const kid = { id: 'K' + L.id, parent_id: L.id, name: 'ipython', state: 'running' }

  const asBranch = buildGrowth([...nodes, kid]).byId.get(L.id)
  const kept = buildGrowth(nodes, { branched: new Set([L.id]) }).byId.get(L.id)
  const lost = buildGrowth(nodes).byId.get(L.id)

  /* 形态变回来时位置和尺寸都不该动：还是原来那条线，只是没叶了 */
  const geom =
    !!asBranch &&
    !!kept &&
    Math.hypot(asBranch.tip.x - kept.tip.x, asBranch.tip.y - kept.tip.y) < 1e-9 &&
    Math.abs(asBranch.len - kept.len) < 1e-9 &&
    Math.abs(asBranch.at - kept.at) < 1e-9 &&
    !kept.blade

  console.log(
    `③ 枝→空枝 ${L.id}（${L.name}）· 长出子实例时是枝 ${!asBranch.blade}` +
      ` · 子实例结束后：带历史仍是枝 ${!kept.blade} · 不带历史退回叶 ${!!lost.blade} · 几何不变 ${geom}`
  )
}

/* ④ 叶变枝：同一条线在同一个点继续长 --------------------------------------
   早先叶和枝是两个类型、两条构造函数，靠对齐参数维持一致，
   于是接缝处只能「尽量小」：实测最远挪过 12.5 单位（母线才 40 长）。

   现在只有一处 `placement` 给出着生点与 `at`，叶和枝拿的是**同一个对象**，
   所以这一项不是「小」，是**恒等于 0**：叶的 mesh 与它变成枝之后的 mesh
   逐位相同，`at` 也相同。剩下唯一的差别是 `base` —— 枝沿父方向回缩一点，
   让喇叭口盖住接头，那是「长在枝上」，不是「挪窝」。 */
{
  const nodes = makeTree(300)
  const m = buildGrowth(nodes)
  const leaves = leavesOf(m).filter(l => l.parent)
  let sum = 0
  let max = 0
  let maxBase = 0
  let atBad = 0
  for (const l of leaves) {
    const after = buildGrowth([...nodes, { id: 'K' + l.id, parent_id: l.id, name: 'x', state: 'running' }])
    const b = after.byId.get(l.id)
    const d = Math.hypot(b.mesh.x - l.mesh.x, b.mesh.y - l.mesh.y)
    const db = Math.hypot(b.base.x - l.mesh.x, b.base.y - l.mesh.y)
    sum += d
    if (d > max) max = d
    if (db > maxBase) maxBase = db
    if (Math.abs(b.at - l.at) > 1e-12) atBad++
  }
  console.log(
    `④ 叶变枝 · ${leaves.length} 片 · 着生点位移 均值 ${(sum / leaves.length).toFixed(4)} 最大 ${max.toFixed(4)} 单位` +
      ` · at 变了 ${atBad} 处 · 基部回缩 最大 ${maxBase.toFixed(2)}（阈值 2.2）` +
      ` · ${max === 0 && atBad === 0 && maxBase <= 2.2 ? 'ok' : '超了'}`
  )
}

/* ⑤ 叶和枝不是两个东西 -----------------------------------------------------
   结构判据，不是观感判据：整棵树只有一个类型，`stems` 里每一条都有
   `mesh/at/angle/len/dur/pts/radii`，叶只是**多了一个 blade**。
   这一节防的是「以后又给叶单开一条构造路径」—— 那条路径一旦回来，
   接缝就一定再裂一次，而它裂得再小也看不出，只有这里能量出来。 */
{
  const m = buildGrowth(makeTree(131))
  const keys = ['mesh', 'base', 'tip', 'angle', 'len', 'r0', 'dur', 'pts', 'radii', 'spine', 'ribbon']
  const miss = m.stems.filter(s => keys.some(k => s[k] === undefined || s[k] === null))
  const leafNoGeom = leavesOf(m).filter(l => !(l.len >= 0) || l.pts.length < 2)
  const oneMap =
    m.stems.every(s => m.byId.get(s.id) === s) && m.byId.size === m.stems.length
  console.log(
    `⑤ 一种线 · ${m.stems.length} 条 · 字段齐全 ${miss.length === 0}` +
      ` · 叶也带完整几何 ${leafNoGeom.length === 0} · 一张表查全 ${oneMap}` +
      ` · 挂叶片 ${leavesOf(m).length} / 不挂 ${barksOf(m).length}`
  )
}

/* ⑥ 侧生分叉：子线从父线**中段**出去，父线在分叉之上继续走 ------------------
   这一条是仿生判据，不是审美偏好：顶部分叉才是「一根枝走到头就分成两股」，
   侧生是「子枝从腋芽出来，父枝的顶芽继续」。所以挂点不能贴着梢头。
   同时验两件事：at 落在 0.16–0.84；分叉点之上父线确实还剩一段（>15% 全长）。
   根没有父线，不参与。 */
{
  const m = buildGrowth(makeTree(300))
  const kids = m.stems.filter(s => s.parent && m.byId.has(s.parent))
  const ats = kids.map(s => s.at)
  const off = kids.filter(s => s.at < 0.16 || s.at > 0.84)
  /* 挂点之上还剩多少：直接量父线 mesh→tip 的距离占比（曲线略长，取弦长下界） */
  const lead = kids.map(s => {
    const p = m.byId.get(s.parent)
    const rest = Math.hypot(p.tip.x - s.mesh.x, p.tip.y - s.mesh.y)
    return rest / Math.max(1e-6, p.len)
  })
  const noLead = kids.filter((_, i) => lead[i] < 0.15)
  console.log(
    `⑥ 侧生 · ${kids.length} 条子线 · at 均值 ${(ats.reduce((a, b) => a + b, 0) / ats.length).toFixed(3)}` +
      ` 区间 ${Math.min(...ats).toFixed(3)}–${Math.max(...ats).toFixed(3)}` +
      ` · 出界 ${off.length} · 分叉之上父线剩余 均值 ${(lead.reduce((a, b) => a + b, 0) / lead.length).toFixed(2)}` +
      ` 最短 ${Math.min(...lead).toFixed(2)} · 不足 ${noLead.length}` +
      ` · ${off.length === 0 && noLead.length === 0 ? 'ok' : '超了'}`
  )
}
