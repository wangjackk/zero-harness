"""SessionWriter -- agent event writer (sqlite backend).

Replaces the append-only JSONL writer. Routes every event through the process
Store (agent/store.py), which owns the background writer thread + queue. Public
API is unchanged so conversation.py / session.py / _agent.py keep working.

Event types (one row each in messages):
  user / assistant / function_call / function_call_output
  session_start / session_state / todo_update / response_checkpoint / session_end

Each entry is stamped with ts (ISO 8601 utc) + a stable uuid (call_id-derived
for FC/FCO, else random) by Store.insert_message.
"""
from __future__ import annotations

from typing import Any

from .store import get_store


class SessionWriter:
    """append agent session events into the sqlite Store.

    usage::

        writer = SessionWriter('abc123', agent_id='main')
        writer.write_user('hello')
        writer.close({'total_tokens': 1234})
    """

    def __init__(
        self,
        session_id: str,
        *,
        agent_id: str,
        cwd: str | None = None,
        model: str | None = None,
        plan_mode: bool = False,
    ) -> None:
        self.session_id = session_id
        self._agent_id = agent_id
        self._cwd = cwd
        # centralized store: ~/.zero/sessions.db.
        self._store = get_store()
        self.append({
            'type': 'session_start',
            'model': model,
            'plan_mode': plan_mode,
            'cwd': cwd,
        })

    @property
    def location(self) -> str:
        """human-readable handle for logging (replaces the old .path)."""
        return f'db:{self._store.path}#{self._agent_id}/{self.session_id}'

    # keep .path as an accessor for any residual readers; returns the db path so
    # log lines still name a real artifact.
    @property
    def path(self):
        return self._store.path

    def append(self, entry: dict[str, Any]) -> None:
        """queue one event row (non-blocking)."""
        self._store.insert_message(self._agent_id, self.session_id, entry)

    def write_user(self, content: str) -> None:
        self.append({'type': 'user', 'content': content})

    def write_assistant(self, content: str) -> None:
        if content:
            self.append({'type': 'assistant', 'content': content})

    def write_function_call(self, name: str, arguments: str, call_id: str) -> None:
        self.append({
            'type': 'function_call',
            'name': name,
            'arguments': arguments,
            'call_id': call_id,
        })

    def write_function_output(
        self,
        call_id: str,
        output: str,
        raw_result: Any | None = None,
    ) -> None:
        """记录工具输出.

        output     - for_llm 过滤后的字符串 (喂给 LLM 的, 对齐旧格式)
        raw_result - 原始工具返回值 (dict/str/None), 供前端展示完整结果
                     (含 cwd/exit_code 等元数据). None 时不存.

        全量存储, 不在写入层截断. DB 是 source of truth, 截断应由消费方
        (给 LLM 的 for_llm 过滤 / 压缩 agent 的 token 预算控制) 按需做.
        """
        entry: dict[str, Any] = {
            'type': 'function_call_output',
            'call_id': call_id,
            'output': output,
        }
        if raw_result is not None:
            entry['raw_result'] = raw_result
        self.append(entry)

    def write_response_checkpoint(
        self,
        response_id: str,
        text: str = '',
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.append({
            'type': 'response_checkpoint',
            'response_id': response_id,
            'text': text,
            'usage': usage,
        })

    def write_compaction(
        self,
        summary: str,
        preserve_from_id: int,
        strategy: str,
        tokens_before: int = 0,
        tokens_after: int = 0,
    ) -> None:
        """追加一个 compaction entry. 不删除已有消息, replay 时投影."""
        self.append({
            'type': 'compaction',
            'strategy': strategy,
            'summary': summary,
            'preserve_from_id': preserve_from_id,
            'tokens_before': tokens_before,
            'tokens_after': tokens_after,
        })

    def flush(self) -> None:
        """阻塞直到所有入队写操作都落盘. 调压缩 agent 前必须调."""
        self._store.flush()

    def write_todo_update(self, old_todos: list[dict[str, Any]], new_todos: list[dict[str, Any]]) -> None:
        self.append({
            'type': 'todo_update',
            'old_todos': old_todos,
            'new_todos': new_todos,
        })

    def write_state_snapshot(self, state: dict[str, Any]) -> None:
        self.append({
            'type': 'session_state',
            'state': state,
        })

    def close(self, usage: dict[str, Any] | None = None) -> None:
        """write a session_end marker row."""
        self.append({'type': 'session_end', 'usage': usage or {}})
