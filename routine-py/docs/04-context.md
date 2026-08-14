# 04 · RunContext API

`RunContext` 是每次 `run()` invocation 的运行上下文。`Routine.__init__` 时未绑,`created` 时由 `LifecycleManager` 绑定到 `self._active_ctx`。`run()` 体里通过 `self.ctx` 或委托方法访问。

文件:[ctx.py](../routine/ctx.py)

## 身份字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `ctx.id` | `str` | kernel 分配的 command id(同 `self.id`) |
| `ctx.name` | `str` | routine name(同 `self.name`) |
| `ctx.peer_id` | `str` | peer 标识(`<conn_id>:<rid>`),wire 事件路由用 |

## 1. Lifecycle ack

`run()` 开始前框架已经发了 `lifecycle.started`,所以**绝大多数 routine 不需要手动调 lifecycle API**。下面两个方法仅在特殊场景(自定义启动协议)用。

### `await ctx.ack_start()`

发 `lifecycle.started` 通知调度器进入 Started 态。**框架默认在 `run()` 调用前已发**,业务侧通常不调。

### `await ctx.request_stop()`

请求 runtime 发起一次正规 stop 流程(类似收到外部 `lifecycle.stop`)。**罕见用法**,正常停止由父侧 `handle.stop()` 触发。

## 2. 子 routine 编排

详见 [05-handle.md](./05-handle.md)。这里只列签名。

### `await ctx.submit(name, kwargs=None) -> RoutineHandle`

提交子 routine,经 kernel 回环:建命令(created),created 时返回 `RoutineHandle`。**不要求父 started**(created 即可)。

```python
handle = await self.ctx.submit('echo', {'text': 'hi'})
# handle.id = kernel 分配的 child_id
# handle.modules = 子 routine on_created() 返回的占用 modules
```

### `await ctx.call(name, kwargs=None) -> Any`

同步拿子 routine 结果:submit → start → wait 一步到位。**要求父 started**。

```python
result = await self.ctx.call('echo', {'text': 'hi'})
```

失败语义:
- start 失败(模块冲突/父未 started):抛 `StartError`
- 子 routine 异常停止:抛 `RuntimeError(error)`
- 子正常 return:返回 result

### `await ctx.force_call(name, kwargs=None) -> Any`

抢占式 call:start 换成 `force_start`——子要占的模块被第三方占时,先打断第三方再 start。

## 3. 模块操作

完整参考:[module-operations.md](./module-operations.md)。这里只列 API 摘要。

| 方法 | 说明 | 异常 |
|---|---|---|
| `await ctx.acquire(modules)` | 运行时占领模块(冲突抛 `AcquireError`) | `RuntimeError`(未 started) / `AcquireError` |
| `await ctx.release(modules)` | 释放指定模块(不全量) | `RuntimeError` / `ReleaseError` |
| `await ctx.force_acquire(modules)` | 驱逐 cone 内第三方后自己占住 | `RuntimeError` / `AcquireError` |
| `await ctx.force_release(modules)` | 驱逐 cone 内第三方后空出(不自己占) | `RuntimeError` / `ReleaseError` |
| `await ctx.load_module(parent, child, name='')` | 加载子模块(只挂树不占用) | `RuntimeError` / `LoadModuleError` |
| `await ctx.unload_module(child)` | 卸载子模块(有子/被占拒绝) | `RuntimeError` / `UnloadModuleError` |
| `ctx.conflict(mods_a, mods_b) -> bool` | **纯本地**算两组 modules 是否冲突(cone 交集非空) | `RuntimeError`(tree 未缓存) |
| `await ctx.get_module_tree() -> Optional[ModuleTree]` | 主动拉 `module.tree` 刷新缓存 | — |

**所有运行时操作都要求父 routine 已 started**(未 started 抛 `RuntimeError`)。`submit/unsubmit` 是例外(created 即可)。

### `conflict` 本地预测

零 round-trip 本地算,读缓存的 module.tree。编排器用 `ctx.conflict(h1.modules, h2.modules)` 判串/并行。完整编排例子见 [05-handle.md](./05-handle.md)。

## 4. p2p 通信

kernel **dumb forward**(按 target_id 转发,不解析 envelope)。三种语义:请求-回执 / 流 / 单向消息。

### `await ctx.req(target, event, data=None, timeout=30.0) -> Any`

对 target routine 发 request,等回执拿 result。target 是对端 routine 的 id。对端用 `@request(event)` 注册的 handler 处理。

```python
# 对端
class Database(Routine):
    @request('get')
    async def handle_get(self, key: str):
        return self._store.get(key)

# 本端
val = await self.ctx.req(db_rid, 'get', data='user:42')
```

异常:
- handler 抛异常 → `ReqError`
- 超时 → `ReqTimeout`

### `await ctx.stream_req(target, event, data=None, timeout=30.0) -> StreamCtx`

对 target 发 stream request,返回 `StreamCtx`(async with → async for)。对端用 `@stream(event)` 注册的 async-generator handler 产数据。

```python
async with await self.ctx.stream_req(counter_rid, 'count', data=5) as s:
    async for chunk in s:
        print(chunk)   # 0, 1, 2, 3, 4
```

### `await ctx.send(target, data=None)`

给 target routine 发定向消息(`message.send`)。派发是 spawn 并发——对端 `on_message` 可能并发 fire + 乱序到达,**业务侧自带 id reorder**。created 后即可收(不必 start)。

```python
# 对端
class Receiver(Routine):
    async def on_message(self, source: RoutineSource, data):
        self._logger.info('got msg from %s: %s', source.id, data)

# 本端
await self.ctx.send(receiver_rid, {'type': 'ping', 'id': 1})
```

## 5. pubsub

kernel 维护订阅表 + fanout。两种用法:类装饰器 `@subscribe`(自动订阅)+ 运行时 `ctx.subscribe`(动态订阅)。

### `await ctx.publish(topic, data=None, *, namespace='')`

发一条 pubsub 消息到 `(namespace, topic)`。kernel fanout 给所有订阅者。

```python
await self.ctx.publish('agent.event', {'type': 'start', 'id': self.id})
```

### `await ctx.subscribe(topic, handler, *, namespace='')`

动态订阅:注册 handler 到本地表 + 发 `pubsub.subscribe` 给 kernel。

```python
async def on_event(source: RoutineSource, data):
    print(f'from {source.id}: {data}')

await self.ctx.subscribe('agent.event', on_event)
```

### `await ctx.unsubscribe(topic, *, namespace='')`

退订 `(namespace, topic)`。

### `ctx.namespace(ns) -> Namespace`

拿到 namespaced 助手,简化调用:

```python
ns = self.ctx.namespace('agent.x')
await ns.publish('event', data)        # 等价 publish('event', data, namespace='agent.x')
await ns.subscribe('event', handler)   # 等价 subscribe('event', handler, namespace='agent.x')
await ns.unsubscribe('event')          # 等价 unsubscribe('event', namespace='agent.x')
```

## 6. 查询

### `await ctx.get_running_routines() -> list`

查 kernel 当前所有 running routine 实例 `[{name, id}]`(跨进程正确,经 kernel 唯一全局视图)。

```python
routines = await self.ctx.get_running_routines()
bridge = next((r for r in routines if r['name'] == 'agent_ws_bridge'), None)
if bridge:
    await self.ctx.req(bridge['id'], 'register_agent', data={...})
```

## 7. Task 池

### `ctx.spawn(coro) -> asyncio.Task`

起一个后台 task。委托 runtime task 池,在 `@stream` provider gen / 任意后台协程场景用。

```python
async def bg_poll(self):
    while not self._stop_event.is_set():
        await asyncio.sleep(1)
        # ...

async def run(self, kwargs):
    self.ctx.spawn(self.bg_poll())   # 后台跑,不阻塞 run
    await self._stop_event.wait()
```

## 时序约束速查

| 操作 | created 后 | started 后 |
|---|:---:|:---:|
| `submit` / `unsubmit` | ✅ | ✅ |
| `send`(单向消息) | ✅(收)/ ✅(发,需有 target id) | ✅ |
| `req` / `stream_req` | ✅(target 必须 started 才能回执) | ✅ |
| `publish` | ✅ | ✅ |
| `subscribe` / `unsubscribe`(动态) | ✅ | ✅ |
| `@subscribe` 装饰器(自动订阅) | ❌(start 时才发订阅给 kernel) | ✅ |
| `acquire` / `release` / `force_*` | ❌ | ✅ |
| `load_module` / `unload_module` | ❌ | ✅ |
| `handle.start()` / `handle.stop()` | ❌ | ✅ |
| `call` / `force_call` | ❌ | ✅ |

> `@subscribe` 装饰器的自动订阅在 `_auto_subscribe()` 里发,**`_auto_subscribe` 在 lifecycle.start 时调**(不在 created 时)——所以装饰器订阅要 started 后才能收到消息。

## 下一步

- 子 routine 句柄完整 API:[05-handle.md](./05-handle.md)
- 异常类型:[06-errors.md](./06-errors.md)
