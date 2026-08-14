# 04 · 全流程案例:实现并调用一个 routine

本章把"怎么写 / 怎么注册 / 怎么调用"串起来,适合第一次接手 zero 项目的开发者。**简单 routine 看这一篇就够了**,觉得不够时再翻框架级章节(各节末尾有链接)。

## 步骤 1 — 写 routine

文件 `zero/routines/user/timestamp.py`(单文件单 routine,payload 用 pydantic 声明):

```python
from typing import Any, Dict
from pydantic import BaseModel, Field
from routine import Routine

import time


class TimestampInput(BaseModel):
    fmt: str = Field(default='%Y-%m-%d %H:%M:%S', description='strftime 格式串')


class Timestamp(Routine):
    """返回当前时间."""

    meta = {
        'description': '返回格式化的当前时间',
        'input_schema': TimestampInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = TimestampInput.model_validate(kwargs)
        return {'text': time.strftime(inp.fmt)}
```

要点:
- **`run` 是 abstractmethod**,必须 override;`stop` / `on_created` 等可选。
- **`meta['input_schema']` 用 pydantic `model_json_schema()` 生成**,给前端 / LLM 工具调用用(约定见 [02-routine-conventions.md](./02-routine-conventions.md#输入-schemapydantic-first))。
- 不占模块的 routine 无需 override `on_created`。
- 详见 [02-routine.md](../../../routine-py/docs/02-routine.md)。

## 步骤 2 — 注册(改 routines.yaml)

在 [`zero/routines.yaml`](../../routines.yaml) 加一行条目:

```yaml
routines:
  # ... 已有条目
  - routines/user/timestamp.py
```

两种生效路径:

| 路径 | 前提 | 何时生效 |
|---|---|---|
| **热注册**(推荐) | 进程在跑,`RoutinesWatcher` 监控 routines.yaml | 保存后秒级自动注册,无需重启 |
| **冷注册** | 下次进程启动 | `RoutinesLoader` 被动拉起时读 yaml 注册 |

- yaml 是启用 routine 的**单一真理源**:加行 = 启用,注释行 = 禁用。
- name / schema / doc 以 Routine 类声明为唯一事实源,yaml 不重复声明。
- 已注册 routine 的**源码变更**(改 `.py`)同样被 watcher 捕获,`importlib.reload` + `hub.reload_routine` 替换路由,无需重启。
- 注册机制细节见 [03-registration.md](../../../routine-py/docs/03-registration.md)。

## 步骤 3 — 调用

HTTP 一键触发(地址读 routines.yaml 条目 kwargs,缺省 7780;当前 yaml 配 7781):

```bash
curl -XPOST localhost:7781/run/timestamp -H 'Content-Type: application/json' -d '{}'
# {"text": "2026-08-14 12:00:00"}
```

routine 在 `run()` 里用 `self.call` / `self.submit` 调用其它已注册 routine(跨进程也行,框架自动路由):

```python
class Orchestrator(Routine):
    async def run(self, kwargs):
        # 同步拿结果:submit + start + wait 一步到位
        result = await self.call('timestamp')

        # 或:submit + start + 流式 wait(子 routine 是 async generator 时可 yield 多个结果)
        handle = await self.submit('timestamp')
        await handle.start()
        async for chunk in handle:
            ...   # 收子 routine yield 的流式结果
        await handle.wait()
```

agent 侧由 `run_routine` 工具走同一条路由(见 [03-modules-overview.md](./03-modules-overview.md#想触发某个-routine))。

详见 [04-context.md](../../../routine-py/docs/04-context.md) 和 [05-handle.md](../../../routine-py/docs/05-handle.md)。

## 速查:注册路径差异

| 路径 | 改动 | 何时生效 |
|---|---|---|
| 新增 routine | 写 `.py` + yaml 加条目 | watcher 秒级自动注册 |
| 修改 routine | 改 `.py` 源码 | watcher reload 替换路由 |
| 停用 routine | yaml 注释条目 | watcher 自动注销 |
| 全部生效的兜底 | 重启进程 | `RoutinesLoader` 冷注册 |

## 下一步

- 写完 routine 想深入理解生命周期 / 类字段:[02-routine.md](../../../routine-py/docs/02-routine.md)
- 注册机制细节:[03-registration.md](../../../routine-py/docs/03-registration.md)
- `run()` 体里能调什么 API:[04-context.md](../../../routine-py/docs/04-context.md)
- 编排子 routine(handle / 流式结果):[05-handle.md](../../../routine-py/docs/05-handle.md)
- 异常处理 / 排错:[06-errors.md](../../../routine-py/docs/06-errors.md)
