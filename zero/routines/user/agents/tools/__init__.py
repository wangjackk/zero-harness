"""prime agent 工具集 ---- file_ops / shell / remote / utils.

本 ``__init__`` 即包 manifest: re-export 的 Routine 类在目录条目加载时注册
(子包经 import 链自然引入, loader 不递归扫目录).
"""
from .file_ops import Edit, Glob, Grep, Read, Write
from .remote import SshConnect, SshExec, SshTransfer, SshDisconnect, SshList, WebFetch, WebSearch
from .shell import Bash, BackgroundShell, IPython
from .utils import RunRoutine, TodoWrite

__all__ = [
    'Edit', 'Glob', 'Grep', 'Read', 'Write',
    'SshConnect', 'SshExec', 'SshTransfer', 'SshDisconnect', 'SshList',
    'WebFetch', 'WebSearch',
    'Bash', 'BackgroundShell', 'IPython',
    'RunRoutine', 'TodoWrite',
]
