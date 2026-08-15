"""XML body 编排 routines(目录条目 manifest).

re-export 的 Routine 类经 routines.yaml 目录条目(``- routines/user/xml``)注册;
``__all__`` 即 manifest ---- 只列叶子 routine,基类 XmlRoutine 不注册
(编排器由 Act/RunXml 组合使用,不单独跑).

详见 xml_routine.py 模块 docstring.
"""
from .act import Act
from .print_body import PrintBody
from .run_xml import RunXml
from .speak import Speak

__all__ = ['Act', 'PrintBody', 'RunXml', 'Speak']
