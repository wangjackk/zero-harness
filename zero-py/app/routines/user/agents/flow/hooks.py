"""进程内扩展点: waterfall 决策链 + serial 顺序观察.

对齐 dsh 的事件分发语义 (但作用域是 agent 进程内, 不跨 wire):

- waterfall: handler(payload, next) -> 决策. 调 next() 委托下游 (下游
  结果可包装后返回); 不调 next() 直接返回即短路改写. 注册序 = 洋葱外层序.
- serial: handler(payload) 按注册序 await, 纯观察无返回值传递.

约定的事件点 (loop 驱动时分发; 首环由 agent 默认注册, 第三方可组合):
- pre_step        waterfall  默认 {kind: enter, messages} | 可改 {kind: reject}
                              (默认首环: pruner 裁剪 → 仍超才压力压缩)
- request         waterfall  payload 携带 tools/effort, 可改写后生效
- request_error   waterfall  返回 {kind: retry} 则重试, 否则终态
                              (默认首环: 上下文溢出恢复)
- turn_stopping   serial     轮次将结束前的观察点
- tool_pre_execute waterfall 默认放行, 可改 {kind: reject, error}
- tool_post_execute waterfall 结果定稿前, 可改写 LLM 可见 output
- tool_result     serial     单个工具结果落定后的观察点
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Awaitable, Callable

WaterfallHandler = Callable[[dict, Callable[[], Awaitable[Any]]], Awaitable[Any]]
SerialHandler = Callable[[dict], Awaitable[None]]


class HookRegistry:
    """waterfall / serial 钩子注册表. agent 内单例, 各事件点独立成链."""

    def __init__(self) -> None:
        self._waterfall: dict[str, list[WaterfallHandler]] = defaultdict(list)
        self._serial: dict[str, list[SerialHandler]] = defaultdict(list)

    def on_waterfall(self, event: str, handler: WaterfallHandler,
                     *, prepend: bool = False) -> None:
        if prepend:
            self._waterfall[event].insert(0, handler)
        else:
            self._waterfall[event].append(handler)

    def on_serial(self, event: str, handler: SerialHandler,
                  *, prepend: bool = False) -> None:
        if prepend:
            self._serial[event].insert(0, handler)
        else:
            self._serial[event].append(handler)

    async def waterfall(self, event: str, payload: dict, default: Any) -> Any:
        """沿注册链分发; 无 handler 时返回 default.

        handler(payload, next): next() 委托下游并返回其结果; 链尾的
        next() 返回 default. 不调 next() 即短路. 注册序 = 执行序
        (先注册 = 洋葱外层).
        """
        handlers = self._waterfall.get(event) or ()

        async def call(i: int) -> Any:
            if i >= len(handlers):
                return default
            return await handlers[i](payload, lambda: call(i + 1))

        return await call(0)

    async def serial(self, event: str, payload: dict) -> None:
        for handler in self._serial.get(event) or ():
            await handler(payload)
