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

    # 压缩唯一权威是 compact() (token 比率触发 + condenser 摘要 + compaction
    # 投影). 不做条数裁剪: 工具密集轮次 fc/fco 条数膨胀极快, 条数上限几轮就
    # 触发, 且无声丢弃前部 items 违反"压缩必须走摘要持久化"的原则.
    def __init__(
        self,
        *,
        writer: Any,
    ) -> None:
        self._writer = writer
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

    def append_user(self, text: str, *, images: list[str] | None = None) -> None:
        item = {'role': 'user', 'content': text}
        entry = {'type': 'user', 'content': text, 'uuid': uuid4().hex}
        if images:
            # 图片引用挂 _images (内部字段): content 保持纯文本喂给所有
            # 字符串消费者 (sys_prompt 视图 / condenser 估算); to_request
            # 发 LLM 时才剥掉并组装 input_image blocks.
            item['_images'] = list(images)
            entry['images'] = list(images)
        self._items.append(item)
        if self._writer:
            self._writer.append(entry)

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

        # 0. 本地快速路径: condenser 内部投影只会减 item 不会增, 本地估算
        # 低于阈值 ⇒ 投影后必然更低 ---- 直接短路, 省掉 condenser_agent 全
        # routine 生命周期 spawn + DB 重读 + 全量投影的成本.
        from ..condenser.strategies.base import estimate_items_tokens
        try:
            ratio = float(condense_config.get('trigger_ratio', 0.5))
        except (TypeError, ValueError):
            ratio = 0.5
        if estimate_items_tokens(self._items) < int(max_context * ratio):
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
