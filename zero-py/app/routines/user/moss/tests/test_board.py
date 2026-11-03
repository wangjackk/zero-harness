"""黑板一族单测: 入参校验 / 交给 board 的那一笔 / 等多久 / 错误怎么报.

纯逻辑, 不起 kernel —— 动词只碰三样外部东西: ``get_running_routines``
(找 board)、``req``(推一笔)、``asyncio.sleep``(等画完), 都能在实例上换掉.
黑板侧真正的线协议(``board/client.py``)归它自己的单测, 这里只钉"我们发出去
的是什么形状" —— 服务端不校验 payload, 所以形状只能在这边守.

跑法::

    PYTHONPATH=routine-py \\
      python -m app.routines.user.mossv2.tests.test_board
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any, Dict, List

from ..board import timing
from ..commands.board import (BbArrow, BbCircle, BbEllipse, BbErase, BbLine,
                              BbObjects, BbPixel, BbPoint, BbPoly, BbRect,
                              BbRing, BbUnderline, BbWrite)
from ..commands.board.base import (BbCommand, anchor, anchors_of, flag_of,
                                   len_of, obj_id, px_of, size_of)

__all__: List[str] = []


class _BoardStub:
    """board 的替身: 记下每一笔, 顺带喂一份"板上有什么"的快照."""

    def __init__(self) -> None:
        self.acts: List[Dict[str, Any]] = []
        self.objects: List[Dict[str, Any]] = []
        self.connected = True

    async def __call__(self, target: str, event: str,
                       data: Dict[str, Any] | None = None,
                       timeout: float = 0.0) -> Any:
        assert target == 'B', target
        d = dict(data or {})
        if event == 'act':
            self.acts.append(d)
            ident = d.get('id') or f'x{len(self.acts)}'
            return {'ok': True, 'id': ident}
        if event == 'objects':
            return {'ok': True, 'connected': self.connected,
                    'objects': self.objects}
        raise AssertionError(f'unexpected request {event}')


def _mount(cls, stub: _BoardStub, **kwargs):
    """造一个动词实例, 把外部两样换掉(不碰 kernel)."""
    inst = cls()
    inst.req = stub                      # type: ignore[assignment]
    inst.get_running_routines = lambda: _running()   # type: ignore[assignment]
    return inst


async def _running() -> list:
    return [{'name': 'board', 'id': 'B'}]


async def _body(inst, text: str) -> None:
    await inst.on_created('r1', {})
    await inst.on_message(None, {'text': text})
    await inst.on_message(None, {'eof': True})


async def _run(cls, stub: _BoardStub, *, body: str | None = None,
               **kwargs) -> Any:
    inst = _mount(cls, stub)
    if body is not None:
        await _body(inst, body)
    return await inst.run(kwargs)


async def _expect_error(cls, stub: _BoardStub, want: str, *,
                        body: str | None = None, **kwargs) -> None:
    try:
        await _run(cls, stub, body=body, **kwargs)
    except RuntimeError as exc:
        assert want in str(exc), (want, exc)
    else:
        raise AssertionError(f'应当报错({want}): {kwargs} body={body!r}')
    assert not stub.acts, stub.acts       # 报错就不许往板上推


async def t_write() -> None:
    """write: body 是原文, at 是落笔点, 回执带 id. 等的时间 = 这行画完."""
    stub = _BoardStub()
    t0 = asyncio.get_running_loop().time()
    r = await _run(BbWrite, stub, body='斜率就是变化率',
                   id='a1', at='0.12,0.28')
    dt = asyncio.get_running_loop().time() - t0
    assert r == {'content': 'id=a1',
                 'details': {'id': 'a1', 'text': '斜率就是变化率'}}, r
    assert stub.acts == [{'id': 'a1',
                          'act': {'do': 'write', 'text': '斜率就是变化率',
                                  'at': [0.12, 0.28], 'size': 'm'}}], stub.acts
    # 8 个字 ≈ 1.3s(黑板侧 STEP=0.155), 回执必须等它画完
    assert dt >= 1.2, dt
    print(f't_write ok: body 走中间, 落笔点结构化, 等 {dt:.2f}s')


async def t_write_refuses() -> None:
    """write 的三条硬规矩: 空 body / 换行 / 太长, 都当场报错."""
    for body, want in [('   ', 'body 是空的'),
                       ('第一行\n第二行', '一行'),
                       ('x' * 121, '太长')]:
        await _expect_error(BbWrite, _BoardStub(), want,
                            body=body, id='a1', at='0.1,0.1')
    print('t_write_refuses ok: 空 / 换行 / 超长 三拒')


async def t_anchors() -> None:
    """锚点只有两种写法; 拼错的前缀要报错(黑板会静默当 below)."""
    assert anchor('0.12,0.28', 'at') == [0.12, 0.28]
    assert anchor(' below:a1 ', 'at') == 'below:a1'
    assert anchor('right-of:s2', 'at') == 'right-of:s2'
    for bad, want in [('rigth-of:a1', '前缀只认'),
                      ('below:', '少了对象 id'),
                      ('0.1', '两段'),
                      ('1.2,0.3', '0~1'),
                      ('a,b', '不是数字'),
                      ('', '必填')]:
        try:
            anchor(bad, 'at')
        except RuntimeError as exc:
            assert want in str(exc), (bad, want, exc)
        else:
            raise AssertionError(f'应当报错: {bad!r}')
    print('t_anchors ok: 两种写法 + 六种错法')


async def t_line_arrow() -> None:
    """line 进对象表(必须自己起 id); arrow 不进(可以不填)."""
    stub = _BoardStub()
    r = await _run(BbLine, stub, id='l1', **{'from': 'below:a1',
                                             'to': '0.62,0.55', 'dash': 'true'})
    assert r == {'content': 'id=l1', 'details': {'id': 'l1'}}, r
    assert stub.acts[-1] == {'id': 'l1',
                             'act': {'do': 'line', 'from': 'below:a1',
                                     'to': [0.62, 0.55], 'dash': True}}

    stub = _BoardStub()
    r = await _run(BbArrow, stub, **{'from': '0.1,0.2', 'to': '0.3,0.4'})
    assert r['details']['id'] == 'x1', r    # 没给 id → 由 board 编号
    assert r['content'] == 'id=x1', r       # 有 id 就得告诉模型
    assert stub.acts[-1]['act'] == {'do': 'arrow',
                                    'from': [0.1, 0.2], 'to': [0.3, 0.4]}
    assert 'id' not in stub.acts[-1], stub.acts[-1]
    print('t_line_arrow ok: line 带 id, arrow 由 board 编号')


async def t_circle_is_geometry() -> None:
    """bb_circle 是**几何圆**(圆心 + 半径), 不再是圈对象 —— 圈对象是 bb_ring."""
    stub = _BoardStub()
    r = await _run(BbCircle, stub, id='c1', at='below:a1', r='0.09')
    assert r == {'content': 'id=c1', 'details': {'id': 'c1'}}, r
    assert stub.acts[-1] == {'id': 'c1',
                             'act': {'do': 'circle', 'at': 'below:a1',
                                     'r': 0.09}}, stub.acts[-1]
    # 半径是短边比例: 必填、0~1
    await _expect_error(BbCircle, _BoardStub(), '缺 r', id='c1', at='0.5,0.4')
    await _expect_error(BbCircle, _BoardStub(), '0~1',
                        id='c1', at='0.5,0.4', r='3')
    print('t_circle_is_geometry ok: 圆心 + 半径, 半径必填且按短边')


async def t_ring_guard() -> None:
    """圈 / 划 / 擦: 有快照时先查 id 在不在, 查不到就报错; 没快照不判死."""
    stub = _BoardStub()
    stub.objects = [{'id': 'a1', 'kind': 'text', 'box': [0, 0, 0.2, 0.1]}]
    r = await _run(BbRing, stub, around='a1')
    assert r['details']['around'] == 'a1', r
    assert r['content'].startswith('圈住 a1'), r
    assert stub.acts[-1]['act']['pad'] == 14, r
    assert stub.acts[-1]['act']['do'] == 'ring', stub.acts[-1]

    try:
        await _run(BbRing, stub, around='nope')
    except RuntimeError as exc:
        assert '没有对象' in str(exc), exc
    else:
        raise AssertionError('引用不存在的 id 应当报错')

    # 没快照(刚连上/板还空) → 不许判死
    stub = _BoardStub()
    r = await _run(BbRing, stub, around='a1')
    assert r['details']['around'] == 'a1', r

    stub = _BoardStub()
    r = await _run(BbErase, stub, around='all')
    assert r['details']['erased'] == 'all', r
    assert r['content'] == '擦了全部', r
    assert stub.acts[-1]['act'] == {'do': 'erase', 'around': 'all'}
    print('t_ring_guard ok: 有快照查 id, 没快照放行')


async def t_shapes() -> None:
    """矩形 / 折线 / 椭圆: 长度按短边、形状不求对象、都得自己起 id."""
    stub = _BoardStub()
    r = await _run(BbRect, stub, id='r1', at='0.35,0.33', w='0.3', h='0.3')
    assert r == {'content': 'id=r1', 'details': {'id': 'r1'}}, r
    assert stub.acts[-1] == {'id': 'r1',
                             'act': {'do': 'rect', 'at': [0.35, 0.33],
                                     'w': 0.3, 'h': 0.3,
                                     'round': 7}}, stub.acts[-1]

    stub = _BoardStub()
    r = await _run(BbPoly, stub, id='p1', close='true',
                   points='0.5,0.16 0.32,0.52 0.68,0.52')
    assert r == {'content': 'id=p1', 'details': {'id': 'p1'}}, r
    assert stub.acts[-1]['act'] == {
        'do': 'poly',
        'points': [[0.5, 0.16], [0.32, 0.52], [0.68, 0.52]],
        'close': True, 'round': 7}, stub.acts[-1]

    stub = _BoardStub()
    r = await _run(BbEllipse, stub, id='e1', at='0.5,0.4', rx='0.18',
                   ry='0.11')
    assert r == {'content': 'id=e1', 'details': {'id': 'e1'}}, r
    assert stub.acts[-1]['act'] == {'do': 'ellipse', 'at': [0.5, 0.4],
                                    'rx': 0.18, 'ry': 0.11}, stub.acts[-1]

    # 不填 ry 就是正圆
    stub = _BoardStub()
    await _run(BbEllipse, stub, id='e2', at='0.5,0.4', rx='0.12')
    assert stub.acts[-1]['act']['ry'] == 0.12, stub.acts[-1]

    # 三样都进对象表 → 都得自己起 id
    for cls, kw in [(BbRect, {'at': '0.3,0.3', 'w': '0.2', 'h': '0.2'}),
                    (BbPoly, {'points': '0.1,0.1 0.2,0.2'}),
                    (BbEllipse, {'at': '0.5,0.4', 'rx': '0.1'})]:
        await _expect_error(cls, _BoardStub(), '缺 id', **kw)
    print('t_shapes ok: rect 中心+圆角 / poly 点串+闭合 / ellipse 缺 ry 即正圆')


async def t_pixel() -> None:
    """pixel: body 是一段 ops(JSON 数组), 校验带下标, 默认值不下发."""
    stub = _BoardStub()
    r = await _run(BbPixel, stub, body=(
        '[{"op":"clearRect","x":0.62,"y":0.1,"w":0.3,"h":0.12},'
        ' {"op":"fillRect","x":0.62,"y":0.1,"w":0.3,"h":0.12,'
        '"color":"#f2efe2","alpha":0.08},'
        ' {"op":"pixel","x":0.1,"y":0.5}]'))
    assert r == {'content': '画了 3 个像素操作',
                 'details': {'ops': 3, 'id': 'x1'}}, r
    act = stub.acts[-1]['act']
    assert act['do'] == 'pixel' and len(act['ops']) == 3, act
    # 没写的 color / alpha 不下发 —— 黑板侧自己有默认值
    assert act['ops'][0] == {'op': 'clearRect', 'x': 0.62, 'y': 0.1,
                             'w': 0.3, 'h': 0.12}, act['ops'][0]
    assert act['ops'][2] == {'op': 'pixel', 'x': 0.1, 'y': 0.5}, act['ops'][2]

    for body, want in [
        ('', 'body 是空的'),
        ('[{"op":"clearRect",', '不是合法 JSON'),
        ('{"ops": [{"op":"pixel","x":0.1,"y":0.1}]}', '不要再包一层'),
        ('[]', '至少一个操作'),
        ('[{"op":"blur","x":0.1,"y":0.1}]', 'ops[0] 不合法'),
        ('[{"op":"pixel","x":0.1,"y":0.1},{"op":"fillRect","x":0.1,'
         '"y":0.1,"h":0.1}]', 'ops[1] 不合法'),
        ('[{"op":"pixel","x":1.4,"y":0.1}]', 'ops[0] 不合法'),
    ]:
        await _expect_error(BbPixel, _BoardStub(), want, body=body)
    print('t_pixel ok: ops 数组 + 下标定位 + 四种坏写法')


async def t_objects() -> None:
    """bb_objects 只读: 默认只给清单(id / kind / 文字开头), 不给 bbox ——
    回执要进上下文, 整张表连 17 位小数一起塞是纯垃圾; box="true" 才要
    几何, 且截到千分位(0.001 已是画布上约两个像素)."""
    stub = _BoardStub()
    stub.objects = [
        {'id': 'a1', 'kind': 'text', 'text': '五个形状，同时画',
         'box': [0.060000000000000005, 0.046544342507645255,
                 0.31974921630094044, 0.07492354740061162]},
        {'id': 'cir0', 'kind': 'stroke', 'box': [0.13, 0.36, 0.0052, 0.0008]},
    ]
    r = await _run(BbObjects, stub)
    d = r['details']
    assert d['connected'] is True and d['count'] == 2, r
    assert d['objects'] == [
        {'id': 'a1', 'kind': 'text', 'text': '五个形状，同时画'},
        {'id': 'cir0', 'kind': 'stroke'}], r
    # 模型读的那一行: 有文字的带文字, 没文字的带 kind, 不要坐标
    assert r['content'] == '2 个: a1「五个形状，同时画」, cir0(stroke)', r
    # 显式要几何: 千分位, 对象一个不少, 而且**必须写进 content** ——
    # details 不进 prompt, 只放在里面等于模型要不到
    r = await _run(BbObjects, stub, box='true')
    assert [o['box'] for o in r['details']['objects']] == [
        [0.06, 0.047, 0.32, 0.075], [0.13, 0.36, 0.005, 0.001]], r
    assert '@0.06,0.047,0.32,0.075' in r['content'], r
    # 长文字截断(认得出是哪句话就够)
    stub.objects = [{'id': 'a2', 'kind': 'text', 'text': '字' * 80}]
    r = await _run(BbObjects, stub)
    assert r['details']['objects'][0]['text'] == '字' * 30 + '…', r
    # 空表: count=0(板空 / 刚连上还没上报)
    r = await _run(BbObjects, _BoardStub())
    assert r['details']['count'] == 0 and r['details']['objects'] == [], r
    assert r['content'] == '板是空的', r
    await _expect_error(BbObjects, _BoardStub(), 'box', box='yes')
    print('t_objects ok: 清单不带 bbox, box="true" 才给且截千分位')


async def t_timing() -> None:
    """时长估算: 抄的是黑板侧的公式(节奏靠它, 抄偏只是快慢)."""
    assert abs(timing.write_seconds('12345678') - (7 * 0.155 + 0.22475)) < 1e-9
    assert timing.estimate({'do': 'erase', 'around': 'all'}) == 0.0
    assert timing.estimate({'do': 'pixel', 'ops': []}) == 0.0
    assert timing.estimate({'do': 'point', 'at': [0.5, 0.5]}) > 0.2
    # line 短的落到 0.3 下限, 长的不会超过 1.6
    short = timing.estimate({'do': 'line', 'from': [0.1, 0.1], 'to': [0.12, 0.1]})
    long = timing.estimate({'do': 'line', 'from': [0.01, 0.01], 'to': [0.99, 0.99]})
    assert 0.3 <= short < 0.4, short
    assert long > short and long <= 1.65, long
    # 圆/椭圆按半径走: 大圆比小圆久
    small = timing.estimate({'do': 'circle', 'at': [0.5, 0.5], 'r': 0.05})
    big = timing.estimate({'do': 'circle', 'at': [0.5, 0.5], 'r': 0.5})
    assert big > small >= 0.3, (small, big)
    assert timing.estimate({'do': 'ellipse', 'at': [0.5, 0.5],
                            'rx': 0.18, 'ry': 0.11}) > 0.3
    # 矩形/折线按点数走; ring 与对象多大无关(固定 151 点)
    assert 1.0 < timing.estimate({'do': 'rect', 'at': [0.3, 0.3],
                                  'w': 0.2, 'h': 0.2}) < 1.65
    assert timing.estimate({'do': 'poly', 'points': [[0.1, 0.1], [0.9, 0.9]]}) > 0.3
    assert abs(timing.estimate({'do': 'ring', 'around': 'a1'})
               - (min(1.6, 151 * 0.0075) + 0.05)) < 1e-9
    # 锚点是引用时算不出像素距离 → 用名义长度, 不能炸
    assert 0.3 <= timing.estimate({'do': 'arrow', 'from': 'below:a1',
                                   'to': [0.6, 0.6]}) <= 1.25
    assert timing.estimate({'do': 'poly',
                            'points': ['below:a1', 'right-of:a1']}) > 0.3
    print('t_timing ok: write/line/circle/rect/poly/ring/point 七档 + 引用兜底')


async def t_helpers() -> None:
    """id / size / 长度 / 像素量 / flag: 错的当场说清楚, 不留给黑板静默忽略."""
    assert obj_id(' a1 ') == 'a1'
    for bad in ('', 'a b', 'x' * 41):
        try:
            obj_id(bad)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f'应当报错: {bad!r}')
    assert size_of('') == 'm' and size_of('L') == 'l'
    assert len_of('', 'r', 0.08) == 0.08 and len_of(' 0.3 ', 'w') == 0.3
    assert px_of('', 'pad', 14) == 14 and px_of('20', 'pad', 14) == 20
    assert px_of('7.6', 'round', 7) == 8          # 像素量取整
    assert anchors_of('0.1,0.2 0.3,0.4', 'points') == [[0.1, 0.2], [0.3, 0.4]]
    assert anchors_of('below:a1;0.3,0.4', 'points') == ['below:a1', [0.3, 0.4]]
    for fn, args, want in [
        (size_of, ('xl',), 'size'),
        (len_of, ('2', 'r'), '0~1'),
        (len_of, ('abc', 'r'), '数字'),
        (px_of, ('300', 'pad', 14), '0~200'),
        (anchors_of, ('0.1,0.2', 'points'), '至少两个'),
        (flag_of, ('yes', 'close'), 'true'),
    ]:
        try:
            fn(*args)
        except RuntimeError as exc:
            assert want in str(exc), (fn.__name__, args, want, exc)
        else:
            raise AssertionError(f'应当报错: {fn.__name__}{args}')
    assert flag_of('', 'dash') is False and flag_of('true', 'dash') is True
    print('t_helpers ok: id / size / 长度 / 像素量 / 锚点串 / flag')


async def t_observe() -> None:
    """十三扇门都出厂声明 observe: 画面是模型看不见的那半世界.

    族级声明(`BbCommand.meta`), 每扇门 `**BbCommand.meta` 展开. 这个用例守
    的正是"新增一门忘了展开" —— meta 是整块替换不是合并, 少一行声明, 那一门
    就静默不点灯(画完那一拍白画, 下一拍得自己想去看).
    """
    assert BbCommand.meta == {'observe': True}, BbCommand.meta
    for cls in (BbArrow, BbCircle, BbEllipse, BbErase, BbLine, BbObjects,
                BbPixel, BbPoint, BbPoly, BbRect, BbRing, BbUnderline,
                BbWrite):
        assert cls.meta.get('observe') is True, cls.__name__
    print('t_observe ok: 十三扇门全点灯(族级声明)')


async def main() -> None:
    only = [a for a in sys.argv[1:] if not a.startswith('-')]
    table = {n: f for n, f in globals().items()
             if n.startswith('t_') and inspect.iscoroutinefunction(f)}
    if only:
        unknown = [a for a in only if a not in table]
        assert not unknown, f'unknown cases: {unknown}'
        for n, f in table.items():
            if n in only:
                await f()
        print(f'ALL PASS ({len(only)} cases)')
        return
    for f in table.values():
        await f()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
