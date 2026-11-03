"""chan 族的公共薄壳: 定位 shell + 发一条控制面 req.

四个动作(开轨/看轨/清轨/回收轨)是**同一张轨表的四扇门**: 共享的是"找
shell"与"回执点灯"这两件事, 各自只声明自己的入参与示例.

分族判据是**对象**: 族内每个动作都针对**一条具体的轨**(都要 ``name``)——
开它、看它、清它、删它. ``interrupt`` 不在族里, 因为它的对象是"所有轨",
没有 name 可说; 它是"全停", 不是"某条轨的操作".

为什么一族四扇门而不是一个 ``<chan op="..."/>``: op 只是同一张表上的动词,
写进名字里模型不必记"op 有哪些取值、哪个要 name"; 也因为四者的**后果**
不同(开轨改拓扑 / 看轨只读 / 清轨丢排在跑的停 / 删轨回收实例), 分成四个
名字, 词表清单才能各给一句自己的话(见 prompt/capability).

控制面入口只有两个: 轨表本身是 ``shell.chan``(op 区分), 清轨是
``shell.clear``(它已经带着"清道夫不清自己"的豁免逻辑, 不复刻). ``op`` 与
动作名后半段、与 channel 自己的 ``@request('status')`` 同名 —— 一层叫
status, 三层都叫 status, 中间没有翻译.

共同声明 ``observe``: 轨表变更的回执是"世界变了"的唯一凭据, 查询
的回执是"答案"本身 —— 不点灯那一拍就白问了. 唯一例外是 ``chan_clear``:
它跟 ``interrupt`` 一样是中断类, 回执不需要点灯(清完就清完了).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from routine.routine import Routine

__all__ = ['ChanCommand']


class ChanCommand(Routine):
    """chan 族基类: ``op`` 由子类声明, 这里只管找宿主与点灯."""

    op: ClassVar[str] = ''

    meta: ClassVar[Dict[str, Any]] = {
        'observe': True,
    }

    async def _shell_rid(self) -> str:
        """宿主 shell 的实例 id: 轨表只有它一本(按名自解析, 同 chan_create)."""
        for r in await self.get_running_routines():
            if r.get('name') == 'shell':
                return r['id']
        raise RuntimeError('routine not running: shell')

    async def _ctl(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """向宿主 shell 发一条控制面 req: 轨表是 ``'chan'``, 清轨是 ``'clear'``."""
        return await self.req(await self._shell_rid(), event, payload)

    async def _chan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._ctl('chan', {'op': self.op, **payload})

    async def stop(self) -> None:
        pass
