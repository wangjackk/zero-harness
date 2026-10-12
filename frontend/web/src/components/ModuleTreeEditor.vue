<template>
  <div class="mte">
    <!-- 顶部工具栏 -->
    <div class="mte-header">
      <span class="mte-title">模块树</span>
      <span class="mte-hint" title="每次编辑即时生效: kernel 内存树 + tree.json">编辑即时生效</span>
      <div class="mte-actions">
        <NButton size="small" quaternary :loading="loading" @click="fetchTree">⟳ 刷新</NButton>
      </div>
    </div>

    <!-- 页面级错误/提示区 -->
    <NAlert v-if="error" type="error" :show-icon="false" class="mte-error">
      <pre>{{ error }}</pre>
    </NAlert>

    <div class="mte-body">
      <NScrollbar style="height:100%">
        <div v-if="root" class="mte-tree">
          <ModuleTreeNode :node="root" :depth="0" />
        </div>
        <!-- 未挂载区: 手写文件造成的孤儿 (正常编辑不会产生), 可删不可移 -->
        <template v-if="orphans.length">
          <div class="mte-orphan-label">未挂载 (可删除)</div>
          <div class="mte-tree mte-orphan-tree">
            <ModuleTreeNode v-for="o in orphans" :key="o.uid" :node="o" :depth="0" />
          </div>
        </template>
        <div v-if="!root && !loading" class="mte-empty">
          {{ error ? '加载失败, 点击刷新重试' : '无数据' }}
        </div>
      </NScrollbar>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, provide, watch } from 'vue'
import { NButton, NAlert, NScrollbar } from 'naive-ui'
import ModuleTreeNode, { type EditNode } from './ModuleTreeNode.vue'
import { confirmDialog } from '../composables/confirm'

const props = defineProps<{
  httpBase: string
  active: boolean
}>()

const root = ref<EditNode | null>(null)
const orphans = ref<EditNode[]>([])
const loading = ref(false)
const error = ref('')

let uidSeq = 0

/** 后端编辑事务: 成功返回 true, 失败显示 error.
 * 统一走 /run_routine {name, kwargs} -> {ok, result | error}. */
async function applyOp(payload: Record<string, any>): Promise<boolean> {
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'tree/apply', kwargs: payload }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.error || 'apply failed')
    const result = data.result || {}
    if (!result.ok) throw new Error(result.error || 'apply failed')
    error.value = ''
    return true
  } catch (e) {
    error.value = (e as Error).message
    return false
  }
}

// ------------------------------------------------------------------
// flat (tree.json modules) → 嵌套展示树
// ------------------------------------------------------------------

/** flat -> 嵌套: 从 root DFS; 引用缺失跳过, 环用 visited 截断, 不可达的进孤儿区. */
function fromFlat(modules: Record<string, any>): { root: EditNode; orphans: EditNode[] } {
  const visited = new Set<string>()
  const build = (id: string): EditNode => {
    visited.add(id)
    const rec = modules[id] || {}
    const children = (rec.children || [])
      .filter((c: string) => c in modules && !visited.has(c))
      .map((c: string) => build(c))
    return { uid: ++uidSeq, id, name: (rec.name && rec.name !== id) ? rec.name : '', children, expanded: true }
  }
  const r = build('root')
  const orph = Object.keys(modules).filter(m => !visited.has(m)).map(m => build(m))
  return { root: r, orphans: orph }
}

// ------------------------------------------------------------------
// 编辑操作 (provide 给递归节点组件; 每操作即调后端事务)
// ------------------------------------------------------------------

function addChild(parent: EditNode) {
  parent.children.push({ uid: ++uidSeq, id: '', name: '', children: [], isNew: true, expanded: true })
  parent.expanded = true
}

function idExists(id: string, except?: EditNode): boolean {
  let hit = false
  const walk = (n: EditNode) => {
    if (n !== except && n.id === id) hit = true
    n.children.forEach(walk)
  }
  if (root.value) walk(root.value)
  orphans.value.forEach(walk)
  return hit
}

async function commitNew(node: EditNode) {
  const id = node.id.trim()
  if (!id) { error.value = '模块 id 不能为空'; return }
  // except=node: v-model 已把输入写进 node.id, 自身不算重复
  if (idExists(id, node)) { error.value = `模块 id '${id}' 已存在`; return }
  // 定位父节点 (新建行的宿主)
  const parent = findParent(node)
  if (!parent) { error.value = '未找到父模块'; return }
  node.id = id
  if (!await applyOp({ op: 'add', parent: parent.id, id, name: node.name.trim() })) return
  node.isNew = false
}

async function remove(node: EditNode) {
  const count = countNodes(node)
  const ok = await confirmDialog({
    title: '删除模块',
    content: `删除 '${node.id || '(未命名)'}' 及其 ${count - 1} 个子模块?`,
    positiveText: '删除', danger: true,
  })
  if (!ok) return
  if (node.isNew) {   // 未转正的新建行: 纯本地, 无后端事务
    const sibs = findSiblings(node)
    if (sibs) sibs.splice(sibs.indexOf(node), 1)
    return
  }
  if (!await applyOp({ op: 'remove', id: node.id })) return
  const sibs = findSiblings(node)
  if (sibs) sibs.splice(sibs.indexOf(node), 1)
}

function countNodes(n: EditNode): number {
  return 1 + n.children.reduce((s, c) => s + countNodes(c), 0)
}

/** 定位 node 的父节点 (树 + 孤儿区), root/找不到返回 null. */
function findParent(node: EditNode): EditNode | null {
  let found: EditNode | null = null
  const walk = (list: EditNode[]) => {
    for (const n of list) {
      if (n.children.includes(node)) { found = n; return }
      walk(n.children)
    }
  }
  if (root.value) walk([root.value])
  if (!found) walk(orphans.value)
  return found
}

/** 定位 node 所在的兄弟数组 (树 + 孤儿区), 找不到返回 null. */
function findSiblings(node: EditNode): EditNode[] | null {
  const parent = findParent(node)
  if (parent) return parent.children
  if (root.value === node) return null
  if (orphans.value.includes(node)) return orphans.value
  return null
}

async function move(node: EditNode, dir: -1 | 1) {
  const parent = findParent(node)
  if (!parent) return
  const sibs = parent.children
  const i = sibs.indexOf(node)
  const j = i + dir
  if (i < 0 || j < 0 || j >= sibs.length) return
  if (!await applyOp({ op: 'reorder', parent: parent.id, id: node.id, dir })) return
  ;[sibs[i], sibs[j]] = [sibs[j], sibs[i]]
}

// ------------------------------------------------------------------
// 拖拽换父 (HTML5 DnD): 被拖节点存 dragNode, 各节点行判定 drop 合法性
// ------------------------------------------------------------------

const dragNode = ref<EditNode | null>(null)

function inSubtree(root: EditNode, target: EditNode): boolean {
  if (root === target) return true
  return root.children.some(c => inSubtree(c, target))
}

async function moveTo(node: EditNode, newParent: EditNode) {
  if (node === newParent || newParent.isNew || inSubtree(node, newParent)) return
  if (!await applyOp({ op: 'move', id: node.id, parent: newParent.id })) return
  // 本地同步: 原位摘除 → 挂到新父尾部
  const old = findParent(node)
  if (old) {
    const i = old.children.indexOf(node)
    if (i >= 0) old.children.splice(i, 1)
  } else {
    const i = orphans.value.indexOf(node)
    if (i >= 0) orphans.value.splice(i, 1)
  }
  newParent.children.push(node)
  newParent.expanded = true
}

async function rename(node: EditNode) {
  await applyOp({ op: 'rename', id: node.id, name: node.name.trim() })
}

provide('mtree-ops', { addChild, commitNew, remove, move, moveTo, rename })
provide('mtree-drag', dragNode)

// ------------------------------------------------------------------
// 加载
// ------------------------------------------------------------------

async function fetchTree() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${props.httpBase}/run_routine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'tree/get' }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.error || 'load failed')
    const result = data.result || {}
    if (!result.ok) throw new Error(result.error || 'load failed')
    const built = fromFlat(result.modules || {})
    root.value = built.root
    orphans.value = built.orphans
  } catch (e) {
    error.value = `加载失败: ${(e as Error).message}`
  } finally {
    loading.value = false
  }
}

// 浮窗每次打开都重新拉取 (编辑即时落盘, 重拉即当前事实)
watch(() => props.active, (v) => { if (v) void fetchTree() })
</script>

<style scoped>
.mte {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #1e2130;
  border-radius: 10px;
  overflow: hidden;
}

.mte-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-bottom: 1px solid #2d3148;
  flex-shrink: 0;
}

.mte-title {
  font-size: 14px;
  font-weight: 600;
  color: #cbd5e1;
}

.mte-hint {
  flex: 1;
  font-size: 11px;
  color: #64748b;
  user-select: none;
}

.mte-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.mte-error {
  margin: 8px 14px 0;
  flex-shrink: 0;
}
.mte-error pre {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: 'Segoe UI', system-ui, sans-serif;
}

.mte-body {
  flex: 1;
  min-height: 0;
  padding: 10px 14px 14px;
}

.mte-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.mte-orphan-label {
  margin: 14px 0 4px;
  font-size: 10px;
  color: #f0b060;
  letter-spacing: .5px;
  user-select: none;
}

.mte-orphan-tree { opacity: .85; }

.mte-empty {
  padding: 24px;
  text-align: center;
  color: #64748b;
  font-size: 12px;
}
</style>
