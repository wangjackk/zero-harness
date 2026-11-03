"""CtmlRoutine —— body 是 CTML 的 routine 的基类: 收流归基类, 解释归子类.

(CTML = Command Token Marked Language, 标签语言的名字; routine 是被调用
的实体. 全称的定义写在 ``body.py`` 头部, 这里不重复.)

一小会儿(``<say>`` / ``<code>`` / ``<all>`` / ``<loop>``)四个消费者各自
写了一遍同一段骨头: 认两条消息、攒原文、eof 幂等、被打断时放行等 body 的
``run``. 长得像的地方只有这些, 不一样的地方恰是每个 routine 的语义 ——
所以基类只收走骨头, **不解释一个字**:

    消息面(全项目唯一一份, 见 ``docs/orchestration.md``)
        {'text': str}   body 增量 —— 分片本身无意义, 拼起来才是 body
        {'eof': True}   body 结束(= 闭标签); 同一实例只认第一次

    子类回答两个问题
        on_body_chunk(text)  这一段来了怎么办 —— 默认只攒着(收完才开工)
        on_body_done()       body 收完了怎么办 —— 默认什么都不做

``say`` 覆盖 chunk(边到边喂引擎), ``code`` 两个都不覆盖(收完才开工),
容器覆盖 chunk(边扫边投), ``loop`` 覆盖 done(整段收完才展开).

基类提供的三样东西, 都是"必须处处一致"的:

- ``body_text``: body 原文(拼好的). 谁要重放/执行/记账都读它, 别自己再攒.
- ``_body_ready``: body 收完了(``await`` 它). **eof 处理失败也会置位** ——
  子类的收尾出错不该把等 body 的人一起卡死.
- ``abort()``: 打断时用. body 不再收, 等 body 的人立刻放行, ``run`` 见
  ``_aborted`` 出截断回执(各 routine 截什么由自己决定).

``meta['body']`` **默认是不收**: 收 body 的类在自己的 meta 里显式写
``'body': True``. 不在基类里盖旗 —— meta 是**机制的输入**, 机制读的每个键都
该在 meta 里看得见; 靠继承推导出来的 True, 读 catalog/看日志的人说不清它是
从哪来的. 漏写的代价是**响的**(喂 body 当场拒 + 一句"内容只能写进参数"),
不是静默. "body 里该写什么"进 ``description``; 能力清单**不印** body /
observe 标记(只有名字, 见 ``prompt/capability``).

**钩子按 MRO 继承**: 中间类覆盖过的钩子, 语义不同的末梢子类必须显式再覆盖
一次 —— ``loop`` 就踩过这个: 它继承 ``Container`` 的"边到边投递", 而它要
"收完才展开", 于是 ``on_body_chunk`` 得显式写成空的, 否则 body 投一遍、
展开再 N 遍.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from routine.routine import Routine

__all__ = ['CtmlRoutine']


class CtmlRoutine(Routine):
    """带 body 的 routine 的基类: 基类管收流, 子类管解释.

    **继承这个类不等于收 body** —— 收 body 的类要在自己的 ``meta`` 里写
    ``'body': True``. 默认是不收, 因为 meta 是机制的输入, 该被机制读到的键
    必须写在 meta 里; 靠类族推导出来的旗读的人说不清来源, 而漏写的后果一旦
    变成静默(内容投进去没人读), 就比多写一个键贵得多.

    这个旗的两个读者都在**机制**侧, 不在提示词侧: ``shell._apply_meta``
    (喂 body 给不收 body 的 routine 当场拒 + 说"内容只能写进参数") 与
    ``code_kernel._scan_no_body`` (代码面 ``call(name, body=…)`` 同一道闸).
    """

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._body_chunks: List[str] = []              # body 原文(分段攒)
        self._body_ready = asyncio.Event()             # body 收完(eof / abort)
        self._closed: bool = False                     # eof 已处理(幂等)
        self._aborted: bool = False                    # 被打断(别等了)

    @property
    def body_text(self) -> str:
        """body 原文(拼好的)."""
        return ''.join(self._body_chunks)

    async def on_message(self, source, data: Any) -> None:
        """body 只有两条消息; 幂等与放行都在这里, 子类不用重复写."""
        if not isinstance(data, dict):
            raise TypeError(
                f'{self.name} input must be dict, got {type(data).__name__}')
        if self._closed:
            return
        if data.get('eof'):
            self._closed = True
            try:
                await self.on_body_done()
            finally:
                self._body_ready.set()   # 收尾失败也要放行等 body 的人
            return
        text = str(data.get('text') or '')
        if text:
            self._body_chunks.append(text)
            await self.on_body_chunk(text)

    async def on_body_chunk(self, text: str) -> None:
        """这一段 body 来了. 默认不动作(body 攒在 ``body_text``, 收完再看)."""

    async def on_body_done(self) -> None:
        """body 收完了. 默认不动作(等 body 的 ``run`` 由 ``_body_ready`` 放行)."""

    def abort(self) -> None:
        """打断: body 不再收, 等 body 的立刻放行(``run`` 看 ``_aborted`` 出截断)."""
        self._aborted = True
        self._closed = True
        self._body_ready.set()
