/* 临时预览：用模拟拓扑喂 RoutineTreeHud，形状与真实运行接近。
   默认一棵几十个实例的小树；加 ?big 换成 100 多个、深 5 层的密冠，
   用来确认节点多了以后还能不能读。只用于截图核对，不参与构建。 */
import { createApp, h, reactive, ref } from 'vue'
import RoutineTreeHud from './components/RoutineTreeHud.vue'
import type { TreeInput } from './lib/growth'

interface N { id: string; parent_id: string; name: string; state: string }

const nodes = reactive(new Map<string, N>())
const input = ref<TreeInput | TreeInput[]>({ kind: 'snapshot', nodes: [] })
const selected = ref<string | null>(null)

const BIG = location.search.includes('big')
const FAST = location.search.includes('fast')

let seed = 20261028
const rnd = () => {
  seed = (seed * 1664525 + 1013904223) >>> 0
  return seed / 4294967296
}

const plan: Array<[string, string, string]> = []
let seq = 0
const push = (pid: string, name: string) => {
  const id = String(++seq)
  plan.push([id, pid, name])
  return id
}

if (BIG) {
  push('0', 'moss')
  const queue: Array<[string, number]> = [['1', 0]]
  let names = 0
  while (queue.length) {
    const [id, d] = queue.shift() as [string, number]
    if (d >= 5) continue
    const k = d === 0 ? 4 : 2 + Math.floor(rnd() * 4)
    for (let i = 0; i < k; i++) {
      if (plan.length > 130) break
      const kid = push(id, 'r' + ++names)
      queue.push([kid, d + 1])
    }
  }
} else {
  const root = push('0', 'moss')
  const shell = push(root, 'shell')
  const ghost = push(root, 'ghost')
  const mind = push(root, 'mindflow')
  const asr = push(root, 'asr')

  const bail = push(shell, 'all')
  for (const n of ['code', 'fetch_a', 'fetch_b', 'merge', 'summary', 'listen']) push(bail, n)
  const loop = push(shell, 'loop')
  for (const n of ['render', 'prompt']) push(loop, n)
  for (const n of ['read', 'grep', 'write']) push(ghost, n)
  const mf = push(mind, 'declare')
  for (const n of ['partial', 'commit']) push(mf, n)
  push(asr, 'listen')
  push(asr, 'commit')
  for (const n of ['code', 'merge', 'read']) {
    const id = plan.find(p => p[2] === n)?.[0]
    if (id) push(id, 'inner_' + n)
  }
}

let i = 0
const step = BIG ? 20 : 240
const timer = setInterval(() => {
  const batch = BIG ? 8 : 1
  const inputs: TreeInput[] = []
  for (let k = 0; k < batch && i < plan.length; k++, i++) {
    const [id, pid, name] = plan[i]
    const state = i % 4 === 0 ? 'created' : 'running'
    nodes.set(id, { id, parent_id: pid, name, state })
    inputs.push({
      kind: 'event',
      event: { t: Date.now(), ev: state === 'created' ? 'created' : 'started', id, pid, name },
    })
    if (state === 'created') {
      /* 专门覆盖真实运行里的第二次状态刷新：它只能改颜色，不能让叶从头再长。 */
      setTimeout(() => {
        if (!nodes.has(id)) return
        nodes.set(id, { id, parent_id: pid, name, state: 'running' })
        input.value = {
          kind: 'event',
          event: { t: Date.now(), ev: 'started', id, pid, name },
        }
      }, 160)
    }
  }
  input.value = inputs
  if (i >= plan.length) {
    clearInterval(timer)
    if (FAST) fastRoutines(12)
    /* 结束一段子树：真实系统里 stop 是逐级发的，这里一次删掉整棵，
       看的是最坏情况 —— 一次收掉十几片叶、几根枝 */
    setTimeout(() => {
      const at = plan[1]
      if (!at) return
      const kill = new Set([at[0]])
      for (let grew = true; grew; ) {
        grew = false
        for (const [id, pid] of plan) {
          if (kill.has(pid) && !kill.has(id)) {
            kill.add(id)
            grew = true
          }
        }
      }
      for (const id of kill) nodes.delete(id)
      input.value = { kind: 'snapshot', nodes: [...nodes.values()] }
      /* 再走一轮：只结束某个实例的**子实例**，它自己还在。
         这一圈子实例都是叶，收掉之后那个实例就没叶了 ——
         它必须还是枝（空枝），不能退回成一片叶。 */
      endKids(6)
    }, 2600)
  }
}, step)

/** ?fast：在常驻 root 下连续制造短命叶。24ms 后停止，部分同拍创建并停止；
 *  每片都应先完成可辨认的生长，再从原梢头飘落，而不是闪一下直接消失。 */
function fastRoutines(rounds: number) {
  let n = 0
  const next = () => {
    const id = `fast-${++n}`
    const name = `quick_${n}`
    const created: TreeInput = {
      kind: 'event',
      event: { t: Date.now(), ev: 'created', id, pid: '1', name },
    }
    const stopped: TreeInput = {
      kind: 'event',
      event: { t: Date.now(), ev: 'stopped', id, pid: '1', name },
    }
    if (n % 3 === 0) {
      nodes.delete(id)
      input.value = [created, stopped]
    } else {
      nodes.set(id, { id, parent_id: '1', name, state: 'created' })
      input.value = created
      setTimeout(() => {
        if (!nodes.has(id)) return
        nodes.delete(id)
        input.value = stopped
      }, 24)
    }
    if (n < rounds) setTimeout(next, 520)
  }
  setTimeout(next, 500)
}

/** 每 900ms 挑一个「子实例全是叶」的节点，只收掉它这一圈子实例。
    收完之后它就是一根空枝：枝在、叶没了 —— 这是「枝不会退回成叶」那一条要看的场面 */
function endKids(rounds: number) {
  setTimeout(() => {
    const byParent = new Map<string, N[]>()
    for (const n of nodes.values()) {
      const arr = byParent.get(n.parent_id)
      if (arr) arr.push(n)
      else byParent.set(n.parent_id, [n])
    }
    const cand: string[] = []
    for (const [pid, kids] of byParent) {
      if (!nodes.has(pid)) continue
      if (kids.some(k => byParent.has(k.id))) continue
      if (kids.length) cand.push(pid)
    }
    if (cand.length) {
      const pid = cand[Math.floor(rnd() * cand.length)]
      for (const k of byParent.get(pid) ?? []) nodes.delete(k.id)
      input.value = { kind: 'snapshot', nodes: [...nodes.values()] }
    }
    if (rounds > 1) endKids(rounds - 1)
  }, 900)
}

createApp({
  render: () => h(RoutineTreeHud, {
    input: input.value,
    selected: selected.value,
    onSelect: (id: string) => { selected.value = id },
    onBack: () => { selected.value = null },
  }),
}).mount('#app')
