"""Bash ---- 在 shell 里执行命令,返回 stdout + stderr 合并输出.

持续 cwd 支持:
  - session 级别持久化 cwd (默认 project_root)
  - 命令末尾的 cd 会更新后续 cwd (LLM 不必每次传 cwd)
  - LLM 显式传 cwd 优先级最高, 且会同步更新 state
"""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import signal
import subprocess
from typing import ClassVar, Dict, Any

from pydantic import BaseModel, Field
from routine import Routine

from .prompt import DESCRIPTION

from zero.routines.user.agents._core.paths import AGENT_ID_KEY, resolve_optional_tool_path
from ..._shared._cwd_state import get_cwd, update_cwd_from_command


def _find_shell() -> str:
    """返回可用的 shell 路径.

    对齐 claude-code windowsPaths.ts::setShellIfWindows():
    Windows 优先使用 Git Bash,使 && / Unix 路径语法天然可用.
    """
    if platform.system() != 'Windows':
        return os.environ.get('SHELL', '/bin/sh')

    candidates = [
        r'C:\Program Files\Git\bin\bash.exe',
        r'C:\Program Files (x86)\Git\bin\bash.exe',
        os.path.expanduser(r'~\AppData\Local\Programs\Git\bin\bash.exe'),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path

    # 没有 Git Bash ---- 回退到 WSL bash
    wsl = shutil.which('bash')
    if wsl:
        return wsl

    return 'powershell'


_SHELL = _find_shell()
_IS_POWERSHELL = _SHELL == 'powershell'
_IS_WINDOWS = platform.system() == 'Windows'


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """杀整个进程树(父+所有子),跨平台.

    只 kill 父进程(bash -c)会留孤儿(uv -> python 各自 fork),子进程
    继续握 stdout 管道 -> communicate() 永远阻塞.必须整树杀.

    Unix 依赖 start_new_session=True 创建新进程组,killpg 才能整树杀.
    Windows 用 taskkill /T /F 递归杀子.
    """
    if proc.returncode is not None:
        return
    pid = proc.pid
    try:
        if _IS_WINDOWS:
            # /T 递归杀子进程, /F 强制
            subprocess.run(
                ['taskkill', '/T', '/F', '/PID', str(pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


class BashInput(BaseModel):
    command: str = Field(
        description=(
            'The bash command to execute. '
            'Use Unix / bash syntax -- not Windows CMD syntax. '
            'Examples: use "ls" not "dir", forward slashes in paths, '
            '"&&" to chain dependent commands, "/dev/null" not "NUL". '
            'Avoid cat/head/tail/grep/find -- prefer Read/Grep/Glob tools instead. '
            'cd at the end of a command persists across calls (e.g. "cd src" then '
            'later "ls" runs in src/).'
        ),
    )
    timeout: int = Field(
        120,
        description='Timeout in seconds. Default 120. Max 600.',
    )
    cwd: str | None = Field(
        None,
        description=(
            'Working directory. If omitted, uses the session\'s current cwd '
            '(defaults to project root). cd at the end of command updates this '
            'for subsequent calls. Pass explicitly to override without cd.'
        ),
    )


class BashOutput(BaseModel):
    output: str = Field(description='Combined stdout and stderr output')
    exit_code: int
    cwd: str = Field(description='Absolute working directory after command (may have changed via cd)')


class Bash(Routine):
    """Execute a shell command and return the combined stdout + stderr output.

    Uses Git Bash on Windows (Unix syntax, && supported).
    Prefer Read/Grep/Glob/Edit tools for file operations -- use Bash only when
    those dedicated tools cannot accomplish the task.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': BashInput.model_json_schema(),
        'output_schema': BashOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        project_root = state.get('project_root')
        inp = BashInput(**kwargs)

        if not inp.command.strip():
            raise ValueError('command 不能为空')

        inp.timeout = min(inp.timeout, 600)

        # 解析 cwd 优先级: 显式 cwd > session state > project_root
        if inp.cwd:
            effective_cwd = resolve_optional_tool_path(inp.cwd, project_root)
        else:
            state_cwd = get_cwd(session_id, default=project_root) if session_id else None
            effective_cwd = resolve_optional_tool_path(state_cwd, project_root) if state_cwd else (project_root or os.getcwd())
        effective_cwd = os.path.abspath(effective_cwd)

        # 显式传 cwd 时同步到 state (后续命令默认用这个 cwd)
        if inp.cwd and session_id:
            from ..._shared._cwd_state import set_cwd
            set_cwd(session_id, effective_cwd)

        # 审批已移除: 所有命令自动通过, 不再弹 ask 弹窗阻塞执行.

        if _IS_POWERSHELL:
            # 没有 Git Bash 时的最后兜底
            ps_cmd = (
                '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
                '$OutputEncoding = [System.Text.Encoding]::UTF8; '
                + inp.command
            )
            args = ['powershell', '-NonInteractive', '-Command', ps_cmd]
        else:
            args = [_SHELL, '-c', inp.command]

        # 后台命令(末尾 &):不卡 communicate,立即返回 pid.
        # 常驻服务(one/zero/web server)前台跑会卡到 timeout;后台跑时 LLM
        # 应重定向输出到文件(> file 2>&1)后用 tail / Read 查看.
        is_background = inp.command.rstrip().endswith('&')

        if is_background:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=effective_cwd,
            )
            output = f'[background] pid={proc.pid} started'
        else:
            # start_new_session=True (Unix) 让子进程成新进程组 leader,
            # _kill_process_tree 才能用 killpg 整树杀. Windows 上此参数无效(忽略).
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=effective_cwd,
                start_new_session=(not _IS_POWERSHELL),
            )

            stdout = b''
            timed_out = False
            cancelled = False
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=inp.timeout,
                )
            except asyncio.TimeoutError:
                timed_out = True
            except asyncio.CancelledError:
                # 用户中断 / shell.interrupt:杀整树,否则子进程孤儿化继续握管道
                cancelled = True
            finally:
                # proc.returncode is None 表示子进程还在跑(超时/取消/异常路径),
                # 必须先杀整树再 communicate,否则管道不关、communicate 永远阻塞.
                if proc.returncode is None:
                    _kill_process_tree(proc)
                try:
                    await proc.communicate()
                except Exception:
                    pass

            if cancelled:
                raise asyncio.CancelledError()
            if timed_out:
                raise TimeoutError(f'命令超时({inp.timeout}s): {inp.command!r}')

            output = stdout.decode('utf-8', errors='replace')

        # 解析末尾 cd 更新 state (不依赖命令是否成功, cd 在 bash 里即使后续失败也生效)
        if session_id:
            new_cwd = update_cwd_from_command(
                session_id, inp.command, effective_cwd, )
        else:
            new_cwd = effective_cwd

        if proc.returncode != 0:
            raise RuntimeError(f'exit {proc.returncode}:\n{output.strip()}')

        return {
            'for_llm': output,
            'exit_code': proc.returncode,
            'cwd': new_cwd,
        }
