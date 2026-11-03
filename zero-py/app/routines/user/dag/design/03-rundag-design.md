# 03 · RunDag 设计:接口契约 + 执行算法

`RunDag` 是一个**应用层编排 routine**:读一张 DAG 定义,用就绪集(ready-set)在合适的时机 push 子节点 routine,用一张 outputs map 做数据流.它是 `xml_routine` 的超集.

本篇是给实现用的契约,不是完整实现.

---

## 1. 从 `xml_routine` 演进的路径

当前 `xml_routine.build_xml_routine_class` 生成的 routine,`start()` 把 body XML 喂给 body parser → 顺序 push 一组子 routine → `wait_done`.

演进四步:

1. 给 spec 加 `depends_on`(节点级依赖声明)
2. 把 body 的**顺序 push** 换成 **就绪集 + done 唤醒**(不按层 barrier,见 §3.2)
3. 加一张 `outputs` map,支持 `$nodeId.output` 解析
4. 定义子工作流的 inputs / result 契约(见 04 篇)

Shell 不动.

---

## 2. DAG 定义 schema(实现版,见 `spec.py`)

```python
@dataclass
class InputSpec:                     # DAG 级输入参数(节点里用 $WI.name 引用)
    name: str
    type: str = 'str'                # str | int | float | bool(加载期强制 coerce)
    desc: str = ''
    default: Any = None
    required: bool = True            # default is None 且未标 optional 时为 True

@dataclass
class RetrySpec:
    max_attempts: int = 1            # 最多重试次数(不含首次),1-5
    delay_ms: int = 2000             # 首次重试延迟,每次 ×2(指数退避),1000-60000
    on_error: str = 'all'            # 'all' | 'transient'(预留,暂不区分)

@dataclass
class DagNodeSpec:
    id: str                          # 唯一标识,outputs map 的 key
    routine: str                     # 要 push 的 routine 名(必填)
    inputs: dict[str, Any] = field(default_factory=dict)  # 传给子 routine,值支持模板
    depends_on: list[str] = field(default_factory=list)
    trigger_rule: str = 'all_success'  # join 语义,见 §3.3
    when: str | None = None          # 条件表达式,如 "$nodeA.output == 'OK'"
    retry: RetrySpec | None = None   # 失败重试配置

@dataclass
class DagSpec:
    name: str
    description: str
    nodes: list[DagNodeSpec]
    inputs: list[InputSpec] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)  # {别名 → node_id}
```

**与早期草案的三处差异(已落地,勿改回):**

- `kwargs` → **`inputs`**:与 DAG 级 `inputs` / `outputs` 命名对称.
- 单数 `output: str` → **`outputs: dict[str, str]`**(别名→node_id):可同时导出多个节点结果,且别名做 API 隔离层(改 DAG 内部 node id 不影响外部契约).`{}` 时返回全量状态表 `{node_id: {state, output, error}}`.
- **没有 `sub_dag` 字段**.子工作流不是特殊节点类型----它就是一个 `routine: run_dag` 的普通节点,`inputs` 里带 `dag` 和 JSON 字符串 `inputs`(见 §5,04 篇).RunDag 不认识"子 DAG"这个概念.

**控制流也是普通 routine,spec 里没有它们的位置:**

- `cancel` → `routine: dag_cancel`,靠 **return 值里的哨兵 `DAG_CANCEL_KEY`** 让父 RunDag 终止整图(`spec.py` 定义该常量).
- `approval` → `routine: dag_approval`,内部 push `ask` 走 UI.

二者都走和其它节点完全相同的 push/done 路径,**执行器无任何特判**----这是相对 Archon 把 7 种 variant 硬编进 schema 的关键简化.

---

## 3. 执行算法

### 3.1 拓扑分层(Kahn)---- 仅用于校验 / 可视化

```
inDegree[node] = len(node.depends_on)
ready = [n for n if inDegree[n]==0]   # Layer 0
while ready:
    layers.append(ready)
    削减每个 ready 节点的下游 inDegree,归零的进入下一层
环检测:sum(layer sizes) < len(nodes) → 有环(加载期就该报错)
```

> 注意:分层**只用于加载期环检测和 UI 可视化**.**执行不按层走**----见 §3.2 的就绪集模型.严格分层会损失并发度(`Layer0=[A(慢),B(快)]`,`Layer1=[C 依赖 B]` 时,C 本可在 B 一 done 就跑,却被迫等慢的 A).

### 3.2 执行:就绪集(ready-set)+ done 唤醒

不按层走.维护 outputs map,**任一**节点完成就重扫"哪些 pending 节点依赖已满足"并立即 push----吃满并发.`done` callback **不直接 push 下游**,只**唤醒中央循环**,由循环统一决策 push 谁.

下面是实现骨架(精简自 `run_dag.py`,省略日志):

```python
# inputs 是 JSON 字符串:前端 XML 和父 DAG 节点都以字符串透传,
# 声明成 dict 会被 smartparams 当非法字典丢弃(见 run_dag.py 注释).
async def start(self, dag: str, inputs: str = ''):
    spec = self._load_spec(dag)
    parsed = json.loads(inputs) if inputs else {}
    dag_inputs = _validate_and_coerce_inputs(spec, parsed)   # 校验必填 + 按 type coerce
    return await self._run(spec, dag_inputs)

async def _run(self, dag, dag_inputs):
    outputs = {}
    pending = {n.id for n in dag.nodes}
    running = set()
    cancel_signal = []          # dag_cancel 哨兵写这里
    retry_counts = {}

    async def try_dispatch():
        for n in [n for n in dag.nodes if n.id in pending]:
            if not deps_all_terminal(n, outputs):        # 上游都进终态了吗
                continue
            pending.discard(n.id)
            if not should_run(n, outputs, dag_inputs):   # trigger_rule + when(fail-closed)
                outputs[n.id] = NodeOutput(state='skipped'); await try_dispatch(); return
            kwargs = resolve_kwargs(n, outputs, dag_inputs)   # $id.output[.field] / $WI.x
            h = await self.push(n.routine, kwargs=kwargs)
            h.on_done = make_done_handler(n)             # 只喂事件,不决策
            running.add(n.id)

    await try_dispatch()                                  # 先派入度 0 节点
    while running or (not cancel_signal and pending):
        if cancel_signal: break
        node, h = await self._done_q.get()                # 等任一完成
        running.discard(node.id)
        if h.error:
            r = next(n for n in dag.nodes if n.id == node.id).retry
            if r and retry_counts.get(node.id, 0) < r.max_attempts:   # 指数退避后重投
                retry_counts[node.id] = retry_counts.get(node.id, 0) + 1
                await asyncio.sleep(r.delay_ms * 2 ** (retry_counts[node.id]-1) / 1000)
                pending.add(node.id)
            else:
                outputs[node.id] = NodeOutput('failed', error=h.error, output=h.result)
        else:
            outputs[node.id] = NodeOutput('completed', output=h.result)
            if isinstance(h.result, dict) and h.result.get(DAG_CANCEL_KEY):   # cancel 哨兵
                cancel_signal.append((node.id, h.result.get('reason', '')))
        if cancel_signal: break
        await try_dispatch()                              # 重扫就绪

    if cancel_signal:
        raise RuntimeError(f'DAG cancelled: {cancel_signal[0][1]}')
    # outputs 非空 → 按别名挑节点 output;为空 → 返回全量状态表
    return ({alias: outputs[nid].output for alias, nid in dag.outputs.items()}
            if dag.outputs else
            {nid: vars(o) for nid, o in outputs.items()})
```

**要点**:

- **不 fail-fast**:单节点失败只写进 outputs,交给下游 trigger_rule 裁决(对应 Archon 的 `Promise.allSettled`).
- **数据流强制延迟 push**:B 的 inputs 含 `$A.output`,只有 A done 后才构造得出----这正是**不能一次性 push 全部,必须 ready-set** 的根因(也是不能直接复用 Shell `StartWait` 的根因之一,见 §6).
- **done callback 是唤醒信号,不是控制流**:直接复用 Zero 的 `Act` 已有模式(`on_routine_done → 队列 → 主循环消费`).
- **retry / cancel 都在 done 循环里裁决**,不污染 dispatch:retry 把节点重新塞回 `pending`;cancel 靠 routine 的 return 哨兵触发,执行器对这两个 routine 名零特判.
- **inputs 是 JSON 字符串**:见 §4.1 与 `start()` 注释.

### 3.3 节点执行前的判定(`_should_run`)

依次判,任一不过则 skip:

**a. trigger_rule(join 语义)** ---- 看 `depends_on` 上游状态:

| rule | 通过条件 |
|------|---------|
| `all_success` | 所有上游 completed(默认) |
| `one_success` | 任一上游 completed |
| `none_failed_min_one_success` | 无失败 且 至少一个成功 |
| `all_done` | 所有上游进入终态(不论成败) |

无 `depends_on` → 直接 run.

**b. when 条件** ---- 由独立模块 `condition_evaluator.evaluate_condition` 求值(**不用 `eval`**),**fail-closed**:解析不了就跳过节点(宁可不跑也不乱跑).支持:

- 字符串相等/不等:`$a.output == 'OK'`,`!=`
- 数值比较(两侧均可解析为有限浮点时生效):`$a.output > '80'`,`>= <= <`
- 字段提取:`$a.output.score >= 0.9`(output 是 dict 或 JSON 字符串时取 key)
- 工作流输入:`$WI.city == '南阳'`
- 复合:`&&` / `||`(AND 优先级高于 OR,无括号),引号内的分隔符不参与拆分

---

## 4. 数据流:一张 outputs map

```python
@dataclass
class NodeOutput:
    state: str          # 'completed' | 'failed' | 'skipped'
    output: Any = None  # 子 routine 的 result(叶子)/ 子 DAG 的 result(子工作流)
    error: str | None = None
```

下游节点的 `inputs` 里写模板变量,`resolve_kwargs` 时从 outputs map / dag_inputs 取值替换.这是固定节点和 AI 节点能在一张图里协作的粘合剂:bash 算出值 → prompt 节点用 `$bash_node.output`.

### 4.1 模板变量语法(`executor.resolve_kwargs`)

| 写法 | 含义 |
|------|------|
| `$nodeId.output` | 引用某节点的 return 值(整体 `str()` 化) |
| `$nodeId.output.field` | 节点 return 是 dict 时,取其中一个 key |
| `$WI.inputName` | 引用 DAG 级输入参数(WI = Workflow Inputs) |

替换顺序:先 `$nodeId.output[.field]`(带 `.output` 后缀,优先级高),再 `$WI.x`.**显式 `$WI.` 命名空间**是刻意的----避免工作流输入和节点 id 撞名导致的歧义(加载期还会强制二者不许重名).引用不到时抛 `KeyError`,该节点记 `failed`.

> **为什么 DAG 入参走 JSON 字符串而非 dict**:`run_dag` 既被前端 XML 调用,也被父 DAG 节点透传,两边都只能传字符串.若把 `inputs` 声明成 `dict`,`smartparams` 会按字典校验,把 JSON 字符串整个丢弃 → 子 DAG 收不到入参.所以 `start(inputs: str)`,内部 `json.loads` + `_validate_and_coerce_inputs`(校验必填,按 `InputSpec.type` 强转).这是踩过两次坑后钉死的契约.

**作用域**:outputs map 是**单个 DAG 实例私有**的.子工作流有自己独立的 outputs,不污染父级(见 04 篇).

---

## 5. 节点类型映射(沿用确定性边界,见 01 篇)

| 类别 | node.routine 指向 | 是否 AI |
|------|------|------|
| 固定 | `bash` / `script` 类 routine | 否 |
| 动态 | `inference`/`act` 类 / 命名 prompt routine | 是 |
| 控制 | `dag_approval`(人工审批)/ `dag_cancel`(终止) | -- |
| **子工作流** | `routine: run_dag`,`inputs` 带 `dag` + JSON 字符串 `inputs` | 取决于内部 |

注意:在 routine 体系里这些"类型"不需要像 Archon 那样在 schema 里硬编码 7 种 variant----**它们都只是 routine 名**.RunDag 不关心被 push 的是 bash 还是子 DAG,只关心"push 一个 routine,等它 done,收 result".

控制流也未破例(见 §2 末尾):`dag_cancel` 用 return 哨兵 `DAG_CANCEL_KEY` 通知父级终止,`dag_approval` 内部 push `ask`----**子→父用 result 通道发消息**,执行器不为它们写任何特判分支.这是 routine 模型比 Archon 扁平 executor 更统一的地方.

---

## 6. 与 Shell 调度的关系:两层正交等待

RunDag 的就绪集和 Shell 的 `StartWait` / `StopWait` 机制**机制同构**----都是"等待集满足 → 推进"的事件驱动调度(Shell 用 `startWaitTopics` 等 topic 集合,集合空了就 start/stop).但它们管**正交的两个维度**,不要合并,**也不要用 Shell 的 StartWait 去实现 DAG 依赖**.

| | Shell 的 StartWait/StopWait | RunDag 的就绪集 |
|---|---|---|
| 依赖来源 | 模块冲突 + 树位置(兄弟/祖先) | `depends_on` + trigger_rule + when + 数据流 |
| 驱动 | 物理资源 / 结构 | 业务逻辑 / 数据 |
| 它懂 | "你俩抢同一个模块,排队" | "等 A,B 任一成功且 `$A.output=='OK'`" |

**为什么不能用 Shell 的 StartWait 直接实现 DAG**(即"一次性 push 所有节点,各挂 `StartWait(dep, Stopped)`"):

1. **trigger_rule / when 是 Shell 不懂的条件**.`StartWait` 只能表达"无条件等 A 到某状态",表达不了 `one_success`,表达不了 `when` 条件.
2. **数据流强制延迟 push**.push B 时需要 `$A.output`,而 A 还没 done----构造不出 kwargs.
3. **skip 语义**.被判 skip 的节点根本不该进调度器.

**正确分层(叠加生效)**:

```
RunDag 就绪集(逻辑依赖):何时,带什么参数,push 谁   ← depends_on / trigger_rule / when / $output
   │ push
   ▼
Shell StartWait/StopWait(物理依赖):兄弟节点能否并发  ← 模块互斥 / 树位置 / 中断
```

RunDag 决定"逻辑上 A 完了可以上 B,C"并 push;Shell 再决定 B,C 物理上能否并发(抢模块就排队).各管一层,互不知道对方的依据.

**关键**:RunDag 复用的是 Shell 的 **`wait_done` / `on_done`**(通用的"完成事件"原子),**不是** `StartWait`(Shell 内部的资源调度策略).站在内核的完成事件之上搭业务等待集----别一看 Shell 有 `StartWait` 就想抄近路.

---

## 7. 实现 checklist

- [x] `DagNodeSpec` / `DagSpec` / `InputSpec` / `RetrySpec` 数据结构 + YAML 解析(`spec.py` / `loader.py`)
- [x] 加载期校验:环检测(Kahn),`depends_on` 引用存在,`$id.output` / `$WI.x` 引用合法,input 与 node id 不重名
- [x] `resolve_kwargs`:`$id.output[.field]` / `$WI.x` 模板替换(`executor.py`)
- [x] `should_run`:trigger_rule 4 种 + when 表达式求值(独立 `condition_evaluator.py`,fail-closed)
- [x] 执行:就绪集 + done 唤醒(不按层 barrier),复用 `done → 队列 → 主循环` 模式(`run_dag.py`)
- [x] outputs map + NodeOutput 状态记录;`DagSpec.outputs` 别名导出
- [x] 节点级 retry(指数退避,`RetrySpec`)
- [x] cancel(`dag_cancel` + `DAG_CANCEL_KEY` 哨兵)/ approval(`dag_approval` push `ask`)---- 均为普通 routine,执行器零特判
- [x] 子工作流嵌套(`routine: run_dag`,见 04 篇)
- [x] **不**复用 Shell 的 `StartWait` 实现 DAG 依赖----逻辑依赖留应用层,物理依赖归 Shell(见 §6)
- [ ] Kahn 分层结果对外暴露(仅环检测已用;UI 可视化数据待做)
- [ ] 中断收尾:Shell 树形中断打到 RunDag 时在跑子节点的收尾语义待验证(见 04 篇)

> 已实现部分的端到端示例见 `dags/*.yaml`(`hello_dag` 数据流,`branch_dag` when/字段提取,`retry_dag`,`cancel_dag`,`approval_dag`,`nested_dag` 子工作流,`trigger_dag`,`test_menu` 菜单),演示用 routine 在 `demo_routines.py`.
