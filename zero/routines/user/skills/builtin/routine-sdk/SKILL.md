---
name: routine-sdk
description: routine SDK 使用指南。当用户要写 routine、改 routine、编排子 routine、查询 ctx/handle API、处理 routine 注册或异常时,使用本 skill。涵盖 Routine 基类、RunContext、RoutineHandle、注册机制、异常类型、模块操作。
---

# Routine SDK 使用指南

routine SDK 的 API 导航入口。具体章节放在 `routine-py/docs/` 下,**按需查阅**——不要一次全读,根据当前问题定位到对应章节再翻。

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

## 范围

本 skill 只讲 **routine SDK 使用方法**(通用,与业务无关)。任何用 `routine` 包的应用都适用。
