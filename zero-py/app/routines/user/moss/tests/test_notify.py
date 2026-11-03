"""notify 单测: 申报出去的是什么形状 / 哪种 mode 落成哪组字段 / 失败怎么报.

纯逻辑, 不起 kernel —— 门只碰两样外部东西: ``get_running_routines``
(找裁判)与 ``req``(申报一条), 都能在实例上换掉.

最要紧的一条断言是 **interrupt 的 effort**: 裸写 ``interrupt=True`` 会被
``declare`` 按原版 primitive 补成 ``effort='none'``(停而不答), 那样这条
通知就等于白送 —— 所以门必须自己带 ``effort='normal'``.

跑法::

    PYTHONPATH=routine-py \\
      python -m app.routines.user.mossv2.tests.test_notify
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from ..commands.notify import Notify
from ..mindflow import Priority

__all__: List[str] = []


class _MindflowStub:
    """裁判的替身: 记下每一条申报, 按剧本回 verdict."""

    def __init__(self, verdict: str = 'accepted') -> None:
        self.sent: List[Dict[str, Any]] = []
        self.verdict = verdict

    async def __call__(self, target: str, event: str,
                       data: Dict[str, Any] | None = None,
                       timeout: float = 0.0) -> Any:
        assert target == 'M', target
        assert event == 'declare', event
        self.sent.append(dict(data or {}))
        return {'verdict': self.verdict}


async def _running() -> list:
    return [{'name': 'mindflow', 'id': 'M'}]


def _mount(stub: _MindflowStub, *, alive: bool = True) -> Notify:
    inst = Notify()
    inst.req = stub                     # type: ignore[assignment]

    async def _find() -> list:
        return await _running() if alive else []

    inst.get_running_routines = _find   # type: ignore[assignment]
    return inst


async def _run(stub: _MindflowStub, *, alive: bool = True, **kwargs) -> Any:
    return await _mount(stub, alive=alive).run(kwargs)


async def _expect_error(stub: _MindflowStub, want: str, *,
                        alive: bool = True, sent_ok: bool = False,
                        **kwargs) -> None:
    try:
        await _run(stub, alive=alive, **kwargs)
    except Exception as exc:      # RuntimeError + pydantic 入参校验
        assert want in str(exc), (want, exc)
    else:
        raise AssertionError(f'应当报错({want}): {kwargs}')
    if not sent_ok:
        assert not stub.sent, stub.sent   # 入参错就不许申报出去


async def t_default_never_preempts() -> None:
    """缺省 = BACKGROUND + notify: 有现任时一定输, 输了挂账(永不丢)."""
    stub = _MindflowStub()
    r = await _run(stub, text='构建完成')
    sent = stub.sent[0]
    assert sent['priority'] == int(Priority.BACKGROUND), sent
    assert sent['mode'] == 'notify', sent
    assert sent['source'] == 'notify', sent      # 不借 user(同源 ×1.1 = 抢自己)
    assert sent['text'] == '构建完成' and sent['complete'] is True, sent
    assert 'interrupt' not in sent, sent
    assert r['content'].endswith('(不打断现在)'), r
    assert r['details']['verdict'] == 'accepted', r
    print('t_default_never_preempts ok: BACKGROUND + notify, 不借 user 源')


async def t_next_forces_next_beat() -> None:
    """next: 仍然不抢(BACKGROUND), 但输了也插队 —— 一定下一拍看见."""
    stub = _MindflowStub()
    r = await _run(stub, text='用户撤回了上一条', mode='next')
    sent = stub.sent[0]
    assert sent['mode'] == 'next', sent
    assert sent['priority'] == int(Priority.BACKGROUND), sent
    assert r['content'] == '已排进下一拍(不打断现在)', r
    print('t_next_forces_next_beat ok: next 排进下一拍')


async def t_interrupt_answers() -> None:
    """interrupt: 停身体 + 照样回答 —— effort 必须显式 normal.

    裸写 interrupt=True 会被 declare 补成 effort='none'(停而不答), 那这条
    通知就没用了; 门自己带 normal 才是"停下现在, 答这条".
    """
    stub = _MindflowStub()
    r = await _run(stub, text='磁盘满了', mode='interrupt')
    sent = stub.sent[0]
    assert sent['interrupt'] is True, sent
    assert sent['effort'] == 'normal', sent      # 关键: 不是 none
    assert sent['priority'] == int(Priority.FATAL), sent
    assert sent['mode'] == 'default', sent
    assert '已打断' in r['content'], r
    print('t_interrupt_answers ok: 停身体但照常回答')


async def t_source_split() -> None:
    """source: 不同来源各自成条(同名才续接), 原样交给申报."""
    stub = _MindflowStub()
    await _run(stub, text='12 个错误', source='build')
    await _run(stub, text='重新构建中', source='build')
    assert [s['source'] for s in stub.sent] == ['build', 'build'], stub.sent
    assert [s['text'] for s in stub.sent] == ['12 个错误', '重新构建中'], stub.sent
    print('t_source_split ok: 来源名原样传递')


async def t_errors() -> None:
    """三种失败: 裁判没起 / verdict 不是 accepted / mode 拼错."""
    await _expect_error(_MindflowStub(), 'mindflow not running',
                        alive=False, text='x')
    for verdict in ('dropped', 'noop', 'ignored'):
        # 这几条已经申报出去了(是裁判没接) —— 不许申报的断言只管入参错
        await _expect_error(_MindflowStub(verdict), '没送出去',
                            text='x', sent_ok=True)
    stub = _MindflowStub()
    await _expect_error(stub, 'mode takes', text='x', mode='yell')
    await _expect_error(_MindflowStub(), 'text', text='')   # 空正文
    print('t_errors ok: 没裁判/没送成/mode 拼错/空正文 都当场报错')


async def main() -> None:
    await t_default_never_preempts()
    await t_next_forces_next_beat()
    await t_interrupt_answers()
    await t_source_split()
    await t_errors()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
