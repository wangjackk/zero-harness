"""ResponseTracker — Responses API 的 response_id 状态管理.

从 Conversation 拆出, 只管:
  - response_id 历史 (_history)
  - 增量请求计算 (cursor → to_request)
  - 过期 response_id 清理 (invalidate)

不碰消息列表 (由 ContextProvider 持有), 通过 items_len 参数对齐 cursor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from routine.logger import setup_logger

_log = setup_logger('prime.response_tracker')


def _strip_internal(items: list[dict]) -> list[dict]:
    """剥掉 _ 前缀内部字段, 只保留 Responses API 认识的字段."""
    return [
        {k: v for k, v in item.items() if not k.startswith('_')}
        for item in items
    ]


@dataclass
class TurnMeta:
    response_id: str
    text: str
    usage: dict[str, Any] | None = None


class ResponseTracker:
    """Responses API response_id 状态管理.

    to_request(items) 返回 (增量 items, prev_rid):
      - 无历史: (全量 items, None)
      - 有历史: (items[cursor:], prev_rid)
    """

    def __init__(self) -> None:
        self._history: list[TurnMeta] = []
        self._cursor: int = 0

    @property
    def history(self) -> list[TurnMeta]:
        return self._history

    @property
    def last_response_id(self) -> str | None:
        return self._history[-1].response_id if self._history else None

    def mark_response(
        self,
        response_id: str,
        text: str = '',
        usage: dict[str, Any] | None = None,
        items_len: int = 0,
    ) -> TurnMeta:
        """记录一次 response 完成, cursor 对齐到当前 items 末尾."""
        meta = TurnMeta(response_id=response_id, text=text, usage=usage)
        self._history.append(meta)
        prev = self._cursor
        self._cursor = items_len
        _log.info(
            'mark_response: rid=%s cursor %d -> %d',
            response_id[:24] + '…', prev, self._cursor,
        )
        return meta

    def invalidate_last(self) -> None:
        """弹出过期 response_id, cursor 归零 (下次全量重发)."""
        if self._history:
            self._history.pop()
        self._cursor = 0

    def to_request(self, items: list[dict]) -> tuple[list[dict], str | None]:
        """计算增量请求: (增量 items, prev_rid).

        剥掉 _ 前缀内部字段, 只发 Responses API 认识的字段.
        """
        prev_rid = self.last_response_id
        if not prev_rid:
            return _strip_internal(items), None
        incremental = _strip_internal(items[self._cursor:])
        _log.info(
            'to_request: prev_rid=%s cursor=%d items=%d incremental=%d',
            prev_rid[:24] + '…', self._cursor,
            len(items), len(incremental),
        )
        return incremental, prev_rid

    def load(self, response_state: dict[str, Any] | None) -> None:
        """从 replay 结果恢复 (resume)."""
        self._history.clear()
        self._cursor = 0
        if response_state:
            self._history.append(TurnMeta(
                response_id=str(response_state.get('response_id') or ''),
                text=str(response_state.get('text') or ''),
                usage=response_state.get('usage')
                if isinstance(response_state.get('usage'), dict) else None,
            ))
            self._cursor = int(response_state.get('cursor') or 0)

    def reset(self) -> None:
        """压缩后清空 (只保留空状态, 等 load 恢复)."""
        self._history.clear()
        self._cursor = 0
