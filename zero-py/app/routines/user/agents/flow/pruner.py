"""FlowPruner ---- dsh compaction-tool-result-pruner 的 routine 形态.

免模型的上下文减压: 超阈值时把**保护窗口之外**的旧 function_call_output
截断 (保留首尾), 不动对话正文. 与 LLM 摘要 (flow_compactor) 的关系:
pruner 先跑, 剪完重测仍超才轮到摘要 ---- 便宜的先来.

无状态纯服务: items 快照进, edits (call_id -> 截断 output) 出.
调用方职责: apply_prune(edits) 落定事件 (表面替换由派生自动生效).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from .context import CondenseConfig, estimate_items_tokens

_log = setup_logger('flow.pruner')

# 单条 output 截断保留的字符预算 (首 + 尾)
_KEEP_HEAD = 800
_KEEP_TAIL = 400
_TRUNCATION_NOTE = '\n...[pruned {dropped} chars]...\n'


class FlowPrunerInput(BaseModel):
    items: list[dict[str, Any]] = Field(description='调用方表面 items 快照.')
    max_context: int = Field(description='模型上下文窗口.')
    force: bool = Field(False, description='绕过阈值 (溢出恢复链路).')
    condense_config: dict[str, Any] = Field(
        default_factory=dict, description='CondenseConfig 覆盖项.')


class FlowPrunerOutput(BaseModel):
    pruned: bool = False
    edits: dict[str, str] = Field(default_factory=dict,
                                  description='{call_id: 截断后 output}')
    tokens_before: int = 0
    tokens_after: int = 0
    over_threshold_after: bool = False   # 剪完仍超 → 调用方接着走 LLM 摘要


class FlowPruner(Routine):
    ID = 'flow_pruner'
    SCHEMA = 'FlowPruner: 免模型裁剪旧工具结果 (上下文减压第一环).'

    _KIND: ClassVar[str] = 'flow_pruner'

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = FlowPrunerInput.model_validate(kwargs)
        cfg = CondenseConfig(**(inp.condense_config or {}))
        tokens = estimate_items_tokens(inp.items)
        threshold = int(inp.max_context * cfg.trigger_ratio)
        if not inp.force and tokens < threshold:
            return FlowPrunerOutput(tokens_before=tokens).model_dump()

        # 保护窗口: 尾部 preserve_recent_tokens 对应的最近 items 不剪
        keep_from = _protected_start(inp.items, cfg.preserve_recent_tokens)
        edits: dict[str, str] = {}
        pruned_items: list[dict[str, Any]] = []
        for idx, item in enumerate(inp.items):
            if (item.get('type') == 'function_call_output'
                    and idx < keep_from):
                output = str(item.get('output') or '')
                if len(output) > _KEEP_HEAD + _KEEP_TAIL:
                    short = (output[:_KEEP_HEAD]
                             + _TRUNCATION_NOTE.format(
                                 dropped=len(output) - _KEEP_HEAD - _KEEP_TAIL)
                             + output[-_KEEP_TAIL:])
                    edits[str(item.get('call_id') or '')] = short
                    pruned_items.append({**item, 'output': short})
                    continue
            pruned_items.append(item)

        if not edits:
            return FlowPrunerOutput(
                tokens_before=tokens, tokens_after=tokens,
                over_threshold_after=tokens >= threshold,
            ).model_dump()

        tokens_after = estimate_items_tokens(pruned_items)
        _log.info('pruned %d tool outputs: %d -> %d tokens',
                  len(edits), tokens, tokens_after)
        return FlowPrunerOutput(
            pruned=True,
            edits=edits,
            tokens_before=tokens,
            tokens_after=tokens_after,
            over_threshold_after=tokens_after >= threshold,
        ).model_dump()


def _protected_start(items: list[dict[str, Any]],
                     preserve_tokens: int) -> int:
    """尾部保护窗口起点: 从尾往前累计 token 直到覆盖 preserve_tokens."""
    used = 0
    for i in range(len(items) - 1, -1, -1):
        used += estimate_items_tokens([items[i]])
        if used > preserve_tokens:
            return i + 1
    return 0
