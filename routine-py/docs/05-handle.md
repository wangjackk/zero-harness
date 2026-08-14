# 05 · RoutineHandle

`RoutineHandle` 是父侧持有的"指向一次 submit 的子 routine"的本地句柄。`self.submit()` 返回它,后续通过它控制子 routine 的 start/stop/wait。

文件:[handle.py](../routine/handle.py)

## 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `handle.id` | `str` | kernel 分配的子 command id(string) |
| `handle.name` | `Optional[str]` | 子 routine name |
| `handle.modules` | `Optional[list[str]]` | 子 routine `on_created()` 返回的占用 modules(编排器据此算冲突) |
| `handle.result` | `Any` | 子 routine `run()` 的返回值(stopped 后填) |
| `handle.error` | `Optional[str]` | 子 routine 异常停止时的 error string |
| `handle.reason` | `Optional[str]` | `lifecycle.stopped` 的 wire reason(`AUTO/STOP/ERROR/CANCEL/FORCE/DISCONNECT`) |

## 状态检查

| 方法 | 返回 | 说明 |
|---|---|---|
| `handle.is_started()` | `bool` | 收到 `lifecycle.started` 后 True |
| `handle.is_done()` | `bool` | 收到 `lifecycle.stopped` 后 True |

## 控制

### `await handle.start() -> None`

让 kernel start 子命令(占模块 + 运行)。**全有或全无**——失败时 kernel 清 node+订阅,本侧清 created instance,handle 失效不可重试。

```python
handle = await self.submit('worker', {'task_id': 42})
await handle.start()   # 失败 raise StartError
```

- 失败(模块冲突/父未 started):抛 `StartError`。
- 需 ctx 绑定 + **父 started**。

### `await handle.try_start() -> Optional[StartError]`

让 kernel start 子命令,**失败时保留可重试**(不清 instance/node)。失败返回 `StartError`(None=成功),不抛——模块冲突是正常业务情况(占住者还没释放),不该打断调用方 `run()` 体。

```python
handle = await self.submit('worker', {'task_id': 42})
err = await handle.try_start()
if err:
    # 模块冲突,稍后重试
    await asyncio.sleep(1)
    await handle.try_start()   # 或 start()
```

### `await handle.force_start() -> None`

抢占式 start:让 kernel **驱逐占住子 declared 模块的第三方**(cascade stop,`reason='force'` 透传)后 start。区别于 `start/try_start`(那俩冲突就放弃/等)。

- 失败 raise `StartError`(跟 `start` 一致)。
- 单轮驱逐不重试(竞态失败)。
- 永不驱逐祖先(打断父亲自己也死)。

### `await handle.stop(*, fire=False) -> None`

让 kernel stop 子命令(级联)。

```python
await handle.stop()              # 等 lifecycle.stopped 回执,返回时子确定停完
await handle.stop(fire=True)     # fire-and-forget,只发 wire 不等 ack
```

- `fire=False`(默认):发 `routine.stop` 后等 `lifecycle.stopped` 回执确认子已停——返回时子确定停完。需父 started。
- `fire=True`:fire-and-forget,绕过 ack 等待。供 `Shell.interrupt` top-down 并发打断用——子的 `lifecycle.stopped` 到达后由 `handle.wait` 自然解除。

### `await handle.unsubmit(*, fire=False) -> None`

撤销提交:清 created 态子命令(未 start 的)。跟 `submit` 对称。

```python
handle = await self.submit('worker', {...})
# 还没 start,改变主意了
await handle.unsubmit()   # 清 created 态
```

- 已 start 的 routine 调本方法会被 kernel 拒(`rejected op=unsubmit`)——该用 `stop`。
- **不要求父 started**(submit 也不要求)。

## 等待

### `await handle.wait() -> Any`

等 `lifecycle.stopped`。成功 → 返回 result;失败/异常停掉 → raise `RuntimeError(error)`。

```python
handle = await self.submit('echo', {'text': 'hi'})
await handle.start()
result = await handle.wait()    # {'echo': 'hi'}
```

### `await handle.wait_started() -> None`

等 `lifecycle.started`。失败兜底也会 resolve(不死锁)。

### `await handle`(等价 `await handle.wait()`)

`RoutineHandle.__await__` 实现了 `await handle` 等价于 `await handle.wait()`。

```python
result = await handle   # 等价 await handle.wait()
```

## 一步到位:`self.call` / `self.force_call`

如果不需要保留 handle(不中途 stop / 不迭代 body),用 `self.call` 一步到位:

```python
# 等价于:
# handle = await self.submit('echo', {'text': 'hi'})
# await handle.start()
# return await handle
result = await self.call('echo', {'text': 'hi'})
```

## 流式结果迭代

如果子 routine 的 `run()` 是 **async generator**(yield 而非 return),`handle` 也是 async iterable:

```python
# 子 routine
class Counter(Routine):
    name = 'counter'
    async def run(self, kwargs):
        for i in range(kwargs['n']):
            yield i
            await asyncio.sleep(0.1)

# 父侧
handle = await self.submit('counter', {'n': 5})
await handle.start()
async for item in handle:
    print(item)   # 0, 1, 2, 3, 4
```

- 每次 yield 的值作为流式结果发给父。
- 子异常或 stop 时,async for 自动终结(避免永远挂)。

## 生命周期回调(可选)

handle 上有两个可选 async 回调,父侧可设置:

```python
async def on_started(h: RoutineHandle) -> None:
    print(f'子 {h.id} started')

async def on_stopped(h: RoutineHandle) -> None:
    print(f'子 {h.id} stopped, result={h.result}')

handle = await self.submit('worker', {...})
handle.on_started_handler = on_started
handle.on_stopped_handler = on_stopped
await handle.start()
```

- 触发是 fire-and-forget spawn(不阻塞 reader 协程,回调里可 await send/等子)。
- 幂等:各靠 Event 只调一次。
- 适合 async-generator 流式 yield 场景(子 done 时 put queue,父 run async gen 从 queue 拉 yield 给上层)——这种场景不能 `await handle.wait()` 阻塞整个 run。

## 完整例子:编排串/并行

```python
class Orchestrator(Routine):
    name = 'orchestrator'

    async def run(self, kwargs):
        # 提交两个子任务
        h1 = await self.submit('write', {'path': '/a'})
        h2 = await self.submit('write', {'path': '/b'})

        # 用 conflict 本地预测算串/并行
        if self.ctx.conflict(h1.modules, h2.modules):
            # 串行
            await h1.start()
            await h1   # 等结果
            await h2.start()
            await h2
        else:
            # 并行
            await h1.start()
            await h2.start()
            await h1
            await h2

        return {'done': True}
```

## 异常

handle 操作可能抛 `StartError`（模块冲突，用 `try_start` 可重试）和 `RuntimeError`（调用时序错误）。完整异常对照见 [06-errors.md](./06-errors.md)。

## 下一步

- 异常类型:[06-errors.md](./06-errors.md)
