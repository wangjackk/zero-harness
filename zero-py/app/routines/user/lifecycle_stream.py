"""LifecycleStream -- kernel routine 树生命周期流的前端桥.

passive 常驻: watch_lifecycle() 订阅 kernel 观察流(快照+增量), 每帧
publish 到 pubsub topic 'lifecycle'; web_server @subscribe('lifecycle')
收帧后 WS 广播给前端渲染 routine 树.

帧格式(wire, 全 uniform): {event:'lifecycle.event', type:created/started/
stopped/destroyed, id, parent_id('0'=根), name, seq}. 消费端按 id 幂等
upsert/remove. 实例停止时框架自动 unwatch(最后一个观察者注销才关流).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from routine import Routine

from routine.logger import setup_logger

_log = setup_logger('lifecycle_stream')


class LifecycleStream(Routine):
    """kernel 生命周期流 → pubsub 'lifecycle' → web_server WS 广播."""

    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'kernel routine 树生命周期流桥: 观察帧经 pubsub '
                       "topic 'lifecycle' 转发, web_server 广播给前端渲染.",
    }

    async def run(self, kwargs: Dict[str, Any]):
        ctx = self.ctx
        q = await ctx.watch_lifecycle()
        _log.info('lifecycle stream up (watcher %s)', ctx.id)
        while True:
            frame = await q.get()
            await ctx.publish('lifecycle', frame)
