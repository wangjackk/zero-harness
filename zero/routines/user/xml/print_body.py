"""PrintBody ---- 最小叶子 routine:收 message body 原样打印.

给 XmlRoutine 当被编排的子 routine,验证 send 投递的 body 流被子正确接收.
不占模块(多个可并行);不编排子(纯叶子,直接继承 Routine 自己 on_message 收).

是 XmlRoutine 子侧的最小样板:任何"收 body 自己处理"的叶子 routine 都长这样----
``on_message(source, data)`` 收 ``{'text': chunk, 'id': seq}``,按 id reorder 顺序
处理,``_eof`` 终结.spawn 并发派发→业务 id reorder 保证顺序.
"""
import asyncio
from typing import Any, Dict

from routine import Routine, RoutineSource


class PrintBody(Routine):
    """收 body 原样打印的叶子 routine."""

    meta = {'description': '收 message body 打印(最小叶子样板)'}

    def __init__(self) -> None:
        super().__init__()
        self._buf: list[str] = []
        self._next_id: int = 0
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._done: bool = False

    async def on_message(self, source: RoutineSource, data: Any) -> None:
        """收 body chunk(带 id):按 id reorder 后 append,保证顺序.

        spawn 并发 fire----多条 chunk 可能乱序到达,靠 ``data['id']`` 排序.
        eof 也带 id,顺序到了才置 _done(避免 eof 抢先丢掉未到的 chunk).
        """
        if self._done or not isinstance(data, dict):
            return
        seq = int(data.get('id', 0))
        self._pending[seq] = data
        # 顺序推进:next_id 到了就处理,直到下一个不连续
        while self._next_id in self._pending:
            m = self._pending.pop(self._next_id)
            self._next_id += 1
            if m.get('_eof'):
                self._done = True
                total = sum(len(s) for s in self._buf)
                self._logger.info('print_body %s done, total=%d chars: %r',
                                  self.id, total, ''.join(self._buf))
                break
            text = m.get('text', '')
            if text:
                self._buf.append(text)
                self._logger.info('print_body %s chunk: %r', self.id, text)

    async def run(self, kwargs: Dict[str, Any]) -> dict[str, int]:
        # start 只负责等 eof 到达(on_message 在 created 后就收).
        # 简单轮询 _done----叶子 routine,body 流终结即完成.
        while not self._done:
            await asyncio.sleep(0.1)
        return {'chars': sum(len(s) for s in self._buf)}
