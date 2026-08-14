"""Format stored session entries into general chat messages.

The store logs events (user/assistant/function_call/function_call_output/...).
We reconstruct the visible conversation as general messages (role/text/results):

  user            -> {role:'user', text}
  assistant       -> {role:'assistant', text}
  FC burst + FCO  -> one {role:'tool', results:[...]} block, ordered to follow
                     the assistant text that triggered the round

Within one LLM response the agent emits text first, then its tool calls, then
their outputs. Stored order is therefore: assistant(text), FC1, FC2, ..., FCO1,
FCO2, .... We pair FC <-> FCO by call_id and group a contiguous burst into a
single tool block (matching how the live agent's emit_feedback sends all tools
of a round in one feedback event).

Entries that are not message-relevant (session_start/session_state/todo_update/
response_checkpoint/session_end) are skipped.
"""
from __future__ import annotations

import json
from typing import Any


def entries_to_messages(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """convert stored session entries into general chat messages."""
    out: list[dict[str, Any]] = []
    # call_id -> {'name','arguments','input'} for FCs awaiting their output.
    pending: dict[str, dict[str, Any]] = {}
    # ordered list of FCs in the current burst; each gets a result slot.
    order: list[str] = []

    def flush() -> None:
        if not order:
            return
        results: list[dict[str, Any]] = []
        for cid in order:
            fc = pending.get(cid)
            if fc is None:
                continue
            # 优先用 raw_result (原始工具返回值, 含 cwd/exit_code 等元数据) 给前端;
            # 老数据没有 raw_result 时 fallback 到 output (for_llm 过滤后的字符串).
            result_val = fc.get('raw_result', fc.get('output'))
            results.append({
                'name': fc.get('name') or '',
                'input': _safe_json(fc.get('arguments')),
                'result': result_val,
                'call_id': cid,
                'status': 'done',
            })
        if results:
            out.append({
                'id': f'tool-{results[0].get("call_id") or len(out)}',
                'role': 'tool',
                'text': '',
                'results': results,
                'final': True,
            })
        pending.clear()
        order.clear()

    for entry in entries:
        typ = entry.get('type')
        if typ == 'user':
            flush()
            content = str(entry.get('content') or '')
            if content:
                out.append({
                    'id': f'hist-user-{entry.get("uuid") or len(out)}',
                    'role': 'user',
                    'text': content,
                    'final': True,
                })
        elif typ == 'assistant':
            flush()
            content = str(entry.get('content') or '')
            if content:
                out.append({
                    'id': f'hist-assistant-{entry.get("uuid") or len(out)}',
                    'role': 'assistant',
                    'text': content,
                    'final': True,
                })
        elif typ == 'function_call':
            call_id = str(entry.get('call_id') or '')
            if not call_id:
                continue
            pending[call_id] = {
                'name': str(entry.get('name') or ''),
                'arguments': str(entry.get('arguments') or ''),
            }
            order.append(call_id)
        elif typ == 'function_call_output':
            call_id = str(entry.get('call_id') or '')
            fc = pending.get(call_id)
            if fc is not None:
                fc['output'] = str(entry.get('output') or '')
                # raw_result 是工具原始返回值 (dict/str), 前端展示完整结果用;
                # 老数据没有这个字段, flush 时 fallback 到 output.
                if 'raw_result' in entry:
                    fc['raw_result'] = entry.get('raw_result')
        # session_start / session_state / todo_update / response_checkpoint /
        # session_end: not panel-relevant.

    flush()
    return out


def _safe_json(raw: Any) -> Any:
    """best-effort parse a tool arguments string into a dict; pass through on fail."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
