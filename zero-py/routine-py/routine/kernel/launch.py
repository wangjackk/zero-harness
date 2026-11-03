"""launch ---- kernel 句柄与组装入口: kernel.create / connect.

用法::

    from routine import kernel

    # dict 模块表 → 起进程内 kernel(listen 可选,开 grpc dial-in 口)
    kernel_ref = await kernel.create(modules, listen='0.0.0.0:52051')

    # str 地址 → 远程 kernel 引用(jetson / IPython 拨入场景,不建本地实体)
    kernel_ref = await kernel.create('127.0.0.1:52051')

    hub = RoutineHub(routines)
    conn = await hub.connect(kernel_ref)   # inproc: link 绑定 + catalog push;
                                           # remote: grpc transport + 重连 + catalog push
    ...
    await conn.close()   # 级联关停(grpc server → kernel → link / transport)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..grpc_client import GrpcClientTransport
from ..hub import RoutineHub
from ..transport import Transport
from .grpc_server import GrpcKernelServer
from .inproc import InProcessLink, InProcessTransport
from .manager import Kernel
from .tree import Tree


class KernelRef:
    """hub.connect() 接受的 kernel 句柄基类(进程内实体或远程引用)."""


class InprocKernel(KernelRef):
    """进程内 kernel 实体(kernel.create(modules) 产物)."""

    def __init__(self, kernel: Kernel, link: InProcessLink,
                 grpc_server: Optional[GrpcKernelServer] = None) -> None:
        self.kernel = kernel
        self.link = link
        self.grpc_server = grpc_server

    @property
    def address(self) -> Optional[str]:
        """grpc dial-in 监听地址(未开 listen 则 None)."""
        return self.grpc_server.address if self.grpc_server else None


class RemoteKernel(KernelRef):
    """远程 kernel 引用(kernel.remote(addr) 产物);hub.connect 时经 grpc 拨入."""

    def __init__(self, address: str) -> None:
        self.address = address


async def create(modules: Optional[Dict[str, Dict[str, Any]]] = None, *,
                 listen: Optional[str] = None) -> InprocKernel:
    """起进程内 kernel.``modules`` 是 flat 模块表 ``{id: {"name": ..., "children":
    [...]}}``(根固定 'root');``listen`` 给出则再开 grpc dial-in 口,返回地址在
    ``.address``.启动后接 ``hub.connect()`` 完成 hub 侧组装."""
    tree = Tree.from_dict({'modules': modules or {'root': {}}})
    kernel = Kernel(tree)
    link = InProcessLink()
    kernel.bind(link)
    grpc_server: Optional[GrpcKernelServer] = None
    if listen:
        grpc_server = GrpcKernelServer(kernel, listen)
        await grpc_server.start()
    await kernel.start()
    return InprocKernel(kernel, link, grpc_server)


def remote(address: str) -> RemoteKernel:
    """远程 kernel 引用(不建本地实体);hub.connect 时经 grpc 拨入."""
    return RemoteKernel(address)


class Connection:
    """hub.connect() 的产物:hub ↔ kernel 连接持有体,``close()`` 关闭连接."""

    def __init__(self, hub: RoutineHub, kernel: KernelRef,
                 transport: Transport) -> None:
        self.hub = hub
        self.kernel = kernel
        self.transport = transport

    async def close(self) -> None:
        """关闭连接:kernel → grpc server → link(inproc)/ transport(dial).

        kernel 只对**同进程** hub 的节点请求-确认(见 _stop_node): 那批 stop
        必须趁 link 还活着发出去, 所以 kernel 在 link 之前。跨进程 hub 的节点
        只结算 kernel 侧的投影, 由对端在断链时自清 ---- grpc 的关闭本身就是
        那条通告, 不需要 kernel 逐个节点去等确认。
        """
        k = self.kernel
        if isinstance(k, InprocKernel):
            await k.kernel.stop()
            if k.grpc_server:
                # 只给 0.5s: peer 是不断重连的 dial-in 客户端, 不会自己关流;
                # 这里在退出路上, 陪它吃满 2s 的 grace 只换来"关停完成 (4.0s)".
                await k.grpc_server.stop(grace=0.5)
            await k.link.stop()
        else:
            await self.transport.close()


async def connect(hub: RoutineHub, kernel: KernelRef) -> Connection:
    """组装 hub ↔ kernel(RoutineHub.connect 的实现体,也可直接调用).

    - inproc: link 双向绑定 + worker 起 + catalog push(启动序列对齐原
      launch(): bind → kernel.start → link.start → push).
    - remote: GrpcClientTransport 拨入(post_connect 自动拉 module.tree +
      catalog push,断线自动重连).
    """
    if isinstance(kernel, InprocKernel):
        transport = InProcessTransport(kernel.link)
        hub.transport = transport
        kernel.link.bind(kernel.kernel, hub.dispatch_inbound)
        await kernel.link.start()
        await hub.send_catalog_push()
    else:
        transport = GrpcClientTransport(kernel.address)
        transport.attach(hub)
        # 断线时 hub 清理本地 handle + 销毁本地实例; await 销毁完成才放行
        # 重连(否则销毁期与重建期重叠, rid 撞车误杀新实例).
        async def _peer_down(peer_id: str) -> None:
            await hub.handle_peer_down(peer_id)
        transport.set_peer_down(_peer_down)
        hub.transport = transport
        await transport.start()
        await transport.wait_ready()
    return Connection(hub, kernel, transport)
