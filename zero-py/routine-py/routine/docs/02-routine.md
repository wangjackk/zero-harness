# 02 · Routine 基类

`Routine` 是所有 routine 的抽象基类。业务侧通过 **继承 + override 几个钩子** 来定义一个 routine。

文件:[routine.py](../routine/routine.py)

## 最小例子

```python
from typing import Any, Dict
from routine import Routine


class Echo(Routine):
    """最简 routine:收到 text,原样返回。"""

    name = 'echo'                       # 命令名(也可省略,见下文)

    async def run(self, kwargs: Dict[str, Any]):
        return {'echo': kwargs.get('text', '')}
```

`run` 是 abstractmethod,**必须 override**。其他全可选。

## 类字段

### `name: ClassVar[str]` —— routine 命令名

- **作用**:routine 在 `Routines` 注册表里的 key,也是 wire 事件 `routine.submit{name}` 用的名字。
- **不显式赋值时**:`__init_subclass__` 从类名蛇形转换自动填充,如 `EditRoutine` → `'edit_routine'`。
- **显式赋值时**:覆盖自动生成。
- **约定**:全小写 + 下划线(蛇形)。per-agent 动态注册的可用 `/` 分层(如 `agent_a/list_skills`)。

```python
class Wait(Routine):
    name = 'WAIT'   # 历史遗留大写,新 routine 建议蛇形

class Edit(Routine):
    pass  # name 自动为 'edit'

class ListSkills(Routine):
    pass  # name 自动为 'list_skills'
```

### `meta: ClassVar[Dict[str, Any]]` —— 元数据

自由扩展字段,框架不强制 schema。wire 透传给 kernel,query 接口返回给前端。**子类按需覆盖自己的 dict,不要原地改继承来的默认值**。

```python
class Edit(Routine):
    meta = {
        'description': '局部修改文件',
        'readonly': False,
        'tags': ['fs'],
        'input_schema': {...},   # JSON schema,前端/LLM 用
    }
```

常见字段(约定,非强制):
| 字段 | 类型 | 说明 |
|---|---|---|
| `description` | str | 人可读描述 |
| `readonly` | bool | 是否只读(不修改状态) |
| `tags` | list[str] | 分组标签(如 `fs`/`shell`/`agent`) |
| `input_schema` | dict | 入参 JSON schema(LLM 工具调用用) |

### `is_passive: ClassVar[bool] = False` —— 是否被动启动

- `False`(默认):由父 routine 显式 `submit + start` 拉起。
- `True`:kernel 在 catalog.push 注册后 **自动 start**(无需父 routine 触发);手动 submit 被拦截。

passive 只决定"谁拉起"(kernel 自动,单实例去重),**不限定 run() 的生存期**----
run() 返回即自然退出(一次性引导),await park 则常驻(服务/manager),由业务自选。

```python
class WebServer(Routine):
    is_passive = True   # kernel 连上就自动 start;常驻靠 run() 里 await
```

### `enable: ClassVar[bool] = True` —— 是否启用

`Routines.register` 时 `enable=False` 的类会被跳过(不注册)。用于 feature flag。

## 生命周期钩子

routine 有完整的生命周期:create → start → [run] → stop。每个阶段都有钩子可 override。

```
lifecycle.create  →  on_created()  →  lifecycle.created 回报
lifecycle.start   →  on_started()  →  run() 开始         →  lifecycle.started 回报
lifecycle.stop    →  stop()        →  on_stopped()       →  lifecycle.stopped 回报
```

### `async def run(self, kwargs: Dict[str, Any]) -> Any` 【必须 override】

routine 主体。`kwargs` 来自 `submit(name, kwargs)` 的入参(单一真理源)。

- **可以是普通 coroutine**:return 值作为结果回报给父。
- **可以是 async generator**:每 `yield` 一项作为流式结果发给父,父侧 `async for item in handle` 拿到。
- 异常上抛会捕获并作为 `error` 回报。

```python
async def run(self, kwargs):
    # 普通:返回结果
    return {'sum': kwargs['a'] + kwargs['b']}

async def run(self, kwargs):
    # 流式:逐项 yield
    for i in range(kwargs['n']):
        await asyncio.sleep(0.1)
        yield i
```

### `async def stop(self) -> None`

正规 stop 流程。框架调你的 `stop()`,你应该 set event 让 `run()` 退出。

```python
class Echo(Routine):
    def __init__(self):
        super().__init__()
        self._stop_event = asyncio.Event()

    async def run(self, kwargs):
        await self._stop_event.wait()    # 阻塞到 stop
        return {'echo': kwargs.get('text', '')}

    async def stop(self):
        self._stop_event.set()           # 让 run 退出
```

- 不实现 `stop`(基类空 pass):`run` 自己跑到完成或异常。
- `stop` 抛异常会被框架吞掉(只 log),不影响 `lifecycle.stopped` 发出。
- 框架有 `STOP_TIMEOUT = 3.0`:`stop()` 超过 3 秒后强制 cancel。

### `async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> Optional[Modules]`

routine 创建时调一次(早于 start)。**返回 `Modules([...])` 声明占用模块**,返回 `None`/不 override 表示不占模块。

```python
from routine import Modules

class PlayMusic(Routine):
    async def on_created(self, rid, kwargs):
        # 占 audio 模块:多个 PlayMusic 串行,不会同时放两首.
        # body 子(如 dance)占 body 模块,跟 audio 不冲突,父子并发.
        return Modules(['audio'])
```

- `rid`:kernel 分配的 command id(string),跟 `self.id` 一致。
- `kwargs`:submit 入参。`on_created` 可读 kwargs 但**不应做重活**(会阻塞 created 回执)。
- 详细模块语义见 [module-operations.md](./module-operations.md)。

### `async def on_started(self) -> None`

`lifecycle.started` 回报后、`run()` 之前调一次。适合做"父 started 才能做"的一次性初始化。基类空实现。

### `async def on_stopped(self, reason: str = 'auto', result: Any = None, detail: str = '') -> None`

`run` 完成或退出后、`lifecycle.stopped` 发出前调一次。`reason` 取值:

| reason | 含义 |
|---|---|
| `'auto'` | `run` 自然 return / 异常退出 |
| `'stop'` | 收到 `lifecycle.stop` 请求(`stop()` 被调) |
| `'error'` | `run` 抛异常 |
| `'cancel'` | task 被 cancel |
| `'force'` | 被别的 routine `force_acquire` / `force_release` / `force_start` 驱逐 |
| `'disconnect'` | 父连接断开,级联清理 |

## 通信装饰器

routine 类的方法可以用三个装饰器标记为通信 handler。`__init__` 时自动扫描建分发表。

### `@request(event: str)` —— p2p 请求 handler

收到 `message.req`{event} 时调用,**返回值作为回执** 给 source。抛异常则回 `ok=false`。

```python
from routine import request

class Database(Routine):
    @request('get')
    async def handle_get(self, key: str):
        return self._store.get(key)
```

父侧用 `ctx.req(target_rid, 'get', data=key)` 调用。

### `@stream(event: str)` —— p2p 流 handler

`async def` + async generator。每 yield 一项发一个 `message.stream_data` 帧。

```python
from routine import stream

class Counter(Routine):
    @stream('count')
    async def handle_count(self, n: int):
        for i in range(n):
            yield i
            await asyncio.sleep(0.1)
```

父侧用 `ctx.stream_req(target_rid, 'count', data=n)` 调用,返回 `StreamCtx`(async with → async for)。

### `@subscribe(topic: str, *, namespace: str = '')` —— pubsub handler

instance created 时框架自动订阅 `(namespace, topic)`,收到 `pubsub.delivered` 时调 `handler(source, data)`。

```python
from routine import subscribe, RoutineSource

class Listener(Routine):
    @subscribe('agent.event')
    async def on_event(self, source: RoutineSource, data):
        self._logger.info('got event from %s: %s', source.id, data)
```

- `source` 是 `RoutineSource(id, name)`,发送方信息。
- **可能并发 fire + 乱序到达**——业务侧自带 id reorder。
- created 后即可收(不必 start)。动态订阅用 `ctx.subscribe(topic, handler)`。

## instance 字段(运行时)

`Routine` 实例上有这些字段可读:

| 字段 | 类型 | 说明 |
|---|---|---|
| `self.id` | `Optional[str]` | kernel 分配的 command id。created 前为 `None`,created 后等于 `self.ctx.id`。 |
| `self.name` | `str` | 类的 `name` 字段。 |
| `self.ctx` | `RunContext` | 运行上下文,created 后绑。未绑时访问抛 `RuntimeError`。 |
| `self._logger` | `logging.Logger` | 框架配置好的 logger,格式跟 Go 侧一致。 |

## 常用方法(委托 ctx)

`Routine` 把 `ctx` 上的常用 API 都委托出来了,在 `run()` 体里直接 `self.submit(...)`/`self.call(...)`/`self.req(...)` 等即可调用,无需写 `self.ctx.xxx`。

完整 API 列表见 [04-context.md](./04-context.md)。

## 下一步

- 注册 routine 让 kernel 能发现:[03-registration.md](./03-registration.md)
- routine 内能调什么 API:[04-context.md](./04-context.md)
