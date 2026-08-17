"""QueryService ---- Req unary 查询:get_modules / get_routines.

也处理 kernel→server 的 module.tree 推送(dial-out 同步 Req,server 缓存后回 ok).
dial-in 下 module.tree 走 Stream 事件(fire-and-forget),由 RoutineHub.dispatch_inbound
调本类的 cache_module_tree 共用缓存逻辑.

dial-in 下 catalog 不走 Req(方向矛盾)----routine 主动 push catalog.push 事件,组
routines 列表用本类的 build_routines(跟 get_routines 共用组装).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .module_tree import ModuleTree
from .protocol import (
    MODULE_TREE,
    REQ_EVENT_GET_MODULES,
    REQ_EVENT_GET_ROUTINES,
)
from .routine import passive_wire


class QueryService:
    def __init__(self, server, runtime):
        self.server = server
        self.runtime = runtime

    async def handle_req(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Req 查询入口(dict 进 dict 出,Frame 编解码在 transport 层)."""
        event = msg.get('event', '')

        # module.tree:kernel 推模块树拓扑(dial-out 走同步 Req).缓存逻辑跟 dial-in
        # Stream 路径共用 cache_module_tree----保证缓存完毕后 kernel 才拉 catalog +
        # auto-start passive(避免 routine 跑起来时拓扑未缓存的竞速).拓扑随
        # connect/disconnect 变,kernel 每次变更重推全量(reconnect 重推直接覆盖).
        # server 缓存后本地算 cone/conflict----业务侧编排策略(AutoSP 等)用.
        if event == MODULE_TREE:
            return {'ok': self.cache_module_tree(msg)}

        if event == REQ_EVENT_GET_MODULES:
            return {'modules': self.runtime.modules}

        if event == REQ_EVENT_GET_ROUTINES:
            # dial-out: hub_id 随首次 get_routines 响应带给 kernel,
            # kernel 据此校验唯一性(重复则 Close 这条 conn,拒绝连接).
            return {
                'routines': self.build_routines(),
                'hub_id': self.server.hub_id,
            }

        return {}

    def cache_module_tree(self, msg: Dict[str, Any]) -> bool:
        """缓存 kernel 推来的 module.tree 拓扑(Req/Stream 共用).成功 True."""
        tree_payload = msg.get('tree')
        if isinstance(tree_payload, dict):
            try:
                self.runtime.module_tree = ModuleTree.from_dict(tree_payload)
            except ValueError as exc:
                # 畸形 tree(payload 缺 id / child 非法):不抛,不拆流,降级警告 +
                # 保留旧树.外层 recv 循环也做 per-message 隔离兜底,此处是针对性修复.
                self.server._logger.warning(
                    f'🌳 module.tree 解析失败: {exc} (payload={tree_payload!r})')
                return False
            self.server._logger.info(
                f'🌳 module tree cached: {self.runtime.module_tree}')
            return True
        self.server._logger.warning(f'🌳 module.tree payload 缺 tree: {msg!r}')
        return False

    def build_routines(self) -> List[Dict[str, Any]]:
        """组装 routine 列表(get_routines Req 与 catalog.push push 共用).

        meta:类级自由扩展字典,随 routine 信息一起序列化.
        消费方(如 react_agent)读 meta['input_schema']/meta['description'] 渲染 LLM
        tool prompt.Go 侧 dumb forward 透传.modules 不在此上报----实例级,由 created
        回报带回(catalog 注册时无实例,无 kwargs,静态上报对 dynamic 不准).
        """
        routines: List[Dict[str, Any]] = []
        for cls in self.runtime.routines.get_routines():
            meta = {**cls.meta}
            if cls.meta.get('hidden', False):
                meta['hidden'] = True
            routines.append({
                'name': cls.name,
                'is_passive': passive_wire(cls),
                'meta': meta,
            })
        return routines

