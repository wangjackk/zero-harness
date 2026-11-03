# 05 · 开放问题(待拍板)

前面 00-04 篇定下了架构骨架.这篇收集**还没定论,需要你拍板**的决策点.每个给:问题 / 选项 / 权衡 / **默认倾向**.

> 实现约定:如果某个问题你还没拍板,便宜模型按"默认倾向"实现即可,但要在代码里留一个 `# OQ-N: 按默认 X 实现,待确认` 注释,方便日后回切.

---

## OQ-1 · `when` 表达式的语法边界

**背景**:03 §3.3 定了 `when` 求值 **fail-closed**(解析失败就 skip),但没定语法范围.

| 选项 | 范围 |
|------|------|
| a | 仅 `==` / `!=` + 字符串相等 |
| b | a + 数值/布尔比较(`> < >= <=`,RHS 支持数字/布尔字面量) |
| c | b + 复合 `&&` / `\|\|`(Archon 支持 `$a.output=='X' && $b.output!='Y'`) |

**权衡**:复合要写个小求值器,复杂度上升;不支持复合则逼用户把条件拆成多个节点 + 中间 bash.

**默认倾向**:**b**(单比较,RHS 支持字符串/数字/布尔字面量).复合 c 按需再加----多数场景一个比较够用,拆节点反而更可读.

**拍板点**:v1 要不要直接上复合 `&&`/`||`?

---

## OQ-2 · `approval` 的暂停粒度

**背景**:Archon 的 approval 暂停**整个 run**.但在 routine 模型里,approval 只是一个"等人类回复的慢 routine"(类比 DESIGN.md 的 `HumanDesigner`).

**问题**:approval 节点 pending 时,就绪集要不要继续跑**不依赖它的并行分支**?

| 选项 | 行为 |
|------|------|
| a | 只 block 它自己 + 下游;不相关的并行分支继续跑(routine 模型的天然行为) |
| b | 暂停整个 RunDag(对齐 Archon) |

**权衡**:a 更高效,更符合 ready-set;但人类审批有时语义上就是"停下,别再动任何东西,等我看完".b 更保守可控.

**默认倾向**:**a**(并行分支继续).需要"全停"时用显式 barrier----让 approval 成为所有后续节点的共同依赖,或加一个 `pause_all: true` 标志按需开启 b.

**拍板点**:默认要 a 还是 b?要不要提供 `pause_all` 开关?

---

## OQ-3 · `cancel` 的传播机制(✅ 已决:无例外,cancel 也是普通 routine)

**背景**:cancel 节点要终止**整个** RunDag----它不是纯叶子,需要"向上终止"的能力.当初担心这打破 03 §5 "RunDag 不关心节点类型" 的纯粹性,于是把它列为"一个有意的例外".

**实现时的结论**:这个例外其实**不需要**.子→父的消息通道框架早就内建了----就是 `result`/`done`:`notify_done(result)` 就是子 routine 把结果投递给父,而 RunDag 的 ready-set 主循环本来就在消费每个子节点的 done 事件.

所以 cancel 落地成一个**普通注册 routine** `dag_cancel`:

- 它跑起来就返回一个 cancel 哨兵(`result` 里带 `DAG_CANCEL_KEY`,见 `spec.py`).
- 父 RunDag 在**它本来就在跑的 done 循环**里识别这个哨兵 → 写 `cancel_signal` → break → 靠 Shell 树形 stop 停掉还在跑的兄弟分支.
- cancel 节点走和其它节点**完全相同**的 push / resolve_kwargs / on_done 路径,`when` / 模板替换 / depends_on 全部免费复用,**dispatch 阶段零特判**.

之前考虑过的两个选项都被否掉:

| 选项 | 否掉原因 |
|------|------|
| a(编排器白名单识别 cancel 类型 + dispatch 特判) | 引入了不必要的节点类型特判 |
| b(抛特殊异常沿 routine 树冒泡) | 异常跨 routine 边界语义难控 |
| ~~c(用 `self.send`/`@event` 显式给父发消息)~~ | 反方向过度设计----`result` 通道已是子→父消息,父已在监听,不需要额外通道 |

**最终形态**:`routine: dag_cancel` 就是普通节点.RunDag 真正做到**零节点类型特判**----所有节点(普通 routine / approval / cancel)都只是 `routine: xxx`,编排器只管依赖和就绪集,不认识任何"特殊节点".比当初设想的"有意例外"更彻底.

approval 同理----它不是控制流例外,就是个"等人类回复的慢 routine"(`dag_approval`),见 OQ-2.

```yaml
- id: abort
  routine: dag_cancel
  inputs:
    reason: "分类失败:$classify.output"   # 模板替换免费复用
  depends_on: [classify]
  when: "$classify.output == 'error'"      # when 条件免费复用
```

---

## OQ-4 · outputs 持久化 / 断点续跑

**背景**:02 篇把"可恢复/断点续跑"列为 DAG 相对 routine 的核心增益.但 RunDag 的 outputs 现在是**内存态**,进程崩溃即丢失.

| 选项 | 范围 |
|------|------|
| a | v1 纯内存(进程内可重试,不跨重启) |
| b | 持久化 outputs 到磁盘/state,resume 时加载并跳过 completed 节点(对齐 Archon 的 prior_success) |

**权衡**:b 才真正兑现"可恢复"的卖点,但要定:序列化格式,scope key(按什么键找上次的 run),以及一个**硬前置约束----节点 result 必须可序列化**(routine result 可以是任意 Python 对象,未必能存盘).

**默认倾向**:**a 先行,b 列入 v2**.理由:先把就绪集/数据流/子工作流跑通;持久化是独立的增量,且会反向约束"result 必须可序列化",不该一开始就绑死.

**拍板点**:resume 是否进 v1?如果进,是否接受"可持久化的节点 result 必须可序列化"这个约束?

---

## OQ-5 · `$id.output` 的类型语义

**背景**:routine 的 result 是**任意 Python 对象**;Archon 的 output 是字符串(stdout).

**问题**:`$id.output` 替换时保留原生对象还是转字符串?

**默认倾向**:直接复用 `xml_routine` 的 `_apply_template` 已验证语义----
- 整个 kwarg 值 **等于** `$x.output` → 返回**原生 result 对象**(保留 dict/list/数值类型)
- 值里**内嵌** `$x.output`(`"前缀 $x.output 后缀"`)→ 字符串插值

**拍板点**:基本无争议,确认即可.附带:`when` 表达式里 `$x.output` 参与比较时如何 stringify(建议 `str()` 后比较,数值比较时两边都尝试转 float).

---

## OQ-6 · 子工作流定义来源:内联 vs 命名引用

**背景**:04 篇用了内联 `sub_dag`.但子工作流也可以是一个**注册过的命名 DAG**.

| 选项 | 形态 |
|------|------|
| a | 内联 `sub_dag`(一次性局部分解) |
| b | 命名引用 `routine: 'my_subdag'`(指向一个注册的 RunDag) |
| c | 都支持 |

**权衡**:命名引用利于复用,独立测试,被多个父调用;内联适合一次性的局部拆分.命名 DAG 本身就是一个注册的 RunDag 实例(吃 inputs,吐 result),天然能被任何父 push----这跟 `save.py` 固化 routine 一脉相承(见 01 篇).

**默认倾向**:**c,但命名引用优先推荐**.内联作为便利糖.

**拍板点**:v1 先做哪个?

---

## 已决(不要再纠结)

这些在 00-04 篇已经定论,列出来防止实现时重新摇摆:

- **fail-closed**:`when` 解析失败 → skip(不乱跑).
- **不复用 Shell `StartWait` 实现 DAG 依赖**:逻辑依赖留应用层,物理依赖归 Shell(03 §6).
- **就绪集(ready-set)而非分层 barrier**:吃满并发(03 §3.2).
- **不 fail-fast**:单节点失败只写 outputs,交 trigger_rule 裁决(对应 `Promise.allSettled`).
- **并发上限靠 Shell 模块互斥自然限流**,RunDag 不自设上限.
- **done callback 是唤醒信号**,不是控制流;复用 `Act` 的 `done → 队列 → 主循环` 模式.
- **作用域隔离**:子工作流有独立 outputs,只通过 inputs/result 与父通信(04 篇).
- **中断靠 Shell 树形递归 stop**,RunDag 不写特殊逻辑.
- **零节点类型特判**:所有节点都是 `routine: xxx`,控制流节点(`dag_cancel` / `dag_approval`)也是普通 routine,靠 `result`/`done` 通道与父通信(OQ-3 已落地).
