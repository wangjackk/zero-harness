"""flow agent: turn/step 循环 (dsh ReactLoopAgent 移植).

零项目基础设施依赖的自治包: 存储/LLM/度量/协议常量/prompt/schema
全部自有 (eventlog / llm / context / wire), 仅依赖 routine 框架与
Shell 编排原语.

``__init__`` 即 manifest: 包内全部 Routine 类 re-export, 经包条目
一次注册. FlowHub / FlowManager / FlowSkills 是 passive routine,
自启配置是类声明的 ``is_passive`` PassiveConfig 默认 (部署时 yaml
可整体覆盖).
"""
from .agent import FlowAgent, FlowAgentInput, FlowAgentOutput
from .compactor import FlowCompactor, FlowCompactorInput, FlowCompactorOutput
from .context import CondenseConfig, estimate_items_tokens, find_cut_index
from .eventlog import EventLog
from .hooks import HookRegistry
from .hub import FlowHub, FlowHubInput
from .inbox import Inbox, InboxMessage
from .llm import Completed, Llm, LlmConnError, ReasoningDelta, TextDelta
from .manager import FlowManager
from .phase import Aborted, Abort, Running
from .pruner import FlowPruner, FlowPrunerInput, FlowPrunerOutput
from .scheduler import ToolScheduler
from .skill_tool import SkillInput, SkillOutput, SkillTool
from .skills import (FlowSkills, FlowSkillsInput, FlowSkillsOutput,
                     invoked_skill_names, render_skill_content)

__all__ = [
    'FlowAgent', 'FlowAgentInput', 'FlowAgentOutput',
    'FlowCompactor', 'FlowCompactorInput', 'FlowCompactorOutput',
    'CondenseConfig', 'estimate_items_tokens', 'find_cut_index',
    'EventLog', 'HookRegistry', 'Inbox', 'InboxMessage',
    'FlowHub', 'FlowHubInput', 'FlowManager',
    'Llm', 'LlmConnError', 'TextDelta', 'ReasoningDelta', 'Completed',
    'Aborted', 'Abort', 'Running',
    'FlowPruner', 'FlowPrunerInput', 'FlowPrunerOutput', 'ToolScheduler',
    'FlowSkills', 'FlowSkillsInput', 'FlowSkillsOutput',
    'SkillTool', 'SkillInput', 'SkillOutput',
    'invoked_skill_names', 'render_skill_content',
]
