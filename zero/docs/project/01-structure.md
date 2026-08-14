# 01 · 项目结构与启动

本文档描述 `zero/` 应用的目录结构、routine 注册约定、两种启动模式,以及与 `kernel` 的协作关系。

> 框架级 API 见 [routine-py/docs/](../../../routine-py/docs/);本文档只讲 `zero` 项目特定的约定。

## 目录结构

```
zero/
├── main.py                   # 应用入口:注册 routines + 打 banner + 起 server/client
├── pyproject.toml            # 依赖声明(uv + path 依赖 routine)
├── models.yaml               # LLM 配置(api_key,不入库;模板见 models.yaml.example)
├── routines.yaml             # 启用 routine 清单(配置驱动注册的单一真理源)
├── modules/
│   └── __init__.py           # 全局 module 常量 + get_modules()
├── routines/
│   ├── __init__.py           # 聚合入口(loader 静态注册,yaml 动态加载其余)
│   ├── banner.py             # 启动 banner 打印(routine / module 列表)
│   ├── loader.py             # RoutinesLoader:读 routines.yaml 动态注册的引导 passive routine
│   ├── watcher.py            # RoutinesWatcher:源码 / yaml 变更热重载(HMR)
│   └── user/                 # 业务 routine(实际功能)
│       ├── web_server/       # WebServer:HTTP+WS 前门(curl 触发 routine + WS 桥接前端)
│       ├── ask.py            # Ask:向用户提问(审批/选项)
│       ├── send_message.py   # SendMessage:agent 间消息投递
│       ├── user_agent.py     # UserAgent:用户侧消息入口(passive)
│       ├── world_agent.py    # WorldAgent:世界侧消息入口(passive)
│       ├── list_routines.py / routine_doc.py / ...   # routine 发现类(列表/文档/状态)
│       ├── list_running_agents.py / get_agent_rid.py / fetch_agent_state.py
│       ├── agents/           # prime agent 子系统
│       │   ├── _core/        # agent 基础设施(llm / store / session / condenser / memory)
│       │   ├── prime/        # PrimeAgentManager + ReactorAgent + prime 专属 skills
│       │   └── tools/        # 内置工具集(file_ops / shell / remote / utils)
│       └── skills/           # 通用 skill 子系统(list / load / install / search / uninstall)
├── frontend/                 # Vue3 + Vite 前端(独立 bun/npm 项目)
└── docs/                     # 本目录:接口使用文档
```

## module 体系

`zero/modules/__init__.py` 定义全局互斥 module 常量:

| 常量 | 值 | 用途 |
|---|---|---|
| `MODULE_OUTPUT` | `'output'` | 输出/显示设备(屏 / UI) |
| `MODULE_UI` | `'ui'` | UI 交互通道 |
| `MODULE_AUDIO` | `'audio'` | 音频播放设备 |
| `MODULE_BODY` | `'body'` | body 帧流(async generator yield) |

`get_modules()` 返回所有 module 名列表,kernel 启动时挂 module.tree 用。

**routine 占模块靠 `on_created()` 返回 `Modules([...])`**——同一 module 同时只能被一个 started routine 占。冲突判定走 `ctx.conflict(a, b)`(cone 交集),由 Shell 编排器消费。

## routine 注册约定

### 配置驱动注册(routines.yaml)

`zero/routines.yaml` 是启用 routine 的**单一真理源**,两种条目粒度:

- **文件条目**(`xxx.py`):import 该模块,注册其命名空间内的 Routine 子类
- **目录条目**(`pkg/`):import 该包 `__init__.py`,re-export 什么就注册什么(`__init__` 即包的 manifest;子包经 import 链自然引入,不递归扫目录)

两种条目形态:纯字符串,或 dict(`path` + `kwargs`)。**kwargs 是 passive routine 的启动配置**——注册时注入类的 `is_passive`(dict 形态),随 catalog 推给 kernel;auto-start 时 `Execute(name, kwargs)` 带参拉起,`run(kwargs)` 直接收参。**配置随注册一次流动,routine 内无需回头读 yaml**;wire 显式传参优先于 yaml。

```yaml
- path: routines/user/web_server/server.py
  kwargs:
    host: 127.0.0.1
    port: 7781
```

`RoutinesLoader` 是唯一静态注册的 passive routine,kernel 自动拉起后读 yaml 动态注册其余全部 routine,注册完成即自然退出。**注释一行条目 = disable**,增删 routine 改 yaml 即可,无需改代码。

name / schema / doc 以 Routine 类声明为唯一事实源,yaml 不重复声明。

### 热重载(HMR)

`RoutinesWatcher`(passive)监控 yaml 条目对应的 `.py` 文件与 `routines.yaml` 本身:

- `.py` 变更 → `importlib.reload` 该模块 → yaml kwargs 重注入新类 → `hub.reload_routine` 替换 kernel 路由
- `routines.yaml` 变更 → 读一次解析,diff 条目增量注册 / 注销;同 path 的 kwargs 变化 → 重注入类 + reload(kernel 覆盖路由带新 kwargs,停老实例并 auto-start 新实例)

全程无需重启进程。

### routine 命名约定

- `Routine.name` 默认由类名 snake_case 转换生成(`__init_subclass__` 自动)。
- 大写 name(如 `name = 'WAIT'`)表示特殊语义 routine——Shell 编排器识别 `wait` / `WAIT` 当 barrier 处理。
- per-agent 动态注册的 routine name 带 agent 前缀(如 `agent_a/list_skills`),避免全局重名。

### `meta` 字段约定

| key | 类型 | 含义 |
|---|---|---|
| `description` | `str` | 给 LLM / 用户看的简短说明 |
| `input_schema` | `dict` | pydantic `model_json_schema()`,LLM function-calling 用 |
| `hidden` | `bool` | 隐藏(不在 banner / 列表显示,如内部 supervisor) |
| `tool` | `bool` | 标记为 agent 工具(给 prime agent 发现用) |
| `readonly` | `bool` | 只读(plan 模式放行) |
| `concurrency_safe` | `bool` | 可与其它工具并行 |

## 启动模式

`zero/main.py` 支持两种启动模式,跟 kernel 的 `config.yaml` 两段配置对应:

### server 模式(默认)

zero 当 gRPC server 监听,kernel 用 `as_grpc_client` 连过来。

```bash
uv run python -m zero.main                      # 默认 0.0.0.0:7777
uv run python -m zero.main 127.0.0.1:50071       # argv 覆盖监听地址
```

适用场景:kernel 在远端 / kernel 想主动连接 zero。

### client 模式(`--client`)

zero 当 gRPC client,拨 kernel 的 `as_grpc_server` 监听地址。kernel config 需配 `as_grpc_server.enable: true`。

```bash
uv run python -m zero.main --client 127.0.0.1:50051
```

适用场景:kernel 是稳定服务端,zero 是客户端(可多个 zero 连同一 kernel)。

**业务层(`RoutineHub`)两模式共用**,只换 transport。代码不感知:

```python
async def serve(addr: str, *, client: bool = False) -> None:
    routines = get_routines()
    modules = get_modules()
    print_banner(routines, modules)

    if client:
        await start_client(routines=routines, modules=modules, address=addr)
    else:
        await start_server(routines=routines, modules=modules, address=addr)
```

### 启动时序

1. `_reconfigure_stdio_utf8()` —— Windows 下强制 stdout/stderr UTF-8(避免 emoji / 中文乱码)。
2. `get_routines()` —— 聚合所有 routine 类(无实例化,只存类)。
3. `get_modules()` —— 拿 module 名表。
4. `print_banner(routines, modules)` —— 打印启动 banner。
5. `start_client` / `start_server` —— 起 routine hub,跟 kernel 建 gRPC Stream,进 main loop。
6. kernel 收到连接后:
   - 推 `module.tree`(全量 module 拓扑)。
   - 收到 zero 的 `catalog.push`(全量 routine 路由)。
   - 对每个 `is_passive=True` 的 routine 发 `lifecycle.start`(auto-start passive),
     其中 `WebServer` 被拉起后自动开 HTTP+WS 监听(地址读 routines.yaml 条目 kwargs `host`/`port`,缺省 `127.0.0.1:7780`;当前 yaml 配 7781)。

## passive routine 的角色

`is_passive=True` 的 routine 由 kernel 在连接建立后**自动拉起**(单实例去重,手动 submit 被拦截),不需要外部触发。是否常驻由业务 `run()` 决定----`zero` 里的 passive routine 多为常驻基础设施,`RoutinesLoader` 则是注册完即退的一次性引导。典型:

| routine | 作用 |
|---|---|
| `WebServer`(`user/web_server/server.py`) | HTTP+WS 前门:curl 触发任意 routine + WS 桥接前端通信 |
| `PrimeAgentManager`(`user/agents/prime/manager.py`) | prime agent 全局 manager(spawn 子 agent) |
| `RoutinesLoader`(`routines/loader.py`) | 读 routines.yaml 动态注册其余 routine,注册完即退 |
| `RoutinesWatcher`(`routines/watcher.py`) | 源码 / yaml 变更热重载 |

passive routine 通常是"基础设施"或"manager",业务 routine 通过 `submit+start` 或 `req` 触发。

## manager + child 模式

`zero` 里 agent 类 routine(prime agent)走 **manager + child** 模式:

- **manager**:passive 常驻,kernel auto-start。持有 `@request` handler(`create_agent` / `list_agents` / `stop_agent`)。
- **child**:非 passive,由 manager 在收到 `create_agent` req 时 `submit+start` 一个子 routine 实例。
- **隔离**:每个 child 独立 `agent_id`,自带 pubsub namespace + session,互不干扰。
- **生命周期**:manager 持有 live child 的 `RoutineHandle`,可 cascade-stop;child 死了 manager 收 `lifecycle.stopped` 回执。

```
┌──────────────────────────────────────────────────────────┐
│  kernel (auto-start passive on connect)                  │
│      │                                                    │
│      ▼ lifecycle.start                                    │
│  ┌──────────────────┐    req('create_agent')             │
│  │ PrimeAgentManager│ ←─────────────── 前端 / bridge      │
│  │  (passive mgr)   │                                    │
│  │                  │ submit+start                       │
│  │  handles: {      │ ──────────────┐                    │
│  │    agent_a: ...  │               ▼                    │
│  │    agent_b: ...  │          ┌──────────────┐          │
│  │  }               │          │ ReactorAgent │          │
│  └──────────────────┘          │  (child)     │          │
│                                │  agent_id=b  │          │
│                                └──────────────┘          │
└──────────────────────────────────────────────────────────┘
```

## per-agent 动态 routine 注册

`zero` 支持运行时为每个 agent 动态注册 per-agent skill routine(如 `agent_a/list_skills`),workspace 在实例化时绑定,实现 agent 间隔离。

详见框架文档 [03-registration.md](../../../routine-py/docs/03-registration.md#运行时注册)。

## 下一步

- routine 编写约定:[02-routine-conventions.md](./02-routine-conventions.md)
- 业务 routine 模块清单:[03-modules-overview.md](./03-modules-overview.md)
