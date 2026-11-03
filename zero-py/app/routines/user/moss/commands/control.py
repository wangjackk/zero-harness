"""Interrupt —— 全停: 所有轨上正在跑与排队中的动作, 立即打断.

声明 ``call_soon`` 的理由来自语义本身: "清空排程"这条指令若排在排程里等
前面动作跑完, 它就什么也清不掉了. 原版同样如此 ——
``interrupt_command = PyCommand(interrupt, call_soon=True)``.

关键: ``call_soon`` 不是给这个名字开的旁路, 而是**任何指令都能声明的
调度属性**(原版 ``PyCommand`` 的构造参数). 形态上它仍是普通实例: 同一个
``route`` 投递、同一个 routine 生命周期、同一条回执链路 —— 差别只在
channel 拿到它时不入队, 立刻起跑(见 ``channel._spawn``).

为什么不在 ``commands/chan/`` 族里: 族内每个动作都针对**一条具体的轨**
(都要 ``name``), 而它的对象是"所有轨" —— 没有 name 可说. 清单轨是
``<chan_clear name="b"/>``.

清道夫不清自己: 它不在队列里、也不占 channel 的在跑槽位(那是串行语义的
位置), 所以不被自己发起的 abort 波及.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from routine.routine import Routine

__all__ = ['Interrupt']


class Interrupt(Routine):
    """停掉所有轨(``<interrupt/>``): 队列丢弃、在跑优雅停止."""

    name = 'interrupt'

    meta: ClassVar[Dict[str, Any]] = {
        'description': ('立即停掉所有轨上正在跑与排队中的 routine(改主意/'
                        '打断时用), 不等待它们; 只清某一条轨用 chan_clear'),
        'call_soon': True,
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        k = dict(kwargs or {})
        self._shell: str = str(k.pop('_shell', ''))     # 宿主(轨表唯一出处)

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        r = await self.req(self._shell, 'clear', {'chans': []})
        cleared = list(r.get('cleared') or [])
        return {'content': (f'停了 {", ".join(cleared)}' if cleared
                            else '没有在跑或排队的东西'),
                'details': {'cleared': cleared}}

    async def stop(self) -> None:
        pass
