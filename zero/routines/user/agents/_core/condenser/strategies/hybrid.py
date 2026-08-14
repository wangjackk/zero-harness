"""HybridCondenser — 混合策略.

先用 BasicCondenser 兜底 (快, 无 LLM), 检查结果是否达标.
如果不达标 (仍超 target), 再上 AgenticCondenser (LLM 摘要).
"""
from __future__ import annotations

from typing import Any

from routine.logger import setup_logger

from .agentic import AgenticCondenser
from .base import (
    CondenseConfig,
    CondenseResult,
)
from .basic import BasicCondenser

_log = setup_logger('prime.condenser.hybrid')


class HybridCondenser:
    """先 Basic 兜底, 不够再 Agentic."""

    def __init__(self, model_key: str) -> None:
        self._basic = BasicCondenser()
        self._agentic = AgenticCondenser(model_key=model_key)

    async def condense(
        self,
        items: list[dict[str, Any]],
        current_tokens: int,
        max_context: int,
        config: CondenseConfig,
        *,
        fork_response_id: str | None = None,
    ) -> CondenseResult:
        target = int(max_context * config.target_ratio)

        # 第一轮: Basic
        result = await self._basic.condense(
            items, current_tokens, max_context, config,
            fork_response_id=fork_response_id,
        )

        if result.tokens_after <= target:
            _log.info(
                'hybrid: basic sufficient (%d -> %d, target %d)',
                current_tokens, result.tokens_after, target,
            )
            return result

        # 第二轮: Basic 不够, 上 Agentic
        _log.info(
            'hybrid: basic insufficient (%d -> %d, target %d), escalating to agentic',
            current_tokens, result.tokens_after, target,
        )
        agentic_result = await self._agentic.condense(
            items, current_tokens, max_context, config,
            fork_response_id=fork_response_id,
        )
        return agentic_result
