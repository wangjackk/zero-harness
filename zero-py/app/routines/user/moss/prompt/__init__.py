"""prompt 片段: 一次性 routine 产文本, Moss 装配时拼一次注入 Ghost.

名字带 ``prompt/`` 命名空间(斜杠合法, registry 只当字符串键); 不进
CTML 词表, 永不被标签调用. 片段排序纪律: 稳定在前(persona →
protocol → creator), 易变在后(capability).
"""
from .capability import Capability
from .creator import Creator
from .persona import Persona
from .protocol import Protocol

__all__ = ['Capability', 'Creator', 'Persona', 'Protocol']
