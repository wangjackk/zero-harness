# 01 · 架构概览

routine SDK 的核心是 **三方协作**:

```
┌─────────────────────┐     gRPC Stream      ┌─────────────────────┐
│   routine hub       │  ←────────────────→  │      kernel         │
│  (Python, 本 SDK)   │   lifecycle + p2p    │  (Go, 调度权威)      │
│                     │   + catalog + pubsub │                     │
└─────────────────────┘                       └─────────────────────┘
        ↑                                              ↑
   Routine 子类                                   module.tree
   (业务逻辑)                                     (互斥拓扑)
```

## 三方职责

| 角色 | 位置 | 职责 |
|---|---|---|
| **routine hub** | `routine/routine/`(本 SDK) | 实例化 Routine 子类 + 跑 `run()` 体 + 收发 wire 事件 |
| **kernel** | `kernel/`(Go) | **唯一调度权威**:命令树 / 模块互斥 / 树形中断 / catalog 路由 / pubsub fanout |
| **Routine 子类** | 业务侧(应用项目) | override `run` / `stop` / `on_created`,声明 `name` / `meta` / `is_passive` |

**关键不变量**:routine hub 只跑业务逻辑,**所有调度决策都经 kernel**。子 routine 的 created/start/stop 都通过 wire 事件请求 kernel,kernel 回执确认后才推进。

## 三种通信路径

| 路径 | 机制 | kernel 角色 |
|---|---|---|
| **lifecycle** | `lifecycle.created/started/stopped` | 驱动 routine 生命周期(create→start→stop) |
| **p2p** | `message.send/req/stream_open/stream_data/stream_cancel` | **dumb forward**(按 target_id 转发,不解析 envelope) |
| **pubsub** | `pubsub.subscribe/unsubscribe/publish/delivered` | 维护订阅表 + fanout |

## 两种启动模式

routine hub 与 kernel 的 gRPC 连接有两个方向,业务层(`RoutineHub`)两模式共用,只换 transport:

### server 模式(dial-out,kernel 当 client)

```
hub (gRPC server, 监听 0.0.0.0:7777)  ←─── kernel (as_grpc_client 连过来)
```

- hub 进程绑端口监听,kernel 主动连。
- 走 `GrpcServerTransport`,`Stream` 是双向流(kernel 发 lifecycle 请求,server 回 lifecycle 回执)。

### client 模式(dial-in,kernel 当 server)

```
hub (gRPC client, 主动拨)             ───→  kernel (as_grpc_server, 监听 127.0.0.1:8889)
```

- kernel 绑端口监听,hub 主动拨。
- 走 `GrpcClientTransport`,同一 `Stream` 双向用。
- kernel config 需配 `as_grpc_server.enable: true`。

> 两种模式业务代码完全一致,只有 transport 实现不同。所有 `RunContext` API 自动适配。具体启动命令由应用项目决定(参见项目级文档)。

## 组件清单

| 组件 | 文件 | 作用 |
|---|---|---|
| `Routine` | [routine.py](../routine/routine.py) | 基类,子类 override `run`/`stop`/`on_created` |
| `Routines` | 同上 | 注册表,`register/deregister` routine 类 |
| `RoutineHub` | [server.py](../routine/server.py) | 对外 server,封装 lifecycle + 出站 wire + inbound 分发 |
| `RunContext` | [ctx.py](../routine/ctx.py) | 每次 `run()` 的上下文,暴露所有主动 API |
| `RoutineHandle` | [handle.py](../routine/handle.py) | 父侧持有的子 routine 句柄,控制 start/stop/wait |
| `LifecycleManager` | [lifecycle.py](../routine/lifecycle.py) | create/start/stop 三入口的实现 |
| `ServerRuntime` | [runtime.py](../routine/runtime.py) | server 运行时状态(instance 表 / future 表 / module_tree 缓存) |
| `Transport` | [transport.py](../routine/transport.py) | 传输抽象,两个实现:`GrpcServerTransport` / `GrpcClientTransport` |
| `ModuleTree` | [module_tree.py](../routine/module_tree.py) | 模块拓扑缓存 + `conflict` 本地计算 |
| `RoutineSource` | [routine.py](../routine/routine.py) | `@subscribe` / `on_message` 收消息时的发送方引用 |

## wire 事件

所有 wire 事件常量在 `routine/routine/protocol.py`。分五组:lifecycle / routine.\* / message.\* / pubsub.\* / catalog.\*。框架自动处理,业务侧通常不需要直接接触 wire 事件。

## 下一步

- 写一个 routine:[02-routine.md](./02-routine.md)
- 注册/发现 routine:[03-registration.md](./03-registration.md)
- routine 内能调什么:[04-context.md](./04-context.md)
- 编排子 routine:[05-handle.md](./05-handle.md)
