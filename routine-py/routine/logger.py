"""routine SDK 日志器.

格式跟 Go 侧 kernel/logger 完全一致::

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
_CYAN = '\033[36m'      # DEBUG
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


# 向后兼容:原私有别名.
_ColoredFormatter = ColoredFormatter


def setup_logger(name: str = 'routine', level: int = logging.INFO) -> logging.Logger:
    """构造/获取命名 logger(单例:同名第二次调返回已有实例).

    handler 只挂一次(避免重复输出).
    Windows 默认控制台编码(GBK)打 emoji 会 UnicodeEncodeError,强制 stdout
    重配为 utf-8(PYTHONIOENCODING 也行,但代码里设更可靠).
    """
    # Windows 控制台默认 GBK,emoji/中文宽字符编码失败 → logger 抛异常被 grpc
    # 吞成 traceback.重配 stdout/stderr 为 utf-8(Python 3.7+ reconfigure).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')
            except Exception:
                pass

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_ColoredFormatter())
    logger.addHandler(handler)
    logger.propagate = False  # 不冒泡到 root logger(避免 basicConfig 重复输出)
    return logger


def configure_root(level: int = logging.INFO) -> None:
    """配 root logger 用 ColoredFormatter,统一所有非 routine SDK logger 的格式.

    应用入口(main.py)调一次,替代 ``logging.basicConfig``.之后所有走 root 的
    logger(第三方库如 websockets / 业务模块用 ``getLogger(__name__)``)都跟
    ``setup_logger`` 创建的 logger 格式一致:时间+级别+name+msg+caller+着色.

    ``setup_logger`` 创建的 logger 仍 ``propagate=False`` 不冒泡,但格式相同,
    视觉上统一.第三方库和用 ``getLogger(__name__)`` 的模块冒泡到 root 走本 handler.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')
            except Exception:
                pass

    root = logging.getLogger()
    root.setLevel(level)
    # 清掉 basicConfig 等历史 handler,避免重复输出 / 格式冲突.
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(ColoredFormatter())
    root.addHandler(handler)
