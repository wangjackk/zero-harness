# 架构设计笔记(design/)

这个文件夹专门存放 **routine 体系**(Zero 应用层 + Go 内核 内核)的架构想法.

定位:**只写"为什么这么设计"和"接口契约",不写完整实现**.实现交给后续的编码流程,照着这里的契约和算法骨架填即可.

---

## 一句话原则清单

1. **万物皆 routine** ---- 编排器是 routine,LLM(Think)也是 routine,没有谁享有特权.
2. **机制 vs 策略分离** ---- Shell 管机制(push / 模块互斥 / 树形中断 / 生命周期);routine 管策略(什么时候 push 谁).**策略永远不进 Shell.**
3. **确定性边界** ---- 能固化成代码/routine 的步骤就固化,只把"不可外包的不确定性"留给 LLM.误差随步数放大,缩短那条链.
4. **DAG 是一种策略,所以它是一个 routine** ---- 不是 Shell 的 feature,是 Shell 之上的普通公民.
5. **嵌套结构性免费** ---- 因为编排器是 routine,routine 能 push routine,Shell 提供树形调度与树形中断.

---

## 文档索引(建议阅读顺序)

| # | 文件 | 内容 |
|---|------|------|
| 00 | [00-principles.md](./00-principles.md) | 三条地基原则:万物皆 routine,机制vs策略,确定性边界 |
| 01 | [01-skill-to-routine.md](./01-skill-to-routine.md) | skill→routine 过渡:解释执行 vs 编译执行,确定性边界为何移动 |
| 02 | [02-dag-as-routine.md](./02-dag-as-routine.md) | 为什么 DAG 必须在应用层实现而不改 Shell |
| 03 | [03-rundag-design.md](./03-rundag-design.md) | `RunDag` 接口契约 + 执行算法(从 `xml_routine` 演进) |
| 04 | [04-subworkflow-nesting.md](./04-subworkflow-nesting.md) | 子工作流 / 嵌套编排器:4 个必须定义的边界语义 |
| 05 | [05-open-questions.md](./05-open-questions.md) | 待拍板的开放问题(when 语法 / approval 粒度 / cancel 传播 / 持久化 / 类型 / 子工作流来源)+ 已决清单 |

---

## 这些想法的来源

源自一系列关于 "routine 体系 vs Archon 工作流引擎" 的对比讨论.核心结论是:Archon 用**编译期声明的扁平 DAG** 把 AI 框在可控边界里;routine 体系用**运行时可组合的递归树**让能力自由生长.两者在"该给 AI 多大自主权"上是光谱两端,但在**"把稳定流程固化,把不确定留给 LLM"**这一点上殊途同归----这正是当前这代 LLM 应用的一个收敛结构.
