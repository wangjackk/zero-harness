"""XML 命名空间总目录(目录条目 manifest).

- 原语 routine(无前缀注册名): Act / RunXml / Wait / ReadImage ---- xml
  body 编排器与工具, 供 agent 消费; 基类 XmlRoutine 不注册.
- agent 子包(xml/agent/): XmlAgent 本体 + 常驻 manager + entry routines
  (xml/agent/*), reactive agent (ContextProvider memory + built-in LLM).

re-export 的 Routine 类经 routines.yaml 目录条目(``- routines/user/xml``)
注册; ``__all__`` 即 manifest.
详见 xml_routine.py 模块 docstring.
"""
from .act import Act
from .run_xml import RunXml
from .wait import Wait
from .read_image import ReadImage
from .agent import (
    XmlAgent, XmlAgents, XmlCondenserAgent,
    CreateXmlAgent, StopXmlAgent, ResumeXmlAgent, ListXmlAgents,
    DeleteXmlAgent, SetRoutineAlias,
)

__all__ = ['Act',
           'RunXml',
           # 'Speak',
           'Wait',
           'ReadImage',
           'XmlAgent', 'XmlAgents', 'XmlCondenserAgent',
           'CreateXmlAgent', 'StopXmlAgent', 'ResumeXmlAgent',
           'ListXmlAgents', 'DeleteXmlAgent', 'SetRoutineAlias']
