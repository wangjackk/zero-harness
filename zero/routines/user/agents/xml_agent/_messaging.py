"""统一 incoming 消息格式 + from 头包装.

``IncomingMessage``: user / agent 消息共用的统一数据格式. from 恒有值
(用户消息填 ``'user'``, agent 消息填 agent_id), agent 内部统一看待.

``wrap_from``: 给 agent 消息正文加 ``from: <id>`` 头. 用户消息不加.

格式拼装由接收方 agent 通过本 helper 完成, send_message 只传结构化字段.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

USER_SOURCE = 'user'

# priority: 决定接收方是否打断当前 react. 高 (>=PRIORITY_HIGH) 立即处理 (打断);
# 低 排队等 idle. user 默认高, agent 默认低.
PRIORITY_HIGH = 10
PRIORITY_LOW = 0


class IncomingMessage(BaseModel):
    """统一 incoming 消息格式 (user / agent 共用).

    - ``text``: 消息正文.
    - ``from_``: 消息来源. 用户消息填 ``'user'``; agent 消息填发送方 agent_id.
      恒有值, agent 内部统一看待, 不再区分 user/agent 链路.
    """

    model_config = {'populate_by_name': True}
    text: str
    from_: str = Field(default=USER_SOURCE, alias='from')

    @classmethod
    def from_payload(cls, data: Any) -> 'IncomingMessage':
        """从裸 dict (事件 / req payload) 构造."""
        d = data or {}
        text = str(d.get('text') or '')
        return cls(text=text, from_=str(d.get('from') or USER_SOURCE))


def wrap_from(content: str, from_agent: str) -> str:
    """给 agent 消息正文加 ``from: <id>`` 头. 用户消息原样返回不加头."""
    if not from_agent or from_agent == USER_SOURCE:
        return content
    return f'from: {from_agent}\n{content}'
