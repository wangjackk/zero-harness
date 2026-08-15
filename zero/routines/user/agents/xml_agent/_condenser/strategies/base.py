"""压缩策略接口 + token 估算工具.

所有策略实现 CondenserStrategy 协议. 策略对象是无状态的(除配置),
接收 items 列表 + token 信息, 返回压缩结果.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CondenseConfig:
    """压缩配置 — 通过 agent 创建参数或 config.yaml 注入."""

    trigger_ratio: float = 0.8       # 占 max_context 的比例达到此值时触发
    target_ratio: float = 0.5        # 压缩后目标占比
    preserve_recent_tokens: int = 8_000  # 保留最近这么多 token 不被摘要 (对齐 opencode)
    summary_max_tokens: int = 4096   # LLM 摘要输出上限 (对齐 opencode)


@dataclass
class CondenseResult:
    """压缩策略的返回值."""

    items: list[dict[str, Any]]      # 压缩后的 items (summary_msg + tail)
    summary: str                     # 摘要文本
    tokens_after: int                # 压缩后估算 token 数
    cut_index: int                   # 切割点 (items[:cut_index] 被摘要, [cut_index:] 保留)


@runtime_checkable
class CondenserStrategy(Protocol):
    """压缩策略接口 — 所有策略实现这个协议.

    fork_response_id: head 最后一条 response_checkpoint 的 response_id.
    策略可用它复用主对话服务端状态做摘要 (不重发 head 历史).
    None 时策略应降级为重发 head.
    """

    async def condense(
        self,
        items: list[dict[str, Any]],
        current_tokens: int,
        max_context: int,
        config: CondenseConfig,
        *,
        fork_response_id: str | None = None,
    ) -> CondenseResult:
        ...


# ──────────────────────────────────────────────────────────────────────────────
# token 估算
# ──────────────────────────────────────────────────────────────────────────────

# CJK Unicode 范围 (粗略, 覆盖中日韩常见字符)
_CJK_RE = re.compile(
    r'[\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff'
    r'\uf900-\ufaff\uff00-\uffef]'
)


def estimate_tokens(text: str) -> int:
    """粗估 token 数.

    ASCII 文本约 4 字符/token (对齐 OpenAI tokenizer 经验值);
    CJK 字符约 1 字符/token. 混合文本按 CJK 密度加权.
    """
    if not text:
        return 0
    total = len(text)
    cjk = len(_CJK_RE.findall(text))
    non_cjk = total - cjk
    return cjk + (non_cjk + 3) // 4


def _item_text(item: dict[str, Any]) -> str:
    """从 item 提取主要文本内容 (对齐 opencode, 按 content 算, 不算 JSON 结构)."""
    text = item.get('content') or item.get('output') or item.get('arguments') or ''
    if isinstance(text, dict):
        text = str(text)
    return str(text)


def estimate_item_tokens(item: dict[str, Any]) -> int:
    """估算单条 item 的 token 数 (按 content 文本, 不含 JSON 结构开销)."""
    return estimate_tokens(_item_text(item))


def estimate_items_tokens(items: list[dict[str, Any]]) -> int:
    """估算 items 列表的总 token 数."""
    return sum(estimate_item_tokens(item) for item in items)


def find_cut_index(
    items: list[dict[str, Any]],
    preserve_recent_tokens: int,
) -> int:
    """从尾部向前累加 token, 找到切割点.

    items[cut_index:] 的 token 总数约等于 preserve_recent_tokens.
    切割点对齐到 user/assistant 消息边界 (不在 function_call 中间切).
    """
    if not items:
        return 0
    tokens = 0
    cut = len(items)
    for i in range(len(items) - 1, -1, -1):
        t = estimate_item_tokens(items[i])
        if tokens + t > preserve_recent_tokens:
            break
        tokens += t
        cut = i
    # 向前对齐到 user/assistant 边界, 不在 function_call/output 中间切
    while cut > 0 and items[cut - 1].get('type') in ('function_call', 'function_call_output'):
        cut -= 1
    return cut
