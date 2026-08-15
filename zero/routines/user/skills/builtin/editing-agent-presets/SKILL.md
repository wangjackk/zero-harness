---
name: editing-agent-presets
agents: [prime]
description: "创建和编辑 agent preset：复制随附 preset、改 preset.yaml 声明、真实 spawn 自验。需要创建新 agent 变体、调整 agent 组装（模型/工具/skill/persona）时使用。"
---

# editing-agent-presets: 创作新 agent preset

preset 是纯声明文件（一个目录一个 `preset.yaml`），没有代码、没有新建空白文件的入口。
创作 = 复制已知良好的 preset + 编辑副本 + 真实试跑自验。

## 两个根

- 随附根（只读，source=shipped）：升级会覆盖，是复制的起点
- 用户根（可写，source=user）：`~/.zero/agent-presets/`，所有编辑都在这里

## 工作流

### 1. 列出现有 preset

调 `agent_preset`（op 缺省 `list`）→ `{presets: ["id (source) name -- description", ...]}`

### 2. 复制（唯一创建入口）

调 `agent_preset`，参数：

| 参数 | 值 |
|---|---|
| `op` | `'copy'` |
| `from` | 来源 preset id（如 `prime`） |
| `id` | 新 id：小写字母/数字/`_`，字母开头 |
| `name` | 显示名（可选） |

id 与任一根已有 preset 同名会被拒绝。

### 3. 编辑副本

用 read/edit 工具改 `~/.zero/agent-presets/<id>/preset.yaml`。字段：

| 字段 | 说明 |
|---|---|
| `name` | 显示名 |
| `description` | 一句话用途 |
| `agent_routine` | 底层 agent 实现 routine 名，一般不动 |
| `model` | 默认模型 key（create 显式传 model 时覆盖） |
| `enabled_tools` | 工具白名单（如 `ipython`） |
| `preload_skills` | L2 skill，全量注入 |
| `level1_skills` | L1 skill，只注入 name+desc 供发现 |
| `extra_instructions` | 附加系统指令（persona） |

未知字段会校验失败——拼错不会静默生效。

### 4. 自验（改完必做）

静态检查不可靠；真实 spawn 一轮才算数：

1. 调 `create_prime_agent`（`preset`、`project_dir`=某个测试目录）→ 验证 preset 可加载、字段合法、skill/工具名有效，返回 `{ok, agent_id}`
2. 调 `send_message`（`to`=agent_id, `message`='ping'）→ 触发一轮真实 LLM 对话
3. （可选）调 `fetch_agent_state`（`agent_id`）确认存活

第 2 步是异步往返：对方处理完会回 `send_message`，回复自动到达你的输入队列（下轮对话可见），不需要订阅。

自验能抓住：model 不存在、skill 名拼错、工具名无效。

### 5. 清理

- 调 `stop_prime_agent`（`agent_id`）：停掉试跑的 agent（记录保留，可 resume）
- 调 `agent_preset`（`op`='delete', `id`）：不要了才删 preset（仅用户根可删，随附只读）

## 原则

- 随附 preset 只读，永不编辑；先 copy 再改
- 每次改完 preset.yaml 都重新自验（步骤 4）
- preset 是组装声明，不是代码；需要新能力时改 routine/skill，preset 只引用
