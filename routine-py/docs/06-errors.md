# 06 · 异常类型对照

routine SDK 的异常分两类:

- **typed exception**(自定义类):业务高频分支,值得 `except XxxError` 类型化捕获。
- **`RuntimeError`**(内建):前置条件违反 / 调用时序错——调用方 bug,改代码而不是 try。

文件:[errors.py](../routine/errors.py)

## 异常总览

| 异常 | 基类 | 触发场景 | 可恢复? |
|---|---|---|---|
| `SubmitError` | `Exception` | `ctx.submit()` 被 kernel 拒(name 找不到 / kwargs 不合法) | 改入参后重新 submit |
| `StartError` | `Exception` | `handle.start/try_start/force_start` 撞冲突 / 父未 started | `try_start` 返回值可重试;`start/force_start` 失败后 handle 失效 |
| `AcquireError` | `Exception` | `ctx.acquire/force_acquire` 失败(模块被占住 / 驱逐后竞态) | 是——重试或 `force_acquire` |
| `ReleaseError` | `Exception` | `ctx.release/force_release` 失败(罕见,rid 未 started) | 检查 lifecycle 状态 |
| `LoadModuleError` | `Exception` | `ctx.load_module()` 失败(child_id 已存在 / parent_id 不存在) | 改入参后重试 |
| `UnloadModuleError` | `Exception` | `ctx.unload_module()` 失败(有子模块 / 被占用 / 不存在) | 先 unload 子 / release 模块 |
| `ReqTimeout` | `TimeoutError` | `ctx.req()` 等回执超时 | 是——重试(idempotency 调用方自负) |
| `ReqError` | `Exception` | `ctx.req()` 的对端 handler 抛异常(或回执 `__ok__=false`) | 取决于对端,改对端代码 |
| `StreamError` | `Exception` | `ctx.stream_req()` 对端开流/产数据时出错 | 取决于对端 |
| `StreamCancelled` | `Exception` | `ctx.stream_req()` 被取消(消费方主动 cancel 或对端取消) | 是——重新开流 |
| `StreamTimeout` | `TimeoutError` | `ctx.stream_req()` 开流握手超时(首帧未到) | 是——重新开流 |
| `RuntimeError` | – | 前置条件违反(见下表) | 改调用时序 |

## 公开导出

`routine.__init__` 公开导出的异常:

```python
from routine import (
    SubmitError, StartError,
    AcquireError, ReleaseError,
    ReqError, ReqTimeout,
    StreamError, StreamCancelled, StreamTimeout,
)
```

`LoadModuleError` / `UnloadModuleError` **未在 package 顶层导出**,需从 `routine.errors` 导入:

```python
from routine.errors import LoadModuleError, UnloadModuleError
```

## typed exception 详解

### `SubmitError`

`ctx.submit()` 失败——kernel 拒了 submit(`routine.submitted` 带 `error`)。

- **常见原因**:`name` 找不到 / `kwargs` 不合法。
- **跟 `StartError` 区别**:submit 失败在 created 阶段(建子命令前),start 失败在 start 阶段(占模块时)。
- **可恢复**:改入参后重新 submit。

```python
try:
    handle = await self.submit('nonexistent', {})
except SubmitError as exc:
    self._logger.warning('submit failed: %s', exc)
```

### `StartError`

`handle.start()` / `handle.try_start()` / `handle.force_start()` 失败——kernel 拒了 start(`routine.rejected op=start`)。

**关键区别:`try_start` vs `start/force_start`**:

| 方法 | 失败行为 | handle 状态 | 可重试? |
|---|---|---|---|
| `try_start()` | **返回** `Optional[StartError]`(None=成功) | instance/node **保留** | 是 |
| `start()` | **抛** `StartError` | instance/node **被 kernel 清掉** | 否 |
| `force_start()` | **抛** `StartError` | instance/node **被 kernel 清掉** | 否 |

模块冲突是正常业务情况(占住者还没释放),不该打断调用方 `run()` 体——所以 `try_start` 把它做成返回值,而不是异常:

```python
handle = await self.submit('worker', {'task_id': 42})
err = await handle.try_start()
if err:
    # 模块冲突,稍后重试(handle 仍可用)
    await asyncio.sleep(1)
    await handle.try_start()
else:
    result = await handle
```

而 `start` / `force_start` 是"all or nothing"——失败后 kernel 已清 node+订阅,handle 失效不可重试。

### `AcquireError`

`ctx.acquire()` / `ctx.force_acquire()` 失败——`routine.acquired ok=false`。

- **`acquire`**:模块被第三方占住。
- **`force_acquire`**:驱逐后仍冲突(竞态,被别人抢了)。**单轮驱逐不重试**——竞态失败抛回给调用方决定。
- **可恢复**:重试 `acquire`,或升级 `force_acquire`,或换模块。

```python
try:
    await self.acquire('output')
except AcquireError:
    # 模块被占,等一会儿重试
    await asyncio.sleep(0.5)
    await self.acquire('output')
```

冲突是业务高频分支(占住者未释放属预期),值得类型化捕获——`except AcquireError` 而非 string match `RuntimeError`。

### `ReleaseError`

`ctx.release()` / `ctx.force_release()` 失败——`routine.released ok=false`。

- 罕见——release 一般不冲突(自己占的自己释放);`force_release` 只驱逐不占,基本总成功。
- **常见原因**:rid 未 started。
- 跟 `AcquireError` 对称类型化。

### `LoadModuleError`

`ctx.load_module()` 失败——`routine.module_loaded ok=false`。

- **常见原因**:`child_id` 已存在 / `parent_id` 不存在。
- load 只挂树不占用,失败是**状态错而非冲突**。
- **可恢复**:改入参后重试。

### `UnloadModuleError`

`ctx.unload_module()` 失败——`routine.module_unloaded ok=false`。

- **常见原因**:module 有子模块未 unload / 被占用未 release / 不存在。
- **可恢复**:先 unload 子模块 / release 占用 / 检查 id。

### `ReqTimeout`

`ctx.req()` 等待回执超时——`TimeoutError` 子类。

```python
try:
    result = await self.req(rid, 'send_message', {'text': 'hi'}, timeout=30)
except ReqTimeout:
    # 对端没在 30s 内回执
    ...
```

- **可恢复**:重试。**idempotency 调用方自负**——req 不去重,重试可能对端收到两次。
- 注意是 `TimeoutError` 子类,`except TimeoutError` 能捕到;`except Exception` 也能。

### `ReqError`

`ctx.req()` 的对端 handler 抛异常(或回执 `__ok__=false`)。

- 对端 handler 抛异常时,runtime 的 `_serve_request` 捕获后回 `__ok__: False, __error__: str(exc)`,本侧 future 解析成 `ReqError`。
- **不抛到调用方 runtime**:对端 routine 自己的 `run()` 体不受影响,只 `@request` handler 抛了。
- **可恢复**:取决于对端——修对端 handler 代码。

### `StreamError` / `StreamCancelled` / `StreamTimeout`

`ctx.stream_req()` 系列(流式 req)的异常。

| 异常 | 触发 | 可恢复? |
|---|---|---|
| `StreamTimeout` | 开流握手超时(首帧未到) | 是——重新 `stream_req` |
| `StreamError` | 对端开流/产数据时抛异常 | 取决于对端 |
| `StreamCancelled` | 消费方主动 cancel 或对端取消 | 是——重新开流 |

`StreamCancelled` 单独成类是为了让消费方能区分**用户主动取消** vs **对端失败**:

```python
try:
    async for chunk in await self.stream_req(...):
        process(chunk)
except StreamCancelled:
    pass  # 正常取消,不算错
except StreamError as exc:
    self._logger.error('stream failed: %s', exc)
```

## `RuntimeError`(前置条件违反)

调用时序错——调用方 bug,**改代码而不是 try**。

| 触发 | 位置 | 修法 |
|---|---|---|
| `no active context` | `Routine.ctx` 在 `bind_ctx` 前访问 | 在 `on_created` 后访问 ctx |
| `must start() before X` | `ctx.call/acquire/release/force_*/load_module/unload_module` 在父 started 前调 | 先 `ack_start` / 等 `run()` 开始 |
| `module tree not cached yet` | `ctx.conflict` 在 kernel 推 module.tree 前调 | 先 `await ctx.get_module_tree()` |
| `no ctx bound, cannot X` | `handle.start/stop/unsubmit` 在未绑 ctx 的 handle 上调 | handle 由 `ctx.submit` 创建,正常不会未绑 |
| `another start/stop in flight` | `handle._send_and_wait` 重入 | 等上一个 start/stop 完成再调 |
| `request_stop requires peer_id` | `RoutineHub.request_stop` 没传 peer_id | 传 peer_id |
| `Cannot bind to address` | gRPC 端口被占 | 改地址 / 释放端口 |
| `routine stopped with reason=ERROR` | `handle.wait()` 等到子异常停止 | 看子 routine error——子 `run()` 抛了 |

### `handle.wait()` 的 `RuntimeError`

`handle.wait()` 在子 routine 异常停止时 raise `RuntimeError(self.error)`——`error` 是子 `run()` 抛出的异常字符串:

```python
handle = await self.submit('worker', {...})
await handle.start()
try:
    result = await handle
except RuntimeError as exc:
    # 子 routine run() 抛了异常
    self._logger.error('worker failed: %s', exc)
```

子 routine `run()` 的原始异常**不会**跨 wire 透传类型——只透传 `str(exc)`。`handle.reason` 字段保留 `lifecycle.stopped` 的 reason 诊断值(`AUTO/STOP/ERROR/CANCEL/FORCE/DISCONNECT`)。

## `lifecycle.stopped` reason 值

`reason` 是 wire 上的诊断字段,**kernel 不按值分流**(dumb-forward),只给父侧 `handle.reason` 和 routine 的 `on_stopped` 钩子用。

| reason | 含义 | 触发 |
|---|---|---|
| `AUTO` | routine 自然结束(`run` return) | runner 正常退出,无 stop 请求 |
| `STOP` | 被 `lifecycle.stop` 正规打断 | 父侧 `handle.stop()` / kernel stop |
| `ERROR` | `run()` 抛异常 | runner 未捕获异常 |
| `CANCEL` | `CancelledError`(force_stop 接管前的取消路径) | task 取消,后续 force_stop 接管 |
| `FORCE` | 被 `force_start/force_acquire/force_release` 驱逐 | 抢占式资源回收 |
| `DISCONNECT` | peer 断连,`force_stop_peer` 清理 | 网络断 / 进程退出 |
| `UNKNOWN` | 未识别的 reason 字符串 | 兜底(不应出现) |

业务层用小写字符串(`'auto'/'stop'/'error'/'cancel'/'force'/'disconnect'`),wire 上传大写 enum(`ControlDoneReason`),由 `lifecycle._REASON_TO_ENUM` 映射。`on_stopped` 钩子收小写,`handle.reason` 收大写。

扩展 reason **不需要改 kernel**——加新值即可,Kernel 透传不解析。

## 错误恢复模式

SDK 在内部用了一些容错模式,业务侧也可借鉴:

### typed exception 优先于 string match

```python
# 好
try:
    await self.acquire('output')
except AcquireError:
    ...

# 不好——string match RuntimeError
try:
    await self.acquire('output')
except RuntimeError as exc:
    if 'conflict' in str(exc):
        ...
```

### `try_start` 处理可重试冲突

模块冲突是预期分支,用 `try_start` 把它做成返回值,不打断 `run()` 体:

```python
for attempt in range(10):
    err = await handle.try_start()
    if err is None:
        return await handle
    await asyncio.sleep(0.5 * attempt)
raise RuntimeError('worker never starts')
```

### 钩子异常不阻断 lifecycle

`on_created` / `auto_subscribe` 等钩子抛异常**不阻断 lifecycle**——按空 modules 兜底,让后续阶段(start)的 `TryAcquire` 兜底。业务侧钩子代码不用担心"抛了会不会卡死整个 routine"。

## 完整异常对照表

| 调用 | 抛什么 | 何时 |
|---|---|---|
| `ctx.submit` | `SubmitError` | kernel 拒 submit |
| `ctx.submit` | `RuntimeError` | 调用 bug(不应发生) |
| `handle.try_start` | (返回值) | `Optional[StartError]`,None=成功 |
| `handle.start` | `StartError` | kernel 拒 start,handle 失效 |
| `handle.force_start` | `StartError` | 驱逐后仍冲突,handle 失效 |
| `handle.stop` | `RuntimeError` | 另一个 stop/start in flight |
| `handle.unsubmit` | `RuntimeError` | 已 started 的 routine 调 unsubmit |
| `handle.wait` / `await handle` | `RuntimeError(self.error)` | 子异常停止 |
| `ctx.call` | `StartError` / `RuntimeError` | start 失败 / 子异常 |
| `ctx.force_call` | `StartError` / `RuntimeError` | force_start 失败 / 子异常 |
| `ctx.acquire` | `AcquireError` | 模块被占 |
| `ctx.force_acquire` | `AcquireError` | 驱逐后竞态失败 |
| `ctx.release` | `ReleaseError` | rid 未 started(罕见) |
| `ctx.force_release` | `ReleaseError` | rid 未 started(罕见) |
| `ctx.load_module` | `LoadModuleError` | child_id 已存在 / parent 不存在 |
| `ctx.unload_module` | `UnloadModuleError` | 有子 / 被占 / 不存在 |
| `ctx.req` | `ReqTimeout` | 回执超时 |
| `ctx.req` | `ReqError` | 对端 handler 抛异常 |
| `ctx.stream_req` | `StreamTimeout` | 开流握手超时 |
| `ctx.stream_req` | `StreamError` | 对端产数据出错 |
| `ctx.stream_req` | `StreamCancelled` | 主动 cancel |
| `ctx.conflict` | `RuntimeError` | module tree 未缓存 |
| `Routine.ctx` | `RuntimeError` | bind_ctx 前访问 |
