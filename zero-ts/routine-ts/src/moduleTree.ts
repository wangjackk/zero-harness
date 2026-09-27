/** ModuleTree ---- hub 侧模块树拓扑缓存,业务侧编排策略算 cone/conflict 用.

 kernel 经 module.tree 事件推拓扑(catalog push / load/unload 后变更推送),
 本模块据此重建本地缓存,算 cone(祖先+自己+后代)和 conflict----跟 kernel 侧
 Tree.tryAcquire 用同一个 cone 语义.
 */
import type { Msg } from './protocol'

export class ModuleTree {
  private _parents: Map<string, string | null>
  private _children: Map<string, string[]>
  private _names: Map<string, string>
  private _coneCache = new Map<string, ReadonlySet<string>>()

  constructor(readonly rootId: string,
              parents: Map<string, string | null>,
              children: Map<string, string[]>,
              names?: Map<string, string>) {
    this._parents = parents
    this._children = children
    this._names = names ?? new Map()
  }

  /** 从 kernel 推来的 flat 拓扑构造视图(与 kernel Tree.serialize 输出同构). */
  static fromDict(treePayload: Msg): ModuleTree {
    if (!treePayload || typeof treePayload !== 'object' || !('modules' in treePayload)) {
      throw new Error(`invalid module tree payload: ${JSON.stringify(treePayload)}`)
    }
    const modules = treePayload.modules
    if (!modules || typeof modules !== 'object' || Array.isArray(modules)) {
      throw new Error(`invalid modules map: ${JSON.stringify(modules)}`)
    }
    const parents = new Map<string, string | null>()
    const children = new Map<string, string[]>()
    const names = new Map<string, string>()
    for (const [mid, recRaw] of Object.entries<any>(modules)) {
      let nm = mid
      let ch: string[] = []
      if (recRaw && typeof recRaw === 'object') {
        nm = recRaw.name || mid
        ch = [...(recRaw.children ?? [])]
      }
      parents.set(mid, null)
      names.set(mid, nm)
      children.set(mid, ch)
    }
    for (const [pid, chlist] of children) {
      for (const c of chlist) parents.set(c, pid)
    }
    const rootId = treePayload.root || 'root'
    if (!parents.has(rootId)) {
      throw new Error(`root ${JSON.stringify(rootId)} not in module tree payload`)
    }
    return new ModuleTree(rootId, parents, children, names)
  }

  /** module 的冲突锥:祖先 + 自己 + 后代.不在树里返回空集. */
  cone(module: string): ReadonlySet<string> {
    const cached = this._coneCache.get(module)
    if (cached) return cached
    if (!this._parents.has(module)) return new Set()
    const out = new Set<string>()
    let p = this._parents.get(module) ?? null
    while (p !== null) {
      out.add(p)
      p = this._parents.get(p) ?? null
    }
    out.add(module)
    const stack = [...(this._children.get(module) ?? [])]
    while (stack.length) {
      const c = stack.pop()!
      out.add(c)
      stack.push(...(this._children.get(c) ?? []))
    }
    const cone: ReadonlySet<string> = out
    this._coneCache.set(module, cone)
    return cone
  }

  /** 两组 modules 是否冲突:任一对的 cone 相交即冲突.空集视为无冲突. */
  conflict(modsA: string[], modsB: string[]): boolean {
    if (!modsA.length || !modsB.length) return false
    const aCone = new Set<string>()
    for (const m of modsA) for (const c of this.cone(m)) aCone.add(c)
    for (const m of modsB) if (aCone.has(m)) return true
    return false
  }

  nameOf(moduleId: string): string { return this._names.get(moduleId) ?? moduleId }

  toString(): string { return `ModuleTree(root=${JSON.stringify(this.rootId)}, ${this._parents.size} nodes)` }
}
