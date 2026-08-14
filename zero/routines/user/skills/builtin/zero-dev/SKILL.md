---
name: zero-dev
description: kshell/zero 项目级开发指南(本仓特定)。在 zero 项目里写/改/注册 routine、查 routine 清单、理解目录结构或启动模式、用 routines.yaml/watcher/prime agent/web_server 时使用。涵盖目录结构、routine 编写约定、业务 routine 清单、routine 管理(list/doc 经 run_routine 调)。
---

# kshell/zero 项目级开发指南

本 skill 是 `zero` 应用项目的**导航入口**。具体章节内容放在 `zero/docs/` 下,**按需 Read**——不要一次全读,根据当前问题定位到对应章节再读。

> 框架级 API(Routine 基类 / RunContext / RoutineHandle / 注册机制 / 异常)不在本 skill 范围,见 `routine-sdk` skill。

## 路径约定

文档路径是**相对项目根目录**的。用 Read 工具读,例如:

```
Read zero/docs/project/01-structure.md
Read zero/docs/project/03-modules-overview.md
```

Read 工具会把相对路径解析为 `<project_root>/<path>`。

## 章节目录

| # | 文档 | 内容 | 何时读 |
|---|---|---|---|
| 01 | `zero/docs/project/01-structure.md` | 目录结构 + routines.yaml 配置驱动注册 + HMR 热重载 + 启动模式 + passive routine + manager+child 模式 | 接手 zero 项目 |
| 02 | `zero/docs/project/02-routine-conventions.md` | 编写约定:pydantic schema first / 生命周期钩子 / 错误处理 / 日志 / UI 交互 / per-agent 身份注入 | 写业务 routine |
| 03 | `zero/docs/project/03-modules-overview.md` | 全部 routine 按子系统分组清单 + "按能力查找"索引(触发/编排/UI/agent) | 找可用 routine / 查入参 |
| 04 | `zero/docs/project/04-end-to-end.md` | 全流程案例:写 .py → yaml 加条目 → 热注册 → 调用 | 第一次写 routine |

## 按问题类型快速定位

| 问题 | 读哪个 |
|---|---|
| 项目目录结构? | `zero/docs/project/01` |
| 第一次写 routine / 想看全流程? | `zero/docs/project/04` |
| 新 routine 写哪里 / 怎么生效? | 写到 `zero/routines/user/` + `routines.yaml` 加条目(watcher 自动热注册,见下文"zero 项目关键约定") |
| 怎么写业务 routine? | `zero/docs/project/02`(约定) + `routine-sdk` skill(框架 API) |
| 有哪些现成 routine? | `zero/docs/project/03` |
| 怎么触发某个 routine? | `zero/docs/project/03`(按能力查找) |
| 怎么编排多个 routine? | `routine-sdk` skill(submit + asyncio.gather) |
| 怎么跟用户交互? | `zero/docs/project/03`(Ask routine) |
| 怎么创建 agent? | `zero/docs/project/03`(manager+child) |
| module 体系? | `zero/docs/project/01`(module 体系) + `routine-sdk` skill(模块操作) |
| routine 怎么命名? | `zero/docs/project/02`(name 约定) |
| passive routine 是什么? | `zero/docs/project/01`(passive 角色) |
| 两种启动模式? | `zero/docs/project/01` + `routine-sdk` skill(框架级) |
| per-agent 身份隔离? | `zero/docs/project/02`(per-agent 身份注入) |
| manager+child 模式? | `zero/docs/project/01`(manager+child) |
| 工具集? | `zero/docs/project/03`(tools 分组) |
| skills 子系统? | `zero/docs/project/03`(skills) + `zero/docs/project/02`(per-agent 身份注入) |

## 参考文档(深挖时用)

| 主题 | 路径 |
|---|---|
| 框架级 API(Routine 基类 / RunContext / 注册 / 异常) | `routine-sdk` skill |
| 模块操作完整参考 | `routine-py/docs/module-operations.md` |

## 使用模式

1. **收到开发任务时**:先 `list_skills` 确认本 skill 已加载,或 `load_skill zero-dev` 加载。
2. **需要框架 API 时**:同时 `load_skill routine-sdk`。
3. **根据任务类型**:查上面"按问题类型快速定位"表,Read 对应章节。
4. **写代码前**:先读 `zero/docs/project/02-routine-conventions.md`(编写约定)。
5. **找现成能力**:读 `zero/docs/project/03-modules-overview.md`,避免重复造轮子。

## zero 项目关键约定

- **zero 是意识主体**:agent 自己不能重启 zero 进程;改 routine 靠 watcher 热重载,不靠重启。
- **routine 注册:`zero/routines.yaml` 是单一真理源**。文件条目(`xxx.py`)注册该文件内的 Routine 子类;目录条目(`pkg/`)import 该包 `__init__.py`(`__init__` 即 manifest,re-export 什么就注册什么,不递归扫目录)。条目可写字符串或 dict(`path` + `kwargs`)——**kwargs 是 passive routine 启动配置**:注册时注入类的 `is_passive` 随 catalog 推给 kernel,auto-start `Execute(name, kwargs)` 带参拉起,`run(kwargs)` 直接收参(routine 不回头读 yaml)。**加行 = 启用,注释行 = 禁用**。name/schema/doc 以类声明为唯一事实源,yaml 不重复声明。
- **热重载(HMR)**:`RoutinesWatcher` passive routine 监控 yaml 条目对应的 `.py` 与 `routines.yaml` 本身——`.py` 变更自动 reload 替换路由,yaml 变更自动增量注册/注销,全程无需重启进程。冷注册兜底:重启进程后 `RoutinesLoader` 读 yaml 全量注册。
- **新 routine 写哪里**:写到 `zero/routines/user/`(单文件单 routine 或子包),再在 `routines.yaml` 加条目。
- **routine 查询**(agent 经 `run_routine` 工具调用,不直接出现在工具列表):
  - `list_routines` —— 列所有已注册 routine。
    - 入参:`{}`
    - 返回:`{routines: [{name, hub_id, is_passive}, ...], for_llm: <str>}`(`for_llm` 是按 hub 聚合的文本摘要,agent 实际看这段)
  - `routine_doc` —— 查指定 routine 的 description + input_schema。
  - `list_running_agents` / `get_agent_rid` / `fetch_agent_state` —— live agent 查询(agent_id / rid / 会话状态)。
  - 调用方式:`run_routine({name: 'list_routines', kwargs: {}})`。
- **module 常量**:`zero/modules/__init__.py` 定义 `MODULE_OUTPUT`/`MODULE_UI`/`MODULE_AUDIO`/`MODULE_BODY`/`MODULE_MOUTH`。
- **启动入口**:`zero/main.py`(client 模式拨 kernel 的 `as_grpc_server`,见 `kernel/config.yaml`);根目录 `start.bat` 一键起 kernel + zero + 前端。
- **HTTP+WS 前门**:`WebServer` passive routine,一个 uvicorn server 同时跑 HTTP(curl 触发任意 routine:`/run/{name}`、`/start`+`/stop` 长跑型、`/req/{rid}/{event}`、`/docs` Swagger)+ WS(`/ws` 端点,前端通信枢纽,框架事件 ↔ web 前端)。监听地址取 `run(kwargs)` 收到的 yaml 条目 kwargs(`host`/`port`,缺省 `127.0.0.1:7780`;当前 yaml 配 7781,改端口需同步前端 App.vue 与 Vite 代理)。
- **并发编排**:无全局编排器,父 routine `ctx.submit` + `asyncio.gather` 并发拉子 routine;模块冲突时用 `try_start` / 重试(见 `routine-sdk` skill 模块操作)。
- **agent 子系统**:`prime` 走 manager+child 模式,passive manager(`prime_agent_manager`)spawn 非 passive child(`prime_agent`),通过 `agent_id` 隔离;入口 routine `create_prime_agent`。
- **per-agent skill**:builtin 源在 `zero/routines/user/skills/builtin/`,agent 初始化时 seed 副本到 workspace;运行时经 `AGENT_ID_KEY`(`from_agent_id`,框架注入)+ `fetch_agent_state` 定位 per-agent skill_dir。
