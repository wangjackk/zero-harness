"""xml_agent -- reactive agent (ContextProvider memory + built-in LLM, autonomous unit).

Multi-instance model: XmlAgents resident manager spawns XmlAgent children
on request; CreateXmlAgent is the entry routine the bridge submits. Each
child runs concurrently, isolated by agent_id.

自治 routine 集: 只依赖 routine SDK + pip 包, 不依赖应用内其他模块
(llm / condenser / bridge / messaging 等均已 vendor 进包). 两个可选集成点
(缺失时优雅降级, 不影响运行):
- skills seeding: 应用提供 ``zero.routines.user.skills.registry``
- OV 长期记忆: 应用提供 ``zero.routines._shared.ov_memory``

目录条目 manifest: re-export 的 Routine 类经 routines.yaml(``- routines/user/
agents/xml_agent``)注册; 其余模块 (memory/provider/llm/_condenser 等) 为包内
实现不经 ``__all__`` 暴露.
"""
from .agent import XmlAgent
from .condenser import XmlCondenserAgent
from .manager import XmlAgents, CreateXmlAgent

__all__ = ['XmlAgent', 'XmlAgents', 'CreateXmlAgent', 'XmlCondenserAgent']
