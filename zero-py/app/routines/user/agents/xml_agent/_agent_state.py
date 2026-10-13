"""AgentState — agent 运行时状态数据模型.

agent 侧 ``@request('agent_state')`` handler 构造本模型返回; tool routine 通过
``fetch_agent_state`` routine (内部 call agent/rid + req agent_state) 拿到.
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
    # 当前 mood 出的 TTS 语音指令(中性带为空): speak 起 run 时现取,
    # 不随 act create 快照 ---- 情绪是 agent 的活状态
    tts_instruction: str | None = None
