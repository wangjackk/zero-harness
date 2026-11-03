# 02 · DAG 作为应用层 Routine(不改 Shell)

## 判断:引入 DAG 有必要,但理由不是"能力",而是"约束"

routine 树是**图灵完备**的:能 push,能递归,能自我修改.任何 DAG 都能用 routine 表达----`depends_on` 无非是 push 的顺序,并行层无非是模块不冲突的兄弟.

所以从"能不能做"看,**DAG 是 routine 的真子集,多余的**.

但工程价值常来自**主动收窄能力**.DAG 的价值不在于它能做什么,**而在于它主动放弃了什么**.

---

## DAG 买到了 routine 天然给不了的四样东西

而且这四样全都源于"它放弃了图灵完备":

1. **可静态分析**:DAG 是无环有向图,跑之前就能算出节点数,依赖,能否并行,有没有环.routine 树做不到----下一步 push 什么是运行时(LLM)决定的,树的形状跑起来才知道.
2. **可视化 / 可审查 / 可 diff**:DAG 能画出来,能进 git review,能版本化.运行时动态生长的 routine 树画不出来.
3. **可恢复 / 断点续跑**:跳过已完成节点重跑,**强依赖"节点边界预先确定且确定性"**.routine 树中途失败无法简单重放----"断点"概念本身不成立.
4. **确定性复现**:同一个 DAG 每次跑结构一样.同一个 routine 任务每次可能长出不同的树.

---

## 本质:DAG = 一棵被冻结的 routine 子树

routine 和 DAG 回答两个不同的问题:

- **routine 树**:运行时如何协调那些动态涌现,由 LLM 临场决定的行动?
- **DAG**:编译期如何固化一个已知,稳定,需要重复执行的流程?

当一个流程稳定,不再需要 LLM 临场判断时,把它从"动态 routine 编排"**降级**成"静态 DAG",是一次 KISS/YAGNI 式的有意收窄.

---

## 所以最优解:把 DAG 做成一个 routine

DAG **不是 routine 的竞争者,是 routine 体系里的一种受限编排模式**.

```
RunDag(BaseRoutine):读 DAG 定义 → 拓扑分层 → 在合适时机 push 子节点 routine
```

它**是**一个 routine,住在应用层,**一行 Shell 代码都不用改**.

`xml_routine.py` 已经是这条路上的第一站----它把 `<body>` 顺序组合固化成命名 routine,本质是"退化版 DAG / 序列固化 routine",从没碰过 Shell.RunDag 是它的超集(加 `depends_on` + 并行 + trigger_rule).详细契约见 [03-rundag-design.md](./03-rundag-design.md).

---

## 为什么这符合"机制 vs 策略"(见 00 篇原则二)

DAG 影响的只是 **push 的时机**----本质还是"在合适的时机 push routine".而"决定 push 时机"是**策略**,按定义就该在应用层,不进 Shell.

RunDag 相对 Think,无非是把"决策权"从一个非确定的 LLM 换成一张确定的静态图.**从 Shell 视角,它俩没区别**:都只是"某个 routine 在决定接下来 push 谁".

---

## 边界提醒

别让 DAG 反向侵蚀 routine 的动态性.DAG 只在**流程已稳定**时才值得固化;过早把还在探索的流程画成 DAG,等于亲手阉割掉最大的优势(运行时自主).

> 下一步该做什么,是已知的 → 用 DAG;要靠 LLM 当场想 → 用 routine.
