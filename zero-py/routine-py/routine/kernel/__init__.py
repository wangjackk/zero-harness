"""kernel ---- 内核子包: Tree + Kernel 调度本体 + 组装入口.

分层:
  command / tree / manager    命令、节点树、多 peer 调度(kernel 核心)
  inproc                      hub↔kernel 进程内直连(FIFO + 查询直达)
  grpc_server                 kernel 的 grpc server(dial-in, 外部 hub 拨入)
  launch                      组装入口: kernel.create / kernel.remote / connect

依赖方向: kernel → routine 顶层(hub/protocol/transport, 单向);
应用侧 ``from routine import kernel`` 后 ``kernel.create(...)`` 组装.
"""
from .command import Command, State
from .grpc_server import GrpcKernelServer
from .manager import Kernel, KernelConn
from .tree import ConflictError, Tree
from .launch import Connection, InprocKernel, KernelRef, RemoteKernel, connect, create, remote

__all__ = [
    'Command', 'State',
    'ConflictError', 'Kernel', 'KernelConn', 'Tree',
    'GrpcKernelServer',
    'Connection', 'InprocKernel', 'KernelRef', 'RemoteKernel',
    'connect', 'create', 'remote',
]
