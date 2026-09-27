"""AgenticCondenser — LLM 摘要策略.

head (早期消息) 送 LLM 做结构化摘要, tail (最近消息) 原样保留.
摘要 prompt 对齐 cline 的 Goal/State/Highlights/Next/Files 结构.
"""
from __future__ import annotations

from typing import Any

from routine.logger import setup_logger

from .._summary_llm import summarize_with_llm
from .base import (
    CondenseConfig,
    CondenseResult,
    estimate_items_tokens,
    find_cut_index,
)

_log = setup_logger('prime.condenser.agentic')

# 摘要 prompt — 对齐 cline 的结构化模板 (Goal/State/Highlights/Next/Files)
# prompt 本身是中文, LLM 自然用中文回复; 不含代码片段/原始 URL/文件行号
_SUMMARY_SYSTEM = (
    '你是对话压缩助手。把提供的编码会话压缩成简洁的续接笔记,'
    '信息密集,不要啰嗦。'
)

_SUMMARY_USER_TEMPLATE = """总结本次会话,便于后续续接。务必简洁、基于事实。

约束:
- 不要包含代码片段
- 不要包含原始 URL
- 不要包含文件行号 (文件路径可以保留)

## 目标
一句话: 正在构建或修复什么.

## 进度
- 已完成: 完成的步骤
- 进行中: 当前工作
- 阻塞: 阻塞点或未决问题

## 要点
关键技术决策或重要发现 (无则省略).

## 下一步
紧接着要做的步骤.

## 文件
读取: {read_files}
修改: {modified_files}

## 对话
{conversation}
"""


def _serialize_items(items: list[dict[str, Any]]) -> str:
    """把 items 序列化成给 LLM 的对话文本."""
    lines: list[str] = []
    for item in items:
        role = item.get('role') or item.get('type') or 'unknown'
        content = item.get('content') or item.get('output') or item.get('arguments') or ''
        if isinstance(content, dict):
            content = str(content)
        content = str(content)
        # 截断超长单条 (避免一条 tool output 吃满摘要预算)
        if len(content) > 2000:
            content = content[:1500] + '\n...[truncated]...\n' + content[-500:]
        lines.append(f'[{role}] {content}')
    return '\n'.join(lines)


def _extract_files(items: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """从 items 中提取读过的文件和改过的文件."""
    read_files: list[str] = []
    modified_files: list[str] = []
    for item in items:
        typ = item.get('type') or ''
        name = item.get('name') or ''
        args = item.get('arguments') or ''
        if isinstance(args, str):
            try:
                import json
                args_dict = json.loads(args) if args else {}
            except Exception:
                args_dict = {}
        else:
            args_dict = args or {}

        if name in ('Read', 'read_file') and 'file_path' in args_dict:
            fp = str(args_dict['file_path'])
            if fp not in read_files:
                read_files.append(fp)
        elif name in ('Edit', 'Write', 'edit_file', 'write_file') and 'file_path' in args_dict:
            fp = str(args_dict['file_path'])
            if fp not in modified_files:
                modified_files.append(fp)

    return read_files, modified_files


class AgenticCondenser:
    """LLM 摘要: head 送 LLM 压缩 + tail 原样保留."""

    def __init__(self, model_key: str) -> None:
        self._model_key = model_key

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
            return CondenseResult(
                items=list(items),
                summary='',
                tokens_after=current_tokens,
                cut_index=0,
            )

        head = items[:cut]
        tail = items[cut:]

        read_files, modified_files = _extract_files(head)
        conversation = _serialize_items(head)

        prompt = _SUMMARY_USER_TEMPLATE.format(
            read_files=', '.join(read_files) if read_files else 'none',
            modified_files=', '.join(modified_files) if modified_files else 'none',
            conversation=conversation or '(empty)',
        )

        try:
            summary = await summarize_with_llm(
                model_key=self._model_key,
                system=_SUMMARY_SYSTEM,
                user_prompt=prompt,
                max_tokens=config.summary_max_tokens,
                previous_response_id=fork_response_id,
            )
        except Exception as exc:
            _log.warning('agentic summarize failed (%s), falling back to basic', exc)
            # 降级: 用 basic 策略的简单摘要
            summary = (
                f'[LLM 摘要失败: {exc}. '
                f'已截断前 {len(head)} 条消息.]'
            )

        summary_msg = {'role': 'user', 'content': f'Context summary:\n\n{summary}'}
        new_items = [summary_msg, *tail]
        return CondenseResult(
            items=new_items,
            summary=summary,
            tokens_after=estimate_items_tokens(new_items),
            cut_index=cut,
        )
