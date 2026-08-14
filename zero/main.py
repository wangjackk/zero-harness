"""zero 应用入口:注册 routines + 打 banner + 起 routine server / client.

用精简 routine SDK(``routine`` 包).骨架阶段不带 inference / output 等业务
链路----只验注册 + 打印 + 起 server 通.

HTTP + WS 前门由 ``WebServer`` passive routine 自动起(kernel auto-start),
不在这里手动启动----main.py 只起 routine server/client.

两种启动模式(跟 kernel 的 ``config.yaml`` 两段对应):

  - **server 模式**(默认):zero 当 grpc server 监听,kernel 用 ``as_grpc_client`` 连.
    跑::

        uv run python -m zero.main                      # 默认 0.0.0.0:7777
        uv run python -m zero.main 127.0.0.1:50071       # argv 覆盖监听地址

  - **client 模式**(``--client``):zero 当 grpc client,拨 kernel 的 ``as_grpc_server``
    监听地址.kernel config 需配 ``as_grpc_server.enable: true``.业务层(RoutineHub)
    两模式共用,只换 transport.跑::

        uv run python -m zero.main --client 127.0.0.1:50051   # 拨 kernel as_grpc_server
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (_PROJECT_ROOT, _PROJECT_ROOT / 'routine'):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

from routine import start_client, start_server
from routine.logger import configure_root

# 统一 root logger 格式(跟 routine SDK 的 setup_logger 一致:ColoredFormatter).
# 所有走 root 的 logger(第三方库 / getLogger(__name__))都跟 setup_logger 格式对齐.
configure_root(logging.INFO)


def _reconfigure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='backslashreplace')
            except Exception:
                pass

if __package__ in (None, ''):
    from zero.routines import get_routines
    from zero.modules import get_modules
    from zero.routines.banner import print_banner
else:
    from .routines import get_routines
    from .modules import get_modules
    from .routines.banner import print_banner


async def serve(addr: str, *, client: bool = False) -> None:
    _reconfigure_stdio_utf8()
    routines = get_routines()
    modules = get_modules()

    print(f'pid: {os.getpid()}')

    print_banner(routines, modules)

    # HTTP + WS 前门由 WebServer passive routine 自动起(kernel auto-start),
    # 这里不手动启动.
    if client:
        print(f'🔌connecting to kernel server: {addr}')
        # 地址单一事实源: 子进程 (IPython kernel) 经 env 透传感知 kernel 地址,
        # hub_routine 等 skill 自动读到, agent 无需关心.
        os.environ['ZERO_KERNEL_ADDR'] = addr
        await start_client(
            routines=routines,
            modules=modules,
            address=addr,
            hub_id='zero',
        )
    else:
        print(f'📡 bind to: {addr}')
        await start_server(
            routines=routines,
            modules=modules,
            address=addr,
            hub_id='zero',
        )


def _main() -> None:
    print(f'Python 版本: {sys.version}')
    client = True
    addr = '0.0.0.0:7777' if not client else '127.0.0.1:8889'
    asyncio.run(serve(addr, client=client))


if __name__ == '__main__':
    _main()
