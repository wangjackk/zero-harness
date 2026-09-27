"""BackgroundShell 运行时:session 级 task registry + 日志 pump + tail.

按 session_id 隔离:多 agent 并发时各自只能看/管自己 session 下的 task.
跟 _cwd_state.py 同一模式.
"""
from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class BgTask:
    task_id: str
    command: str
    cwd: str
    pid: int
    log_path: str
    start_time: float
    process: asyncio.subprocess.Process
    pump_task: asyncio.Task[Any]


class BgRegistry:
    """session_id -> {task_id -> BgTask} 的进程级注册表.

    多 agent 并发时按 session 隔离:agent A 看不到 agent B 的 task.
    """

    _tasks: ClassVar[dict[str, dict[str, BgTask]]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def set(cls, session_id: str, task_id: str, task: BgTask) -> None:
        with cls._lock:
            cls._tasks.setdefault(session_id, {})[task_id] = task

    @classmethod
    def get(cls, session_id: str, task_id: str) -> BgTask | None:
        with cls._lock:
            return cls._tasks.get(session_id, {}).get(task_id)

    @classmethod
    def remove(cls, session_id: str, task_id: str) -> BgTask | None:
        with cls._lock:
            session = cls._tasks.get(session_id)
            if session is None:
                return None
            task = session.pop(task_id, None)
            if not session:
                cls._tasks.pop(session_id, None)
            return task

    @classmethod
    def list_session(cls, session_id: str) -> dict[str, BgTask]:
        with cls._lock:
            return dict(cls._tasks.get(session_id, {}))

    @classmethod
    def clear_session(cls, session_id: str) -> None:
        with cls._lock:
            cls._tasks.pop(session_id, None)


async def pump_to_log(proc: asyncio.subprocess.Process, log_path: str) -> None:
    """把 proc stdout+stderr 持续写日志文件,直到进程退出 stdout 关闭."""
    stream = proc.stdout
    if stream is None:
        await proc.wait()
        return
    with open(log_path, 'wb') as f:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            f.write(chunk)
            f.flush()
    await proc.wait()


def tail_file(path: str, n: int = 50) -> str:
    """读日志文件末尾 n 行(utf-8,errors replaced)."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except FileNotFoundError:
        return ''
    if not lines:
        return ''
    return ''.join(lines[-n:])


def is_running(task: BgTask) -> bool:
    return task.process.returncode is None
