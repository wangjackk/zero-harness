"""_file_state ---- 跨工具共享的 session-level 文件读取状态.

对齐 claude-code FileReadTool 的 readFileState:
  - Read 成功后写入 {content, timestamp, offset, limit}
  - Edit / Write 校验 "已 read" + "mtime 未变",通过则无需额外 ask

状态以 session_id(agent push 工具时注入 kwargs 的 SESSION_ID_KEY)为粒度隔离,
避免多 agent 并发时互相污染,由 agent 注入 SESSION_ID_KEY 进 kwargs.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Optional

_lock = threading.Lock()

# session_key → { abs_path → ReadEntry }
_registry: dict[str, dict[str, 'ReadEntry']] = {}


@dataclass
class ReadEntry:
    content: str
    timestamp: float    # mtime (seconds) at read time
    offset: int | None  # None = full read
    limit: int | None   # None = full read

    @property
    def is_partial(self) -> bool:
        return self.offset is not None or self.limit is not None


class FileReadState:
    """单个 session 的文件读取状态视图."""

    def __init__(self, session_key: str) -> None:
        self._key = session_key
        with _lock:
            if session_key not in _registry:
                _registry[session_key] = {}

    def set(
        self,
        path: str,
        content: str,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> None:
        mtime = _safe_mtime(path)
        entry = ReadEntry(content=content, timestamp=mtime, offset=offset, limit=limit)
        with _lock:
            _registry[self._key][path] = entry

    def get(self, path: str) -> Optional[ReadEntry]:
        with _lock:
            return _registry[self._key].get(path)

    def update_after_write(self, path: str, content: str) -> None:
        """Edit/Write 成功后更新 state(去掉 offset/limit,记录新 mtime)."""
        mtime = _safe_mtime(path)
        entry = ReadEntry(content=content, timestamp=mtime, offset=None, limit=None)
        with _lock:
            _registry[self._key][path] = entry

    def validate_for_write(self, path: str) -> tuple[bool, str]:
        """检查是否可以安全写入 path.

        返回 (ok, error_message).ok=True 时 error_message 为空.
        """
        entry = self.get(path)
        if entry is None:
            return False, (
                f'File has not been read yet. '
                f'Use the Read tool to read "{path}" before editing it.'
            )
        if entry.is_partial:
            return False, (
                f'File was only partially read (offset/limit). '
                f'Read the full file with limit=0 before editing: "{path}".'
            )
        mtime = _safe_mtime(path)
        if mtime > entry.timestamp:
            # Windows 上 mtime 有时因 buffering 假变化;content 一致则放行
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    current = f.read()
                if current == entry.content:
                    return True, ''
            except OSError:
                pass
            return False, (
                f'File "{path}" has been modified since it was last read. '
                f'Read it again before editing.'
            )
        return True, ''


# ---------------------------------------------------------------------------
# 进程级注册表访问(供工具调用,用 session_id 作 key)
# ---------------------------------------------------------------------------

def get_state(session_key: str) -> FileReadState:
    return FileReadState(session_key)


def clear_session(session_key: str) -> None:
    with _lock:
        _registry.pop(session_key, None)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
