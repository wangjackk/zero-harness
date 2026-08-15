"""BasicCondenser — 无 LLM 截断策略.

保留最近 N token 的消息原样, 更早的消息直接丢弃.
速度快, 不额外调 LLM, 但会丢失早期上下文. 适合:
- 压缩预算紧张时兜底
- LLM 摘要失败时降级
"""
from __future__ import annotations

from typing import Any

from .base import (
    CondenseConfig,
    CondenseResult,
    estimate_items_tokens,
    find_cut_index,
)


class BasicCondenser:
    """无 LLM 截断: 保留最近 N token, 丢弃更早的消息."""

    async def condense(
        self,
        items: list[dict[str, Any]],
        current_tokens: int,
        max_context: int,
        config: CondenseConfig,
        *,
        fork_response_id: str | None = None,
    ) -> CondenseResult:
        cut = find_cut_index(items, config.preserve_recent_tokens)
        if cut <= 0:
            # 全部保留 (极少见: preserve_recent 覆盖了全部)
            return CondenseResult(
                items=list(items),
                summary='',
                tokens_after=current_tokens,
                cut_index=0,
            )
        head = items[:cut]
        tail = items[cut:]
        summary = (
            f'[已截断前 {len(head)} 条消息以适配上下文窗口,'
            f'完整历史见 DB.]'
        )
        summary_msg = {'role': 'user', 'content': f'Context summary:\n\n{summary}'}
        new_items = [summary_msg, *tail]
        return CondenseResult(
            items=new_items,
            summary=summary,
            tokens_after=estimate_items_tokens(new_items),
            cut_index=cut,
        )
