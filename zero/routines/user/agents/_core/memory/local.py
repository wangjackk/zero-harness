"""LocalContextProvider — 本地 sqlite + condenser_agent 实现.

持有内存态消息列表 (_items), 通过 SessionWriter 持久化到 sqlite,
压缩走 condenser_agent routine (跨 wire).

本地 DB 是 source of truth.

从 Conversation 拆出消息管理 + 持久化, 去掉 response_id 管理.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from routine.logger import setup_logger

_log = setup_logger('prime.memory.local')


class LocalContextProvider:
    """本地上下文 provider: sqlite 持久化 + condenser 压缩.

    内存态 _items 是 Responses API input 格式 (跟旧 Conversation 一致).
    持久化通过 SessionWriter (append-only event log).
    """

    def __init__(
        self,
        *,
        writer: Any,
        max_items: int | None = 80,
    ) -> None:
        self._writer = writer
        self._max_items = max_items
        self._items: list[dict] = []

    @property
    def enabled(self) -> bool:
        return True

    # --- 生命周期 ---

    async def init_session(self, session_id: str) -> None:
        """Local 无需初始化 (EventLog 由 SessionStore.open replay)."""

    async def finalize_session(self) -> None:
        """Local 无需收尾 (EventLog 由 SessionWriter.close 管理)."""

    # --- 写入 ---

    def append_user(self, text: str) -> None:
        self._items.append({'role': 'user', 'content': text})
        if self._writer:
            self._writer.append({'type': 'user', 'content': text, 'uuid': uuid4().hex})
        self._maybe_trim()

    def append_assistant(self, text: str) -> None:
        if not text:
            return
        self._items.append({'role': 'assistant', 'content': text})
        if self._writer:
            self._writer.append({'type': 'assistant', 'content': text, 'uuid': uuid4().hex})

    def append_function_call(
        self, name: str, arguments: str, call_id: str,
    ) -> None:
        self._items.append({
            'type': 'function_call',
            'name': name,
            'arguments': arguments,
            'call_id': call_id,
        })
        if self._writer:
            self._writer.write_function_call(name, arguments, call_id)

    def append_function_output(
        self, call_id: str, output: str,
        raw_result: Any | None = None,
    ) -> None:
        self._items.append({
            'type': 'function_call_output',
            'call_id': call_id,
            'output': output,
        })
        if self._writer:
            self._writer.write_function_output(call_id, output, raw_result)

    # --- 读取 ---

    def items(self) -> list[dict]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    # --- 持久化屏障 ---

    def flush(self) -> None:
        if self._writer:
            self._writer.flush()

    # --- 压缩 ---

    async def compact(
        self,
        *,
        agent_id: str,
        session_id: str,
        model_key: str,
        max_context: int,
        plan_mode: bool,
        condense_config: dict[str, Any],
        project_root: str | None,
        cwd: str | None,
        call: Any,
    ) -> dict[str, Any] | None:
        """flush → condenser_agent → store.replay → 替换 _items.

        压缩完全本地化 (走 condenser_agent).
        返回 response_state (供 ResponseTracker 恢复) 或 None.
        """
        if plan_mode:
            return None
        if max_context <= 0:
            return None
        if len(self._items) < 2:
            return None

        # 1. flush 确保 DB 有最新消息
        self.flush()

        # 2. 调压缩 agent (走 wire, 跨 conn 路由)
        try:
            result = await call('condenser_agent', {
                'agent_id': agent_id,
                'session_id': session_id,
                'model_key': model_key,
                'strategy': 'hybrid',
                'config': condense_config,
            })
        except Exception as exc:
            _log.warning('condenser call failed: %s (skipping)', exc)
            return None

        if not isinstance(result, dict) or not result.get('condensed'):
            return None

        _log.info(
            'context condensed: %d -> %d tokens (strategy=%s)',
            result.get('tokens_before', 0),
            result.get('tokens_after', 0),
            result.get('strategy', ''),
        )

        # 3. 从 DB 重新加载 (replay 会走 compaction 投影)
        from ..store import get_store
        store = get_store()
        _, items, response_state = store.replay(
            agent_id, session_id,
            cwd=cwd,
            project_root=project_root,
            model=model_key,
            plan_mode=plan_mode,
        )
        self._items = list(items)
        return response_state

    # --- 长期记忆 ---

    async def find(self, query: str, limit: int = 5) -> str:
        """本地模式不支持语义检索."""
        return '本地模式不支持语义检索'

    # --- 增量推送 ---

    def tick(self) -> None:
        """本地模式无增量推送需求, no-op."""

    # --- 内部 ---

    def load_items(self, items: list[dict]) -> None:
        """从 replay 结果加载内存态 (不触发 writer)."""
        self._items = list(items)
        self._maybe_trim()

    def _maybe_trim(self) -> None:
        """超出 max_items 时裁剪到最近 max_items 条 (从第一个 user/assistant 起)."""
        if not self._max_items or len(self._items) <= self._max_items:
            return
        drop = len(self._items) - self._max_items
        while drop < len(self._items):
            if self._items[drop].get('role') in ('user', 'assistant'):
                break
            drop += 1
        self._items = self._items[drop:]
