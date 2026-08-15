"""Streaming XML-ish body parser ---- routine 端"自己解释"自己 body 的工具.

设计:

一个 routine 的 body 是它"<开标签>...</闭标签>"之间的文本 (LLM 写出来
的).每个 routine 自己持一个 parser, parser 视野**只一层深**::

    parent body ::=  <text> ( <child_open> child_body <child_close> | <text> )*  </SELF>  <leftover>

字节流进来时 parser 增量发出事件:

- :class:`Text` ``(s)``       ---- parent body 顶层的纯文本.
- :class:`ChildOpen`         ---- 见到 ``<child .../>`` 或 ``<child ...>``;
  自闭合 ``<x/>`` 等价于空 body 的 ``<x></x>`` (见 :class:`ChildOpen`).
- :class:`ChildBody` ``(s)``  ---- 子标签内部的字节, 留给子的 ``on_body_chunk``
  自己再解释.
- :class:`ChildClose`        ---- 子的匹配 ``</child>``.
- :class:`SelfClose`         ---- 我自己的 ``</SELF>``.
- :class:`Leftover` ``(s)``   ---- ``</SELF>`` 之后又来的字节; 这些其实属
  于我的 enclosing scope, 由调用方 (XmlRoutine) 负责"反吞"回上层.

Parser 只下钻一层: 子内部 (含再嵌套的标签) 全部作为 :class:`ChildBody`
原样转给子, 子有自己的 parser 处理自己的嵌套.

简化点: 不支持 DTD / CDATA / 注释 / XML 声明; 属性值必须加引号 (单/双
都行); 引号内的 ``>`` 不算闭合 (e.g. ``<play name="a>b"/>`` 能正确切).

parser 是同步,分配轻量的 ---- 字节流堆 buf 然后惰性扫.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional, Union


# -----------------------------------------------------------------------------
# Event types
# -----------------------------------------------------------------------------


@dataclass
class Text:
    """Parent body 顶层的纯文本."""

    text: str
    start: int
    end: int


@dataclass
class ChildOpen:
    """碰见一个 ``<child ...>`` 或 ``<child .../>`` 标签.

    自闭合语法等价于**空 body**: ``<child/>`` 跟 ``<child></child>``
    解析路径完全一致 ---- 调用方都会先看到 :class:`ChildOpen` 再看到匹配
    的 :class:`ChildClose` (自闭时立即, 显式 body 时夹中间几帧
    :class:`ChildBody`).这里**故意没有** "has body" flag, 让下游 forward
    管线统一一条路: 每次子调用都有 body, 只不过有时候 0 字节而已.
    """

    name: str
    kwargs: dict
    start: int
    end: int
    is_self_closing: bool = False


@dataclass
class ChildBody:
    """开着的子标签内部传过来的原始字节 (要灌给子的 ``on_body_chunk``)."""

    text: str


@dataclass
class ChildClose:
    """当前子标签的匹配 close tag."""

    name: str
    start: int
    end: int


@dataclass
class SelfClose:
    """看见我自己的 ``</SELF>`` 闭合标签."""


@dataclass
class Leftover:
    """``</SELF>`` 之后又来的字节 ---- 应该被 enclosing parent 吞回去."""

    text: str


Event = Union[Text, ChildOpen, ChildBody, ChildClose, SelfClose, Leftover]


# -----------------------------------------------------------------------------
# Tag scanner helpers
# -----------------------------------------------------------------------------

_TAG_NAME_RE = re.compile(r"[A-Za-z_][\w\-.]*")

_ATTR_RE = re.compile(
    r"""
    \s*
    (?P<key>[A-Za-z_][\w\-.]*)
    \s*=\s*
    (?:
        "(?P<dq>[^"]*)"
      | '(?P<sq>[^']*)'
      | (?P<nq>[^\s"'<>]+)
    )
    """,
    re.VERBOSE,
)


def _find_tag_end(buf: str, start: int) -> int:
    """找 ``buf[start]=='<'`` 这个标签的关闭 ``>`` 位置.

    没扫完 (标签没收齐) 返回 -1.引号内的 ``>`` 不当 tag end.
    """
    i = start + 1
    n = len(buf)
    in_quote: Optional[str] = None
    while i < n:
        ch = buf[i]
        if in_quote:
            if ch == in_quote:
                in_quote = None
        else:
            if ch == '"' or ch == "'":
                in_quote = ch
            elif ch == ">":
                return i
        i += 1
    return -1


@dataclass
class _ParsedTag:
    name: str
    is_close: bool
    is_self_close: bool
    kwargs: dict = field(default_factory=dict)


def _parse_tag(buf: str, start: int, end: int) -> Optional[_ParsedTag]:
    """解析 ``buf[start:end+1]`` == ``'<...>'`` 一个标签 span 成 :class:`_ParsedTag`.

    span 不是合法标签 (e.g. ``<<`` 之类的 garbage) 返回 None.
    """
    inner = buf[start + 1 : end]  # strip '<' and '>'
    if not inner:
        return None

    is_close = False
    is_self_close = False
    if inner.startswith("/"):
        inner = inner[1:].strip()
        m = _TAG_NAME_RE.match(inner)
        if not m or m.end() != len(inner):
            return None
        return _ParsedTag(name=m.group(0), is_close=True, is_self_close=False)

    if inner.endswith("/"):
        is_self_close = True
        inner = inner[:-1]

    m = _TAG_NAME_RE.match(inner)
    if not m:
        return None
    name = m.group(0)
    attrs_src = inner[m.end() :]

    kwargs: dict = {}
    for am in _ATTR_RE.finditer(attrs_src):
        val = am.group("dq")
        if val is None:
            val = am.group("sq")
        if val is None:
            val = am.group("nq") or ""
        kwargs[am.group("key")] = val

    return _ParsedTag(name=name, is_close=is_close, is_self_close=is_self_close, kwargs=kwargs)


# -----------------------------------------------------------------------------
# Streaming parser
# -----------------------------------------------------------------------------


class XmlBodyParser:
    """One-level XML body parser, 绑给一个 parent routine name.

    用法::

        p = XmlBodyParser(self_name='bgm')
        for ev in p.feed(chunk):
            ...
        for ev in p.close():
            ...

    看到 :class:`SelfClose` 之后, 再 feed/close 进来的字节会出 :class:`Leftover`
    事件 ---- 调用方应该把它们吐回 enclosing scope 让外层 parser 接着处理.
    """

    _STATE_TOP = "top"
    _STATE_CHILD = "child"
    _STATE_DONE = "done"

    def __init__(self, name: str) -> None:
        self.name = name
        self._buf = ""
        self._offset = 0
        self._state = self._STATE_TOP
        self._child_name: Optional[str] = None
        self._child_depth: int = 0

    # ---- public API ----

    def feed(self, chunk: str) -> Iterator[Event]:
        if not chunk:
            return
        self._buf += chunk
        yield from self._drain()

    def close(self) -> Iterator[Event]:
        """调用方知道字节流彻底结束时调一次.

        剩余 buf 按当前状态 flush: top → 先 drain 解析完整标签 (ChildOpen/ChildClose)
        再 yield 剩余文本 + 强制 :class:`SelfClose`; child → :class:`ChildBody` +
        合成 :class:`ChildClose` + 转 top + 强制 :class:`SelfClose`; done → :class:`Leftover`.

        注意 top 状态下不能直接把 buf 当 Text yield----buf 里可能有完整的自闭合
        标签 (如 ``<random_compliment count="20"/>``) 还没被 drain 解析.先调
        ``_drain_top`` 让它解析完整标签, drain 不掉的 (不完整标签/纯文本) 再 yield Text.
        """
        if self._state == self._STATE_DONE:
            if self._buf:
                yield Leftover(self._buf)
                self._offset += len(self._buf)
                self._buf = ""
            return
        if self._state == self._STATE_CHILD:
            if self._buf:
                yield ChildBody(self._buf)
                self._offset += len(self._buf)
                self._buf = ""
            assert self._child_name is not None
            end = self._offset - 1 if self._offset > 0 else 0
            yield ChildClose(self._child_name, start=end, end=end)
            self._child_name = None
            self._child_depth = 0
            self._state = self._STATE_TOP
        if self._state == self._STATE_TOP:
            # 先 drain: 解析 buf 里完整的标签 (自闭合 <x/> 或开标签 <x>...</x>).
            # _drain_top 解析掉完整标签后, 剩余不完整片段/纯文本留在 buf 里.
            yield from self._drain()
            # drain 后剩余 buf (不完整标签 / 纯文本) 作为 Text yield.
            if self._buf:
                start = self._offset
                end = self._offset + len(self._buf) - 1
                yield Text(self._buf, start=start, end=end)
                self._offset += len(self._buf)
                self._buf = ""
            yield SelfClose()
            self._state = self._STATE_DONE

    # ---- internal state machine ----

    def _drain(self) -> Iterator[Event]:
        while True:
            if self._state == self._STATE_DONE:
                if self._buf:
                    yield Leftover(self._buf)
                    self._offset += len(self._buf)
                    self._buf = ""
                return

            if self._state == self._STATE_TOP:
                progressed = yield from self._drain_top()
                if not progressed:
                    return
                continue

            if self._state == self._STATE_CHILD:
                progressed = yield from self._drain_child()
                if not progressed:
                    return
                continue

            return  # unreachable

    def _drain_top(self) -> Iterator[Event]:
        idx = self._buf.find("<")
        if idx < 0:
            if self._buf:
                start = self._offset
                end = self._offset + len(self._buf) - 1
                yield Text(self._buf, start=start, end=end)
                self._offset += len(self._buf)
                self._buf = ""
                return True
            return False

        if idx > 0:
            yield Text(
                self._buf[:idx],
                start=self._offset,
                end=self._offset + idx - 1,
            )
            self._offset += idx
            self._buf = self._buf[idx:]
            return True

        end = _find_tag_end(self._buf, 0)
        if end < 0:
            return False  # wait for more bytes

        tag = _parse_tag(self._buf, 0, end)
        span = self._buf[: end + 1]
        span_start = self._offset
        span_end = self._offset + end
        self._buf = self._buf[end + 1 :]
        self._offset = span_end + 1
        if tag is None:
            yield Text(span, start=span_start, end=span_end)
            return True

        if tag.is_close:
            if tag.name == self.name:
                self._state = self._STATE_DONE
                yield SelfClose()
                return True
            raw = f"</{tag.name}>"
            yield Text(raw, start=span_start, end=span_start + len(raw) - 1)
            return True

        if tag.is_self_close:
            yield ChildOpen(
                name=tag.name,
                kwargs=tag.kwargs,
                start=span_start,
                end=span_end,
                is_self_closing=True,
            )
            yield ChildClose(name=tag.name, start=span_start, end=span_end)
            return True

        yield ChildOpen(
            name=tag.name,
            kwargs=tag.kwargs,
            start=span_start,
            end=span_end,
            is_self_closing=False,
        )
        self._child_name = tag.name
        self._child_depth = 1
        self._state = self._STATE_CHILD
        return True

    def _drain_child(self) -> Iterator[Event]:
        assert self._child_name is not None

        while self._buf:
            idx = self._buf.find("<")
            if idx < 0:
                yield ChildBody(self._buf)
                self._offset += len(self._buf)
                self._buf = ""
                return True

            if idx > 0:
                yield ChildBody(self._buf[:idx])
                self._offset += idx
                self._buf = self._buf[idx:]
                return True

            end = _find_tag_end(self._buf, 0)
            if end < 0:
                return False  # wait for more bytes

            tag = _parse_tag(self._buf, 0, end)
            span = self._buf[: end + 1]
            span_start = self._offset
            span_end = self._offset + end

            if tag is not None and tag.is_close and tag.name == self._child_name:
                self._child_depth -= 1
                if self._child_depth == 0:
                    self._buf = self._buf[end + 1 :]
                    self._offset = span_end + 1
                    name = self._child_name
                    self._child_name = None
                    self._state = self._STATE_TOP
                    yield ChildClose(name, start=span_start, end=span_end)
                    return True
                yield ChildBody(span)
                self._buf = self._buf[end + 1 :]
                self._offset = span_end + 1
                return True

            if (
                tag is not None
                and not tag.is_close
                and tag.name == self._child_name
                and not tag.is_self_close
            ):
                self._child_depth += 1
                yield ChildBody(span)
                self._buf = self._buf[end + 1 :]
                self._offset = span_end + 1
                return True

            yield ChildBody(span)
            self._buf = self._buf[end + 1 :]
            self._offset = span_end + 1
            return True

        return False
