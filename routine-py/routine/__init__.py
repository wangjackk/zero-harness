"""精简 routine SDK -- create / start / stop 生命周期 + 跨 routine 通信.

通信(req / streamreq 骑 p2p 隧道,kernel dumb forward)经 ``@request`` / ``@stream``
装饰器 + ``RunContext.req`` / ``stream_req`` 暴露.routine 体由本 SDK 的 server
实例化运行;调度器(kernel)通过 gRPC lifecycle 事件驱动,并 dumb-forward p2p 帧.

uv 安装:``uv pip install -e routine-py`` 或作 path 依赖被 demo 引用.
"""
from .ctx import RunContext, RoutineHubLike, RoutineIO
from .errors import (
    AcquireError, ReqError, ReqTimeout, ReleaseError, StartError,
    StreamCancelled, StreamError, StreamTimeout, SubmitError,
)
from .grpc_client import GrpcClientTransport
from .grpc_server import GrpcServerTransport
from .handle import RoutineHandle
from .module_tree import ModuleTree
from .routine import Modules, RoutineSource, Routine, Routines, request, stream, subscribe
from .server import RoutineHub, start_client, start_server
from .transport import Transport

__all__ = [
    'Routine',
    'Routines',
    'RoutineHandle',
    'RunContext',
    'RoutineHub',
    'RoutineHubLike',
    'RoutineIO',
    'ModuleTree',
    'Modules',
    'Transport',
    'GrpcServerTransport',
    'GrpcClientTransport',
    'start_server',
    'start_client',
    'RoutineSource',
    'request',
    'stream',
    'subscribe',
    'ReqError',
    'ReqTimeout',
    'StartError',
    'SubmitError',
    'AcquireError',
    'ReleaseError',
    'StreamError',
    'StreamCancelled',
    'StreamTimeout',
]
