"""FlowCompactor ---- dsh compaction-basic 的 routine 形态.

dsh "压缩是独立插件, 监听 agent 事件" 在 zero 的对应物: 压缩是一个
routine, agent 经 call() 调用 (无状态纯服务, 表面快照进, 压缩结果出).
落盘不在这一层 ---- 调用方拿到结果后写 compaction/replacement 事件,
事件流是唯一真相 (EventLog 派生自动生效).

流程 (对齐 BasicCompactionEngine.compactIfNeeded):
1. 测量/阈值: trigger_ratio × max_context; force 绕过 (溢出恢复)
2. 选区: find_cut_index 尾部保护窗口, 边界 tool-pairing 对齐
3. 摘要: fork 模式优先 (fork_rid 复用主对话服务端状态); 失败降级
   为截断文案 (对齐 dsh AgenticCondenser 降级)

调用方职责: apply_replacement(summary, cut_index) 落定事件.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from .context import CondenseConfig, estimate_items_tokens, find_cut_index
from .llm import Llm, TextDelta, Completed

_log = setup_logger('flow.compactor')

_SUMMARY_SYSTEM = (
    'You are a conversation summarizer for a coding agent. '
    'Produce a dense, factual summary of the conversation so far: '
    'the task, decisions made, files read/modified, tool outcomes that '
    'matter later, and any unresolved questions. Keep all paths, '
    'identifiers and error messages exact. No preamble.'
)

_SUMMARY_TEMPLATE = (
    'Summarize the conversation below for handoff. It will replace the '
    'older part of the context.\n\n'
    'Files read: {read_files}\n'
    'Files modified: {modified_files}\n\n'
    'Conversation:\n{conversation}'
)


class FlowCompactorInput(BaseModel):
    items: list[dict[str, Any]] = Field(description='调用方表面 items 快照.')
    max_context: int = Field(description='模型上下文窗口.')
    model_key: str = Field(description='摘要用模型 key.')
    force: bool = Field(False, description='绕过阈值 (溢出恢复).')
    fork_response_id: str | None = Field(
        None, description='复用主对话服务端状态做摘要 (无则冷上下文).')
    condense_config: dict[str, Any] = Field(
        default_factory=dict, description='CondenseConfig 覆盖项.')


class FlowCompactorOutput(BaseModel):
    condensed: bool = False
    summary: str = ''
    tokens_before: int = 0
    tokens_after: int = 0
    cut_index: int = 0


class FlowCompactor(Routine):
    """无状态压缩服务: items 快照进, 新 items 出."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'tool': False,
        'readonly': False,
        'input_schema': FlowCompactorInput.model_json_schema(),
        'output_schema': FlowCompactorOutput.model_json_schema(),
        'description': 'In-process-style compaction as a routine (dsh basic engine).',
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = FlowCompactorInput.model_validate(kwargs)
        if inp.max_context <= 0 or len(inp.items) < 2:
            return FlowCompactorOutput().model_dump()

        cfg = self._config(inp)
        tokens = estimate_items_tokens(inp.items)
        threshold = int(inp.max_context * cfg.trigger_ratio)
        if tokens < threshold:
            return FlowCompactorOutput(tokens_before=tokens).model_dump()

        cut = find_cut_index(inp.items, cfg.preserve_recent_tokens)
        if cut <= 0:
            return FlowCompactorOutput(tokens_before=tokens).model_dump()
        head = inp.items[:cut]

        summary = await self._summarize(head, inp, cfg)
        tokens_after = estimate_items_tokens([
            {'role': 'user', 'content': summary}, *inp.items[cut:],
        ])
        _log.info('compacted: %d -> %d tokens (cut=%d)',
                  tokens, tokens_after, cut)
        return FlowCompactorOutput(
            condensed=True,
            summary=summary,
            tokens_before=tokens,
            tokens_after=tokens_after,
            cut_index=cut,
        ).model_dump()

    @staticmethod
    def _config(inp: FlowCompactorInput) -> CondenseConfig:
        fields = set(CondenseConfig.__dataclass_fields__)
        cfg = CondenseConfig(**{
            k: v for k, v in (inp.condense_config or {}).items()
            if k in fields and v is not None
        })
        if inp.force:
            cfg.trigger_ratio = 0.0   # 阈值归零: 恒触发
        return cfg

    @staticmethod
    async def _summarize(head: list[dict], inp: FlowCompactorInput,
                         cfg: CondenseConfig) -> str:
        read_files, modified_files = _extract_files(head)
        prompt = _SUMMARY_TEMPLATE.format(
            read_files=', '.join(sorted(read_files)) or 'none',
            modified_files=', '.join(sorted(modified_files)) or 'none',
            conversation=_serialize(head) or '(empty)',
        )
        try:
            client = Llm(model=inp.model_key or None)
            parts: list[str] = []
            async for ev in client.stream(
                [{'role': 'user', 'content': prompt}],
                instructions=_SUMMARY_SYSTEM,
                previous_response_id=inp.fork_response_id,
                max_output_tokens=cfg.summary_max_tokens,
                disable_reasoning=True,
            ):
                if isinstance(ev, TextDelta):
                    parts.append(ev.text)
                elif isinstance(ev, Completed):
                    break
            summary = ''.join(parts).strip()
            if not summary:
                raise RuntimeError('empty summary')
            return summary
        except Exception as exc:
            _log.warning('summarize failed (%r), falling back to truncation', exc)
            return f'[LLM 摘要失败: {exc}. 已截断前 {len(head)} 条消息.]'


def _serialize(items: list[dict]) -> str:
    """items → 紧凑可读文本 (截断超长 output)."""
    lines: list[str] = []
    for it in items:
        t = it.get('type')
        if t == 'function_call':
            lines.append(f"tool {it.get('name')}({it.get('arguments')})")
        elif t == 'function_call_output':
            out = str(it.get('output') or '')
            if len(out) > 2000:
                out = out[:2000] + '…'
            lines.append(f'  -> {out}')
        else:
            role = it.get('role') or '?'
            content = str(it.get('content') or '')
            lines.append(f'{role}: {content}')
    return '\n'.join(lines)


def _extract_files(items: list[dict]) -> tuple[set[str], set[str]]:
    """从工具调用参数里抽 file_path (读类/写类启发式)."""
    read_files: set[str] = set()
    modified_files: set[str] = set()
    for it in items:
        if it.get('type') != 'function_call':
            continue
        name = str(it.get('name') or '').lower()
        try:
            args = json.loads(it.get('arguments') or '{}')
        except (json.JSONDecodeError, TypeError):
            continue
        path = args.get('file_path') or args.get('path')
        if not isinstance(path, str) or not path:
            continue
        if any(k in name for k in ('write', 'edit', 'delete', 'create', 'move')):
            modified_files.add(path)
        else:
            read_files.add(path)
    return read_files, modified_files
