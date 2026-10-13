"""LLM 摘要调用封装 — 收集 stream 的 TextDelta 拼成完整文本.

轻量封装, 不走完整 agent react 循环. 只用于 condenser 的 LLM 摘要.
"""
from __future__ import annotations

from typing import Any

from routine.logger import setup_logger

from ..llm import LLMClient, TextDelta, ReasoningDelta, Completed

_log = setup_logger('prime.condenser.llm')


async def summarize_with_llm(
    *,
    model_key: str,
    system: str,
    user_prompt: str,
    max_tokens: int = 1024,
    previous_response_id: str | None = None,
) -> str:
    """调 LLM 做摘要, 返回完整文本.

    fork 模式: 传 previous_response_id 复用主对话 head 的服务端状态,
    只发 1 条 user 消息 (摘要指令), 不重发历史. 摘要 response_id 丢弃,
    不污染主对话的 response 链.

    用非流式语义 (收集所有 TextDelta). 不带 tools.
    失败抛异常, 由调用方 (AgenticCondenser) 降级处理.
    """
    client = LLMClient(model=model_key)
    input_items: list[dict[str, Any]] = [
        {'role': 'user', 'content': user_prompt},
    ]

    parts: list[str] = []
    async for ev in client.stream(
        input_items,
        instructions=system,
        tools=None,
        previous_response_id=previous_response_id,
        max_output_tokens=max_tokens,
        disable_reasoning=True,
    ):
        if isinstance(ev, TextDelta):
            parts.append(ev.text)
        elif isinstance(ev, Completed):
            # 流结束
            break

    summary = ''.join(parts).strip()
    if not summary:
        raise RuntimeError('LLM returned empty summary')
    _log.info(
        'summarize_with_llm: %d chars (model=%s, fork=%s)',
        len(summary), model_key, bool(previous_response_id),
    )
    return summary
