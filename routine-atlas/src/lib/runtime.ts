/* 运行中的树：唯一的外部事件入口 + 内部生长历史 + 任一时刻的画面。
 *
 * 组件不再分别维护节点表、出生表、分叉历史和形态转换队列。它只做两件事：
 * `ingest(input, receivedAt)` 提交一次外界变化，`sample(t)` 读取这一帧。
 * 相同快照与 created→started 都不会重置已有线的生长。
 *
 * 这里不认识 Vue、DOM 或 rAF；时钟由调用方注入，所以测试可以逐帧推进。 */
import {
  buildGrowth,
  type GStem,
  type GrowthNode,
  type Skeleton,
} from './skeleton'
import {
  BEAT,
  anyGrowing,
  birthTime,
  grownFor,
  leafFrame,
  leafFrameAt,
  rawP,
  stemP,
  type LeafFrame,
} from './growth'

export interface TreeEvent {
  t: number
  ev: string
  id: string
  pid: string
  name: string
}

export type TreeInput =
  | { kind: 'event'; event: TreeEvent }
  | { kind: 'snapshot'; nodes: GrowthNode[] }

export interface TurnedStem {
  key: string
  stem: GStem
  t0: number
}

export interface DepartedStem {
  key: string
  kind: 'leaf' | 'branch' | 'root'
  /** 落叶是它自己；断枝是断口那根；root 是淡出的主干 */
  stem: GStem
  /** 断口以下的整段枝干 */
  group: GStem[]
  /** 开始离枝/断落/淡出的时刻。短命叶可晚于外部 stop。 */
  t0: number
  /** 原生长起点；stop 不会把这段历史抹掉 */
  birth: number
  /** sample(t) 给出离枝前的连续生长状态；开始飘落后页面改用落体轨迹 */
  growth: LeafFrame | null
  /** 固定在离枝时刻的实际位姿。飘落只能以它为原点，不能回用静态 blade 坐标 */
  release: LeafFrame | null
}

export interface TreeFrame {
  time: number
  model: Skeleton
  progress: ReadonlyMap<string, number>
  leaves: ReadonlyMap<string, LeafFrame>
  turned: TurnedStem[]
  departed: DepartedStem[]
  busy: boolean
}

const TURN_MS = 360
const LEAF_LINGER_MS = 3000
const BRANCH_LINGER_MS = 1800
const ROOT_LINGER_MS = 800

export class TreeRuntime {
  private nodes = new Map<string, GrowthNode>()
  private branched = new Set<string>()
  private born = new Map<string, number>()
  private model: Skeleton = buildGrowth([])
  private turned: TurnedStem[] = []
  private departed: DepartedStem[] = []

  /** 唯一的外界写入口。receivedAt 是本地接收时刻，不使用事件里的历史时间
   *  驱动动画：重连回放的是事实，不该让一棵树瞬间跳过自己的生长。 */
  ingest(input: TreeInput | TreeInput[], receivedAt = Date.now()): TreeFrame {
    /* 同一拍里到达的一组事件只重建、对账一次，避免批量停止时逐节点闪烁。 */
    const items = Array.isArray(input) ? input : [input]
    for (const item of items) {
      if (item.kind === 'snapshot') {
        this.nodes = new Map(item.nodes.map(n => [n.id, { ...n }]))
        continue
      }
      const e = item.event
      if (!e.id) continue
      if (e.ev === 'created' || e.ev === 'started') {
        const old = this.nodes.get(e.id)
        this.nodes.set(e.id, {
          id: e.id,
          parent_id: e.pid || old?.parent_id || '0',
          name: e.name || old?.name || e.id,
          state: e.ev === 'started' ? 'running' : 'created',
        })
      } else {
        const old = this.nodes.get(e.id)
        /* 同一批里 created 后立刻 stopped：先让这次创建进入视觉历史，
         * 再删除拓扑。否则最终对账只看到「从来没有它」，短命实例连生长和
         * 落叶都不会有。普通批次仍只对账一次，这里只处理这种瞬时实例。 */
        if (old && !this.model.byId.has(e.id)) this.reconcile(receivedAt)
        this.nodes.delete(e.id)
      }
    }
    this.reconcile(receivedAt)
    return this.sample(receivedAt)
  }

  /** 读取一个确定时刻。除清理过期残影外不吸收任何外部事实。 */
  sample(t = Date.now()): TreeFrame {
    this.turned = this.turned.filter(x => t - x.t0 < TURN_MS)
    this.departed = this.departed.filter(x => {
      const ttl = x.kind === 'leaf' ? LEAF_LINGER_MS : x.kind === 'branch' ? BRANCH_LINGER_MS : ROOT_LINGER_MS
      return t - x.t0 < ttl
    })
    const progress = new Map<string, number>()
    const leaves = new Map<string, LeafFrame>()
    for (const s of this.model.stems) {
      progress.set(s.id, stemP(s, this.born, t))
      const leaf = leafFrame(s, this.born, t)
      if (leaf) leaves.set(s.id, leaf)
    }
    const departed = this.departed.map(x => ({
      ...x,
      growth: x.stem.blade ? leafFrameAt(x.stem, x.birth, t) : null,
    }))
    return {
      time: t,
      model: this.model,
      progress,
      leaves,
      turned: this.turned,
      departed,
      busy:
        this.turned.length > 0 ||
        this.departed.length > 0 ||
        anyGrowing(this.model, this.born, t),
    }
  }

  private reconcile(t: number) {
    const prev = this.model
    /* 当前 kids 与历史 branched 一起决定形态。曾长出过子实例的线，
       子实例全部结束后仍是空枝，不退回叶。 */
    const next = buildGrowth([...this.nodes.values()], { branched: this.branched })
    for (const s of next.stems) if (s.kids > 0) this.branched.add(s.id)
    for (const id of this.branched) if (!next.byId.has(id)) this.branched.delete(id)

    for (const s of next.stems) {
      const old = prev.byId.get(s.id)
      if (old?.blade && !s.blade) {
        /* 同一条线从叶柄继续抽成枝。只有这种真实的形态变化才重置进度；
           created→started 与快照重建都走不到这里。 */
        const oldProgress = rawP(old, this.born, t)
        this.born.set(s.id, t)
        if (oldProgress > 0) {
          this.turned.push({ key: `turn:${s.id}:${t}`, stem: old, t0: t })
        }
        continue
      }
      if (this.born.has(s.id)) continue
      const par = s.parent ? next.byId.get(s.parent) : undefined
      this.born.set(s.id, birthTime(s, par, this.born, t))
    }

    const live = next.byId
    const gone = prev.stems.filter(s => !live.has(s.id))
    if (gone.length) {
      const goneIds = new Set(gone.map(s => s.id))
      /* 落叶只收真正离开的线；叶变枝的 id 仍在 next 中，不会误入这里。
         外部 stop 是拓扑事实，不是「取消它还没演完的生长」。父枝还在时，
         短命叶沿原出生时刻长完，再从梢头离枝；不能用 rawP>0 把未展开的叶
         过滤掉，也不能随机抽掉一部分落叶。整棵子树一起结束时仍跟随断枝，
         没露过面的叶不额外制造悬空残影。 */
      for (const s of gone) {
        if (!s.blade) continue
        const birth = this.born.get(s.id)
        if (birth == null) continue
        const parentGone = goneIds.has(s.parent)
        if (parentGone && !(rawP(s, this.born, t) > 0)) continue
        const release = parentGone ? t : Math.max(t, birth + BEAT * s.dur)
        this.departed.push({
          key: `leaf:${s.id}:${t}`,
          kind: 'leaf',
          stem: s,
          group: [s],
          t0: release,
          birth,
          growth: null,
          release: leafFrameAt(s, birth, release),
        })
      }

      const goneBranches = gone.filter(s => !s.blade)
      const children = new Map<string, GStem[]>()
      for (const s of goneBranches) {
        const arr = children.get(s.parent)
        if (arr) arr.push(s)
        else children.set(s.parent, [s])
      }
      for (const s of goneBranches) {
        if (goneIds.has(s.parent)) continue
        const group: GStem[] = []
        const walk = (x: GStem) => {
          group.push(x)
          for (const c of children.get(x.id) ?? []) walk(c)
        }
        walk(s)
        const kind = s.depth === 0 ? 'root' : 'branch'
        if (kind === 'branch' && !(grownFor(s, this.born, t) > BEAT * s.dur * 0.35)) continue
        this.departed.push({
          key: `${kind}:${s.id}:${t}`,
          kind,
          stem: s,
          group,
          t0: t,
          birth: this.born.get(s.id) ?? t,
          growth: null,
          release: null,
        })
      }
      for (const s of gone) this.born.delete(s.id)
    }

    /* 不按固定条数截断残影：一次停止超过 90 个节点时，截断会让前一批叶
       无声消失。过期记录统一在 sample(t) 清理；忙碌时页面本来就会逐帧采样。 */
    this.model = next
  }
}
