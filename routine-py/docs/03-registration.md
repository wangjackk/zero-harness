# 03 · 注册与发现

routine 必须先 **注册** 才能被 kernel 发现和路由。注册分两层:

- **本地注册**(`Routines` 类):routine hub 进程内的类注册表。
- **kernel 路由同步**:本地注册后框架自动同步给 kernel,无需手动处理。

文件:
- `Routines`:[routine.py](../routine/routine.py)
- `RoutineHub.register_routine/deregister_routine`:[server.py](../routine/server.py)

## 命名约定

全栈命名统一为 `register/deregister`:

| 层 | 注册 | 移除 |
|---|---|---|
| **本地注册表**(`Routines`) | `register(*routines)` | `deregister(name)` |
| **对外 API**(`RoutineHub`) | `register_routine(*routines)` | `deregister_routine(name)` |

## 本地注册:`Routines` 类

`Routines` 是 routine 类(`Type[Routine]`)的注册表,**存类不存实例**(实例化在 lifecycle.create 时按需做)。

### `register(*routines)` —— 注册

接受多个 `Routine` 子类或 `Routines` 组(自动 flatten)。同名覆盖。`enable=False` 的类跳过。

```python
from routine import Routines, Routine

class Foo(Routine): ...
class Bar(Routine): ...

reg = Routines()
reg.register(Foo, Bar)

# 也可以传 Routines 组(flatten)
group = Routines()
group.register(Foo)
reg.register(group)   # 等价于 reg.register(Foo)
```

### `deregister(name) -> Optional[Type[Routine]]` —— 移除

按 `name` 移除,返回被移除的类。不存在返回 `None`(不报错)。

```python
reg.deregister('foo')          # 返回 Foo 类或 None
reg.deregister('not_exists')   # 返回 None,不抛
```

### 查询方法

| 方法 | 返回 |
|---|---|
| `get_routine(name) -> Optional[Type[Routine]]` | 按 name 查类 |
| `get_routines() -> List[Type[Routine]]` | 所有已注册类 |
| `get_routine_names() -> List[str]` | 所有 name 列表 |

## 对外 API:`RoutineHub`

`RoutineHub` 在 `Routines` 之上加了 **kernel 路由同步**——本地注册/移除的同时,自动同步 kernel 路由表。

### `register_routine(*routines) -> None` —— 运行时注册

```python
srv.register_routine(DynA, DynB)
```

行为:
1. 本地 `Routines.register`。
2. 对每个新增的 name,自动发 `catalog.register` 事件同步 kernel 路由表。
3. `transport` 未连时只本地注册,重连时全量 `catalog.push` 兜底。

### `deregister_routine(name) -> Optional[Type[Routine]]` —— 运行时移除

```python
removed = srv.deregister_routine('agent_a/list_skills')
```

行为:本地移除 + 自动发 `catalog.deregister` 事件。返回被移除的类(不存在返 `None`)。

### 启动时全量注册

进程启动时 `RoutineHub` 发一次 `catalog.push`,**全量** 把所有 routine 推给 kernel。后续运行时变更走单条 `catalog.register/deregister`。重连时也会发 `catalog.push` 兜底。

## 典型场景

### 场景 1:启动时全量注册

进程启动时框架自动发 `catalog.push` 全量同步。业务侧只需把 routine 类用 `Routines.register` 加进注册表。

```python
# 应用项目 main.py
routines = Routines()
routines.register(Foo, Bar, ...)
await start_server(routines=routines, modules=modules, address=addr, hub_id='myapp')
```

`hub_id` 是进程级稳定身份(如 `zero`/`one`),必填。kernel 首次连接时校验唯一性,重复则拒绝连接;`list_routines` 用 `hub_id` 标识 routine 归属。

### 场景 2:per-agent 动态注册

agent 启动时为自己的 skill 操作动态注册带前缀的 routine,agent 销毁时移除。

```python
# agent 启动
prefix = f'agent_{agent_id}'
for skill in skills:
    DynCls = make_skill_routine(f'{prefix}/{skill.name}', skill)
    server.register_routine(DynCls)   # 本地 + 自动同步 kernel

# agent 销毁
for skill in skills:
    server.deregister_routine(f'{prefix}/{skill.name}')
```

### 场景 3:transport 未连时的注册

`register_routine` 检测到 `transport is None` 时只本地注册,不发 wire 事件。等 transport 连上时,`catalog.push` 全量兜底。

```python
srv = RoutineHub(routines, transport=None)
srv.register_routine(Dyn)   # 只本地,不报错
# transport attach 后,自动发 catalog.push 把 Dyn 也推给 kernel
```

## 下一步

- routine 内能调什么 API:[04-context.md](./04-context.md)
- 编排子 routine:[05-handle.md](./05-handle.md)
