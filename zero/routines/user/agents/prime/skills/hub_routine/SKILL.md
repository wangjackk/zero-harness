---
name: hub_routine
description: "IPython 里一键启动 routine hub + passive routine 实例,拿到实例后直接用其 call/subscribe/publish 方法。需要从 kernel 侧主动调 routine、监听事件、或搭建常驻被动 routine 时使用。"
---

# hub_routine: 一键启动 hub

routine_bridge skill 的高级版,在 IPython 里一键启动 routine hub,拿到一个常驻 passive routine 实例,
直接通过该实例 `call` / `subscribe` / `publish`,无需手写 hub/transport 样板。

## 与 routine_bridge skill 的关系

| | `routine_bridge` skill | `hub_routine` skill |
|---|---|---|
| 调用方向 | kernel → 其他 routine (单向调用) | kernel ↔ 其他 routine (双向) |
| 通信方式 | HTTP bridge,一次请求一次响应 | gRPC,常驻连接 |
| 能力 | 只能 `run_routine(name, kwargs)` | call + subscribe + publish |
| 适用场景 | 调用 read/grep/bash 等工具 routine | 监听事件、发布事件、常驻被动 routine |
| 状态 | 无状态,每次调用独立 | 有状态,hub + passive 实例常驻 |

**选择建议**:
- 只需要调一下 routine (read/grep/bash/list_routines 等) → 用 `routine_bridge` skill 的 `run_routine`
- 需要订阅事件 (assistant_output 等)、发布事件、或搭建常驻 routine → 用 `hub_routine`

两者可以共存:`routine_bridge` skill 用于普通调用,`hub_routine` 用于需要双向通信的场景。

## 快速开始

```python
import asyncio
from hub_routine import start_hub, stop_hub

# 一键启动:起 hub + passive routine,拿到实例
r = await start_hub()

# r 是 passive routine 实例,直接用其原生方法:

# 调用其他 routine (经 kernel 回环)
res = await r.call("echo", {"message": "hi"})

# 订阅事件
async def handler(source, data):
    print(f"收到: {data}")

await r.subscribe("assistant_output", handler, namespace="prime_6")

# 发布事件
await r.publish("my_topic", {"key": "val"}, namespace="prime_6")

# 结束
await stop_hub()
```

## 注册自定义 routine

```python
from routine import Routine
from hub_routine import start_hub

class MyRoutine(Routine):
    name = "my_routine"
    async def run(self, kwargs):
        yield "ready"
        # ...常驻逻辑

r = await start_hub(routines=[MyRoutine])
```

## API

| 函数 | 说明 |
|------|------|
| `start_hub(routines=None, hub_id="hub_routine", wait=2.0)` | 启动 hub,返回 passive 实例 |
| `stop_hub()` | 停止 hub |

passive 实例返回后,直接用 `Routine` 原生方法:`call` / `submit` / `subscribe` / `unsubscribe` / `publish`。

## 配置

kernel 地址无需关心:IPython 启动时已自动注入 `ZERO_KERNEL_ADDR`, `start_hub()` 直接用。
