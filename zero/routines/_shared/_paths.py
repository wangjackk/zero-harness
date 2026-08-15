"""跨 routine 共享的常量.

AGENT_ID_KEY: agent push tool routine 时注入的调用方身份键.
用 from_agent_id 避免跟 tool routine 自身的 agent_id 输入字段冲突.
"""

AGENT_ID_KEY = 'from_agent_id'
