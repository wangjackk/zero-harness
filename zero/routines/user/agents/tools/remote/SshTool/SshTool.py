"""Ssh ---- 基于 AsyncSSH 管理持久连接, 持久 shell 和文件传输.

拆成 5 个独立 routine, 共用 SshRegistry (按 agent session 隔离).
LLM 通过 alias 给每条连接起语义化名字, 同 agent session 内可同时保持多条连接.
"""
from __future__ import annotations

import asyncio
import os
import shlex
from typing import Any, ClassVar, Dict, Literal

import asyncssh
from pydantic import BaseModel, Field
from routine import Routine

from .runtime import SshCommandError, SshRegistry, SshSession, close_session, pump_stdout, require_active_session, require_session, run_shell_command
from .shared import (
    DEFAULT_TIMEOUT_SECONDS,
    SHELL_PATH,
    build_connect_kwargs,
    compose_command_with_cwd,
    ensure_local_parent_exists,
    make_command_non_interactive,
    normalize_timeout,
    parse_target,
    require_non_empty,
)
from .transfer import is_remote_directory
from zero.routines.user.agents._core.paths import AGENT_ID_KEY, resolve_tool_path


# ── base ──

class _SshBase(Routine):
    """公共: 注入 agent_id, 解析 session_id / project_root."""

    async def _ctx(self, kwargs: Dict[str, Any]) -> tuple[str, str | None]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        project_root = state.get('project_root')
        if not session_id:
            raise ValueError('session_id is required (injected by agent)')
        return session_id, project_root


# ── connect ──

class SshConnectInput(BaseModel):
    alias: str = Field(description='SSH 连接别名, LLM 自命名 (如 robot/server/gpu-box).')
    target: str = Field(description='SSH target, for example "root@192.168.1.20".')
    port: int = Field(22, ge=1, le=65535, description='SSH port. Default 22.')
    identity_file: str | None = Field(None, description='Optional local private key path.')
    password: str | None = Field(None, description='Optional SSH password.')
    cwd: str | None = Field(None, description='Initial remote working directory.')
    timeout: int = Field(DEFAULT_TIMEOUT_SECONDS, description='Connect timeout in seconds. Default 120.')


class SshConnect(_SshBase):
    """Open a persistent SSH connection with a long-lived /bin/sh shell."""

    name = 'ssh_connect'

    meta: ClassVar[Dict[str, Any]] = {
        'readonly': False,
        'input_schema': SshConnectInput.model_json_schema(),
        'output_schema': {'type': 'string', 'description': 'Connection result.'},
        'description': 'Open a persistent SSH connection. Pass an alias (e.g. robot/server) and target (e.g. root@1.2.3.4). The alias identifies this connection in later ssh_exec/ssh_transfer/ssh_disconnect calls.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        session_id, project_root = await self._ctx(kwargs)
        inp = SshConnectInput(**kwargs)

        alias = require_non_empty(inp.alias, name='alias')
        target = require_non_empty(inp.target, name='target')
        timeout = normalize_timeout(inp.timeout)
        host, username = parse_target(target)
        connect_kwargs = build_connect_kwargs(
            host=host, username=username, port=inp.port,
            identity_file=inp.identity_file, password=inp.password,
            strict_host_key_checking=True, project_root=project_root,
        )

        if SshRegistry.get(session_id, alias):
            raise ValueError(f'alias 已存在: {alias} (请先 ssh_disconnect 或换一个别名)')

        connection = await asyncio.wait_for(asyncssh.connect(host, **connect_kwargs), timeout=timeout)
        process = await asyncio.wait_for(connection.create_process(SHELL_PATH), timeout=timeout)
        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        pump_task = asyncio.create_task(pump_stdout(process, queue))
        session = SshSession(
            session_id=alias, target=target, host=host, username=username,
            port=inp.port, identity_file=inp.identity_file,
            strict_host_key_checking=True, cwd=None,
            connection=connection, process=process, queue=queue, pump_task=pump_task,
        )
        SshRegistry.set(session_id, alias, session)

        try:
            await run_shell_command(session, 'exec 2>&1', timeout=timeout)
            if inp.cwd:
                await run_shell_command(session, f'cd {shlex.quote(inp.cwd)}', timeout=timeout)
                session.cwd = inp.cwd
        except Exception:
            await close_session(session)
            SshRegistry.remove(session_id, alias)
            raise

        lines = [f'已建立 SSH 连接: {alias}', f'target={target}', f'port={inp.port}']
        if inp.cwd:
            lines.append(f'cwd={inp.cwd}')
        if inp.identity_file:
            lines.append(f'identity_file={inp.identity_file}')
        return '\n'.join(lines)


# ── exec ──

class SshExecInput(BaseModel):
    command: str = Field(description='Remote command to execute.')
    alias: str | None = Field(None, description='SSH 连接别名. 省略则用当前 active 连接.')
    cwd: str | None = Field(None, description='Remote working directory. Updates the shell state if set.')
    timeout: int = Field(DEFAULT_TIMEOUT_SECONDS, description='Timeout in seconds. Default 120.')


class SshExec(_SshBase):
    """Run a command inside an existing SSH shell."""

    name = 'ssh_exec'

    meta: ClassVar[Dict[str, Any]] = {
        
        'readonly': False,
        'input_schema': SshExecInput.model_json_schema(),
        'output_schema': {'type': 'string', 'description': 'Remote command output.'},
        'description': 'Run a command on a remote host via an existing SSH connection. Pass alias to pick a specific connection; omit to use the active one. Shell state (cd, export) persists across calls.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        session_id, _ = await self._ctx(kwargs)
        inp = SshExecInput(**kwargs)

        session = require_session(session_id, inp.alias) if inp.alias else require_active_session(session_id)
        command = require_non_empty(inp.command, name='command')
        timeout = normalize_timeout(inp.timeout)
        command = make_command_non_interactive(command)
        effective_command = compose_command_with_cwd(command=command, cwd=inp.cwd)

        try:
            output = await run_shell_command(session, effective_command, timeout=timeout)
        except SshCommandError:
            if inp.cwd is not None:
                session.cwd = inp.cwd
            raise
        except Exception:
            await close_session(session)
            SshRegistry.remove(session_id, session.session_id)
            raise

        if inp.cwd is not None:
            session.cwd = inp.cwd
        return output


# ── transfer ──

class SshTransferInput(BaseModel):
    direction: Literal['upload', 'download'] = Field(description='Transfer direction: upload (local->remote) or download (remote->local).')
    local_path: str = Field(description='Local file or directory path.')
    remote_path: str = Field(description='Remote file or directory path.')
    alias: str | None = Field(None, description='SSH 连接别名. 省略则用当前 active 连接.')
    timeout: int = Field(DEFAULT_TIMEOUT_SECONDS, description='Timeout in seconds. Default 120.')


class SshTransfer(_SshBase):
    """Upload or download files/directories over an existing SSH connection."""

    name = 'ssh_transfer'

    meta: ClassVar[Dict[str, Any]] = {
        
        'readonly': False,
        'input_schema': SshTransferInput.model_json_schema(),
        'output_schema': {'type': 'string', 'description': 'Transfer result.'},
        'description': 'Transfer files or directories via SCP over an existing SSH connection. direction=upload sends local->remote, direction=download fetches remote->local. Auto-detects directory recursion.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        session_id, project_root = await self._ctx(kwargs)
        inp = SshTransferInput(**kwargs)

        session = require_session(session_id, inp.alias) if inp.alias else require_active_session(session_id)
        timeout = normalize_timeout(inp.timeout)

        if inp.direction == 'upload':
            local_path = resolve_tool_path(require_non_empty(inp.local_path, name='local_path'), project_root)
            remote_path = require_non_empty(inp.remote_path, name='remote_path')
            if not os.path.exists(local_path):
                raise FileNotFoundError(f'本地路径不存在: {local_path}')
            recurse = os.path.isdir(local_path)

            async with session.lock:
                await asyncio.wait_for(
                    asyncssh.scp(local_path, (session.connection, remote_path), recurse=recurse),
                    timeout=timeout,
                )
            kind = '目录' if recurse else '文件'
            return f'已上传{kind}: {local_path} -> {remote_path}'

        # download
        remote_path = require_non_empty(inp.remote_path, name='remote_path')
        local_path = resolve_tool_path(require_non_empty(inp.local_path, name='local_path'), project_root)
        recurse = await is_remote_directory(session, remote_path)
        ensure_local_parent_exists(local_path)

        async with session.lock:
            await asyncio.wait_for(
                asyncssh.scp((session.connection, remote_path), local_path, recurse=recurse),
                timeout=timeout,
            )
        kind = '目录' if recurse else '文件'
        return f'已下载{kind}: {remote_path} -> {local_path}'


# ── disconnect ──

class SshDisconnectInput(BaseModel):
    alias: str | None = Field(None, description='SSH 连接别名. 省略则断开当前 active 连接.')


class SshDisconnect(_SshBase):
    """Close an SSH connection by alias (or active one if omitted)."""

    name = 'ssh_disconnect'

    meta: ClassVar[Dict[str, Any]] = {
        
        'readonly': False,
        'input_schema': SshDisconnectInput.model_json_schema(),
        'output_schema': {'type': 'string', 'description': 'Disconnect result.'},
        'description': 'Close a persistent SSH connection by alias. Omit alias to close the active one.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        session_id, _ = await self._ctx(kwargs)
        inp = SshDisconnectInput(**kwargs)

        session = require_session(session_id, inp.alias) if inp.alias else require_active_session(session_id)
        alias = session.session_id
        removed = SshRegistry.remove(session_id, alias)
        if not removed:
            raise ValueError(f'未找到 SSH 会话: {alias}')
        await close_session(removed)
        return f'已断开 SSH 持久连接: {alias}'


# ── list ──

class SshListInput(BaseModel):
    pass


class SshList(_SshBase):
    """List all active SSH connections in the current agent session."""

    name = 'ssh_list'

    meta: ClassVar[Dict[str, Any]] = {
        'readonly': True,
        'input_schema': SshListInput.model_json_schema(),
        'output_schema': {'type': 'string', 'description': 'List of active SSH connections.'},
        'description': 'List all active SSH connections in the current agent session with their aliases and targets.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        session_id, _ = await self._ctx(kwargs)
        sessions = SshRegistry.list_session(session_id)
        if not sessions:
            return '当前没有活跃的 SSH 持久连接.'

        lines = ['当前 SSH 持久连接:']
        for alias, session in sessions.items():
            line = f'- {alias}: target={session.target}, port={session.port}'
            if session.cwd:
                line += f', cwd={session.cwd}'
            lines.append(line)
        return '\n'.join(lines)
