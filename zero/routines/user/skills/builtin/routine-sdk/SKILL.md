---
name: routine-sdk
description: routine SDK 使用指南。当用户要写 routine、改 routine、编排子 routine、选通信方式（req/send/pubsub/call）、查询 ctx/handle API、处理 routine 注册或异常时,使用本 skill。涵盖通信选型、Routine 基类、RunContext、RoutineHandle、注册机制、异常类型、模块操作。
---

# Routine SDK 使用指南

所有能力都是 `self.*`（`self.req` / `self.subscribe` / `self.call` ...），基类已委托好,不需要碰 `self.ctx`。

routine SDK 的 API 导航入口。具体章节放在 `routine-py/docs/` 下,**按需查阅**——不要一次全读,根据当前问题定位到对应章节再翻。

## 通信选型

| 方式 | 调用 | 对端入口 | 寻址 | 语义 |
|---|---|---|---|---|
| **编排** | `await self.call('name', kwargs)` | `run(kwargs)` | **name**(kernel 路由) | 起 routine 跑完拿结果(submit→start→wait 一步到位);另 submit(只建不启)/ force_call(抢占) |
| **请求** | `await self.req(rid, event, data)` | `@request(event)` 方法 | **rid**(p2p) | 等回执;handler 返回值即结果;抛错 ReqError / 超时 ReqTimeout(默认 30s) |
| **开流** | `await self.stream_req(rid, event, data)` | `@stream(event)` async gen | **rid**(p2p) | `async with ... as s: async for` 逐 chunk 收 |
| **定向消息** | `await self.send(rid, data)` | `on_message(source, data)` | **rid**(p2p) | fire-and-forget;对端并发 fire、乱序到达,业务侧自带 id reorder |
| **广播** | `await self.publish(topic, data)` | `@subscribe(topic)` / `self.subscribe()` | (namespace, topic) | kernel fanout 给所有订阅者 |

判断顺序:
- 调一个**已注册 routine 跑一遍** → `call`(唯一按 name 寻址)
- 与**运行中实例**问答/拉流 → `req` / `stream_req`
- **通知**运行中实例(不等回执) → `send`
- **一对多**事件 → `publish` + `@subscribe`

### name vs rid 寻址

- `call('name')` 走 kernel 路由表(注册名)
- `req`/`stream_req`/`send` 的 target 是**运行实例 id**(rid),不是 name
- rid 发现:`await self.get_running_routines()` → `[{name, id}]`;agent 有 helper `get_agent_rid`
- `self.id` 是自己;`self.parent_rid` 是父(None=root),tool routine 常用它反向 req 父

### 时序注意

- `call` 内含 start 子,**父自己 started 后**才可用——run() 在 started 后才被调,所以 run 体里 `self.call()` 永远合法;唯一禁区是 `on_created()`(早于 started),那里只能 `self.submit()` 不能 call/start
- `@subscribe` 装饰器与动态 `self.subscribe()`:**created 后**即可收(created 回报前同步完成订阅,无 publish race)
- `req` 对端必须 started 才能回执

## 路径约定

文档路径是**相对项目根目录**的,例如:

```
routine-py/docs/02-routine.md
routine-py/docs/06-errors.md
```

相对路径会被解析为 `<project_root>/<path>`。

## 章节目录

| # | 文档 | 内容 | 何时看 |
|---|---|---|---|
| 02 | `routine-py/docs/02-routine.md` | `Routine` 基类:`run`/`stop`/`on_created`/`on_started`/`on_stopped` 生命周期 + `name`/`meta`/`is_passive` 类字段 | 写第一个 routine |
| 03 | `routine-py/docs/03-registration.md` | `Routines.register/deregister` + `RoutineHub.register_routine/deregister_routine` 运行时动态注册 | 注册 routine / 运行时动态注册 |
| 04 | `routine-py/docs/04-context.md` | `RunContext` API:lifecycle ack / 模块操作 / p2p 通信 / pubsub / 子 routine 编排 | 查 ctx 能调什么 |
| 05 | `routine-py/docs/05-handle.md` | `RoutineHandle`:submit/start/stop/wait + async generator body 迭代 | 编排子 routine |
| 06 | `routine-py/docs/06-errors.md` | 异常类型对照 + 可恢复性 + `lifecycle.stopped` reason 枚举 + 错误恢复模式 | 处理异常 / 排错 |
| ref | `routine-py/docs/module-operations.md` | 模块操作完整参考(acquire/release/force_*/load_module/unload_module/conflict) | 用模块操作时 |

## 按问题类型快速定位

| 问题 | 看哪个 |
|---|---|
| 怎么写一个 routine? | `routine-py/docs/02` |
| 用哪种通信方式? | 本 skill 通信选型表 |
| `run()` 里能调什么? | `routine-py/docs/04` |
| 怎么编排子 routine? | `routine-py/docs/05` |
| 怎么注册 routine? | `routine-py/docs/03` |
| 异常怎么处理? | `routine-py/docs/06` |
| 怎么占模块 / 处理冲突? | `routine-py/docs/02`(on_created) + `routine-py/docs/04`(conflict) + `routine-py/docs/module-operations.md` |
| routine 怎么命名? | `routine-py/docs/02`(name 字段) |
| passive routine 是什么? | `routine-py/docs/02`(is_passive) |
| `@request`/`@stream`/`@subscribe` 装饰器? | `routine-py/docs/02`(通信装饰器) |
| pubsub 怎么用? | `routine-py/docs/04`(pubsub) |
| p2p 通信(req/send/stream)? | `routine-py/docs/04`(p2p 通信) |
| async generator body 怎么迭代? | `routine-py/docs/05`(async generator body 迭代) |
| `try_start` vs `start` 区别? | `routine-py/docs/05` + `routine-py/docs/06`(StartError) |

## 使用模式

1. **收到开发任务时**:先 `list_skills` 确认本 skill 已加载,或 `load_skill routine-sdk` 加载。
2. **具体问题**:查上面"按问题类型快速定位"表,翻对应章节。
3. **遇到异常**:翻 `routine-py/docs/06-errors.md`,查异常类型和可恢复性。

## 关键不变量(永远记住)

- **万物皆 routine**:工具是 routine,agent 是 routine,编排器是 routine——没有特权公民。
- **module 是互斥单元**:`on_created()` 返回 `Modules([...])` 声明占用,同 module 同时只能一个 started routine 占。
- **状态放实例字段**,禁止模块级变量(热重载会重置);routine 间通信只走 wire 方式,禁止全局变量传状态。

## 范围

本 skill 只讲 **routine SDK 使用方法**(通用,与业务无关)。任何用 `routine` 包的应用都适用。
