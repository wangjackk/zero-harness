"""routine 日志器(与 Go 侧 kernel/logger 格式一致的着色 formatter).

自 routine-py 的 logger.py 原样拷入: 业务代码 ``from routine.logger import
setup_logger`` 与原 ``from routine.logger import ...`` 用法一致.

格式::

    2025-10-27 15:16:28.080 INFO RoutineHub - message server.py:42

整行按级别着色(INFO 绿 / DEBUG 青 / WARNING 黄 / ERROR 红),调用者文件名灰色.
self-contained:不依赖 colorlog,直接用 ANSI 转义码.
"""
from __future__ import annotations

import logging
import sys

# ANSI 颜色代码(跟 Go 侧 kernel/logger 一致).
_RESET = '\033[0m'
_RED = '\033[31m'      # ERROR
_YELLOW = '\033[33m'   # WARNING
_GREEN = '\033[32m'    # INFO
_CYAN = '\033[36m'     # DEBUG
_GRAY = '\033[90m'     # 文件名(灰色)

_LOG_COLORS = {
    'DEBUG': _CYAN,
    'INFO': _GREEN,
    'WARNING': _YELLOW,
    'ERROR': _RED,
    'CRITICAL': _RED,
}


class ColoredFormatter(logging.Formatter):
    """着色 formatter:时间+级别+name+msg+caller,整行按级别着色,caller 灰色."""

    def format(self, record: logging.LogRecord) -> str:
        color = _LOG_COLORS.get(record.levelname, _RESET)
        time_str = self.formatTime(record, datefmt='%Y-%m-%d %H:%M:%S')
        msecs = int(record.msecs)
        caller = f'{record.filename}:{record.lineno}'
        # 格式:<color><time>.<msec> <LEVEL> <name> - <msg><reset> <gray><caller><reset>
        return (
            f'{color}{time_str}.{msecs:03d} {record.levelname} '
            f'{record.name} - {record.getMessage()}{_RESET} '
            f'{_GRAY}{caller}{_RESET}'
        )


def _reconfigure_stdio_utf8() -> None:
    # Windows 控制台默认 GBK,emoji/中文宽字符编码失败 → 重配为 utf-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')
            except Exception:
                pass


def setup_logger(name: str = 'routine', level: int = logging.INFO) -> logging.Logger:
    """构造/获取命名 logger(单例:同名第二次调返回已有实例).

    handler 只挂一次(避免重复输出).
    """
    _reconfigure_stdio_utf8()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(ColoredFormatter())
    logger.addHandler(handler)
    logger.propagate = False  # 不冒泡到 root logger(避免 basicConfig 重复输出)
    return logger


def configure_root(level: int = logging.INFO) -> None:
    """配 root logger 用 ColoredFormatter,统一所有非 routine/kernel logger 的格式.

    应用入口调一次,替代 ``logging.basicConfig``.
    """
    _reconfigure_stdio_utf8()
    root = logging.getLogger()
    root.setLevel(level)
    # 清掉 basicConfig 等历史 handler,避免重复输出 / 格式冲突.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(ColoredFormatter())
    root.addHandler(handler)
