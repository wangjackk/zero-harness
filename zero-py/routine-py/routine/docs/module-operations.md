# 模块操作手册

routine 侧模块操作完整参考.模块是 routine 互斥调度的核心:每个模块有唯一 **`id`**(作 key,互斥依据)和可重复的 **`name`**(显示名,渲染用).

模块树由 kernel 唯一持有(`tree.json` 配置 + 运行时动态增删),routine 侧缓存拓扑后本地算 cone/conflict.

---

## 概念速览

| 概念 | 说明 |
|---|---|
| **module_id** | 模块唯一标识,作 flat map 的 key.cone/conflict/acquire 全用它.不可重复. |
| **name** | 显示名,可重复(如左右手都有"大拇指").缺省=module_id.渲染/UI 用. |
| **cone** | 模块的冲突锥 = 祖先 + 自己 + 后代.占一个节点会挡住 cone 内任意节点. |
| **conflict** | 两组 modules 的 cone 相交即冲突→业务侧串行;不相交→可并行. |
| **occupy (holder)** | routine acquire 一个模块后,该模块 holders 列表追加 rid.释放才移除. |
| **静态声明** | `on_created()` 返回 `Modules([...])`,created 时占,stop 时自动释放. |
| **运行时占/释放** | `acquire()`/`release()`/`force_release()`/`force_acquire()`,run() 体里动态调. |
| **动态加载/卸载** | `load_module()`/`unload_module()`,改全局树拓扑,只挂树不占用. |

---

## 操作清单

### 1. 静态声明占用(on_created)

routine 创建时声明占用哪些模块,created 阶段就占(早于 start).

```python
from routine import Routine, Modules

class WriteTool(Routine):
    async def on_created(self, rid, kwargs):
        path = kwargs.get('path')
        return Modules([path])  # 文件路径作 module_id,cone 互斥同文件写
```

- **返回 `Modules([...])`**:created 时 kernel `TryAcquire` 占住,stop 时自动全清.
- **返回 `None`/不 override**:不占模块,跟谁都无冲突(只读工具).
- 占用是**实例级**:`on_created` 根据 `kwargs` 返回(如写入路径由参数定).这是单一真理源,编排器用 `handle.modules` 算 conflict.

> 静态声明等价于 created 时自动 `acquire`,stop 时自动 `release`.要更细控制用下面的运行时 API.

---

### 2. 运行时占领:`acquire(modules)`

run() 体里动态占模块.只 started 后可用.

```python
await self.acquire(['new_task'])
```

- **底层**:发 `routine.acquire`{req_id, id, modules} → kernel `TryAcquire` → 回 `routine.acquired`{req_id, ok, error}.
- **冲突**(cone 内有非祖先的第三方占用):抛 `AcquireError`.
- **幂等**:重复 acquire 同一模块是 no-op(holders 去重).
- **父子共占**:子 acquire 父已占的模块,ancestors 含父 → 跳过 → 成功叠加(父子协作共占).
- **超时**:`ACK_TIMEOUT`(30s)等不到 ack 抛 `TimeoutError`.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | 未 started 就调 |
| `AcquireError` | 模块被第三方占用(冲突) |
| `TimeoutError` | ack 超时(kernel 异常) |

---

### 3. 运行时释放:`release(modules)`

只释放指定模块(不全量).只 started 后可用.

```python
await self.release(['new_task'])
```

- **底层**:发 `routine.release`{req_id, id, modules} → kernel `ReleaseModules` → 回 `routine.released`{req_id, ok}.
- 节点不存在 / 自己没占都是 no-op.
- **stop 兜底**:routine stop 时 kernel 自动 `Release(rid)` 全量清理该 routine 占的所有模块,无需手动 release.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | 未 started 就调 |
| `ReleaseError` | 罕见(release 一般不冲突) |

---

### 4. 强制释放:`force_release(modules)`

驱逐 cone 内第三方 holder(cascade stop,带 `reason='force'`)后**空出 modules,不自己占**.要占住另调 `acquire` / `force_acquire`.

```python
await self.force_release(['blocked_module'])
# 模块空出了, 但本 routine 没占住. 要占:
await self.acquire(['blocked_module'])  # 或 force_acquire
```

- **语义**:只清场(打断占住者),不占.名字名副其实 = 强制释放.
- **跟 force_acquire 的区别**:force_acquire 驱逐后自己占住(原子无竞态);force_release 只清场,驱逐与后续 acquire 间有竞态窗口(调用方自担).
- **永不驱逐祖先**(打断父亲自己也死).单轮驱逐不重试.
- **底层**:发 `routine.force_release`{req_id, id, modules} -> kernel `EvictableHolders` + cascade stop -> 回 `routine.released`.基本总成功(驱逐本身不失败).

- **只动自己**: `ReleaseModules` 检查 `hasHolder(rid)`, 只移除自己那份 holder, 不动别人. 别人占的模块 release = no-op; 自己和别人共占的模块 release 只摘自己, 别人还在.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | 未 started |

- **不碰自己/祖先**: `EvictableHolders` 算驱逐集时第一道过滤 `h == rid` 跳过自己, ancestors 里的也跳过. 所以自己 force_acquire 占住后再 force_release 同一模块 = 驱逐集空, no-op 不自杀. force 是打断别人, 绝不打断自己/祖先(打断父亲自己也死).
| `ReleaseError` | 罕见(rid 未 started) |

---

### 5. 强制占领:`force_acquire(modules)`

驱逐 cone 内第三方 holder(cascade stop,带 `reason='force'`)后,本 routine 自己占住 modules(带驱逐的 acquire,原子无竞态).

```python
await self.force_acquire(['blocked_module'])  # 驱逐占住者后自己占住
```

- **跟 acquire 的区别**:acquire 冲突直接抛(等占住者自然释放);force_acquire 主动打断占住者抢过来.
- **跟 force_release 的区别**:force_release 只驱逐不占(空出模块);force_acquire 驱逐后自己占住.
- **永不驱逐祖先**(打断父亲自己也死).单轮驱逐不重试.
- **底层**:发 `routine.force_acquire`{req_id, id, modules} -> kernel `EvictableHolders` + cascade stop + `TryAcquire` -> 回 `routine.acquired`(ack 复用 acquire 通路).
- **失败**:驱逐后仍冲突(竞态,被别人抢了)抛 `AcquireError`.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | 未 started |

- **不碰自己/祖先**: 同 force_release. `EvictableHolders` 排除 `rid` 自己和 ancestors. 即使目标模块当前被自己占着(重复 force_acquire 同模块), 也不会驱逐自己, 只是 TryAcquire 幂等 no-op(自己已在 holders 里).
| `AcquireError` | 驱逐后仍撞竞态(被别人抢了) |

---

### 6. 加载子模块:`load_module(parent, child, name='')`

往父模块下动态挂一个子模块(全局树增拓扑).**只挂树不占用**----要独占另调 `acquire`.

```python
await self.load_module('figure', 'new_task', name='新任务')
```

- **底层**:发 `routine.load_module`{req_id, parent_id, child_id, name} → kernel `LoadModule` → 回 `routine.module_loaded`{req_id, ok, error} → 成功后 kernel `pushModuleView` 重推 module.tree 给所有 conn.
- **name**:显示名,可重复(左右手都 `大拇指`);空则用 `child_id`.
- 成功后本地 `runtime.module_tree` 缓存由重推刷新(也可主动 `get_module_tree()` 拉).
- **只挂树**:load 后该模块对所有 routine 可见,任何 routine 都能 `acquire` 它.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | 未 started |
| `LoadModuleError` | `child_id` 已存在 / `parent_id` 不存在 / `child_id` 为空 |

---

### 7. 卸载子模块:`unload_module(child)`

动态删子模块(全局树删拓扑).**对标文件系统删文件/文件夹**:

| 条件 | 报错 |
|---|---|
| 不存在 | `module "X" not found` |
| **有子模块** | `module "X" has children [C, D], unload them first`(先卸子) |
| **自身被占** | `module "X" is occupied by routines [1, 2], release first`(先释放) |
| 通过 | 从父摘下 + 删除 |

```python
await self.unload_module('new_task')
```

- **底层**:发 `routine.unload_module`{req_id, child_id} → kernel `UnloadModule` → 回 `routine.module_unloaded`{req_id, ok, error} → 成功后 kernel 重推 module.tree.
- **检查顺序**:`has-children` 先于 `holders`.所以"自身被占 + 还有子"时报 `has children`(表层),真实根因(自身被占)被遮住----要先卸完子才会撞到自身的 occupied 报错.
- **必须自底向上**:卸一棵子树要逐层 unload,每层都要求 `Children` 为空.占用链上任意一点被占,整条 unload 路径都走不通,最终卡在被占节点上报 `occupied`.
- **为什么不自动清占用**:holders 是实例级运行时状态.删节点不清 holders → 占用者还以为占着 → 后续 `TryAcquire` 查 cone 撞到已删节点 → 破坏互斥正确性.所以 unload 只接受叶子 + 无人占的节点.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | 未 started |
| `UnloadModuleError` | 有子 / 被占 / 不存在 |

---

### 8. 查冲突:`conflict(mods_a, mods_b)`

纯本地计算(零 round-trip),读缓存的 module.tree 算 cone 相交.**编排器串/并行判定用**.

```python
if self.ctx.conflict(['body'], prev_handle.modules):
    # 串行:等上一个完成
else:
    # 并行
```

- 语义:`conflict(a,b)=False` 意味着(无竞态时)a,b 能并行 acquire 不撞;`=True` 意味着并行必有一个被拒,应串行化.
- **空集无冲突**:不占模块的 routine 跟谁都无冲突.
- **未知模块当无冲突**(保守放行,caller 负责保证模块名合法).
- **树未缓存抛 `RuntimeError`**(不静默返 False----那会让冲突对误并行).正常 catalog 窗口已推完,远早于 run(),不会撞.

| 异常 | 含义 |
|---|---|
| `RuntimeError` | module tree 未缓存(kernel 未推 module.tree) |

---

### 9. 查 module.tree:`get_module_tree()`

主动从 kernel 拉当前 module.tree 并刷新本地缓存,返回 `ModuleTree`.

```python
tree = await self.get_module_tree()
print(tree.root_id)              # 'root'
print(tree.name_of('left_thumb')) # '大拇指'(显示名)
print(tree.conflict(['a'], ['b'])) # True/False
```

- **两种 transport 都问 kernel**(唯一真理源):
  - dial-in(GrpcClientTransport):经 Req unary(`self.req({'event':'get_module_tree'})`).
  - dial-out(GrpcServerTransport):经 Stream 请求-回执(`routine.get_module_tree` → `routine.get_module_tree_reply`).
- **刷新缓存**:拉到后 `runtime.module_tree` 更新,`conflict()` 立即可用.
- 平时靠 kernel 推送缓存(`module.tree` 事件 / dial-out 同步 Req);本方法用于推送未到或想主动刷新的场景.
- 失败/超时/无 peer 返当前缓存(可能 `None`),不阻塞.

---

## wire 事件一览

每个操作底层都有对应的 wire 事件(`routine.acquire`/`routine.release`/`routine.load_module` 等),框架自动处理请求-回执配对,业务侧无需关心。

---

## 异常类型对照

| 异常 | 触发场景 | 可恢复? |
|---|---|---|
| `AcquireError` | `acquire()` / `force_acquire()` 撞冲突 | 是,等释放后重试 |
| `ReleaseError` | `release()` / `force_release()` 失败 | force_release 罕见(rid 未 started) |
| `LoadModuleError` | `load_module()` child 已存在/parent 不存在 | 改 id/parent 后重试 |
| `UnloadModuleError` | `unload_module()` 有子/被占/不存在 | 先卸子或先 release |
| `RuntimeError` | 未 started 调运行时 API / tree 未缓存 | 修调用时序 |
| `StartError` | `handle.start()` 撞冲突(走 rejected) | 是,保留可重试 |

```python
from routine.errors import AcquireError, ReleaseError, LoadModuleError, UnloadModuleError

try:
    await self.acquire(['busy_module'])
except AcquireError as e:
    # 被占,等会儿重试或降级
    ...
```

---

## 典型场景

### 场景 1:只读 vs 占用模块的互斥(示意例)

```python
class ListMusic(Routine):
    """列本地音乐清单,纯读盘不占模块,跟谁都并行."""
    async def on_created(self, rid, kwargs):
        return None

class PlayMusic(Routine):
    """播音乐占 audio 模块,多个 PlayMusic 串行(不会同时放两首)."""
    async def on_created(self, rid, kwargs):
        return Modules([MODULE_AUDIO])
```

编排器(Shell)用 `ctx.conflict(handle_a.modules, handle_b.modules)` 判串/并行:
- 两个 `PlayMusic` → 都占 `audio` → cone 相交 → 串行
- `PlayMusic` + `ListMusic` → `ListMusic` 不占模块 → 无冲突 → 并行
- `PlayMusic` + `Dance`(占 `body`)→ `audio` 跟 `body` 不相交 → 并行(边放边跳)

### 场景 2:动态加载子模块 + 占用 + 卸载

```python
async def run(self, kwargs):
    # 1. 动态挂一个任务模块(只挂树)
    await self.load_module('tasks', 'task_42', name='用户任务#42')

    try:
        # 2. 占用它(独占)
        await self.acquire(['task_42'])
        # ... 干活 ...
    finally:
        # 3. 释放占用
        await self.release(['task_42'])

    # 4. 卸载模块(必须先 release + 无子)
    await self.unload_module('task_42')
```

### 场景 3:编排器串/并行判定

```python
# 父 routine 编排多个子工具
shell = Shell(self)
for fc in tool_calls:
    await shell.push(fc.name, tool_args)  # push 不立即跑
shell.complete()
results = await shell.join()
# Shell 内部按 handle.modules(on_created 返回)+ ctx.conflict 自动判串/并行
```

### 场景 4:主动刷新 module.tree

```python
# 推送未到或想确保最新拓扑时
tree = await self.get_module_tree()
if tree and tree.conflict(['a'], ['b']):
    ...
```

---

## 设计原则

1. **id 唯一作 key,name 可重复作显示**:互斥用 id(保证正确),渲染用 name(友好).
2. **load 只挂树,占用另调 acquire**:职责分离.load 改拓扑,acquire 改占用.
3. **unload 对标文件系统**:非空(有子)拒绝,被占拒绝.必须自底向上卸,先 release.
4. **conflict 是静态预测**:零 round-trip 本地算,用于编排策略.真正的互斥由 kernel 保证.
5. **静态声明 vs 运行时**:`on_created` 返回 Modules 是 created 时自动占;`acquire`/`release` 是 run() 体里动态调.
