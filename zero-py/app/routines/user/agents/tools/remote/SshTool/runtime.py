from __future__ import annotations

import asyncio
import atexit
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import uuid4


class SshCommandError(RuntimeError):
    def __init__(self, exit_code: int, output: str) -> None:
        self.exit_code = exit_code
        self.output = output
        message = f'exit {exit_code}:\n{output}'.rstrip()
        super().__init__(message)


@dataclass
class SshSession:
    session_id: str
    target: str
    host: str
    username: str | None
    port: int
    identity_file: str | None
    strict_host_key_checking: bool
    cwd: str | None
    connection: Any
    process: Any
    queue: asyncio.Queue[tuple[str, str]]
    pump_task: asyncio.Task[Any]
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    read_buffer: str = ''
    last_active_at: float = field(default_factory=time.time)


# idle 超时: 超过此时间未活动的 SSH 会话自动断开.
_IDLE_TIMEOUT_SECONDS = 1800  # 30 分钟
_SWEEPER_INTERVAL_SECONDS = 60


class SshRegistry:
    """session_id -> {ssh_session_id -> SshSession} 的注册表.

    多 agent 并发时按 session 隔离:agent A 看不到 agent B 的 SSH 会话.
    跟 BgRegistry 同一模式.

    Ssh routine 无状态, SshRegistry 作为单例 manager 存所有 agent 的 sessions,
    通过 session_id (agent_id) 路由. idle 超时的 session 由 sweeper 自动清理.
    """

    _sessions: ClassVar[dict[str, dict[str, SshSession]]] = {}
    _active: ClassVar[dict[str, str]] = {}  # session_id -> ssh_session_id
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _sweeper_task: ClassVar[asyncio.Task[None] | None] = None
    _sweeper_started: ClassVar[bool] = False

    @classmethod
    def set(cls, session_id: str, ssh_session_id: str, session: SshSession) -> None:
        with cls._lock:
            cls._sessions.setdefault(session_id, {})[ssh_session_id] = session
            cls._active[session_id] = ssh_session_id
        cls._ensure_sweeper()

    @classmethod
    def get(cls, session_id: str, ssh_session_id: str) -> SshSession | None:
        with cls._lock:
            return cls._sessions.get(session_id, {}).get(ssh_session_id)

    @classmethod
    def remove(cls, session_id: str, ssh_session_id: str) -> SshSession | None:
        with cls._lock:
            sess_map = cls._sessions.get(session_id)
            if sess_map is None:
                return None
            session = sess_map.pop(ssh_session_id, None)
            if not sess_map:
                cls._sessions.pop(session_id, None)
                cls._active.pop(session_id, None)
            elif cls._active.get(session_id) == ssh_session_id:
                cls._active[session_id] = next(reversed(sess_map))
            return session

    @classmethod
    def list_session(cls, session_id: str) -> dict[str, SshSession]:
        with cls._lock:
            return dict(cls._sessions.get(session_id, {}))

    @classmethod
    def get_active(cls, session_id: str) -> SshSession | None:
        with cls._lock:
            ssh_sid = cls._active.get(session_id)
            if ssh_sid:
                return cls._sessions.get(session_id, {}).get(ssh_sid)
            sess_map = cls._sessions.get(session_id, {})
            if len(sess_map) == 1:
                return next(iter(sess_map.values()))
            return None

    @classmethod
    def clear_session(cls, session_id: str) -> list[SshSession]:
        """agent 停止时调用:弹出该 session 下所有 SSH 会话 (同步关连接,不 await)."""
        with cls._lock:
            sess_map = cls._sessions.pop(session_id, None)
            cls._active.pop(session_id, None)
        if not sess_map:
            return []
        sessions = list(sess_map.values())
        for session in sessions:
            _sync_close(session)
        return sessions

    @classmethod
    def _ensure_sweeper(cls) -> None:
        """lazy 启动 sweeper task (首次 set session 时起)."""
        if cls._sweeper_started:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        cls._sweeper_started = True
        cls._sweeper_task = loop.create_task(cls._sweep_loop())

    @classmethod
    async def _sweep_loop(cls) -> None:
        """每 60s 扫一次, 超过 idle 超时的 session 自动断开."""
        while True:
            await asyncio.sleep(_SWEEPER_INTERVAL_SECONDS)
            now = time.time()
            expired: list[tuple[str, str, SshSession]] = []
            with cls._lock:
                for sess_id, sess_map in cls._sessions.items():
                    for ssh_sid, session in sess_map.items():
                        if now - session.last_active_at > _IDLE_TIMEOUT_SECONDS:
                            expired.append((sess_id, ssh_sid, session))
            for sess_id, ssh_sid, session in expired:
                try:
                    await close_session(session)
                except Exception:
                    pass
                cls.remove(sess_id, ssh_sid)


def _sync_close(session: SshSession) -> None:
    """同步关闭 session 资源 (不 await),用于 atexit/clear_session."""
    try:
        session.pump_task.cancel()
    except Exception:
        pass
    try:
        session.process.close()
    except Exception:
        pass
    try:
        session.connection.close()
    except Exception:
        pass


def require_session(session_id: str, ssh_session_id: str) -> SshSession:
    session = SshRegistry.get(session_id, ssh_session_id)
    if not session:
        raise ValueError(f'未找到 SSH 会话: {ssh_session_id}')
    return session


def require_active_session(session_id: str) -> SshSession:
    session = SshRegistry.get_active(session_id)
    if not session:
        raise ValueError('当前没有活跃的 SSH 会话,请先 connect.')
    return session


async def pump_stdout(process: Any, queue: asyncio.Queue[tuple[str, str]]) -> None:
    try:
        while True:
            chunk = await process.stdout.read(4096)
            if not chunk:
                break
            await queue.put(('stdout', chunk))
    except Exception as exc:
        await queue.put(('error', f'读取远端输出失败: {exc}'))
    finally:
        await queue.put(('eof', ''))


async def run_shell_command(session: SshSession, command: str, *, timeout: int) -> str:
    marker = f'__TRAE_SSH_DONE_{uuid4().hex}__'
    wrapped = (
        f'{command}\n'
        '__trae_status=$?\n'
        f'printf \'\\n{marker} %s\\n\' "$__trae_status"\n'
    )
    pattern = re.compile(rf'\n{re.escape(marker)} (?P<status>\d+)\r?\n')
    deadline = asyncio.get_running_loop().time() + timeout

    async with session.lock:
        session.last_active_at = time.time()
        session.process.stdin.write(wrapped)
        await session.process.stdin.drain()

        buffer = session.read_buffer
        while True:
            match = pattern.search(buffer)
            if match:
                session.read_buffer = buffer[match.end():]
                output = buffer[:match.start()]
                exit_code = int(match.group('status'))
                output = output.rstrip('\r\n')
                session.last_active_at = time.time()
                if exit_code != 0:
                    raise SshCommandError(exit_code, output)
                return output

            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f'SSH 命令超时({timeout}s): {command!r}')
            try:
                source, data = await asyncio.wait_for(session.queue.get(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(f'SSH 命令超时({timeout}s): {command!r}') from exc

            if source == 'stdout':
                buffer += data
                continue
            if source == 'error':
                raise RuntimeError(data)
            raise RuntimeError(f'SSH 会话已关闭: {session.session_id}')


async def close_session(session: SshSession) -> None:
    try:
        session.pump_task.cancel()
    except Exception:
        pass

    try:
        session.process.stdin.write('exit\n')
        await session.process.stdin.drain()
    except Exception:
        pass

    try:
        session.process.close()
    except Exception:
        pass

    try:
        session.connection.close()
    except Exception:
        pass

    wait_closed = getattr(session.connection, 'wait_closed', None)
    if callable(wait_closed):
        try:
            await wait_closed()
        except Exception:
            pass


def close_all_sessions_now() -> None:
    """atexit: 进程退出时同步关闭所有 session 的连接."""
    if SshRegistry._sweeper_task and not SshRegistry._sweeper_task.done():
        SshRegistry._sweeper_task.cancel()
    with SshRegistry._lock:
        all_sessions = [s for sess_map in SshRegistry._sessions.values() for s in sess_map.values()]
        SshRegistry._sessions.clear()
        SshRegistry._active.clear()
    for session in all_sessions:
        _sync_close(session)


atexit.register(close_all_sessions_now)
