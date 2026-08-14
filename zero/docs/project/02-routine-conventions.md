# 02 · Routine 编写约定

本文档列出 `zero` 项目内编写 routine 的约定,包括类字段、生命周期钩子、`meta` schema、命名、错误处理、日志等。

> 框架级 API 参考 [02-routine.md](../../../routine-py/docs/02-routine.md),这里只讲项目特有约定。

## 最小 routine 模板

```python
"""Echo -- 回显示例 routine(骨架验证用).

start 收到 text,原样返回.占 output 模块,有显式 stop(set event 让 start 退出).
"""
import asyncio
from asyncio import Event
from typing import Any, Dict, Optional

from routine import Modules, Routine
from zero.modules import MODULE_OUTPUT


class Echo(Routine):
    """回显示例:start 收到 text,原样返回.骨架示例."""

    meta = {'description': '回显示例 routine(骨架)'}

    def __init__(self):
        super().__init__()
        self._stop_event = Event()

    async def on_created(self, rid: Optional[str] = None,
                    kwargs: Optional[Dict] = None) -> Modules:
        return Modules([MODULE_OUTPUT])

    async def run(self, kwargs: Dict[str, Any]):
        self._logger.info(f'echo {self.id} start')
        await self._stop_event.wait()
        self._logger.info('echo st stopped')
        return {'echo': kwargs.get('text', '')}

    async def stop(self) -> None:
        self._logger.info(f'echo {self.id} stopping')
        self._stop_event.set()
```

## 文件结构约定

- **一个 routine 一个文件**(`.py`),文件名 snake_case,跟类名对应(`Echo` → `echo.py`)。
- **文件头 docstring** 说明:routine 作用 + 占什么模块 + 典型用法示例(curl / 代码 / XML)。
- **类 docstring** 简述语义,给 LLM / 开发者看。
- **复杂子系统用子目录**:`__init__.py` 聚合 `Routines` 组,导出 `routines` 或 `get_routines()`。

```python
# 子系统 __init__.py 模板
from routine import Routines
from .play_music import PlayMusic
from .list_music import ListMusic

def get_routines() -> Routines:
    rs = Routines()
    rs.register(PlayMusic, ListMusic)
    return rs
```

## 类字段约定

### `name`

默认由 `__init_subclass__` 从类名 snake_case 转换生成(`Echo` → `'echo'`),**通常不显式写**。

显式写的场景:
- **大写 name**(如 `name = 'WAIT'`):特殊语义 routine,Shell 编排器识别 `wait`/`WAIT` 当 barrier 处理。
- **per-agent 动态注册**(如 `agent_a/list_skills`):运行时生成,带 agent 前缀避免全局重名。

```python
class Wait(Routine):
    name = 'WAIT'   # Shell 识别为 barrier
```

### `meta`(必填 `description`)

`meta` 是 dict,推荐用 `ClassVar[Dict[str, Any]]` 标注。约定 key:

| key | 类型 | 必填? | 含义 |
|---|---|---|---|
| `description` | `str` | **必填** | 给 LLM / 用户看的简短说明 |
| `input_schema` | `dict` | 推荐填 | pydantic `model_json_schema()`,LLM function-calling 用 |
| `output_schema` | `dict` | 可选 | 输出 schema,LLM 看返回结构 |
| `hidden` | `bool` | 可选 | 隐藏(不在 banner / 列表显示) |
| `tool` | `bool` | 可选 | 标记为 agent 工具 |
| `readonly` | `bool` | 可选 | 只读(plan 模式放行) |
| `concurrency_safe` | `bool` | 可选 | 可与其它工具并行 |

```python
class Ask(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'description': '给用户发一个单选题并等待选择结果; 支持 allow_other 自由输入.',
        'input_schema': AskInput.model_json_schema(),
        'output_schema': AskOutput.model_json_schema(),
    }
```

### `is_passive`

`True` = kernel 连接后 auto-start(单实例去重,手动 submit 被拦截)。是否常驻由 `run()` 决定:返回即退(一次性引导)或 await park(服务)。**多用于基础设施 / manager**(参见 [01-structure.md](./01-structure.md#passive-routine-的角色))。

```python
class WebServer(Routine):
    is_passive = True   # kernel auto-start;常驻 HTTP+WS 前门(run 内 await)
```

### `enable`

`False` = 注册时跳过(`register` 检查)。**罕见**——通常用环境变量 / config 控制,不写死在类上。

## 输入 schema:pydantic First

**所有给 LLM / 外部触发的 routine 必须用 pydantic 定义 `Input` / `Output` model**,然后 `model_json_schema()` 进 `meta['input_schema']`。

```python
from pydantic import BaseModel, Field

class GetAgentRidInput(BaseModel):
    agent_id: str = Field(description='要查的 agent_id')

class GetAgentRid(Routine):
    meta = {
        'description': '按 agent_id 反查运行时 rid. 复用 list_running_agents.',
        'input_schema': GetAgentRidInput.model_json_schema(),
    }
```

(真实代码见 [get_agent_rid.py](../../routines/user/get_agent_rid.py))

约定:
- `Field` 必带 `description`,给 LLM 看参数语义。
- 默认值用 `Field(default=..., description=...)`,不要用类属性默认值(便于 schema 输出)。
- 可选字段用 `Optional[T]` + `Field(default=None)`。
- `run()` 体内用 `Input.model_validate(kwargs)` 或 `Input(**kwargs)` 解析(带校验)。

## 生命周期钩子

### `async def on_created(rid, kwargs) -> Optional[Modules]`

kernel 建命令后回调。返回 `Modules([...])` 声明占用模块;返回 `None` / 空不占模块。

约定:
- **占模块的 routine**(output/ui/audio/body)必须 override,返回 `Modules([...])`。
- **不占模块的 routine**(UI 弹窗 / 控制面)可不 override,或返回 `None`。
- **state 初始化放这里**,不放 `__init__`——`__init__` 在注册时就跑(只一次),`on_created` 每次 kernel 建命令跑一次。

```python
async def on_created(self, rid: str, kwargs: Dict[str, Any]):
    self._stop_event = Event()        # state 初始化
    return Modules([MODULE_OUTPUT])   # 占 output
```

### `async def run(kwargs) -> Any`(必填,abstractmethod)

routine 主体。`kwargs` 是 `submit` 时传的 dict。return 值传给父侧 `handle.result`。

约定:
- **阻塞型 routine**(等外部信号 / 长跑):`await self._stop_event.wait()` 让出控制,`stop()` 里 `set()`。
- **快速 routine**(算完即返):直接 return,不需要 `_stop_event`。
- **async generator routine**(流式 yield):`yield` 而非 `return`,父侧 `async for handle` 迭代。

### `async def stop() -> None`(可选)

父侧 `handle.stop()` 触发。约定:
- **只放行 `run()` 体**——让 `await self._stop_event.wait()` 返回,让 `run` 自然走完。
- **不做重资源清理**(关连接 / 删文件)——那是 `on_stopped` 的事。
- **不抛异常**——抛了被 lifecycle 吞,reason 改 `ERROR`。

```python
async def stop(self) -> None:
    self._stop_event.set()
```

### `async def on_started()` / `async def on_stopped(reason, detail)`(可选,罕见)

lifecycle 状态机回调。约定:
- **`on_started`**:进入 Started 态。罕见 override——`run()` 开始即 Started,不需要额外动作。
- **`on_stopped`**:进入 Stopped 态,资源清理用。`reason` 是小写字符串(`'auto'/'stop'/'error'/'cancel'/'force'/'disconnect'`)。

```python
async def on_stopped(self, reason: str, detail: str = '') -> None:
    await self._cleanup_resources()
```

## 错误处理约定

### typed exception 优先

```python
# 好
try:
    await self.ctx.acquire('output')
except AcquireError:
    ...

# 不好——string match
try:
    await self.ctx.acquire('output')
except RuntimeError as exc:
    if 'conflict' in str(exc):
        ...
```

详见 [06-errors.md](../../../routine-py/docs/06-errors.md)。

### `run()` 体抛异常 = reason='ERROR'

`run()` 抛未捕获异常 → lifecycle 标 `reason='ERROR'`,父侧 `await handle` 收到 `RuntimeError(str(exc))`。

约定:
- **业务异常直接 raise**(如 `FileNotFoundError` / `ValueError`)——让父侧决定语义。
- **不吞异常**——尤其审批 / 投票类,超时必须 fail-closed(`Ask` 不把超时当有效答案)。
- **钩子异常不阻断 lifecycle**——`on_created` 抛了按空 modules 兜底,后续 start 的 `TryAcquire` 接力。

### `@request` handler 异常

`@request` 装饰的方法抛异常 → runtime 捕获后回 `__ok__: False, __error__: str(exc)`,**不抛到 `run()` 体**——`run()` 不受影响,只 req 调用方收到 `ReqError`。

```python
@request('send_message')
async def _on_send(self, text: str, **kwargs):
    if not text:
        raise ValueError('text required')   # 调用方收 ReqError,run() 不受影响
    ...
```

## 日志约定

每个 routine 实例自带 `self._logger`(`logging.getLogger`),名字 = routine name + id。

```python
async def run(self, kwargs):
    self._logger.info('echo %s start', self.id)
    ...
    self._logger.info('echo stopped')
```

约定:
- **info**:lifecycle 关键节点(start / stop / created)。
- **warning**:可恢复异常(超时 / 重试)。
- **error**:不可恢复异常(配置错 / 外部 API 挂)。
- **debug**:详细数据(只在 debug 模式开)。
- **不 print**——一律走 logger(便于生产环境关 / 重定向)。
- **参数化**:`self._logger.info('echo %s start', self.id)`,不 f-string(logging 延迟格式化)。

## 编排约定

### 并发子 routine:`ctx.submit` + `asyncio.gather`

批量并发跑子 routine 直接用原生原语(工具 routine 不声明 modules,无冲突):

```python
async def _run(h):
    err = await h.start()
    return err if err else await h

async def run(self, kwargs):
    hs = [await self.ctx.submit(n, kw) for n, kw in specs]
    results = await asyncio.gather(*(_run(h) for h in hs))
```

单个一次性调用用 `ctx.call` 一步到位(见下)。

### 用 `ctx.call` / `ctx.force_call` 做一次性子 routine

不需要中途 stop / 不迭代 body 时用 `ctx.call` 一步到位:

```python
result = await self.ctx.call('ask', {
    'question': '选哪个?', 'options': ['a', 'b'],
})
```

### 用 `ctx.req` 触发常驻 routine 的 handler

manager / agent 类常驻 routine 用 `req` 触发其 `@request` handler:

```python
result = await self.ctx.req(manager_rid, 'create_agent', {
    'agent_id': 'a', 'project_dir': '/path',
})
```

## UI 交互约定

`zero` 的 UI 交互**走 wire**:routine 经 `ctx.req(web_server_id, 'ui_request', ...)`
把请求发给 `WebServer` 的 `@request('ui_request')` handler,
WebServer 广播 `ui_request` 给前端,等前端 `ui_response` 后返回回执。不散落各 routine
自己直接发 ws 消息,也不依赖模块级全局变量(`_pending` / `_broadcast` 等都已移除)。

业务侧通常不直接调 wire,而是 push 已有的 `ask` routine(万物皆 routine):

```python
# 推荐:复用 ask routine(它内部走 wire 到 bridge)
value = await self.ctx.call('ask', {
    'question': '选哪个?',
    'options': ['a', 'b'],
    'timeout': 300,
})
```

需要自定义组件时,经 `ctx.req` 走 wire 到 bridge(参考 `ask.py` 实现):

```python
# 查 bridge routine id(bridge 是独立 passive routine,可能跨进程)
routines = await self.ctx.get_running_routines()
bridge_id = next(
    (str(r['id']) for r in routines if r.get('name') == 'agent_ws_bridge'),
    None,
)
if bridge_id is None:
    raise RuntimeError('bridge not running')

result = await self.ctx.req(
    bridge_id, 'ui_request',
    {
        'component': 'selector',
        'props': {'question': '选哪个?', 'options': ['a', 'b']},
        'timeout': 300,
    },
    timeout=310,  # 略大于 props.timeout,让 bridge 内部超时回执先到
)
if not result.get('ok'):
    raise RuntimeError(result.get('error') or 'ui_request failed')
value = result.get('value')
```

约定:
- **UI 弹窗不占 module**——前端 `uiQueue` 已串行,不需要 kernel 模块互斥。
- **复杂 UI 交互包成 routine**——如 `Ask` 把"单选题"包成一个 routine,其它 routine `ctx.call('ask', ...)`。
- **跨 routine 共享状态走 wire**——UI 请求的 pending future 是 `WebServer` 实例字段(`_pending_ui`),不是模块级全局变量;WebServer 停止时随 instance GC 自动回收。
- **不吞超时**——审批 / 投票类必须 fail-closed。

## per-agent 身份注入

prime agent 子系统的 skill / tool routine 通过框架注入的 `AGENT_ID_KEY`(`'from_agent_id'`)拿调用方 agent 身份,再经 `fetch_agent_state` 查 per-agent 状态(skill_dir 等),实现 agent 间隔离:

```python
from zero.routines.user.agents._core.paths import AGENT_ID_KEY

async def run(self, kwargs: Dict[str, Any]):
    agent_id = kwargs.pop(AGENT_ID_KEY, '')
    state = await self.call('fetch_agent_state', {'agent_id': agent_id})
    skill_dir = state.get('skill_dir')   # per-agent skill workspace
    ...
```

约定:
- **per-agent 数据**(skill 副本 / 会话状态)按 `agent_id` 隔离,经 `fetch_agent_state` 拿路径,不污染全局。
- **agent push tool routine 时**框架自动注入 `from_agent_id`(wire 协议),routine 内不依赖模块级全局变量区分调用方。
- **跨 agent 共享数据** 走全局路径(builtin skills 源目录等)。

## 测试约定

直接实例化 Routine 子类调 `run()`,**不需要 ctx**(skill 类 routine):

```python
routine = SearchSkill()
result = await routine.run({'query': 'git'})
```

需要 ctx 的(编排 / 子 routine)走 `RoutineHub` + 真实 kernel,或 mock ctx。详见 [04-context.md](../../../routine-py/docs/04-context.md)。

## 下一步

- 业务 routine 模块清单:[03-modules-overview.md](./03-modules-overview.md)
