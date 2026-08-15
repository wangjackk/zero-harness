# 03 · 业务 Routine 模块清单

本文档列出 `zero` 项目内所有已注册的 routine,按子系统分组。给 dev agent 用:发现可用能力 / 查找入参 schema / 决定编排路径。

> 框架级 API 见 [routine-py/docs/](../../../routine-py/docs/);编写约定见 [02-routine-conventions.md](./02-routine-conventions.md)。

## 总览

启用的 routine 由 [`zero/routines.yaml`](../../routines.yaml)(单一真理源)声明,`RoutinesLoader` passive routine 拉起时动态注册:

| 分组 | 条目 | 角色 |
|---|---|---|
| `routines/loader.py` | 静态注册 | yaml 引导(注册完即退) |
| `routines/watcher.py` | 静态注册 | HMR 热重载 |
| `routines/user/web_server/` | 目录 | HTTP+WS 前门 |
| `routines/user/*.py` | 单文件 ×9 | agent 基础设施 routine |
| `routines/user/agents/prime/` | 文件 ×2 | prime agent 入口 + manager |
| `routines/user/agents/_core/condenser/` | 文件 ×1 | 上下文压缩 |
| `routines/user/agents/tools/` | 目录 | 内置工具集 |
| `routines/user/skills/` | 目录 | 通用 skill 子系统 |

## routines/ 顶层 —— 引导与热重载

| 类 | name | passive | 说明 |
|---|---|---|---|
| `RoutinesLoader` | `routines_loader` | ✅ | 读 routines.yaml 动态注册其余全部 routine,注册完成自然退出(hidden) |
| `RoutinesWatcher` | `routines_watcher` | ✅ | 监控 yaml 条目 `.py` 与 routines.yaml 变更,自动 reload / 增量注册注销 |

## user/ 顶级 —— 前门与 agent 基础设施

| 类 | name | passive | 说明 |
|---|---|---|---|
| `WebServer` | `web_server` | ✅ | HTTP+WS 前门:`/run/{name}` 以 user 身份触发、`/agents/{id}/run/{name}` 按指定 agent 触发、`/agents` 系列(create/stop/resume/delete)管理、`/docs` Swagger、`/ws` 桥接前端 |
| `Ask` | `ask` | – | 给用户发单选题,等选择结果;支持 `allow_other` 自由输入。UI 弹窗不占 module |
| `SendMessage` | `send_message` | – | agent 间消息投递(带 `from_agent_id` 身份注入) |
| `UserAgent` | `user_agent` | ✅ | 用户侧消息入口(前端对话 → agent 循环) |
| `WorldAgent` | `world_agent` | ✅ | 世界侧消息入口(事件 / 定时驱动 → agent 循环) |
| `ListRunningAgents` | `list_running_agents` | – | 列 live agent(agent_id / rid / type) |
| `GetAgentRid` | `get_agent_rid` | – | 按 agent_id 反查 rid |
| `FetchAgentState` | `fetch_agent_state` | – | 按 agent_id 查会话状态(skill_dir 等 per-agent 数据入口) |
| `ListRoutines` | `list_routines` | – | 列已注册 routine(name / hub / passive) |
| `RoutineDoc` | `routine_doc` | – | 查指定 routine 的 description + input_schema |

## user/agents/prime/ —— prime agent

manager + child 模式(见 [01-structure.md](./01-structure.md#manager--child-模式)):

| 类 | name | passive | 说明 |
|---|---|---|---|
| `PrimeAgentManager` | `prime_agent_manager` | ✅ | 常驻 manager:`create` / `list` / `stop` req handler,spawn `ReactorAgent` 子实例;agent 记录持久化 sqlite |
| `CreatePrimeAgent` | `create_prime_agent` | – | 入口 routine,经 manager 生成新 agent,返回 `agent_id` |
| `ReactorAgent` | `prime_agent` | – | 编码 agent 子实例(LLM 对话循环 + 工具编排 + 会话日志),由 manager spawn |

配套 `agents/_core/`(不直接注册为业务 routine,是被 import 的基础设施):`llm`(模型路由)/ `store`(sqlite 会话存储)/ `session` / `condenser`(上下文压缩)/ `memory` / `messages`。

`CondenserAgent`(`_core/condenser/routine.py`)作为独立 routine 注册,负责会话上下文压缩。

## user/agents/tools/ —— 内置工具集(整包注册)

prime agent 的工具,每个工具一个 routine,跟普通 routine 同构。`meta` 承载工具语义(`readonly` / `concurrency_safe` / `needs_approval`)。

### file_ops/

| 类 | name | readonly | 说明 |
|---|---|---|---|
| `Read` | `read` | ✅ | 读文件(文本/图片/PDF/notebook),支持行范围 |
| `Write` | `write` | ❌ | 创建或覆盖文件;现有文件需先 Read |
| `Edit` | `edit` | ❌ | 字符串替换式局部修改;需先 Read |
| `Glob` | `glob` | ✅ | 按 glob 找文件(`**/*.ts`) |
| `Grep` | `grep` | ✅ | ripgrep 内容检索(正则) |

### shell/

| 类 | name | readonly | 说明 |
|---|---|---|---|
| `Bash` | `bash` | ❌ | 执行 shell 命令(Windows 走 PowerShell) |
| `BackgroundShell` | `background_shell` | ❌ | 长跑命令(后台 start / poll / stop) |
| `IPython` | `ipython` | ❌ | 持久 IPython kernel(真 ipykernel + ZMQ),per-session 状态保持 |

### remote/

| 类 | name | readonly | 说明 |
|---|---|---|---|
| `WebFetch` | `web_fetch` | ✅ | 抓 URL,trafilatura 抽正文 |
| `WebSearch` | `web_search` | ✅ | 联网搜索 |
| `SshConnect` | `ssh_connect` | ❌ | AsyncSSH 持久连接 + 长活 shell |
| `SshExec` | `ssh_exec` | ❌ | 在已连 shell 里执行命令 |
| `SshTransfer` | `ssh_transfer` | ❌ | 上传 / 下载文件与目录 |
| `SshDisconnect` | `ssh_disconnect` | ❌ | 按 alias 关连接 |
| `SshList` | `ssh_list` | ✅ | 列当前 session 活跃连接 |

### utils/

| 类 | name | readonly | 说明 |
|---|---|---|---|
| `RunRoutine` | `run_routine` | ❌ | agent 内调用任意已注册 routine(路由入口) |
| `TodoWrite` | `todo_write` | ❌ | 写结构化任务清单 |

## user/skills/ —— 通用 skill 子系统(整包注册)

per-agent skill 管理,builtin 源在 [skills/builtin/](../../routines/user/skills/builtin/),安装副本按 agent_id 隔离(见 [02-routine-conventions.md](./02-routine-conventions.md#per-agent-身份注入))。

| 类 | name | 说明 |
|---|---|---|
| `SearchSkill` | `search_skill` | 搜 skill 索引,返回候选 + 说明 |
| `ListSkills` | `list_skills` | 列可用 skill(name + 简述) |
| `LoadSkill` | `load_skill` | 加载 skill 完整说明到对话 |
| `InstallSkill` | `install_skill` | 从本地目录或 URL 装 skill 到 agent workspace |
| `UninstallSkill` | `uninstall_skill` | 卸载 workspace 副本(不动 builtin 源) |

builtin skills:`routine-sdk`(框架 API)/ `agent-messaging` / `skill-creator`。prime 专属 (`agents: [prime]` 受众声明,如 `hub_routine` / `routine_bridge` / `routine-creator` / `editing-agent-presets` skill)也统一放 `skills/builtin/`,seed 时按 `skill_profile` 过滤。

## 按能力查找

### 想触发某个 routine

| 场景 | 入口 |
|---|---|
| 一次性触发(算完即返) | `ctx.call(name, kwargs)` 或 HTTP `POST /run/{name}` |
| 长跑型(中途 stop) | `ctx.submit + handle.start + handle.stop`(HTTP 侧无此形态) |
| 触发常驻 routine 的 handler | `ctx.req(rid, event, body)`(HTTP 侧无此形态) |
| 触发 passive routine | 不用触发——kernel 连上后 auto-start |

### 想编排多个 routine

| 场景 | 入口 |
|---|---|
| 并发批量 | `ctx.submit` + `asyncio.gather` |

### 想跟用户交互

| 场景 | 入口 |
|---|---|
| 单选题 | `ctx.call('ask', {'question': ..., 'options': [...]})` |
| 前端弹窗(自定义组件) | `ctx.req(web_server_id, 'ui_request', {component, props, timeout})`(详见 `02-routine-conventions.md` UI 交互约定) |

### 想创建 / 操作 agent

| 场景 | 入口 |
|---|---|
| 创建 prime agent | `ctx.call('create_prime_agent', {'agent_id': ..., 'project_dir': ...})` |
| 列 live agent | `ctx.call('list_running_agents')` |
| 跟 agent 对话 | `ctx.call('send_message', {'to': agent_id, 'message': ...})`(或直接 `ctx.req(agent_rid, 'chat_message', {'message': ...})`) |
| 查 agent 会话状态 | `ctx.call('fetch_agent_state', {'agent_id': ...})` |

## 下一步

- 框架级 API:[routine-py/docs/](../../../routine-py/docs/)
- 编写约定:[02-routine-conventions.md](./02-routine-conventions.md)
