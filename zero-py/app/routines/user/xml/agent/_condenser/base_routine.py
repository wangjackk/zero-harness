"""BaseCondenserRoutine — 上下文压缩 routine 模板基类.

模板方法 run() 封装完整压缩流程:
  读消息 → 投影 → 估算 token → trigger 判断 → 选策略执行 → 写 summary → 返回

子类只需实现两个抽象方法:
  - _load_items(inp) → CondenseLoadResult (读各自 DB + 投影 + 转 items)
  - _write_summary(inp, result, covered_from, covered_to, tokens_before) (写各自 DB)

策略包 (BasicCondenser/AgenticCondenser/HybridCondenser) 共享.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from .strategies import (
    CondenseConfig,
    CondenseResult,
    estimate_items_tokens,
    find_cut_index,
)
from .strategies.basic import BasicCondenser
from .strategies.agentic import AgenticCondenser
from .strategies.hybrid import HybridCondenser
from ..llm import LLMClient

_log = setup_logger('condenser.base')

_STRATEGIES: dict[str, type] = {
    'basic': BasicCondenser,
    'agentic': AgenticCondenser,
    'hybrid': HybridCondenser,
}


# ──────────────────────────────────────────────────────────────────────────────
# 共享 Input / Output schema
# ──────────────────────────────────────────────────────────────────────────────


class CondenseInput(BaseModel):
    agent_id: str = Field(description='Target agent id')
    session_id: str = Field(description='Target session id')
    model_key: str = Field(
        description="LLM model key ('claude' / 'doubao' / 'qwen'). "
        'Used for token estimation (max_context) and LLM summarization.',
    )
    strategy: str = Field(
        default='hybrid',
        description="Condensation strategy: 'basic' / 'agentic' / 'hybrid'",
    )
    config: Dict[str, Any] = Field(
        default_factory=dict,
        description='Override default condense config '
        '(trigger_ratio, target_ratio, preserve_recent_tokens, summary_max_tokens)',
    )


class CondenseOutput(BaseModel):
    condensed: bool = Field(description='Whether condensation was performed')
    strategy: str = Field(default='', description='Strategy used')
    tokens_before: int = Field(default=0, description='Estimated tokens before condensation')
    tokens_after: int = Field(default=0, description='Estimated tokens after condensation')
    summary: str = Field(default='', description='Summary text (empty if not condensed)')
    covered_from: str = Field(default='', description='First covered message_id (empty if not condensed)')
    covered_to: str = Field(default='', description='Last covered message_id (empty if not condensed)')
    reason: str = Field(default='', description='Reason if not condensed')


# ──────────────────────────────────────────────────────────────────────────────
# _load_items 返回结构
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CondenseLoadResult:
    """_load_items 返回的统一结构.

    items: Responses API input 格式 ({role, content} 或 {type: function_call, ...})
    items_message_ids: 每条 item 对应的 message_id (统一 str, 供 covered_from/to 用)
    items_response_ids: 每条 item 最近的 response_id (None if 无, 供 fork 用)
    """
    items: list[dict[str, Any]] = field(default_factory=list)
    items_message_ids: list[str] = field(default_factory=list)
    items_response_ids: list[str | None] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# BaseCondenserRoutine
# ──────────────────────────────────────────────────────────────────────────────


class BaseCondenserRoutine(Routine):
    """上下文压缩 routine 模板基类.

    子类实现 _load_items (读 DB + 投影) 和 _write_summary (写 DB).
    run() 模板方法封装共享的压缩流程.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'tool': False,
        'readonly': False,
        'input_schema': CondenseInput.model_json_schema(),
        'output_schema': CondenseOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CondenseInput(**kwargs)

        # 1. 读消息 + 投影 (子类实现)
        loaded = await self._load_items(inp)
        if not loaded.items:
            return CondenseOutput(
                condensed=False, strategy=inp.strategy,
                reason='no conversational items after projection',
            ).model_dump()

        # 2. 估算 token + 取 max_context
        max_context = int(LLMClient(model=inp.model_key).max_context)
        if max_context <= 0:
            return CondenseOutput(
                condensed=False, strategy=inp.strategy,
                reason=f'unknown max_context for model {inp.model_key!r}',
            ).model_dump()

        current_tokens = estimate_items_tokens(loaded.items)

        # 3. 解析配置
        cfg = CondenseConfig(
            **{
                k: v for k, v in inp.config.items()
                if k in CondenseConfig.__dataclass_fields__
            }
        )

        # 4. trigger 判断
        trigger_threshold = int(max_context * cfg.trigger_ratio)
        if current_tokens < trigger_threshold:
            return CondenseOutput(
                condensed=False, strategy=inp.strategy,
                tokens_before=current_tokens,
                reason=(
                    f'below threshold ({current_tokens} < {trigger_threshold}, '
                    f'trigger_ratio={cfg.trigger_ratio})'
                ),
            ).model_dump()

        _log.info(
            'condense: agent=%s session=%s tokens=%d/%d (trigger=%d) strategy=%s',
            inp.agent_id, inp.session_id, current_tokens, max_context,
            trigger_threshold, inp.strategy,
        )

        # 5. 选策略执行
        strategy_factory = _STRATEGIES.get(inp.strategy)
        if strategy_factory is None:
            return CondenseOutput(
                condensed=False, strategy=inp.strategy,
                tokens_before=current_tokens,
                reason=f'unknown strategy: {inp.strategy!r}',
            ).model_dump()

        _cut = find_cut_index(loaded.items, cfg.preserve_recent_tokens)

        # fork_response_id: head 段最后一条有 response_id 的 item
        fork_response_id: str | None = None
        if _cut > 0:
            for i in range(_cut - 1, -1, -1):
                if i < len(loaded.items_response_ids) and loaded.items_response_ids[i]:
                    fork_response_id = loaded.items_response_ids[i]
                    break

        if inp.strategy in ('agentic', 'hybrid'):
            strategy = strategy_factory(model_key=inp.model_key)
        else:
            strategy = strategy_factory()
        result: CondenseResult = await strategy.condense(
            loaded.items, current_tokens, max_context, cfg,
            fork_response_id=fork_response_id,
        )

        # 6. 算 covered_from / covered_to
        covered_from = loaded.items_message_ids[0] if loaded.items_message_ids else ''
        covered_to = (
            loaded.items_message_ids[_cut - 1]
            if _cut > 0 and _cut <= len(loaded.items_message_ids)
            else (loaded.items_message_ids[-1] if loaded.items_message_ids else '')
        )

        # 7. 写 summary (子类实现)
        await self._write_summary(
            inp, result, covered_from, covered_to, current_tokens,
        )

        _log.info(
            'condense done: agent=%s %d->%d tokens (covered=%s..%s, strategy=%s)',
            inp.agent_id, current_tokens, result.tokens_after,
            covered_from, covered_to, inp.strategy,
        )

        return CondenseOutput(
            condensed=True,
            strategy=inp.strategy,
            tokens_before=current_tokens,
            tokens_after=result.tokens_after,
            summary=result.summary,
            covered_from=covered_from,
            covered_to=covered_to,
        ).model_dump()

    # ── 子类实现 ──

    async def _load_items(self, inp: CondenseInput) -> CondenseLoadResult:
        """读各自 DB + 投影 + 转 items. 子类必须实现."""
        raise NotImplementedError

    async def _write_summary(
        self,
        inp: CondenseInput,
        result: CondenseResult,
        covered_from: str,
        covered_to: str,
        tokens_before: int,
    ) -> None:
        """写 summary 到各自 DB. 子类必须实现."""
        raise NotImplementedError
