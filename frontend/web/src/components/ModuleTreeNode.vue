<template>
  <div class="mtn-node" :style="{ marginLeft: (depth * 16) + 'px' }">
    <!-- 新建行: id 待填, 填完转正 -->
    <div v-if="node.isNew" class="mtn-row mtn-row-new">
      <NInput
        v-model:value="node.id"
        size="tiny"
        placeholder="模块 id (如 head)"
        class="mtn-new-id"
        @keydown.enter="ops.commitNew(node)"
      />
      <NInput
        v-model:value="node.name"
        size="tiny"
        placeholder="名称 (可空)"
        class="mtn-new-name"
        @keydown.enter="ops.commitNew(node)"
      />
      <button class="mtn-act" title="确认 (Enter)" @click="ops.commitNew(node)">✓</button>
      <button class="mtn-act mtn-act-danger" title="取消" @click="ops.remove(node)">✕</button>
    </div>
    <!-- 常规行 (可拖拽换父: 拖到目标行上释放) -->
    <div
      v-else
      class="mtn-row"
      :class="{ 'mtn-drop-ok': dropOk }"
      :draggable="!editing"
      @dragstart="onDragStart"
      @dragend="onDragEnd"
      @dragover="onDragOver"
      @dragleave="dropOk = false"
      @drop="onDrop"
    >
      <button
        v-if="node.children.length"
        class="mtn-fold"
        :title="node.expanded ? '折叠' : '展开'"
        @click="node.expanded = !node.expanded"
      >{{ node.expanded ? '▾' : '▸' }}</button>
      <span v-else class="mtn-fold mtn-fold-leaf">·</span>
      <span class="mtn-id" :class="{ 'mtn-id-root': isRoot }" :title="`id: ${node.id}`">{{ node.id }}</span>
      <!-- 别名: 查看态纯文本 (空则 hover 淡显 ✎ 加名入口), 点击进编辑 -->
      <span
        v-if="!editing"
        class="mtn-name-view"
        :class="{ 'mtn-name-empty': !node.name }"
        title="别名"
        @click="startEdit"
      >{{ node.name || '✎' }}</span>
      <NInput
        v-else
        ref="nameInput"
        v-model:value="node.name"
        size="tiny"
        placeholder=" "
        class="mtn-name-edit"
        @blur="commitEdit"
        @keydown.enter="commitEdit"
      />
      <div class="mtn-acts">
        <button class="mtn-act" title="添加子模块" @click="ops.addChild(node)">＋</button>
        <template v-if="!isRoot">
          <button class="mtn-act" title="上移" @click="ops.move(node, -1)">↑</button>
          <button class="mtn-act" title="下移" @click="ops.move(node, 1)">↓</button>
          <button class="mtn-act mtn-act-danger" title="删除 (含子树)" @click="ops.remove(node)">✕</button>
        </template>
      </div>
    </div>
    <template v-if="node.children.length && node.expanded">
      <ModuleTreeNode
        v-for="c in node.children"
        :key="c.uid"
        :node="c"
        :depth="depth + 1"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { inject, nextTick, ref, type Ref } from 'vue'
import { NInput } from 'naive-ui'

export interface EditNode {
  uid: number
  id: string
  name: string
  children: EditNode[]
  isNew?: boolean
  expanded: boolean
}

export interface TreeOps {
  addChild: (parent: EditNode) => void
  commitNew: (node: EditNode) => void
  remove: (node: EditNode) => void
  move: (node: EditNode, dir: -1 | 1) => void
  moveTo: (node: EditNode, newParent: EditNode) => void
  rename: (node: EditNode) => void
}

const props = defineProps<{
  node: EditNode
  depth: number
}>()

const ops = inject<TreeOps>('mtree-ops')!

const isRoot = props.node.id === 'root' && props.depth === 0

// 别名 click-to-edit: 查看态文本/✎, 点击 → 短输入框 (Enter/blur 提交, 有变更才发事务)
const editing = ref(false)
const nameInput = ref<InstanceType<typeof NInput> | null>(null)
let editPrev = ''

function startEdit() {
  editPrev = props.node.name
  editing.value = true
  nextTick(() => nameInput.value?.focus())
}

function commitEdit() {
  if (!editing.value) return
  editing.value = false
  if (props.node.name !== editPrev) ops.rename(props.node)
}

// ── 拖拽换父 ──
const dragNode = inject<Ref<EditNode | null>>('mtree-drag')!
const dropOk = ref(false)

/** 本行是否可作 drop 目标: 有被拖节点, 非自身/其子树, 非未转正行. */
function dropValid(): boolean {
  const d = dragNode.value
  if (!d || d === props.node || props.node.isNew) return false
  const inSub = (r: EditNode): boolean => r === props.node || r.children.some(inSub)
  return !inSub(d)
}

function onDragStart(e: DragEvent) {
  dragNode.value = props.node
  e.dataTransfer!.effectAllowed = 'move'
  e.dataTransfer!.setData('text/plain', props.node.id)   // Firefox 需非空 data
}

function onDragEnd() {
  dragNode.value = null
  dropOk.value = false
}

function onDragOver(e: DragEvent) {
  if (!dropValid()) return
  e.preventDefault()   // 允许 drop
  e.dataTransfer!.dropEffect = 'move'
  dropOk.value = true
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  const d = dragNode.value
  const ok = dropValid()   // 先判定, 再清 dragNode (dropValid 依赖它)
  dragNode.value = null
  dropOk.value = false
  if (d && ok) ops.moveTo(d, props.node)
}
</script>

<style scoped>
.mtn-node {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.mtn-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 4px 0 6px;
  border-radius: 5px;
  background: #171a26;
  border: 1px solid #23273a;
}
.mtn-row:hover .mtn-acts { opacity: 1; }

/* drop 目标高亮 */
.mtn-row.mtn-drop-ok {
  border-color: #6366f1;
  box-shadow: 0 0 0 1px #6366f1 inset;
}

.mtn-row-new {
  border-style: dashed;
  border-color: #6366f1;
}

.mtn-fold {
  width: 14px;
  flex-shrink: 0;
  background: none;
  border: none;
  color: #8b90a8;
  font-size: 11px;
  cursor: pointer;
  line-height: 1;
  padding: 0;
}
.mtn-fold-leaf { cursor: default; color: #3d4270; }

.mtn-id {
  flex-shrink: 0;
  min-width: 44px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #818cf8;
  user-select: none;
}
.mtn-id-root { color: #c7d2fe; font-weight: 600; }

.mtn-name-view {
  color: #b9bdd1;
  font-size: 11px;
  cursor: text;
  user-select: none;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mtn-name-view:hover { color: #e2e4f0; }

/* 空别名的 ✎ 入口: 平时隐形, hover 行淡显 */
.mtn-name-empty { opacity: 0; color: #565b78; }
.mtn-row:hover .mtn-name-empty { opacity: 1; }

.mtn-name-edit {
  width: 96px;
  flex-shrink: 0;
}

.mtn-new-id {
  width: 160px;
  flex-shrink: 0;
}
.mtn-new-name {
  flex: 1;
  min-width: 80px;
}

/* 行内操作按钮: hover 浮现, 不占常驻视觉 */
.mtn-acts {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity .12s;
}

.mtn-act {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 4px;
  color: #8b90a8;
  font-size: 12px;
  cursor: pointer;
  line-height: 1;
  padding: 0;
}
.mtn-act:hover { color: #c7d2fe; background: #23273a; }
.mtn-act-danger:hover { color: #f87171; background: #3a1d1d; }
</style>
