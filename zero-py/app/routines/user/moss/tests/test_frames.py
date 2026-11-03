"""回执契约单测: 四个字段怎么收 / 怎么校验 / 进了 prompt 的是哪一个.

这是 ``frames.py`` 的护栏. 它守的是**分层**: 信封(kind/verb)说成没成,
载荷(content/details/images/observe)说结果是什么, 而 ``details`` **没有
通往 prompt 的路** —— 这一条塌了, 黑板那张十七位小数的表就又会爬回上下文.
``images`` 是唯一的非文本内容(图没法写成一行字), 只收路径列表, 组装成
多模态 block 的责任在下游 ``brain``.

跑法::

    PYTHONPATH=routine-py python -m app.routines.user.mossv2.tests.test_frames
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, model_validator

from ..frames import FRAME_CLIP, failure, frame_line, receipt

__all__: List[str] = []


def _raises(fn, want: str) -> None:
    try:
        fn()
    except Exception as exc:
        assert want in str(exc), (want, exc)
    else:
        raise AssertionError(f'应当报错: {want}')


async def t_shape() -> None:
    """三字段: content 缺省 'ok', details 缺省 {}, observe 只在真值时留."""
    assert receipt('x', None) == {'content': 'ok', 'details': {}}, 'None'
    assert receipt('x', {}) == {'content': 'ok', 'details': {}}, '空 dict'
    assert receipt('x', {'content': ''}) == {'content': 'ok',
                                             'details': {}}, '空串也是 ok'
    assert receipt('x', {'content': 'id=a1', 'details': {'id': 'a1'}}) == {
        'content': 'id=a1', 'details': {'id': 'a1'}}
    got = receipt('x', {'content': 'c', 'observe': True})
    assert got == {'content': 'c', 'details': {}, 'observe': True}, got
    # observe 为假就不留键(帧上少一个字段, 少一次误读)
    assert 'observe' not in receipt('x', {'observe': False}), 'observe=False'
    # images: 路径列表原样收; 空列表与坏形状当场炸(不然图会在下游静默消失)
    got = receipt('read_image', {'content': 'c', 'images': ['/a.png', '/b.png']})
    assert got['images'] == ['/a.png', '/b.png'], got
    assert 'images' not in receipt('read_image', {'content': 'c'}), '没图不放键'
    _raises(lambda: receipt('read_image', {'images': []}), '没交回结果')
    _raises(lambda: receipt('read_image', {'images': {'a': 1}}), '没交回结果')
    _raises(lambda: receipt('read_image', {'images': ['']}), '没交回结果')
    print('t_shape ok: content 缺省 ok / details {} / observe 只留 True / '
          'images 路径列表')


async def t_gate() -> None:
    """三字段之外的键当场炸 —— 唯一校验处. 悄悄收下才是最坏的结果:
    拼错的键安静消失, 模型拿着残缺的回执猜它本来想说什么."""
    _raises(lambda: receipt('bb_write', {'id': 'a1'}), '没交回结果')
    _raises(lambda: receipt('bb_write', {'content': 'c', 'ok': True}),
            '没交回结果')
    _raises(lambda: receipt('bb_write', ['a']), '没交回结果')
    # details 过不去传输层 = 回执永远到不了模型: 当场变错误帧, 别静默丢
    _raises(lambda: receipt('bb_write', {'details': {'f': object()}}),
            '没交回结果')
    print('t_gate ok: 多余键 / 非 dict / 不可序列化 details 三拒')


async def t_gate_speaks_world() -> None:
    """错误帧那一行是给模型看的: 契约字段名与信封词汇不进这一行.

    模型改不了 routine 的返回结构, 让它看见"多了 battery_pct, 信封由
    channel 写"只会教它复读内部词汇. 精确原因在日志里(``_broken``).
    """
    cases = [{'id': 'a1'}, ['a'], {'details': {'f': object()}},
             {'content': 'c', 'battery_pct': 80}]
    for bad in cases:
        try:
            receipt('car/status', bad)
        except RuntimeError as exc:
            line = frame_line({'kind': 'error', 'verb': 'car/status',
                               'text': str(exc)})
            for word in ('content', 'details', 'observe', '信封', 'channel',
                         '载荷'):
                assert word not in line, (word, line)
            # 能力名由 frame_line 拼, 且只出现一次(别双重前缀)
            assert line.startswith('error(car/status): '), line
            assert line.count('car/status') == 1, line
        else:
            raise AssertionError(f'应当报错: {bad!r}')
    print('t_gate_speaks_world ok: 错误行只说结果, 不吐内部字段名')


class _ShakeInput(BaseModel):
    """car/shake 的入参形状(只为复现那几条校验错误)."""

    mode: Literal['yaw', 'x', 'y'] = 'yaw'
    amp: float = Field(1.5, ge=0.05, le=2.0)
    cycles: int = Field(5, ge=1, le=20)

    @model_validator(mode='after')
    def _cross(self) -> '_ShakeInput':
        if self.mode != 'yaw' and self.amp > 0.6:
            raise ValueError('x/y 模式 amp 须 ≤ 0.6 m/s')
        return self


def _failure_of(**kw: Any) -> str:
    try:
        _ShakeInput.model_validate(kw)
    except Exception as exc:
        return failure(exc)
    raise AssertionError(f'应当校验失败: {kw}')


async def t_failure_text() -> None:
    """校验错误: 脱掉 pydantic 外衣(类名 / 入参 dump / 文档链接), 留下
    模型能改的那件事 —— 哪个字段、允许什么、给了什么."""
    cross = _failure_of(mode='y', amp=0.8)
    assert '参数不对' in cross and '0.6' in cross, cross
    assert 'amp' in cross, cross
    bound = _failure_of(amp=9.0)
    assert 'amp' in bound and '要 ≤ 2' in bound and '给了 9' in bound, bound
    # 相邻的两个字段都有错: 一次说完(模型的下一步是改参数, 不是再撞一次)
    both = _failure_of(amp=9.0, mode='nope')
    assert 'mode' in both and 'amp' in both, both
    # 常见形态都有自己的话, 不落到 pydantic 的英文原句
    assert '别带小数' in _failure_of(cycles=3.7), _failure_of(cycles=3.7)
    assert '要是整数' in _failure_of(cycles='abc'), _failure_of(cycles='abc')
    for text in (cross, bound, both, _failure_of(cycles=3.7)):
        for junk in ('ValidationError', 'pydantic', 'input_value',
                     'input_type', 'type=value_error', 'http',
                     'Input should be'):
            assert junk not in text, (junk, text)
    # 作者自己抛的异常原样穿过(那句话本来就是人话)
    try:
        raise RuntimeError('导航栈未启动(slam-nav), 先 car/navigate/manage 启动')
    except RuntimeError as exc:
        assert failure(exc).startswith('导航栈未启动'), failure(exc)
    print('t_failure_text ok: 只留字段/阈值/给了什么, 不吐 pydantic 原文')


async def t_frame_line() -> None:
    """进 prompt 的那一行只取 content; details 里写什么都不出现."""
    f: Dict[str, Any] = {'kind': 'action', 'verb': 'bb_objects',
                         'result': {'content': '2 个: a1「字」, cir0(stroke)',
                                    'details': {'objects': [{'box': [
                                        0.060000000000000005,
                                        0.046544342507645255]}]}}}
    line = frame_line(f)
    assert line == 'bb_objects: 2 个: a1「字」, cir0(stroke)', line
    assert '0.06' not in line, line          # details 一个字符都没漏出来
    # 没 content 的帧(琐碎成功)显示 ok
    assert frame_line({'kind': 'action', 'verb': 'bb_underline',
                       'result': {'details': {}}}) == 'bb_underline: ok'
    # error 帧走 text, 尖括号换全角(防回声帧把标签原样复读)
    err = frame_line({'kind': 'error', 'verb': 'bb_write',
                      'text': "入参不对: 缺 <r>"})
    assert err == 'error(bb_write): 入参不对: 缺 〈r〉', err
    print('t_frame_line ok: 只读 content / 无 content 显示 ok / error 走 text')


async def t_clip() -> None:
    """content 有兜底上限: 事故看得见, 但撑不爆一拍."""
    long = 'x' * (FRAME_CLIP + 10)
    line = frame_line({'kind': 'action', 'verb': 'x',
                       'result': {'content': long}})
    assert '截断' in line and str(FRAME_CLIP + 10) in line, line[:80]
    assert len(line) < FRAME_CLIP + 100, len(line)
    ok = frame_line({'kind': 'action', 'verb': 'x',
                     'result': {'content': 'y' * 10}})
    assert ok == 'x: ' + 'y' * 10, ok
    print(f't_clip ok: content 上限 {FRAME_CLIP} 字, 短的原样')


async def main() -> None:
    await t_shape()
    await t_gate()
    await t_gate_speaks_world()
    await t_failure_text()
    await t_frame_line()
    await t_clip()
    print('ALL PASS')


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
