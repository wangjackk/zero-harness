"""记忆系统 provider 抽象.

统一实现 LocalContextProvider: 本地 sqlite + condenser_agent 压缩.

agent 只持 ContextProvider 接口, 不感知后端.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def build_context_provider(
    *,
    writer: Any,
    workspace: Path | None,
    max_items: int = 80,
) -> Any:
    """构造 ContextProvider.

    返回 ctx: LocalContextProvider.
    """
    from .local import LocalContextProvider
    return LocalContextProvider(writer=writer, max_items=max_items)
