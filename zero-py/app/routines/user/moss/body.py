"""Body —— CTML(Command Token Marked Language)的 body 扫描器, **视野只一层**.

CTML 是这套**标签语言**的名字(原版 MOSShell 词汇, 不是新概念): 它回答"一次
调用怎么写出来", 而 routine 是**被调用的那个东西** —— 两者不同类, 像一种语言
与用它写出的程序. 语法规则在 ``prompt/protocol.md``, 这里只管 body 怎么切.
(全仓只此一处写全称, 别的地方直接用缩写.)

这是统一指令形态的底座(见 ``docs/orchestration.md``): 每个实例只解释
**自己这一层**的 body. 父看到子标签就把内部字节原样转发, 从不看子内部
的语法; 子拿着自己的扫描器从零解释.

    parent body ::=  ( text | <child ...> child_body </child> | <child .../> )*

与"全局栈解析器"的分工(旧 ``CtmlParser`` 已退役): 那种解析器要
知道整棵文档的形状(命令栈/作用域栈/嵌套禁止), 一个扫描器扛下所有
层; 本扫描器只知道"我在本层, 碰到一个标签, 它的 body 到配对的闭合
为止". 边界由父负责切, 所以:

- 没有 ``SelfClose`` / ``Leftover`` —— 本层永远看不到自己的闭合标签
  (父已切掉), 也不会多出别人的字节;
- 没有"标签的 body 里禁止嵌套" —— 子 body 里的一切(含再嵌套)整体作为
  ``ChildBody`` 原样转发, 是不是合法由子那一层自己判.

自闭合 ``<x/>`` 与空 body 的 ``<x></x>`` 走同一条路(``ChildOpen`` +
立即 ``ChildClose``): 下游不必分两种形态.

``Malformed`` 是词法层唯一的错误出口(坏标签/坏属性), 由调用方转成
error 帧 —— 模型写错标签要当场看见, 不能被当成正文吞掉.

**保留字符**: 按 XML 语义, 属性值与**本层文本**里的实体会被还原
(``&lt;`` ``&gt;`` ``&amp;`` ``&quot;`` ``&apos;`` 与 ``&#NN;`` / ``&#xHH;``);
认不出的实体**保持原样**(不报错 —— 单写的 ``&`` 太常见, 报错只会误伤).
``<![CDATA[ ... ]]>`` 里的字节是**字面**(不还原), 标记本身被剥掉.

两处刻意不解码, 都是"body 是字节流"的直接后果(见 ``docs/orchestration.md``):

- ``ChildBody``(子标签内部的字节)原样转发 —— 父若解码, ``<all>&lt;x/&gt;</all>``
  里的转义就会变成真的标签, 那是注入不是语义;
- 于是实体只在本层被消费时才有效: 属性(父看得到)与本层文本。

**``<`` 的判定**: ``<`` 之后不是标签名字符(空格/数字/中文/``=``)时当场当
文本吐出, 不等人 —— 否则 ``1 < 2`` 会把后面整段吞进缓冲, 流结束时只剩一句
"tag unterminated", 而那话根本没写标签. 真像标签但流断了(``<say`` 没写完)
才置 ``unclosed`` 并把剩余字节**当文本吐出**: 内容不丢, 错误仍可见.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple, Union

__all__ = [
    'Text', 'Malformed', 'ChildOpen', 'ChildBody', 'ChildClose',
    'Event', 'BodyScanner', 'decode_body',
]

# 标签名: ``chan:cmd`` 或裸 ``cmd``(字母/下划线开头, 可带数字/下划线/连字符/
# 点/斜杠). **斜杠是 routine 名里真实存在的字符**(``car/move``): capability 按
# 注册名原样印名字, 模型照抄 —— 词法不认, 清单那行名字就永远写不成标签, 报出来
# 还是"malformed tag"(看着像模型写错了). 名字是事实, 词法跟着名字走.
_TAG_NAME = re.compile(
    r'[A-Za-z_][\w\-./]*(?::[A-Za-z_][\w\-./]*)?')
# 属性对: key="value"(单/双引号)或 key=bare
_ATTR = re.compile(
    r"""\s*(?P<key>[A-Za-z_][\w\-.]*)\s*=\s*
        (?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<nq>[^\s"'<>]+))""",
    re.VERBOSE,
)

# XML 只有五个预定义实体; 数字实体按码点还原.
# 只认**带分号**的形态: 宽松到无分号会误伤正文里的 "R&D"/"a &lt b".
_ENTITY = re.compile(r'&(?:#([0-9]+)|#[xX]([0-9A-Fa-f]+)|(lt|gt|amp|quot|apos));')
_ENTITIES = {'lt': '<', 'gt': '>', 'amp': '&', 'quot': '"', 'apos': "'"}

_CDATA_OPEN = '<![CDATA['
_CDATA_CLOSE = ']]>'
# 一个可能还没写完的实体: '&' 后只由 '#'/十六进制/字母组成(还没有 ';')
_ENTITY_PREFIX = re.compile(r'&(?:#[0-9]*|#[xX][0-9A-Fa-f]*|[A-Za-z]*)')


def _entity_tail(buf: str) -> int:
    """文本尾部是不是"半截实体" —— 是则返回 '&' 的位置(留给下一片).

    实体跨分片时不能就地解码: ``&#65`` 到了而 ``;`` 还在下一个 chunk,
    先吐出去就再也拼不回来了.
    """
    amp = buf.rfind('&')
    if amp < 0:
        return -1
    tail = buf[amp:]
    if len(tail) > 12 or not _ENTITY_PREFIX.fullmatch(tail):
        return -1                        # 有 ';' 或夹了别的字符 = 不是实体
    return amp


def decode_body(text: str, tail: str = '') -> Tuple[str, str]:
    """把一段 body 字节还原成真字符, 尾部半截实体留给下一片.

    给**叶子消费者**用的(say 的话 / code 的源码 / 黑板要写的字): 这些
    body 到此为止, 不会再被谁当 CTML 重扫, 所以可以就地还原 —— 不还原
    的代价是模型写 ``&lt;`` 会字面进 Python, 当场 SyntaxError。

    容器的 body 不走这里: 它是**下一层的标签源码**, 父若还原, 一个转义就
    变成执行(见模块头"保留字符")。

    返回 ``(可投递文本, 新的尾巴)``; 片边界上 ``&#65`` 到了而 ``;`` 还在
    下一片时, 这一段先扣住不吐, 否则拆开就再也拼不回来。
    """
    buf = tail + text
    cut = _entity_tail(buf)
    if cut < 0:
        return _unescape(buf), ''
    return _unescape(buf[:cut]), buf[cut:]


def _unescape(text: str) -> str:
    """还原 XML 实体; 认不出的保持原样(见模块头"保留字符")."""
    if '&' not in text:
        return text

    def _rep(m: 're.Match[str]') -> str:
        named = m.group(3)
        if named:
            return _ENTITIES[named]
        try:
            code = int(m.group(1)) if m.group(1) else int(m.group(2), 16)
            return chr(code)
        except (ValueError, OverflowError):
            return m.group(0)

    return _ENTITY.sub(_rep, text)


def _looks_like_tag(buf: str) -> bool:
    """``<`` 之后像不像一个标签的开头(决定"等"还是"当文本").

    ``<`` 后必须是名字首字符(字母/下划线)或 ``/``(闭合) —— 空格、数字、
    中文、``=``、``&`` 都不可能起头, 当场当字面量, 不必等到流尾.

    只有一个 ``<`` 时**返回 True(等)**: 分片边界上"<"与"say"是两个 chunk,
    这一刻还看不出它是什么, 判死会把每个标签都拆成文本.

    **竖线也等**(半角 ``|`` 与全角 ``｜``): 它不是标签名字符, 却会起头另一种
    标记 —— ``<|im_start|>``(ChatML)、``<｜｜DSML｜｜ invoke name="noop"/>``
    (DeepSeek 原生函数调用). 这类字节整块到达时会被 ``_parse_tag`` 判死成
    ``Malformed``(正确: 让模型看见自己格式错了), 但逐字流式到达时, "当场当
    字面量"会让同一段字节变成**旁白**, 于是模型的调用格式被当成它说的话念了
    出来。同一段字节不许有两种结论 —— 所以这里等 ``>``, 由词法层统一判死。
    代价: 正文里 ``x < |y|`` 且后面有 ``>`` 才会误判, 而没有 ``>`` 的会在
    ``close()`` 里原样吐出(内容不丢).
    """
    if len(buf) < 2:
        return True
    return bool(re.match(r'[A-Za-z_/|｜]', buf[1]))


def _cdata_partial(buf: str) -> bool:
    """``buf`` 是 ``<![CDATA[`` 的一个**未写完的前缀**(分片边界上)."""
    return len(buf) < len(_CDATA_OPEN) and _CDATA_OPEN.startswith(buf)



@dataclass
class Text:
    """本层纯文本(不进任何子)."""

    text: str


@dataclass
class Malformed:
    """词法错误: 这段字节像标签但不是合法标签. 调用方转 error 帧."""

    text: str


@dataclass
class ChildOpen:
    """子标签开启. ``chan``/``cmd`` 已按 ``chan:cmd`` 拆好(裸名 = main)."""

    name: str                        # 原始标签名(闭合配对用)
    chan: str                        # 轨道名; 裸名 = 'main'
    cmd: str                         # 指令名
    kwargs: dict = field(default_factory=dict)
    self_closing: bool = False


@dataclass
class ChildBody:
    """子标签内部的原始字节, 原样转给子自己解释."""

    text: str


@dataclass
class ChildClose:
    """当前子标签的配对闭合(自闭合标签在 ``ChildOpen`` 之后立即来一条)."""

    name: str


Event = Union[Text, Malformed, ChildOpen, ChildBody, ChildClose]


@dataclass
class _Tag:
    """一次标签词法解析的结果(内部用)."""

    name: str
    kwargs: dict
    is_close: bool
    self_closing: bool


def _find_tag_end(buf: str) -> int:
    """在 ``buf``(以 '<' 开头)里找标签闭合的 '>'; 引号内的 '>' 不算.

    返回下标; 未闭合返回 -1(等更多字节).
    """
    i, n, quote = 1, len(buf), ''
    while i < n:
        c = buf[i]
        if quote:
            if c == quote:
                quote = ''
        elif c in ('"', "'"):
            quote = c
        elif c == '>':
            return i
        i += 1
    return -1


def _parse_tag(src: str) -> Optional[_Tag]:
    """解析 ``'<...>'`` 的中间部分; 非法返回 None(上游转 Malformed)."""
    inner = src.strip()
    if not inner:
        return None
    if inner.startswith('/'):
        name = inner[1:].strip()
        if not _TAG_NAME.fullmatch(name):
            return None
        return _Tag(name=name, kwargs={}, is_close=True, self_closing=False)

    self_closing = inner.endswith('/')
    if self_closing:
        inner = inner[:-1]
    m = _TAG_NAME.match(inner)
    if m is None:
        return None
    name = m.group(0)
    rest = inner[m.end():]
    kwargs: dict = {}
    pos = 0
    for am in _ATTR.finditer(rest):
        if rest[pos:am.start()].strip():
            return None                     # 属性区有残料 = 非法标签
        val = am.group('dq')
        if val is None:
            val = am.group('sq')
        if val is None:
            val = am.group('nq') or ''
        kwargs[am.group('key')] = _unescape(val)
        pos = am.end()
    if rest[pos:].strip():
        return None
    return _Tag(name=name, kwargs=kwargs, is_close=False,
                self_closing=self_closing)


def _split_name(name: str) -> Optional[tuple[str, str]]:
    """``chan:cmd`` → (chan, cmd); 裸名 → ('main', name); 多段 → None."""
    chan, sep, cmd = name.partition(':')
    if not sep:
        return 'main', name
    if not chan or not cmd or ':' in cmd:
        return None
    return chan, cmd


def _cmd_of(name: str) -> str:
    """标签名的指令部分(闭合配对用: ``</say>`` 关闭 ``<main:say>``)."""
    return name.partition(':')[2] or name


class BodyScanner:
    """一层 body 的增量扫描器. 纯逻辑, 零框架依赖.

    用法::

        s = BodyScanner()
        for chunk in stream:
            for ev in s.feed(chunk):
                dispatch(ev)
        for ev in s.close():          # 流结束/父切到闭合为止
            dispatch(ev)
        if s.unclosed:                # 有子标签没闭合(流断了)
            ...
    """

    def __init__(self) -> None:
        self._buf: str = ''
        self._child: Optional[str] = None    # 当前开着的子标签名(原始)
        self._child_cmd: str = ''            # 它的指令部分(闭合配对用)
        self._depth: int = 0                 # 同名嵌套深度
        self.unclosed: bool = False          # close() 时有未闭合子标签

    # --- 对外 ---

    def feed(self, text: str) -> List[Event]:
        if text:
            self._buf += text
        return list(self._drain())

    def close(self) -> List[Event]:
        """流结束: 冲掉尾字节; 未闭合子标签合成一条 ChildClose 并置
        ``unclosed``(调用方据此报协议错误 —— 内容不丢, 错误可见).

        尾巴上的半个标签(``<a:x``)**当文本吐出**并置位: 原先直接丢掉,
        于是"内容不丢"只在正常路径成立 —— 而流断的时刻恰恰最需要留下
        证据(它到底写到哪了).
        """
        out = list(self._drain())
        if self._child is not None:
            self.unclosed = True
            out.append(ChildClose(self._child))
            self._child, self._child_cmd, self._depth = None, '', 0
        elif self._buf:
            tail, self._buf = self._buf, ''
            if len(tail) > 1 and tail.startswith('<') and _looks_like_tag(tail):
                self.unclosed = True          # 半个标签: 交给调用方报 unterminated
                out.append(Text(tail))        # 但字节还是吐出来, 别吞
            else:
                out.append(Text(_unescape(tail)))
        return out

    # --- 状态机 ---

    def _drain(self) -> Iterator[Event]:
        while True:
            if self._child is not None:
                progressed = yield from self._drain_child()
            else:
                progressed = yield from self._drain_top()
            if not progressed:
                return

    def _drain_top(self) -> Iterator[Event]:
        """本层: 文本 / 子标签. 碰到闭合标签是父该切的地方 —— 当作
        文本吐出(边界归父, 本层不认自己的闭合)."""
        lt = self._buf.find('<')
        if lt < 0:
            hold = _entity_tail(self._buf)
            if hold > 0:
                seg, self._buf = self._buf[:hold], self._buf[hold:]
                yield Text(_unescape(seg))
                return True
            if hold == 0:
                return False                 # 整段是一个还没写完的实体
            if self._buf:
                seg, self._buf = self._buf, ''
                yield Text(_unescape(seg))
                return True
            return False
        if lt > 0:
            # '&' 后紧跟 '<' 不可能是实体, 不必留尾
            seg, self._buf = self._buf[:lt], self._buf[lt:]
            yield Text(_unescape(seg))
            return True

        if self._buf.startswith(_CDATA_OPEN):
            got = self._cdata()
            if got is None:
                return False                 # 等 ']]>'(流断了由 close 兜底)
            text, size = got
            self._buf = self._buf[size:]
            yield Text(text)                 # CDATA 是字面: 不还原实体
            return True
        if _cdata_partial(self._buf):
            return False                     # '<![CDA' 这类开头还没收完

        end = _find_tag_end(self._buf)
        if end < 0:
            if not _looks_like_tag(self._buf):
                # '<' 后面不是标签名字符(如 "1 < 2"): 当场当字面量, 不等人.
                # 否则它会把后面整段吞进缓冲, 流尾只剩一句莫须有的
                # "tag unterminated".
                self._buf = self._buf[1:]
                yield Text('<')
                return True
            return False                     # 半个标签, 等更多字节
        span = self._buf[:end + 1]
        self._buf = self._buf[end + 1:]
        tag = _parse_tag(span[1:-1])
        if tag is None or tag.is_close:
            yield Malformed(span)
            return True
        pair = _split_name(tag.name)
        if pair is None:
            yield Malformed(span)
            return True
        chan, cmd = pair
        yield ChildOpen(name=tag.name, chan=chan, cmd=cmd,
                        kwargs=tag.kwargs, self_closing=tag.self_closing)
        if tag.self_closing:
            yield ChildClose(tag.name)       # 自闭合 ≡ 空 body
            return True
        self._child, self._child_cmd, self._depth = tag.name, cmd, 1
        return True

    def _drain_child(self) -> Iterator[Event]:
        """子 body 里: 原样转发, 只数同名标签的配对(找边界)."""
        assert self._child is not None
        while self._buf:
            lt = self._buf.find('<')
            if lt < 0:
                seg, self._buf = self._buf, ''
                yield ChildBody(seg)
                return True
            if lt > 0:
                seg, self._buf = self._buf[:lt], self._buf[lt:]
                yield ChildBody(seg)
                return True

            # CDATA: 里面的字节是内容(含闭合标签的字面), 标记剥掉.
            # 必须在 _looks_like_tag 之前判 —— '<!' 不像标签名, 但它是
            # 唯一合法的"内容保护区".
            if self._buf.startswith(_CDATA_OPEN):
                got = self._cdata()
                if got is None:
                    return False
                text, size = got
                self._buf = self._buf[size:]
                yield ChildBody(text)
                return True
            if _cdata_partial(self._buf):
                return False                 # '<![CDA' 还没收完

            if not _looks_like_tag(self._buf):
                # 子体里的 "1 < 2": 只吐这一个 '<', 别把 '< 2</say>' 整段
                # 当 body 转发 —— 那样会把闭合标签一起吞掉, 深度永远不减.
                self._buf = self._buf[1:]
                yield ChildBody('<')
                return True

            end = _find_tag_end(self._buf)
            if end < 0:
                return False
            span = self._buf[:end + 1]
            self._buf = self._buf[end + 1:]
            tag = _parse_tag(span[1:-1])
            if tag is not None and _cmd_of(tag.name) == self._child_cmd:
                if tag.is_close:
                    self._depth -= 1
                    if self._depth == 0:
                        name = self._child
                        self._child, self._child_cmd = None, ''
                        yield ChildClose(name)
                        return True
                elif not tag.self_closing:
                    self._depth += 1
            yield ChildBody(span)
            return True
        return False

    def _cdata(self) -> Optional[tuple[str, int]]:
        """``buf`` 以 ``<![CDATA[`` 开头时取内容与总长; 还没等到 ``]]>`` → None."""
        end = self._buf.find(_CDATA_CLOSE, len(_CDATA_OPEN))
        if end < 0:
            return None
        return self._buf[len(_CDATA_OPEN):end], end + len(_CDATA_CLOSE)
