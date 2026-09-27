"""flow 自有的跨 wire 协议常量.

值与生态约定保持一致 (工具 routine 读取这些键/格式), 但代码归 flow
自己所有, 不 import 项目内其他基础设施.
"""
from __future__ import annotations

from pathlib import Path

# agent push tool routine 时注入的调用方身份键 (工具侧约定读此键)
AGENT_ID_KEY = 'from_agent_id'

USER_SOURCE = 'user'

# 消息优先级: high → followup, mid → steer, low → inject
PRIORITY_HIGH = 10
PRIORITY_LOW = 0

# 前端 chat_message 三档 → 优先级 (hub/前端共用)
MODE_PRIORITY = {'followup': PRIORITY_HIGH, 'steer': 5, 'inject': PRIORITY_LOW}


def workspace_for(agent_id: str, project_root: str | None) -> Path:
    """agent workspace 位置 (agent 与 hub 共用的唯一规则).

    project_root 存在 → <root>/.agents/<id>; 否则全局根
    ~/.zero/agent-workspaces/<id>. EventLog (sessions/*.jsonl) 与
    agent.json (hub 元数据) 都落在这里 ---- 事件流是唯一真相,
    agent.json 只是 hub 用来发现可 resume 的 agent.
    """
    if project_root:
        return Path(project_root) / '.agents' / agent_id
    return Path.home() / '.zero' / 'agent-workspaces' / agent_id


def wrap_from(content: str, from_agent: str) -> str:
    """agent 间消息加 ``from: <id>`` 头; 用户消息原样."""
    if not from_agent or from_agent == USER_SOURCE:
        return content
    return f'from: {from_agent}\n{content}'
