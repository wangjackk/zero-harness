"""flow 自有的上下文度量: token 估算 + 压缩切点 + 配置.

粗估模型 (OpenAI tokenizer 经验值): ASCII ~4 字符/token, CJK ~1
字符/token, 按 CJK 密度加权. 只按 content/output/arguments 文本算,
不含 JSON 结构开销 ---- 内部相对比较用, 一致性比绝对精度重要.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_CJK_RE = re.compile(
    r'[\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff'
    r'\uf900-\ufaff\uff00-\uffef]'
)


@dataclass
class CondenseConfig:
    """压缩配置 (manager/preset 可覆盖)."""

    trigger_ratio: float = 0.5        # 占 max_context 比例达到即触发
    preserve_recent_tokens: int = 8_000   # 尾部保护窗口
    summary_max_tokens: int = 4096    # 摘要输出上限


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    return cjk + (len(text) - cjk + 3) // 4


def _item_text(item: dict[str, Any]) -> str:
    text = item.get('content') or item.get('output') or item.get('arguments') or ''
    return str(text) if not isinstance(text, str) else text


def estimate_items_tokens(items: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(_item_text(it)) for it in items)


def find_cut_index(items: list[dict[str, Any]],
                   preserve_recent_tokens: int) -> int:
    """从尾向前累计 token 找切割点, items[:cut] 摘掉 / [cut:] 保留.

    边界向前对齐到消息边界 ---- 不在 function_call(_output) 对中间
    切开 (dsh tool-pairing 平衡: 调用与结果不拆).
    """
    if not items:
        return 0
    tokens = 0
    cut = len(items)
    for i in range(len(items) - 1, -1, -1):
        t = estimate_tokens(_item_text(items[i]))
        if tokens + t > preserve_recent_tokens:
            break
        tokens += t
        cut = i
    while cut > 0 and items[cut - 1].get('type') in (
            'function_call', 'function_call_output'):
        cut -= 1
    return cut
