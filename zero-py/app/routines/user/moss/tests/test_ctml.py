"""CtmlRoutine 契约单测(纯逻辑, 不起 kernel).

只验基类收走的那副骨头 —— 收流顺序 / eof 幂等 / 收尾失败也放行 / 打断放行.
各命令自己的解释(say 喂引擎、loop 展开)由 test_dispatch 覆.

运行(cwd=zero-py)::

    ./.venv/Scripts/python.exe -m app.routines.user.mossv2.tests.test_ctml
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, List

from ..ctml import CtmlRoutine


class _Probe(CtmlRoutine):
    """记录钩子调用; run 只报体收了什么."""

    name = 'probe'
    # 收 body 要在 meta 里写出来(默认是不收, 见 ctml.CtmlRoutine)
    meta: ClassVar[Dict[str, Any]] = {'body': True}

    def __init__(self) -> None:
        super().__init__()
        self.seen: List[str] = []
        self.done_calls: int = 0
        self.fail_done: bool = False

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await self._body_ready.wait()
        return {'aborted': self._aborted, 'text': self.body_text}

    async def on_body_chunk(self, text: str) -> None:
        self.seen.append(text)

    async def on_body_done(self) -> None:
        self.done_calls += 1
        if self.fail_done:
            raise RuntimeError('boom')


def t_stream_then_close() -> None:
    """分片顺序到达(边到边可看), eof 后 run 拿得到全文."""

    async def m() -> None:
        p = _Probe()
        await p.on_created('r1', {})
        await p.on_message(None, {'text': '你'})
        await p.on_message(None, {'text': '好'})
        assert p.seen == ['你', '好'], p.seen          # 钩子按到达序
        assert p.body_text == '你好'
        assert not p._body_ready.is_set()              # 没 eof 不算收完
        await p.on_message(None, {'eof': True})
        assert p._body_ready.is_set()
        assert await p.run({}) == {'aborted': False, 'text': '你好'}

    asyncio.run(m())


def t_eof_is_idempotent() -> None:
    """第二个 eof 不再触发收尾, eof 之后到的体也丢掉."""

    async def m() -> None:
        p = _Probe()
        await p.on_created('r1', {})
        await p.on_message(None, {'text': 'a'})
        await p.on_message(None, {'eof': True})
        await p.on_message(None, {'text': 'late'})     # 闭标签后: 丢弃
        await p.on_message(None, {'eof': True})        # 重复 eof: 不重跑
        assert p.done_calls == 1, p.done_calls
        assert p.body_text == 'a', p.body_text

    asyncio.run(m())


def t_hook_failure_still_releases() -> None:
    """收尾钩子炸了也要放行等体的人 —— 否则 run 永久卡死."""

    async def m() -> None:
        p = _Probe()
        await p.on_created('r1', {})
        p.fail_done = True
        try:
            await p.on_message(None, {'eof': True})
        except RuntimeError:
            pass
        else:
            raise AssertionError('body hook error should surface')
        assert p._body_ready.is_set(), 'run would hang forever'

    asyncio.run(m())


def t_abort_releases_and_cuts() -> None:
    """打断: 不再收体, run 立刻返回且带 aborted 标记(截断由各命令自己出)."""

    async def m() -> None:
        p = _Probe()
        await p.on_created('r1', {})
        await p.on_message(None, {'text': '说到一半'})
        p.abort()
        assert await p.run({}) == {'aborted': True, 'text': '说到一半'}
        await p.on_message(None, {'text': '再说'})     # 打断后不再收
        await p.on_message(None, {'eof': True})
        assert p.done_calls == 0, 'abort 之后不该再走收尾钩子'
        assert p.body_text == '说到一半'

    asyncio.run(m())


def t_bad_message() -> None:
    """体只有 dict 一种形态; 别的一律当场报错(不静默丢)."""

    async def m() -> None:
        p = _Probe()
        await p.on_created('r1', {})
        try:
            await p.on_message(None, 'raw text')
        except TypeError as exc:
            assert 'probe input must be dict' in str(exc), exc
        else:
            raise AssertionError('non-dict body should be refused')

    asyncio.run(m())


def main() -> None:
    t_stream_then_close()
    t_eof_is_idempotent()
    t_hook_failure_still_releases()
    t_abort_releases_and_cuts()
    t_bad_message()
    print('test_ctml: 5 passed')


if __name__ == '__main__':
    main()
