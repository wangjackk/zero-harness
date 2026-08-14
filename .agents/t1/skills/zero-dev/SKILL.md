---
name: zero-dev
description: kshell/zero 项目级开发指南(本仓特定)。在 kshell/zero 项目里写/改/注册 routine、查 routine 清单、理解目录结构或启动模式、用 Shell/DAG/claudecode/web_server 时使用。涵盖目录结构、routine 编写约定、业务 routine 清单、routine 管理(list/register/reload/deregister 经 run_routine 调)。
---

# kshell/zero 项目级开发指南

本 skill 是 `kshell/zero` 应用项目的**导航入口**。具体章节内容放在 `zero/docs/` 下,**按需 Read**——不要一次全读,根据当前问题定位到对应章节再读。

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
| 01 | `zero/docs/project/01-structure.md` | 目录结构 + routine 注册约定 + 启动模式 + passive routine + manager+child 模式 + per-agent 动态注册 + Shell 编排 | 接手 zero 项目 |
| 02 | `zero/docs/project/02-routine-conventions.md` | 编写约定:pydantic schema first / 生命周期钩子 / 错误处理 / 日志 / UI 交互 / per-agent workspace 隔离 | 写业务 routine |
| 03 | `zero/docs/project/03-modules-overview.md` | 50 个 routine 按子系统分组清单 + "按能力查找"索引(触发/编排/UI/agent) | 找可用 routine / 查入参 |
| 04 | `zero/docs/project/04-end-to-end.md` | 全流程案例:实现并调用一个 routine(冷注册/热注册 + 跨 routine 调用) | 第一次写 routine |

## 按问题类型快速定位

| 问题 | 读哪个 |
|---|---|
| 项目目录结构? | `zero/docs/project/01` |
| 第一次写 routine / 想看全流程? | `zero/docs/project/04` |
| 新 routine 写哪里 / 怎么生效? | 写到 `one/routines/`,冷注册(重启 one 进程)或热注册(见下文"zero 项目关键约定"里的"routine 管理") |
| 怎么写业务 routine? | `zero/docs/project/02`(约定) + `routine-sdk` skill(框架 API) |
| 有哪些现成 routine? | `zero/docs/project/03` |
| 怎么触发某个 routine? | `zero/docs/project/03`(按能力查找) |
| 怎么编排多个 routine? | `zero/docs/project/01`(Shell) |
| 怎么跟用户交互? | `zero/docs/project/03`(Ask routine) |
| 怎么创建 agent? | `zero/docs/project/03`(manager+child) |
| 怎么编排 DAG? | `zero/docs/project/03`(dag 子系统) |
| module 体系? | `zero/docs/project/01`(module 体系) + `routine-sdk` skill(模块操作) |
| routine 怎么命名? | `zero/docs/project/02`(name 约定) |
| passive routine 是什么? | `zero/docs/project/01`(passive 角色) |
| 两种启动模式? | `zero/docs/project/01` + `routine-sdk` skill(框架级) |
| per-agent workspace 隔离? | `zero/docs/project/02`(per-agent workspace) |
| manager+child 模式? | `zero/docs/project/01`(manager+child) |
| claudecode 工具集? | `zero/docs/project/03`(claudecode tools) + `zero/routines/user/claudecode/DESIGN.md` |
| skills 子系统? | `zero/docs/project/03`(claudecode skills) + `zero/docs/project/02`(per-agent workspace) |

## 参考文档(深挖时用)

| 主题 | 路径 |
|---|---|
| 框架级 API(Routine 基类 / RunContext / 注册 / 异常) | `routine-sdk` skill |
| 模块操作完整参考 | `routine/docs/module-operations.md` |
| DAG 设计文档(原则 / RunDag / 子工作流 / 开放问题) | `zero/routines/user/dag/design/` |
| claudecode 工具集设计(工具 = routine + meta 约定) | `zero/routines/user/claudecode/DESIGN.md` |

## 使用模式

1. **收到开发任务时**:先 `list_skills` 确认本 skill 已加载,或 `load_skill zero-dev` 加载。
2. **需要框架 API 时**:同时 `load_skill routine-sdk`。
3. **根据任务类型**:查上面"按问题类型快速定位"表,Read 对应章节。
4. **写代码前**:先读 `zero/docs/project/02-routine-conventions.md`(编写约定)。
5. **找现成能力**:读 `zero/docs/project/03-modules-overview.md`,避免重复造轮子。

## zero 项目关键约定

- **zero 是意识主体**:agent 自己不能重启 zero。zero 里只保留核心 agent(prime + user/world + web_server 等意识主体基础设施),业务 routine 会逐步搬到 `one/`。
- **routine 聚合**:`zero/routines/__init__.py` 聚合 `user` 一组。`one/routines/__init__.py` 是 routine 主战场。
- **新 routine 写哪里**:写到 `one/routines/`(独立 host,zero 重启不影响 one)。两种生效方式:**冷注册**(重启 one 进程 `uv run python -m one.main --client 127.0.0.1:8888`,全量重注册),或**热注册**(见下文"routine 管理")。
- **routine 管理**:agent 经 `run_routine` 工具调用以下 routine(不直接出现在工具列表):
  - `list_routines` —— 列出所有已注册 routine。
    - 入参:`{}`
    - 返回:`{routines: [{name, hub_id, is_passive}, ...], for_llm: <str>}`
    - `hub_id`:进程级身份(如 `zero`/`one`),首次连接时校验唯一性,重复则拒绝连接。
    - `for_llm`:按 hub 聚合的文本摘要(含数量统计 + passive 标记),agent 实际看到的是这段文本。
  - `register_routine` —— 从 .py 文件注册 Routine 子类(同名 fail)。
    - 入参:`{file_path: <绝对路径>}`
    - 返回:`{registered: [name, ...], file_path, failed?}`
  - `reload_routine` —— 从 .py 文件重载 Routine 子类(同名覆盖)。
    - 入参:`{file_path: <绝对路径>}`
    - 返回:`{reloaded: [name, ...], file_path, failed?}`
  - `deregister_routine` —— 移除已注册的 routine(管理 routine 自身受保护)。
    - 入参:`{name: <routine name>}`
    - 返回:`{name, removed: bool, class_name?}`
    - 保护名单:`register_routine` / `reload_routine` / `deregister_routine` 不可删(删后丧失管理能力)。
  - 调用方式(经 `run_routine` 工具):
    ```
    run_routine({name: 'list_routines',     kwargs: {}})
    run_routine({name: 'register_routine',  kwargs: {file_path: '/abs/path/to/new_routine.py'}})
    run_routine({name: 'reload_routine',    kwargs: {file_path: '/abs/path/to/edited_routine.py'}})
    run_routine({name: 'deregister_routine',kwargs: {name: 'some_routine'}})
    ```
  - `file_path` 必须是绝对路径(agent 写文件时用 Write 工具的绝对路径)。
- **module 常量**:`zero/modules/__init__.py` 定义 `MODULE_OUTPUT`/`MODULE_UI`/`MODULE_AUDIO`/`MODULE_BODY`。
- **启动入口**:`zero/main.py`(意识主体,不随便重启),支持 server/client 两种模式(`--client` 切换)。`one/main.py` 是 routine host 入口(可自由重启)。
- **HTTP+WS 前门**:`WebServer` passive routine,一个 uvicorn server 同时跑 HTTP(curl 触发任意 routine:`/run/{name}` / `/docs`)+ WS(`/ws` 端点,前端通信枢纽,框架事件 ↔ web 前端)。默认端口 7780。
- **编排器**:`zero/shell.py` 的 `Shell` 类,模块自动串并行(冲突→串行,否则→并行),`wait` 当 barrier。
- **agent 子系统**:`prime` 走 manager+child 模式,passive manager(`prime_agent_manager`)spawn 非 passive child,通过 `agent_id` 隔离。
- **per-agent skill**:`<project>/.agents/<agent_id>/skills/` workspace 隔离,skill routine name 带 agent 前缀。
