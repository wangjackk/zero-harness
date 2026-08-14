"""_cwd_state ---- session-level 持续 cwd 状态.

BashTool 的 cd 持续化:
  - 每次 Bash 调用默认从 state 读 cwd (session 级别持久)
  - 解析 command 里的 cd 命令, 执行后更新 state
  - LLM 不需要每次传 cwd, 也不需要用 && 串联

状态以 session_id (agent push 工具时注入 kwargs 的 SESSION_ID_KEY) 为粒度隔离,
避免多 agent 并发时互相污染. 跟 _file_state.py 同一模式.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

_lock = threading.Lock()

# session_id -> cwd (absolute path)
_registry: dict[str, str] = {}


def get_cwd(session_key: str, default: str | None = None) -> str | None:
    """读 session 的当前 cwd. 没有则返回 default (通常是 project_root)."""
    with _lock:
        return _registry.get(session_key, default)


def set_cwd(session_key: str, cwd: str) -> None:
    """更新 session 的 cwd (规范化为绝对路径)."""
    abs_cwd = os.path.abspath(cwd)
    with _lock:
        _registry[session_key] = abs_cwd


def clear_session(session_key: str) -> None:
    with _lock:
        _registry.pop(session_key, None)


# 匹配命令末尾的 cd path (支持 && / ; / | 串联场景里的最后一段 cd)
# 形如: cd src/components
#       cd /abs/path
#       cd "../foo"
#       cd ~/projects
# 不处理: cd && ls (cd 无参数时通常是回到 home, 这里忽略)
_CD_RE = re.compile(
    r'(?:^|&&|;|\|)\s*'          # 行首 或 命令连接符
    r'cd\s+'
    r'(["\']?)([^\'"\s]+)\1'     # 路径 (可选引号)
    r'\s*$'                       # 行尾 (cd 必须是最后一段, 才影响后续 cwd)
)


def extract_cd_target(command: str) -> str | None:
    """从 command 里提取 cd 目标路径.

    只提取「最后一段 cd」, 因为只有最后的 cd 会影响后续命令的 cwd.
    例如 "ls && cd src" -> "src"
         "cd a && cd b && pwd" -> "b" (pwd 不影响 cwd)
         "cd src && ls" -> None (cd 后面还有命令, cwd 已经在本次 subprocess 用过)

    Returns None 表示 command 不以 cd 结尾 (cwd 不需要更新).
    """
    # 去掉末尾注释和多余空白
    cmd = command.strip()
    m = _CD_RE.search(cmd)
    if m:
        return m.group(2)
    return None


def update_cwd_from_command(
    session_key: str,
    command: str,
    current_cwd: str,
) -> str:
    """从 command 解析 cd 目标, 计算新 cwd 并更新 state.

    current_cwd 是本次命令执行时用的 cwd (绝对路径).
    返回执行后的新 cwd (如果 command 末尾有 cd, 否则返回 current_cwd).
    """
    target = extract_cd_target(command)
    if target is None:
        return current_cwd
    # 解析 target 相对 current_cwd (cd 的标准行为)
    # 支持 ~ / $HOME (Git Bash 会展开, 但我们在 Python 侧展开以便记录)
    if target.startswith('~'):
        home = os.path.expanduser('~')
        target = os.path.join(home, target[1:].lstrip('/\\'))
    # 绝对路径直接用, 相对路径基于 current_cwd
    if os.path.isabs(target):
        new_cwd = target
    else:
        new_cwd = os.path.normpath(os.path.join(current_cwd, target))
    # 不要求目录存在 (subprocess 会失败), 但只更新到 state 里
    set_cwd(session_key, new_cwd)
    return new_cwd
