"""asr —— 耳朵包(自实现, 非总线下游).

- ``sauc.py``: 豆包(火山 SAUC)流式识别的二进制 WS 协议(组包/解包);
- ``ear.py``: 器官实现 —— 麦克风采集 + 常驻识别流 + 断线重连 + 半双工闸门;
- ``routine.py``: routine 面 —— 定稿分句 → mindflow.declare, 诊断 req。

不依赖设备总线: 麦克风在本机(``sounddevice``), 识别在豆包(``websockets``)。
"""
from .ear import Ear, EarEvent
from .routine import Asr, AsrInput

__all__ = ['Asr', 'AsrInput', 'Ear', 'EarEvent']
