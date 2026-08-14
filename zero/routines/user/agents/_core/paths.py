"""Agent 工具路径约定: 身份键 + 路径解析/展示 helper."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

PROJECT_DIR_ROOT_PATH_KEY = 'project_dir_root_path'
AGENT_ID_KEY = 'from_agent_id'  # agent push tool routine 时注入的调用方身份; 用 from_agent_id 避免跟 tool routine 自身的 agent_id 输入字段冲突


def pop_project_root(kwargs: dict[str, Any]) -> str | None:
    value = kwargs.pop(PROJECT_DIR_ROOT_PATH_KEY, None)
    if not value:
        return None
    return os.path.abspath(str(value))


def resolve_tool_path(path: str, project_root: str | None = None) -> str:
    if os.path.isabs(path):
        return os.path.abspath(path)
    root = os.path.abspath(project_root or os.getcwd())
    return os.path.abspath(os.path.join(root, path))


def resolve_optional_tool_path(path: str | None, project_root: str | None = None) -> str:
    if path:
        return resolve_tool_path(path, project_root)
    return os.path.abspath(project_root or os.getcwd())


def display_tool_path(path: str | Path, project_root: str | None = None) -> str:
    root = os.path.abspath(project_root or os.getcwd())
    absolute = os.path.abspath(str(path))
    relative = os.path.relpath(absolute, root)
    if relative == '.':
        return relative
    if relative == '..' or relative.startswith(f'..{os.sep}'):
        return absolute
    return relative
