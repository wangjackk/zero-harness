"""CondenserAgent — prime 共用的上下文压缩 routine.

基于 store.py (sqlite):
- 读: 直接查 messages 表 (带 row id), 手动投影 (compaction entry)
- 写: store.write_compaction (追加 compaction entry, replay 时投影)
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Dict

from routine.logger import setup_logger

from .base_routine import (
    BaseCondenserRoutine,
    CondenseInput,
    CondenseLoadResult,
    CondenseOutput,
    CondenseResult,
)
from ..store import get_store

_log = setup_logger('condenser.agent')


class CondenserAgent(BaseCondenserRoutine):
    """reactor/prime 上下文压缩 routine (基于 store).

    读 messages 表 → 投影 (最后一个 compaction entry) → 转 items →
    执行策略 → write_compaction (追加新 compaction entry).

    compaction entry 不删原始消息, replay 时按 preserve_from_id 投影.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'tool': False,
        'readonly': False,
        'input_schema': CondenseInput.model_json_schema(),
        'output_schema': CondenseOutput.model_json_schema(),
        'description': (
            'reactor/prime 上下文压缩 routine. 读 store messages, '
            '应用压缩策略, 写 compaction entry.'
        ),
    }

    async def _load_items(self, inp: CondenseInput) -> CondenseLoadResult:
        """读 store messages 表 + 投影 + 转 items (带 row id + response_id)."""
        store = get_store()
        store.flush()

        with store._tx() as c:
            rows = c.execute(
                'SELECT id, type, data FROM messages '
                'WHERE agent_id = ? AND session_id = ? ORDER BY id ASC',
                (inp.agent_id, inp.session_id),
            ).fetchall()

        # 找最后一个 compaction entry
        last_compaction: dict[str, Any] | None = None
        last_compaction_row_id = 0
        for row in rows:
            try:
                entry = json.loads(row['data'])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(entry, dict) and entry.get('type') == 'compaction':
                last_compaction = entry
                last_compaction_row_id = int(row['id'])

        items: list[dict[str, Any]] = []
        items_message_ids: list[str] = []
        items_response_ids: list[str | None] = []
        current_response_id: str | None = None

        def _process_row(row: Any, skip_compaction: bool = False) -> None:
            """处理单行: 转 item + 收集 message_id + 跟踪 response_id."""
            nonlocal current_response_id
            try:
                entry = json.loads(row['data'])
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(entry, dict):
                return
            typ = entry.get('type')
            row_id_str = str(int(row['id']))

            if typ == 'compaction':
                return
            if typ == 'response_checkpoint':
                rid = str(entry.get('response_id') or '')
                if rid:
                    current_response_id = rid
                return
            if typ in ('session_start', 'session_state', 'todo_update'):
                return

            if typ == 'user':
                items.append({'role': 'user', 'content': str(entry.get('content') or '')})
                items_message_ids.append(row_id_str)
                items_response_ids.append(current_response_id)
            elif typ == 'assistant':
                content = str(entry.get('content') or '')
                if content:
                    items.append({'role': 'assistant', 'content': content})
                    items_message_ids.append(row_id_str)
                    items_response_ids.append(current_response_id)
            elif typ == 'function_call':
                items.append({
                    'type': 'function_call',
                    'name': str(entry.get('name') or ''),
                    'arguments': str(entry.get('arguments') or ''),
                    'call_id': str(entry.get('call_id') or ''),
                })
                items_message_ids.append(row_id_str)
                items_response_ids.append(current_response_id)
            elif typ == 'function_call_output':
                items.append({
                    'type': 'function_call_output',
                    'call_id': str(entry.get('call_id') or ''),
                    'output': str(entry.get('output') or ''),
                })
                items_message_ids.append(row_id_str)
                items_response_ids.append(current_response_id)

        if last_compaction is not None:
            preserve_from_id = int(last_compaction.get('preserve_from_id') or 0)
            summary = str(last_compaction.get('summary') or '')
            if summary:
                items.append({'role': 'user', 'content': f'Context summary:\n\n{summary}'})
                items_message_ids.append(str(last_compaction_row_id))
                items_response_ids.append(None)
            for row in rows:
                row_id = int(row['id'])
                if row_id == last_compaction_row_id:
                    continue
                if row_id < preserve_from_id:
                    continue
                _process_row(row)
        else:
            for row in rows:
                _process_row(row)

        _log.info(
            'load: agent=%s session=%s items=%d (compaction=%s)',
            inp.agent_id, inp.session_id, len(items),
            last_compaction_row_id if last_compaction else 'none',
        )
        return CondenseLoadResult(
            items=items,
            items_message_ids=items_message_ids,
            items_response_ids=items_response_ids,
        )

    async def _write_summary(
        self,
        inp: CondenseInput,
        result: CondenseResult,
        covered_from: str,
        covered_to: str,
        tokens_before: int,
    ) -> None:
        """写 compaction entry 到 store messages 表.

        covered_to 是 head 段最后一条消息的 row_id (str), 转 int 作为
        preserve_from_id: 该 id 之前的消息被 summary 替代, 之后的保留.
        """
        store = get_store()
        preserve_from_id = int(covered_to) if covered_to.isdigit() else 0
        # preserve_from_id 之后的保留, 所以 +1 (covered_to 本身被替代)
        if preserve_from_id > 0:
            preserve_from_id += 1
        store.write_compaction(
            inp.agent_id, inp.session_id,
            summary=result.summary,
            preserve_from_id=preserve_from_id,
            strategy=inp.strategy,
            tokens_before=tokens_before,
            tokens_after=result.tokens_after,
        )
        store.flush()
