"""BackgroundShell ---- 后台执行常驻命令,日志写文件,后续可查状态/输出/停止.

BashTool 对不退出的命令会卡 timeout;本工具启动后立即返回 task_id,
stdout/stderr 持续 pump 到 <cwd>/.bg/<task_id>.log.

按 session_id 隔离 task:多 agent 并发时各自只能看/管自己 session 下的 task.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, ClassVar, Dict, Literal
from uuid import uuid4

from pydantic import BaseModel, Field
from routine import Routine

from .prompt import DESCRIPTION
from .runtime import BgRegistry, BgTask, is_running, pump_to_log, tail_file

from ..BashTool.BashTool import _find_shell
from zero.routines.user.agents._core.paths import AGENT_ID_KEY, resolve_optional_tool_path


_SHELL = _find_shell()
_IS_POWERSHELL = _SHELL == 'powershell'


class BackgroundShellInput(BaseModel):
    action: Literal['start', 'status', 'stop', 'list'] = Field(
        description='start: launch in background; status: tail recent output + state; stop: kill; list: show all tasks.',
    )
    task_id: str | None = Field(
        None,
        description='Task id (for status/stop). Ignored for start/list.',
    )
    command: str | None = Field(
        None,
        description='Command to launch (for start). Unix/bash syntax.',
    )
    cwd: str | None = Field(
        None,
        description='Working directory for start. Defaults to project root.',
    )
    lines: int = Field(
        50,
        ge=1,
        le=1000,
        description='Tail lines for status. Default 50.',
    )


class BackgroundShell(Routine):
    """Spawn long-running commands in background; inspect output later."""

    meta: ClassVar[Dict[str, Any]] = {
        'input_schema': BackgroundShellInput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        project_root = state.get('project_root')
        inp = BackgroundShellInput(**kwargs)

        if not session_id:
            raise ValueError('session_id is required (injected by agent)')

        if inp.action == 'start':
            return await self._start(inp, project_root, session_id)
        if inp.action == 'status':
            return self._status(inp, session_id)
        if inp.action == 'stop':
            return await self._stop(inp, session_id)
        return self._list(session_id)

    async def _start(self, inp: BackgroundShellInput, project_root: str | None,
                     session_id: str) -> Dict[str, Any]:
        if not inp.command or not inp.command.strip():
            raise ValueError('command is required for start')

        # resolve cwd: explicit cwd > project_root > os.getcwd()
        if inp.cwd:
            effective_cwd = resolve_optional_tool_path(inp.cwd, project_root)
        elif project_root:
            effective_cwd = project_root
        else:
            effective_cwd = os.getcwd()
        effective_cwd = os.path.abspath(effective_cwd)

        # 审批已移除: 所有命令自动通过, 不再弹 ask 弹窗阻塞执行.

        # log dir
        log_dir = os.path.join(effective_cwd, '.bg')
        os.makedirs(log_dir, exist_ok=True)

        task_id = f'bg_{uuid4().hex[:12]}'
        log_path = os.path.join(log_dir, f'{task_id}.log')

        # spawn
        if _IS_POWERSHELL:
            ps_cmd = (
                '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
                '$OutputEncoding = [System.Text.Encoding]::UTF8; '
                + inp.command
            )
            args = ['powershell', '-NonInteractive', '-Command', ps_cmd]
        else:
            args = [_SHELL, '-c', inp.command]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=effective_cwd,
        )

        task = BgTask(
            task_id=task_id,
            command=inp.command,
            cwd=effective_cwd,
            pid=proc.pid,
            log_path=log_path,
            start_time=time.time(),
            process=proc,
            pump_task=asyncio.create_task(pump_to_log(proc, log_path)),
        )
        BgRegistry.set(session_id, task_id, task)

        self._logger.info('background_shell: started %s (pid=%d, session=%s) log=%s',
                          task_id, proc.pid, session_id, log_path)

        return {
            'task_id': task_id,
            'status': 'running',
            'pid': proc.pid,
            'log_path': log_path,
            'for_llm': (
                f'Background task {task_id} started (pid={proc.pid}). '
                f'Log: {log_path}. '
                f'Use BackgroundShell action=status task_id={task_id} to check output.'
            ),
        }

    def _status(self, inp: BackgroundShellInput, session_id: str) -> Dict[str, Any]:
        if not inp.task_id:
            raise ValueError('task_id is required for status')
        task = BgRegistry.get(session_id, inp.task_id)
        if task is None:
            raise ValueError(f'task not found: {inp.task_id}')

        running = is_running(task)
        tail = tail_file(task.log_path, inp.lines)
        state = 'running' if running else 'exited'

        return {
            'task_id': task.task_id,
            'status': state,
            'exit_code': task.process.returncode,
            'pid': task.pid,
            'command': task.command,
            'log_path': task.log_path,
            'tail': tail,
            'for_llm': (
                f'Task {task.task_id} {state}'
                + (f' (exit_code={task.process.returncode})' if not running else '')
                + f'.\n--- last {inp.lines} lines ---\n{tail}'
            ),
        }

    async def _stop(self, inp: BackgroundShellInput, session_id: str) -> Dict[str, Any]:
        if not inp.task_id:
            raise ValueError('task_id is required for stop')
        task = BgRegistry.get(session_id, inp.task_id)
        if task is None:
            raise ValueError(f'task not found: {inp.task_id}')

        was_running = is_running(task)
        if was_running:
            task.process.kill()
            await task.process.wait()
            await task.pump_task  # pump finishes after stdout closes

        BgRegistry.remove(session_id, inp.task_id)
        self._logger.info('background_shell: stopped %s (was_running=%s, session=%s)',
                          task.task_id, was_running, session_id)

        return {
            'task_id': task.task_id,
            'status': 'killed' if was_running else 'already_exited',
            'exit_code': task.process.returncode,
            'log_path': task.log_path,
            'for_llm': (
                f'Task {task.task_id} '
                + ('killed' if was_running else 'was already exited')
                + f' (exit_code={task.process.returncode}). Log: {task.log_path}'
            ),
        }

    def _list(self, session_id: str) -> Dict[str, Any]:
        tasks = BgRegistry.list_session(session_id)
        if not tasks:
            return {
                'tasks': [],
                'for_llm': 'No background tasks.',
            }
        items = []
        for t in tasks.values():
            running = is_running(t)
            items.append({
                'task_id': t.task_id,
                'command': t.command,
                'pid': t.pid,
                'status': 'running' if running else 'exited',
                'exit_code': t.process.returncode,
                'log_path': t.log_path,
            })
        lines = [f'{it["task_id"]} {it["status"]} pid={it["pid"]} cmd={it["command"]!r}'
                 for it in items]
        return {
            'tasks': items,
            'for_llm': 'Background tasks:\n' + '\n'.join(lines),
        }
