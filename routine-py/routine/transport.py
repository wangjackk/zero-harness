"""Transport ---- 传输层抽象:入站投递 + 出站发送 + 启停,传输无关.

对标 Go 侧 kernel/conn(契约)+ kernel/grpc(实现)的 split:RoutineHub 持有一个
Transport,不关心 wire(grpc / 将来 in-proc / ws).两个 grpc 实现:

  - GrpcServerTransport(routine 当 grpc server,dial-out 模型):kernel 主动 dial 进来.
  - GrpcClientTransport(routine 当 grpc client,dial-in 模型):routine 主动 dial kernel server.

入站:transport 收到命令 dict 时调 inbound 回调 `(peer_id, msg)`----server.dispatch_inbound
按 event 分发(lifecycle.start/stop/destroy/created → LifecycleManager;其余 → on_inbound).
出站:server 的 send_* helpers build payload 后调 `transport.send_event(payload, peer_id)`.
peer 断开:transport 调 peer_down 回调 → server.on_peer_down → lifecycle.force_stop_peer.

peer_id 语义:dial-out server 下 = context.peer()(可多 kernel client);dial-in client 下
= 固定常量(单 kernel,路由退化为一路).`prid = f'{peer_id}:{rid}'` 仍唯一(rid 全局唯一).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .module_tree import ModuleTree

# 入站回调:(peer_id, msg) -> awaitable
InboundHandler = Callable[[str, Dict[str, Any]], Awaitable[None]]
# peer 断开回调:peer_id -> awaitable
PeerDownHandler = Callable[[str], Awaitable[None]]
# Req 处理(dial-out server 才用):request Struct -> reply Struct
ReqHandler = Callable[[Any], Awaitable[Any]]


class Transport:
    """传输层接口.两个 grpc 实现 + 将来的 in-proc/ws 实现都满足此契约.

    子类需实现 start/stop/send_event.inbound/peer_down/req_handler 由 server 经
    attach 注入(解 t↔s 循环:先建 transport,再建 server 持它,再 attach).

    方向特化方法(仅特定实现支持,调用方据此知道持的是哪种 transport):
      - ``req``:仅 dial-in client 实现(routine 当 grpc client,主动 Req 拉 kernel).
        dial-out server 继承基类 NotImplementedError(routine 当 server 时无 kernel stub).
      - ``resolve_get_running`` / ``resolve_get_module_tree`` / ``set_req_handler``:仅 dial-out
        server 实现.dial-in client 不收 kernel->routine Req,也不走 Stream 请求-回执查 running/tree.
      - ``get_running_routines`` / ``get_module_tree``:两实现都支持(dial-in 走 req,
        dial-out 走 Stream 请求-回执),可在抽象层(ctx)暴露.
    """

    def __init__(self) -> None:
        self._inbound: Optional[InboundHandler] = None
        self._peer_down: Optional[PeerDownHandler] = None
        self._req_handler: Optional[ReqHandler] = None

    # --- 注入回调(server.attach 调)---

    def set_inbound(self, handler: InboundHandler) -> None:
        self._inbound = handler

    def set_peer_down(self, handler: PeerDownHandler) -> None:
        self._peer_down = handler

    def set_req_handler(self, handler: ReqHandler) -> None:
        """Req 处理器(dial-out server 用:kernel→routine Req 查询).
        dial-in client 不用(不收 kernel→routine Req),留 None."""
        self._req_handler = handler

    # --- 子类实现 ---

    async def start(self) -> None:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError

    async def send_event(self, payload: Dict[str, Any],
                         peer_id: Optional[str] = None) -> None:
        """出站:发事件 dict 给 peer(peer_id=None 广播所有 peer).fire-and-forget."""
        raise NotImplementedError

    async def req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """routine→kernel Req 查询(同步回执).
        dial-in client 实现(发 Req 给 kernel);dial-out server 继承本方法
        (routine 当 server 时无到 kernel 的 client stub,方向矛盾不支持).
        """
        raise NotImplementedError

    async def get_running_routines(self) -> list:
        """查当前所有 running routine 实例 [{name, id}].

        两种实现都问 kernel(kernel 有全局 nodes 视图,跨进程正确),不读本地 runtime:
        - dial-in client:经 Req unary(self.req)问 kernel.
        - dial-out server:经 Stream 请求-回执(routine.get_running -> routine.get_running_reply,
          对标 submit/submitted)--routine 当 server 时无到 kernel 的 client stub,Req 方向
          矛盾不支持,只能骑 Stream.
        """
        raise NotImplementedError

    async def get_routines(self) -> list:
        """查全量路由表(catalog 注册的全部 routine,跨所有 conn)[{name, conn_id, is_passive}].

        dial-in client 经 Req unary 问 kernel(kernel HandleReq 处理 get_routines).
        dial-out server 暂未实现(无 kernel client stub,需走 Stream 请求-回执,后续按需补).
        """
        raise NotImplementedError

    def resolve_get_running(self, msg: Dict[str, Any]) -> None:
        """收 routine.get_running_reply 回执(dial-out server 用).基类 no-op--
        dial-in client 不走 Stream 查询(req 直接返回),收不到此事件."""
        pass

    async def get_module_tree(self) -> "Optional[ModuleTree]":
        """主动从 kernel 拉当前 ``module.tree`` 并刷新本地缓存,返回 ``ModuleTree``.

        两种实现都问 kernel(唯一有全局模块树真理源),刷新 ``runtime.module_tree``
        缓存后返回:

        - dial-in client:经 Req unary(``self.req``)问 kernel.
        - dial-out server:经 Stream 请求-回执(``routine.get_module_tree`` ->
          ``routine.get_module_tree_reply``,对标 ``get_running_routines``).

        平时靠 kernel 推送缓存(``module.tree`` 事件 / dial-out 同步 Req);本方法用于
        推送未到或想主动刷新的场景.失败/超时返当前缓存(可能 ``None``).
        """
        raise NotImplementedError

    def resolve_get_module_tree(self, msg: Dict[str, Any]) -> None:
        """收 ``routine.get_module_tree_reply`` 回执(dial-out server 用).基类 no-op--
        dial-in client 走 ``req`` 直接返回,收不到此事件."""
        pass
