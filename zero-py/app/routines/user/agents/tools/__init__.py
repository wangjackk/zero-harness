"""remote 工具集 ---- Ssh* / WebFetch / WebSearch.

本 ``__init__`` 即包 manifest: re-export 的 Routine 类在目录条目加载时注册
(子包经 import 链自然引入, loader 不递归扫目录).

prime 时期的 file_ops / shell / utils 已随 prime 下线, 代码保留在各自
子包供考古, 不再 re-export/注册.
"""
from .remote import SshConnect, SshExec, SshTransfer, SshDisconnect, SshList, WebFetch, WebSearch

__all__ = ['SshConnect', 'SshExec', 'SshTransfer', 'SshDisconnect', 'SshList',
           'WebFetch', 'WebSearch']
