/* 封闭生长运行时的回归检查：直接执行产品 TS，不复制一套状态机。
   用法：node scripts/runtime-check.cjs；任一断言失败即 exit 1。 */
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
const { TreeRuntime, BEAT } = mod.exports

const fallOut = buildSync({
  entryPoints: [path.join(__dirname, '../src/lib/leafFall.ts')],
  bundle: true,
  format: 'cjs',
  platform: 'node',
  write: false,
  logLevel: 'warning',
})
const fallMod = { exports: {} }
new Function('module', 'exports', 'require', fallOut.outputFiles[0].text)(fallMod, fallMod.exports, require)
const { createLeafFall, leafFallAt } = fallMod.exports

let failures = 0
function check(ok, label) {
  if (!ok) failures++
  console.log(`${ok ? 'ok' : 'FAIL'} · ${label}`)
}
const root = { id: '1', parent_id: '0', name: 'moss', state: 'running' }
const leaf = { id: '2', parent_id: '1', name: 'ipython', state: 'created' }
const event = (ev, n, t = 0) => ({ kind: 'event', event: { t, ev, id: n.id, pid: n.parent_id, name: n.name } })
const snapshot = nodes => ({ kind: 'snapshot', nodes })

/* 1. 叶片从 0 透明、6% 尺度开始，连续单调地长满；中途 started 与相同快照
   都不能重置它。历史事件的时间戳也不参与本地动画时钟。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root, leaf]), 10000)
  const first = rt.sample(10000)
  check(first.leaves.get('2').opacity === 0, '出生帧叶片完全透明')
  check(first.leaves.get('2').scale === 0.06, '出生帧叶片从 6% 开始，而非 45% 突然出现')

  let previous = -1
  let monotone = true
  for (let t = 10000; t <= 11000; t += 16) {
    const p = rt.sample(t).progress.get('2')
    if (p < previous - 1e-12) monotone = false
    previous = p
  }
  check(monotone && previous === 1, '逐 16ms 采样单调生长并最终长满')

  const before = rt.sample(10400)
  const p = before.progress.get('2')
  const op = before.leaves.get('2').opacity
  const started = rt.ingest(event('started', { ...leaf, state: 'running' }, 1), 10400)
  check(started.progress.get('2') === p && started.leaves.get('2').opacity === op,
    'created→started 不重置叶进度（同一帧数值完全相等）')
  const replay = rt.ingest(snapshot([root, { ...leaf, state: 'running' }]), 10400)
  check(replay.progress.get('2') === p && replay.leaves.get('2').opacity === op,
    '相同快照重放不重置叶进度')
}

/* 2. 叶变枝：旧叶进入 360ms 收拢队列，新线从 0 连续抽出；
   它不是落叶。子实例结束后留下空枝，不退化成叶。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root, leaf]), 0)
  rt.sample(2000)
  const child = { id: '3', parent_id: '2', name: 'run', state: 'running' }
  const turning = rt.ingest(event('created', child), 2000)
  check(!turning.model.byId.get('2').blade, '长出子实例后原节点成为枝')
  check(turning.turned.some(x => x.stem.id === '2'), '旧叶有连续收拢过渡')
  check(!turning.departed.some(x => x.stem.id === '2'), '叶变枝不会误记成落叶')
  check(turning.progress.get('2') === 0, '形态转换当帧新枝从 0 起长')
  const middle = rt.sample(2000 + BEAT * 0.2)
  check(middle.progress.get('2') > 0 && middle.progress.get('2') < 1, '转换中段进度连续位于 0–1')

  const ended = rt.ingest(event('stopped', child), 4000)
  check(!ended.model.byId.get('2').blade, '子实例结束后仍为空枝')
  check(!ended.turned.some(x => x.stem.id === '2'), '空枝不会再次生成叶变枝过渡')
}

/* 3. 一次提交整批停止，只产生一批结束残影；重复空快照不重复制造。
   所有生长与残影到期后 busy 必须归零，rAF 可以停止。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root, leaf]), 0)
  rt.sample(2000)
  const batch = rt.ingest([event('stopped', leaf), event('stopped', root)], 2000)
  const count = batch.departed.length
  check(count > 0 && batch.model.stems.length === 0, '批量停止一次对账并保留结束画面')
  const replay = rt.ingest(snapshot([]), 2016)
  check(replay.departed.length === count, '重复快照不会重复制造结束残影')
  check(!rt.sample(12000).busy, '生长与残影结束后运行时可以静止')
}

/* 4. 短命叶：父枝还在时，外部 stop 只结束拓扑，不取消视觉生命周期。
   叶片沿原出生时刻长完，release 那一帧必须刚好长满，随后才进入飘落。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root, leaf]), 0)
  const stopped = rt.ingest(event('stopped', leaf), 20)
  const d = stopped.departed.find(x => x.kind === 'leaf' && x.stem.id === '2')
  check(!!d && d.t0 > 20, '20ms 结束的叶保留结束记录并推迟离枝')
  check(!stopped.model.byId.has('2'), '短命叶的 stop 仍立即从活拓扑生效')
  const mid = rt.sample((d.birth + d.t0) / 2).departed.find(x => x.key === d.key)
  check(!!mid?.growth && mid.growth.scale > 0.06 && mid.growth.scale < 1,
    '结束等待期叶片连续生长，不是消失后凭空落下')
  const release = rt.sample(d.t0).departed.find(x => x.key === d.key)
  check(!!release?.growth && release.growth.scale === 1 && release.growth.opacity === 1 &&
    release.growth.x === release.stem.tip.x && release.growth.y === release.stem.tip.y,
    '离枝当帧长满且位姿与梢头精确接上')
  check(rt.sample(d.t0 - 1).busy, '等待短命叶长完时运行时保持帧推进')
  check(!rt.sample(d.t0 + 4000).busy, '短命叶飘落结束后运行时静止')
}

/* 5. created 与 stopped 在同一批：先把创建纳入视觉历史，再结束拓扑。
   这个顺序不能靠调用方拆批或延时。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root]), 0)
  const frame = rt.ingest([event('created', leaf), event('stopped', leaf)], 40)
  const d = frame.departed.find(x => x.kind === 'leaf' && x.stem.id === '2')
  check(!!d && d.t0 > 40, '同拍创建并结束也有完整生长与落叶排期')
  const replay = rt.ingest(snapshot([root]), 56)
  check(replay.departed.filter(x => x.stem.id === '2').length === 1,
    '同拍短命实例不会因后续快照重复落叶')
}

/* 6. 不再按概率省略落叶；已经长完的叶仍立即开始落下，不额外等待。 */
{
  const rt = new TreeRuntime()
  const leaves = Array.from({ length: 128 }, (_, i) => ({
    id: `L${i}`, parent_id: '1', name: `leaf${i}`, state: 'running',
  }))
  rt.ingest(snapshot([root, ...leaves]), 0)
  const stops = leaves.map(n => event('stopped', n))
  const ended = rt.ingest(stops, 4000)
  check(ended.departed.filter(x => x.kind === 'leaf').length === leaves.length,
    '128 片叶全部生成落叶，不按概率或队列上限省略')
  check(ended.departed.filter(x => x.kind === 'leaf').every(x => x.t0 === 4000),
    '已长完的叶在 stop 当帧直接开始结束动画')
}

/* 7. 整棵子树一起停止时，没露面的叶不制造悬空残影。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root, leaf]), 0)
  const ended = rt.ingest([event('stopped', leaf), event('stopped', root)], 0)
  check(!ended.departed.some(x => x.kind === 'leaf'), '级联停止不补画从未露面的悬空叶')
  check(ended.departed.some(x => x.kind === 'root'), '级联停止仍保留主干收尾')
}

/* 8. 生长 → 转黄 → 离枝必须是同一片叶的连续位姿。
   这里直接跑页面使用的落体函数，覆盖静态蓝图坐标与 sin(phase) 初值两类跳变。 */
{
  const rt = new TreeRuntime()
  const leaves = Array.from({ length: 32 }, (_, i) => ({
    id: `S${i}`, parent_id: '1', name: `seam${i}`, state: 'running',
  }))
  rt.ingest(snapshot([root, ...leaves]), 0)
  const ended = rt.ingest(leaves.map(n => event('stopped', n)), 4000)
  let exact = true
  let held = true
  let continuous = true
  for (const d of ended.departed.filter(x => x.kind === 'leaf')) {
    const fall = createLeafFall(d.key, d.stem, d.release, d.t0)
    const atRelease = leafFallAt(fall, d.t0)
    const atDetach = leafFallAt(fall, d.t0 + fall.hold * 1000)
    const after = leafFallAt(fall, d.t0 + fall.hold * 1000 + 1)
    exact = exact && atRelease.x === d.release.x && atRelease.y === d.release.y &&
      atRelease.scale === d.release.scale && atRelease.opacity === d.release.opacity
    held = held && Math.hypot(atDetach.x - d.release.x, atDetach.y - d.release.y) < 1e-6 &&
      Math.abs(atDetach.scale - d.release.scale) < 1e-12
    continuous = continuous && Math.hypot(after.x - atDetach.x, after.y - atDetach.y) < 0.25
  }
  check(exact, '32 片叶在 stop 帧精确继承离枝位姿，不回跳静态蓝图坐标')
  check(held, '转黄停留期间位置与尺度不变，叶柄仍连着原梢头')
  check(continuous, '真正离枝后的 1ms 内无横向摆动初值造成的跳变')
}

/* 9. 级联停止允许尚未长满的可见叶立即离枝；此时也不能突然放大到 100%。 */
{
  const rt = new TreeRuntime()
  rt.ingest(snapshot([root, leaf]), 0)
  const initial = rt.sample(0)
  const leafStem = initial.model.byId.get('2')
  const rootStem = initial.model.byId.get('1')
  /* 叶要等父枝长到挂点才出生；取出生后的 40ms，确保它已可见但远未长满 */
  const stopAt = Math.ceil(BEAT * rootStem.dur * leafStem.at + 40)
  rt.sample(stopAt)
  const ended = rt.ingest([event('stopped', leaf), event('stopped', root)], stopAt)
  const d = ended.departed.find(x => x.kind === 'leaf' && x.stem.id === '2')
  const fall = d && createLeafFall(d.key, d.stem, d.release, d.t0)
  const pose = fall && leafFallAt(fall, d.t0)
  check(!!d?.release && d.release.scale > 0.06 && d.release.scale < 1,
    '级联停止保留离枝当帧的部分展开状态')
  check(!!pose && pose.x === d.release.x && pose.y === d.release.y &&
    pose.scale === d.release.scale && pose.opacity === d.release.opacity,
    '未长满就离枝时，位置、尺度与透明度仍精确连续')
}

console.log(`runtime-check · ${failures ? `${failures} 处失败` : '全部通过'}`)
process.exitCode = failures ? 1 : 0
