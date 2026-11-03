"""body 扫描器单测(纯逻辑, 不起 kernel).

运行(cwd=zero-py)::

    ./.venv/Scripts/python.exe -m app.routines.user.mossv2.tests.test_body
"""
from __future__ import annotations

from ..body import (
    BodyScanner,
    ChildBody,
    ChildClose,
    ChildOpen,
    Malformed,
    Text,
)


def _all(src: str, *, step: int = 5) -> list:
    """分片喂入(模拟字流), 返回全部事件."""
    s = BodyScanner()
    out: list = []
    for i in range(0, len(src), step):
        out.extend(s.feed(src[i:i + step]))
    out.extend(s.close())
    return out


def _compact(evs: list) -> list:
    """相邻 ChildBody/Text 合并(切分粒度不同, 语义相同)."""
    out: list = []
    for e in evs:
        if out and type(e) is type(out[-1]) and isinstance(e, (Text, ChildBody)):
            out[-1] = type(e)(out[-1].text + e.text)
        else:
            out.append(e)
    return out


def _check(src: str, want: list) -> None:
    for step in (5, 1, 1000):
        got = _compact(_all(src, step=step))
        assert got == want, (step, src, got, want)


def t_namespaced_name() -> None:
    """名字里的斜杠: ``car/move`` 是注册名, 标签就得能写成这样.

    capability 按注册名原样印清单行, 模型照抄 —— 词法若把 ``/`` 当非法字符,
    清单里那一行名字永远写不成标签, 报出来还是"malformed tag"(看着像模型写
    错的). 多段也认(``car/navigate/manage``); 同名闭合照裸名配对.
    """
    _check('<car/move action="forward" speed="0.15"/>', [
        ChildOpen('car/move', 'main', 'car/move',
                  {'action': 'forward', 'speed': '0.15'}, True),
        ChildClose('car/move'),
    ])
    _check('<car/navigate/manage action="start"/>', [
        ChildOpen('car/navigate/manage', 'main', 'car/navigate/manage',
                  {'action': 'start'}, True),
        ChildClose('car/navigate/manage'),
    ])
    # 带 body 的形态: 闭合按指令名配对(裸名, 不带轨道前缀)
    _check('<b:car/where>任</car/where>', [
        ChildOpen('b:car/where', 'b', 'car/where', {}, False),
        ChildBody('任'),
        ChildClose('b:car/where'),
    ])


def t_text_and_selfclosing() -> None:
    """纯文本 + 自闭合: 自闭合等于"开+立即闭"."""
    _check('hmm <main:wave/> ok', [
        Text('hmm '),
        ChildOpen('main:wave', 'main', 'wave', {}, True),
        ChildClose('main:wave'),
        Text(' ok'),
    ])


def t_body_forwarded_verbatim() -> None:
    """子体内的一切原样转发(再嵌套也不解析) —— 只数同名配对."""
    _check('<main:say>你<all><left:x/></all>好</say>之后', [
        ChildOpen('main:say', 'main', 'say', {}, False),
        ChildBody('你<all><left:x/></all>好'),
        ChildClose('main:say'),
        Text('之后'),
    ])


def t_nested_same_name() -> None:
    """同名嵌套靠深度计数找边界."""
    _check('<v:a><v:a>x</v:a>y</v:a>', [
        ChildOpen('v:a', 'v', 'a', {}, False),
        ChildBody('<v:a>x</v:a>y'),
        ChildClose('v:a'),
    ])


def t_attrs_and_bare_name() -> None:
    """属性解析 + 裸名落 main 轨 + 引号内的 '>' 不算闭合."""
    _check('<speak voice="warm">hi</speak>', [
        ChildOpen('speak', 'main', 'speak', {'voice': 'warm'}, False),
        ChildBody('hi'),
        ChildClose('speak'),
    ])
    _check('<play name="a>b"/>', [
        ChildOpen('play', 'main', 'play', {'name': 'a>b'}, True),
        ChildClose('play'),
    ])


def t_quantifier_names_are_plain() -> None:
    """量词就是普通标签名 —— 扫描器不认识它们."""
    _check('<all><left:x/></all>', [
        ChildOpen('all', 'main', 'all', {}, False),
        ChildBody('<left:x/>'),
        ChildClose('all'),
    ])
    _check('<left:all/>', [
        ChildOpen('left:all', 'left', 'all', {}, True),
        ChildClose('left:all'),
    ])
    _check('<_>x</_>', [
        ChildOpen('_', 'main', '_', {}, False),
        ChildBody('x'),
        ChildClose('_'),
    ])


def t_malformed() -> None:
    """坏标签/坏属性出 Malformed, 不吞掉也不当成正文."""
    s = BodyScanner()
    evs = s.feed('a<3> b') + s.close()
    assert any(isinstance(e, Malformed) for e in evs), evs
    s = BodyScanner()
    evs = s.feed('<x a="1" !bad>y</x>') + s.close()
    assert isinstance(evs[0], Malformed), evs


def t_unclosed() -> None:
    """流断在子体内: 内容不丢, 合成闭合, unclosed 置位."""
    s = BodyScanner()
    evs = s.feed('<main:say>说到一半')
    assert s._child == 'main:say'
    evs += s.close()
    assert s.unclosed
    assert evs[-1] == ChildClose('main:say'), evs
    assert any(isinstance(e, ChildBody) and e.text == '说到一半' for e in evs)


def t_half_tag_waits() -> None:
    """跨 chunk 的半个标签在缓冲里等, 不产假事件."""
    s = BodyScanner()
    assert s.feed('hi <ma') == [Text('hi ')]
    evs = s.feed('in:x/>')
    assert evs == [ChildOpen('main:x', 'main', 'x', {}, True),
                   ChildClose('main:x')], evs


def t_entities() -> None:
    """属性与**本层文本**还原 XML 实体; body 是字节流, 原样转给子."""
    # 值里写引号/& 的唯一办法就是实体; 正文里的 &lt; 也一样.
    _check('<say title="a&quot;b &amp; c">x</say>', [
        ChildOpen('say', 'main', 'say', {'title': 'a"b & c'}, False),
        ChildBody('x'),
        ChildClose('say'),
    ])
    # 数字实体; 认不出的保持原样(单写 & 太常见, 报错只会误伤)
    _check('R&D &#65;&#x42; &unknown;', [Text('R&D AB &unknown;')])
    # body 不解码: 父若解码, <all>&lt;x/&gt;</all> 里的转义就成了真标签(注入)
    _check('<all>&lt;left:x/&gt;</all>', [
        ChildOpen('all', 'main', 'all', {}, False),
        ChildBody('&lt;left:x/&gt;'),
        ChildClose('all'),
    ])


def t_cdata() -> None:
    """CDATA 是内容保护区: 标记剥掉, 内容字面(不还原实体)."""
    # 顶层: 顶端的 < > 与 &lt; 都当字面
    _check('前<![CDATA[1 < 2 &lt; 3]]>后', [Text('前1 < 2 &lt; 3后')])
    # body 内: 里面的闭合标签字面不算闭合 —— 这正是它存在的理由
    _check('<say>a<![CDATA[</say><x/>]]>b</say>', [
        ChildOpen('say', 'main', 'say', {}, False),
        ChildBody('a</say><x/>b'),
        ChildClose('say'),
    ])


def t_less_than() -> None:
    """不像标签的 '<' 当场当字面量 —— 不许吞掉后面整段."""
    s = BodyScanner()
    evs = s.feed('1 < 2 对吧') + s.close()
    assert all(isinstance(e, Text) for e in evs), evs
    assert ''.join(e.text for e in evs) == '1 < 2 对吧', evs
    assert not s.unclosed, '小于号不是标签, 不该报 unterminated'
    # 分片边界上的单个 '<' 必须等(判死会把每个标签都拆成文本)
    s = BodyScanner()
    assert s.feed('a<') == [Text('a')]
    assert s.close() == [Text('<')]
    assert not s.unclosed
    # 真像标签(字母开头)但流断了: 字节吐出来, 同时置位
    s = BodyScanner()
    evs = s.feed('看看 <html') + s.close()
    assert evs == [Text('看看 '), Text('<html')], evs
    assert s.unclosed


def t_foreign_markup() -> None:
    """别家的标记(ChatML / DeepSeek 的 ``<｜…｜>``)是词法错误, 不是旁白.

    这一条钉的是**分片无关**: 整块到达时 ``_parse_tag`` 本来就判死, 逐字流式
    到达时原先会走"当场当字面量"那条路, 于是模型自己的函数调用格式被当成它
    说的话念了出来(线上就是这个形态: ``Moss: <｜｜DSML｜｜ invoke name="noop"/>``).
    同一段字节不许有两种结论.

    它不需要专门的处理 —— 作用等同于"未知 routine": 一条错误帧 + 债, 下一拍
    模型自己改. 这里只保证它一定走到那条路上, 不额外解释它是什么.
    """
    for src in ('<｜｜DSML｜｜ invoke name="noop"/>', '<|im_start|>'):
        for step in (1, 3, 1000):
            s = BodyScanner()
            evs: list = []
            for i in range(0, len(src), step):
                evs.extend(s.feed(src[i:i + step]))
            evs.extend(s.close())
            assert len(evs) == 1 and isinstance(evs[0], Malformed), (step, evs)
            assert evs[0].text == src, (step, evs[0])
            assert not s.unclosed, '整块到了就不是"半个标签"'

    # 代价: ``<`` 后**紧邻**竖线的正文会等到流尾(有 '>' 就误判).
    # 正常写法 ``x < |y|`` 中间有空格, 走不到这里(见 t_less_than). 无 '>' 的
    # 这种字节在 close() 里原样吐出 —— 内容不丢, 只是多一条 unterminated.
    s = BodyScanner()
    evs = s.feed('x <|y| 是绝对值') + s.close()
    assert ''.join(e.text for e in evs) == 'x <|y| 是绝对值', evs
    assert s.unclosed, '竖线开头被当成"疑似标签", 流尾该置位'


def main() -> None:
    t_text_and_selfclosing()
    t_body_forwarded_verbatim()
    t_nested_same_name()
    t_attrs_and_bare_name()
    t_namespaced_name()
    t_quantifier_names_are_plain()
    t_malformed()
    t_unclosed()
    t_half_tag_waits()
    t_entities()
    t_cdata()
    t_less_than()
    t_foreign_markup()
    print('test_body: 13 passed')


if __name__ == '__main__':
    main()
