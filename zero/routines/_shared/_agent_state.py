"""AgentState — agent 运行时状态数据模型.

agent 侧 ``@request('agent_state')`` handler 构造本模型返回; tool routine 通过
``fetch_agent_state`` routine (内部 call get_agent_rid + req agent_state) 拿到.
"""
from __future__ import annotations

from pydantic import BaseModel


class AgentState(BaseModel):
    """agent 运行时状态, 由 agent 的 @request('agent_state') handler 返回."""

    agent_id: str | None = None
    skill_dir: str | None = None
    skill_index_cache_dir: str | None = None
    project_root: str | None = None
    session_id: str | None = None
