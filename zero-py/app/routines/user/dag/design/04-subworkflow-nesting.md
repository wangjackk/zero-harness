# 04 · 子工作流 / 嵌套编排器

## 结论先行:不要"每个节点自带编排器"

一个常见但错误的形态是:给每个 `NodeRoutine` 内置一个编排器,靠节点嵌套实现子工作流.

**否掉它**,因为它把两个该分开的职责焊死了:

- **节点(Node)** 的职责:*我这一个工作单元怎么跑*(bash / prompt / script)
- **编排器(Orchestrator)** 的职责:*一组节点按什么依赖和时机跑*

让叶子 `bash` 节点也背一个编排器是浪费且语义错乱----它根本没有子节点要编排.违反 SRP.

---

## 正解:编排器本身就是一种 routine,子工作流 = 节点 push 一个编排器 routine

因为 routine 能 push routine(见 00 篇原则一的推论),**嵌套结构性免费**:

```
RunDag(主工作流)
 ├─ push  nodeA   → BashRoutine          (叶子)
 ├─ push  nodeB   → RunDag(子工作流)     ← 一个节点 push 了另一个编排器
 │                    ├─ push nodeB1
 │                    └─ push nodeB2
 └─ push  nodeC   → PromptRoutine        (叶子)
```

`RunDag` push `RunDag` ---- 嵌套就是递归,**一行特殊代码都不用写**.

子 RunDag 在父眼里就是一个普通被 push 的节点:父的就绪集等它的 `done`(`wait_done`/`on_done`),它内部跑自己的就绪集 + done 唤醒循环(见 03 篇 §3.2).**ready-set 模型对任意嵌套深度自动适用**----每一层各跑各的循环,各管各的 outputs map,互不知道对方存在.

这是 **Composite 模式**:
- **Composite** = `RunDag` / `xml_routine`(start() 里编排并 push 子节点)
- **Leaf** = `bash` / `prompt`(自己干活,不 push 子节点)
- 两者实现同一个 `BaseRoutine` 接口

`xml_routine` 已经是 Composite 的雏形----它注册成普通 routine,所以已经能被别的 routine push,能嵌套.RunDag 只是把"顺序组合"升级成"拓扑分层 + 并行 + trigger_rule".

---

## 嵌套真正要解决的 4 件事

形态定下来后,难点不在"怎么嵌套",而在这四个边界语义.**这才是子工作流的工程核心.**

### 1. 变量作用域(`$nodeId.output` 命名空间)

**强烈建议隔离**:子工作流内部的 outputs 自成一个 map,节点名不污染父级.父子之间**只通过显式的 inputs / 返回值通信**.

否则深层嵌套会出现命名冲突和"远程依赖",可读性崩坏.

### 2. 输入 / 输出契约(天然映射 routine 现成机制)

子工作流对父来说是**一个黑盒节点**:吃 kwargs,吐 result.

- **输入** = push 时的 `kwargs`.父把 `$某节点.output` 算出来当参数传进子工作流(对应 `DagSpec.inputs`).
- **输出** = 子工作流的 `result`.把内部 N 个节点的产出**聚合成单一返回值**(对应 `DagSpec.output` 指定哪个节点的 output 作为整体 result).这样父级的 `$nodeB.output` 才有意义----**父不该看到子工作流的内部节点**.

这就是封装:

```
父级 nodeB.kwargs = { "topic": "$nodeA.output" }   # 输入:父算好传进去
   └─ 子 RunDag(inputs=["topic"], output="summary")
        ├─ nodeB1 用 $topic
        └─ summary 节点 → 它的 output 成为子工作流的 result
父级 outputs["nodeB"] = <summary 的 output>          # 输出:单一值冒泡回父
```

### 3. 中断传播 ---- 这是相对 Archon 的杀手级优势

父被打断时,子工作流必须级联停止.**Shell 已经免费给了**:树形中断从根递归 stop 到叶.

- Archon 的 `executeDagWorkflow` 要在 executor 里自己处理暂停/取消的层间检查.
- routine 体系里,无论嵌套多深,一个 interrupt 从树根原子性停掉整棵----**这是内核能力,不是你要写的逻辑**.

RunDag 自身**无需为中断写任何特殊代码**.

### 4. 失败聚合 / trigger_rule

子工作流作为父的一个节点,它的 `state`(completed/failed)= 它内部执行的聚合结果:

```
子工作流内部某关键节点失败
   → 子 RunDag 的 result 为失败 / 抛错
   → 父的 wait_done 看到 error
   → outputs["nodeB"].state = 'failed'
   → 父的下游节点按 trigger_rule 裁决
```

失败语义自然沿树向上冒泡.

---

## 为什么 routine 模型做嵌套碾压 Archon

| | routine 体系 | Archon |
|---|---|---|
| 嵌套 | 递归免费(routine push routine) | `executeDagWorkflow` 是单层扁平拓扑,要嵌套得自己递归调 executor |
| 级联中断 | Shell 树形中断,结构性免费 | 要手写暂停/取消的层间传播 |
| 节点类型 | 都只是 routine 名,RunDag 不关心 | schema 里硬编码 7 种 variant |

**你要写的只有 4 样东西的语义:作用域隔离,输入输出契约,(中断已免费),失败聚合.** 嵌套和级联中断本身是白送的.

---

## 实现 checklist

- [ ] RunDag 支持 `node.routine == 'rundag'` + `node.sub_dag`:push 一个新的 RunDag 实例,kwargs 传 inputs
- [ ] 子 RunDag 用**独立的 outputs map**(作用域隔离)
- [ ] `DagSpec.inputs`:子工作流声明入参,`_resolve` 时从 push 的 kwargs 注入
- [ ] `DagSpec.output`:指定聚合节点,其 output 作为子 RunDag 的 `result` 返回
- [ ] 验证父级只能引用子工作流节点的整体 result,不能穿透引用其内部节点
- [ ] 中断:确认依赖 Shell 树形 stop,RunDag 不写特殊逻辑(写测试验证级联停止)
