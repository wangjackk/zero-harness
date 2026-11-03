/* 树的绘制原语只有一个来源：routine-atlas/src/lib/tree.ts 与 treeArt.ts。
   先抄一份到 web 里最省事，但抄完两边就会各改各的 ——
   锥形轮廓的锥度、叶片的卵形、叶变换的绕点，这几处一不一致，两棵树就不再是同一棵树。

   这里只做转发，不实现。要改树怎么画，去 atlas 那边改，两边同时生效。
   （routine-atlas 是这棵树的 "SDK"，web 只是它的一个消费者。） */
export {
  smooth,
  ribbonPath,
  leafPath,
  type Pt,
  type LeafNode,
} from '../../../../routine-atlas/src/lib/tree'

export {
  LEAF_UNIT,
  LEAF_W,
  leafT,
  leafAbs,
  tone,
} from '../../../../routine-atlas/src/lib/treeArt'
