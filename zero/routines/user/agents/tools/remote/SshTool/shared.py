from __future__ import annotations

import os
import re
import shlex
from typing import Any

from zero.routines.user.agents._core.paths import resolve_tool_path

DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 600
SHELL_PATH = '/bin/sh'


def build_connect_kwargs(
    *,
    host: str,
    username: str | None,
    port: int,
    identity_file: str | None,
    password: str | None,
    strict_host_key_checking: bool,
    project_root: str | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'port': port,
        'username': username,
    }
    if identity_file:
        resolved_identity_file = resolve_tool_path(os.path.expanduser(identity_file), project_root)
        kwargs['client_keys'] = [resolved_identity_file]
    if password is not None:
        kwargs['password'] = password
    if not strict_host_key_checking:
        kwargs['known_hosts'] = None
    return kwargs


def parse_target(target: str) -> tuple[str, str | None]:
    if '@' not in target:
        return target, None
    username, host = target.rsplit('@', 1)
    if not username or not host:
        raise ValueError(f'target 格式不合法: {target!r}')
    return host, username


def compose_command_with_cwd(*, command: str, cwd: str | None) -> str:
    if not cwd:
        return command
    return f'cd {shlex.quote(cwd)} && {command}'


def make_command_non_interactive(command: str) -> str:
    stripped = command.lstrip()
    if not stripped.startswith('sudo '):
        return command
    if re.match(r'sudo\s+(?:-n(?:\s|$)|--non-interactive(?:\s|$))', stripped):
        return command

    leading = command[: len(command) - len(stripped)]
    return f'{leading}sudo -n {stripped[len("sudo "):]}'


def format_connect_preview(
    *,
    target: str,
    port: int,
    identity_file: str | None,
    has_password: bool,
    cwd: str | None,
    strict_host_key_checking: bool,
) -> str:
    parts = [
        'action=connect',
        f'target={target}',
        f'port={port}',
        f'strict_host_key_checking={strict_host_key_checking}',
    ]
    if cwd:
        parts.append(f'cwd={cwd}')
    if identity_file:
        parts.append(f'identity_file={identity_file}')
    if has_password:
        parts.append('password=***')
    return '\n'.join(parts)


def format_bash_preview(*, ssh_session_id: str, target: str, command: str) -> str:
    return '\n'.join([
        'action=exec',
        f'ssh_session_id={ssh_session_id}',
        f'target={target}',
        f'command={command}',
    ])


def format_transfer_preview(
    *,
    action: str,
    ssh_session_id: str,
    target: str,
    local_path: str,
    remote_path: str,
    recurse: bool,
    preserve: bool,
) -> str:
    return '\n'.join([
        f'action={action}',
        f'ssh_session_id={ssh_session_id}',
        f'target={target}',
        f'local_path={local_path}',
        f'remote_path={remote_path}',
        f'recursive={recurse}',
        f'preserve={preserve}',
    ])


def normalize_timeout(timeout: int) -> int:
    return max(1, min(timeout, MAX_TIMEOUT_SECONDS))


def require_non_empty(value: str | None, *, name: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f'{name} 不能为空')
    return value.strip()


def ensure_local_parent_exists(local_path: str) -> None:
    parent = os.path.dirname(local_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
