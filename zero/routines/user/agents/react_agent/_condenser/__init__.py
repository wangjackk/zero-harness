"""condenser 基础机制 (vendor 自 _core/condenser, 供 ReactCondenserAgent 复用).

- base_routine: BaseCondenserRoutine 模板方法 (trigger/策略/covered_from_to)
- strategies: Basic / Agentic / Hybrid 压缩策略
- _summary_llm: LLM 摘要调用 (走本包 llm.py)

routine.py (prime 版 CondenserAgent, 依赖 prime store) 不随附.
"""
from .base_routine import (
    BaseCondenserRoutine,
    CondenseInput,
    CondenseLoadResult,
    CondenseOutput,
    CondenseResult,
)

__all__ = [
    'BaseCondenserRoutine',
    'CondenseInput',
    'CondenseLoadResult',
    'CondenseOutput',
    'CondenseResult',
]
