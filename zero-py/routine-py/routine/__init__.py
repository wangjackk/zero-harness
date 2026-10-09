"""routine ---- SDK: 业务 routine 编写面 + 内嵌 kernel.

分层:
  protocol / errors / transport / logger   wire 常量、错误、传输抽象、日志
  routine / ctx / handle                   Routine 基类 / 运行上下文 / 句柄
  lifecycle / runtime / hub                hub 侧生命周期、运行时、通信编排
  grpc_client                              hub 侧 grpc client transport(拨入 kernel)
  kernel                                   内核子包(Tree/Kernel 调度本体 + 组装入口)

组装::

    from routine import kernel

    k = await kernel.create(modules, listen='0.0.0.0:52051')   # dict → 进程内 kernel
    k = kernel.remote('127.0.0.1:52051')                        # addr → 远程 kernel 引用
    conn = await hub.connect(k)                                 # 传输 + catalog push

依赖方向: kernel 子包 → 顶层(hub/protocol/transport), 单向.
"""
from .errors import (
    AcquireError, DeregisterError, LoadModuleError, RegisterError, ReloadError,
    ReleaseError, ReqError, ReqTimeout, StartError, StreamCancelled,
    StreamError, StreamTimeout, SubmitError, UnloadModuleError,
)
from .grpc_client import GrpcClientTransport
from .handle import RoutineHandle
from .hub import RoutineHub
from .kernel import (
    Connection, GrpcKernelServer, InprocKernel, KernelRef, RemoteKernel,
    connect,
)
from .logger import configure_root, setup_logger
from .module_tree import ModuleTree
from .routine import (
    PRI_NORMAL, PRI_SAFETY, PRI_USER,
    Modules, PassiveConfig, Routine, RoutineSource, Routines, passive_wire,
    request, stream, subscribe,
)

__all__ = [
    'AcquireError', 'DeregisterError', 'LoadModuleError', 'RegisterError',
    'ReloadError', 'ReleaseError', 'ReqError', 'ReqTimeout', 'StartError',
    'StreamCancelled', 'StreamError', 'StreamTimeout', 'SubmitError',
    'UnloadModuleError',
    'GrpcClientTransport',
    'RoutineHub',
    'RoutineHandle',
    'ModuleTree',
    'Connection', 'GrpcKernelServer', 'InprocKernel', 'KernelRef',
    'RemoteKernel', 'connect',
    'PRI_NORMAL', 'PRI_SAFETY', 'PRI_USER',
    'Modules', 'PassiveConfig', 'Routine', 'RoutineSource', 'Routines',
    'passive_wire', 'request', 'stream', 'subscribe',
    'configure_root', 'setup_logger',
]
