"""react_agent -- reactive agent (ContextProvider memory + built-in LLM, autonomous unit).

Multi-instance model: ReactAgents resident manager spawns ReactAgent children
on request; CreateReactAgent is the entry routine the bridge submits. Each
child runs concurrently, isolated by agent_id.

目录条目 manifest: re-export 的 Routine 类经 routines.yaml(``- routines/user/
agents/react_agent``)注册; memory/provider 是内部模块不经 ``__all__`` 暴露.
"""
from .agent import ReactAgent
from .condenser import ReactCondenserAgent
from .manager import ReactAgents, CreateReactAgent

__all__ = ['ReactAgent', 'ReactAgents', 'CreateReactAgent', 'ReactCondenserAgent']
