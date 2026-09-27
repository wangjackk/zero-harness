/** 权威模块树(kernel 侧)---- 移植 z2/kernel/tree.py.

 cone 语义:占用 m 会挡住 cone(祖先+自己+后代)内任意节点;cone 内任意节点
 被占也会挡住 m.tryAcquire check-and-occupy 原子(JS 单线程无锁).
 */
import type { Msg } from '../protocol'

export class ConflictError extends Error {
  constructor(readonly requested: string, readonly blockedBy: string,
              readonly holders: number[]) {
    super(`module ${JSON.stringify(requested)} blocked at ${JSON.stringify(blockedBy)} by routines ${JSON.stringify(holders)}`)
  }
}

interface Module {
  id: string
  name: string
  parent: string | null
  children: string[]
  holders: number[]
}

export class Tree {
  private _root: string
  private _modules = new Map<string, Module>()

  constructor(rootId: string, records: Record<string, { children?: string[]; name?: string }>) {
    this._root = rootId
    for (const [mid, rec] of Object.entries(records)) {
      this._modules.set(mid, {
        id: mid, name: rec.name || mid, parent: null,
        children: [...(rec.children ?? [])], holders: [],
      })
    }
    for (const m of this._modules.values()) {
      for (const c of m.children) {
        const child = this._modules.get(c)
        if (child) child.parent = m.id
      }
    }
  }

  static fromDict(payload: Msg): Tree {
    const modules = payload?.modules
    if (!modules || typeof modules !== 'object' || Array.isArray(modules)) {
      throw new Error(`invalid module tree payload: ${JSON.stringify(payload)}`)
    }
    const root = payload.root || 'root'
    if (!(root in modules)) throw new Error(`root ${JSON.stringify(root)} not in modules`)
    return new Tree(root, modules)
  }

  get rootId(): string { return this._root }

  /** module 的冲突锥:祖先 + 自己 + 后代.不存在返回空集. */
  cone(module: string): Set<string> {
    const n = this._modules.get(module)
    if (!n) return new Set()
    const out = new Set<string>([module])
    let p = n.parent
    while (p !== null) {
      out.add(p)
      const pm = this._modules.get(p)
      p = pm ? pm.parent : null
    }
    const stack = [...n.children]
    while (stack.length) {
      const c = stack.pop()!
      out.add(c)
      const m = this._modules.get(c)
      if (m) stack.push(...m.children)
    }
    return out
  }

  /** 为 rid 占用 modules.冲突抛 ConflictError,缺失模块抛 Error.

   ancestors 是 rid 的 routine 树祖先 id 集:cone 内被占节点的 holder
   若全是 rid 自己或其祖先,放行(父子共占);否则冲突. */
  tryAcquire(rid: number, modules: string[], ancestors: Set<number>): void {
    for (const m of modules) {
      if (!this._modules.has(m)) throw new Error(`module ${JSON.stringify(m)} not found in tree`)
      for (const c of this.cone(m)) {
        for (const h of this._modules.get(c)!.holders) {
          if (h === rid || ancestors.has(h)) continue
          throw new ConflictError(m, c, [...this._modules.get(c)!.holders])
        }
      }
    }
    for (const m of modules) {
      const n = this._modules.get(m)!
      if (!n.holders.includes(rid)) n.holders.push(rid)
    }
  }

  /** cone 内需被 force 驱逐的第三方 holder 集(排除自己/祖先).驱逐后必过. */
  evictableHolders(modules: string[], ancestors: Set<number>, rid: number): number[] {
    const out = new Set<number>()
    for (const m of modules) {
      if (!this._modules.has(m)) continue
      for (const c of this.cone(m)) {
        for (const h of this._modules.get(c)!.holders) {
          if (h === rid || ancestors.has(h)) continue
          out.add(h)
        }
      }
    }
    return [...out].sort((a, b) => a - b)
  }

  /** 释放 rid 占用的所有模块(routine 停止时全量清理). */
  release(rid: number): void {
    for (const n of this._modules.values()) {
      const i = n.holders.indexOf(rid)
      if (i >= 0) n.holders.splice(i, 1)
    }
  }

  /** 释放 rid 占用的指定模块(运行时 release). */
  releaseModules(rid: number, modules: Iterable<string>): void {
    for (const m of modules) {
      const n = this._modules.get(m)
      if (n) {
        const i = n.holders.indexOf(rid)
        if (i >= 0) n.holders.splice(i, 1)
      }
    }
  }

  /** 往 parent 下挂 child(动态增拓扑).只挂树不占用. */
  loadModule(parentId: string, childId: string, name = ''): void {
    if (!childId) throw new Error('child_id is empty')
    if (this._modules.has(childId)) throw new Error(`module ${JSON.stringify(childId)} already exists`)
    const p = this._modules.get(parentId)
    if (!p) throw new Error(`parent module ${JSON.stringify(parentId)} not found`)
    this._modules.set(childId, { id: childId, name: name || childId, parent: parentId, children: [], holders: [] })
    p.children.push(childId)
  }

  /** 摘掉 child(动态删拓扑).有子/被占报错. */
  unloadModule(childId: string): void {
    const n = this._modules.get(childId)
    if (!n) throw new Error(`module ${JSON.stringify(childId)} not found`)
    if (n.children.length) throw new Error(`module ${JSON.stringify(childId)} has children ${JSON.stringify(n.children)}, unload them first`)
    if (n.holders.length) throw new Error(`module ${JSON.stringify(childId)} is occupied by routines ${JSON.stringify(n.holders)}, release first`)
    if (n.parent !== null) {
      const p = this._modules.get(n.parent)
      if (p) {
        const i = p.children.indexOf(childId)
        if (i >= 0) p.children.splice(i, 1)
      }
    }
    this._modules.delete(childId)
  }

  /** 序列化成 flat map(与 Tree.fromDict 输入同构). */
  serialize(): Msg {
    const mods: Record<string, { name?: string; children?: string[] }> = {}
    for (const [mid, m] of this._modules) {
      const rec: { name?: string; children?: string[] } = {}
      if (m.name && m.name !== mid) rec.name = m.name
      if (m.children.length) rec.children = [...m.children]
      mods[mid] = rec
    }
    return { root: this._root, modules: mods }
  }
}
