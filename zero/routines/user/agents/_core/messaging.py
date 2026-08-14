"""Agent 消息约定: 优先级常量 + from 头包装.

priority: 决定接收方是否打断当前 react. 高 (>=PRIORITY_HIGH) 立即处理 (打断);
低排队等 idle. user 默认高, agent 默认低.

``wrap_from``: 给 agent 消息正文加 ``from: <id>`` 头. 用户消息不加.
"""
from __future__ import annotations

USER_SOURCE = 'user'

# priority: 决定接收方是否打断当前 react. 高 (>=PRIORITY_HIGH) 立即处理 (打断);
# 低 排队等 idle. user 默认高, agent 默认低.
PRIORITY_HIGH = 10
PRIORITY_LOW = 0


def wrap_from(content: str, from_agent: str) -> str:
    """给 agent 消息正文加 ``from: <id>`` 头. 用户消息原样返回不加头."""
    if not from_agent or from_agent == USER_SOURCE:
        return content
    return f'from: {from_agent}\n{content}'
