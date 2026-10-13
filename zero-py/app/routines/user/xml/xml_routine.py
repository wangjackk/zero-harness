"""XmlRoutine -- message 驱动的 XML body 编排器.

hook 写法对齐(子类 override 的 API 一致),内部驱动不同:

- **参考实现**:框架 wire body.chunk -> ``on_body_chunk(BodyChunk)``.
- **本实现**:on_message 收 ``{text}`` -> body 队列 -> _parse_loop 构造 BodyChunk ->
  ``on_body_chunk``.on_body_chunk 是 parser 前的统一入口.

双 shell(body + normal)实例隔离(参考实现用 shell_id 路由,本实现用 shell_id 区分 push):
- **body_shell**(``shell_id='body'``):XML body 解析派生的 push 走这(``on_xml_event`` 默认派发).
  ChildOpen -> ``body_shell.push`` 拿 handle(父在 ``on_xml_event`` 里自接:设 stopped 回调 / 收 handle 列表);ChildBody -> ``send`` 给当前子
  ``shell_id='body'``);ChildBody -> ``send`` 给子;ChildClose -> 给子发 ``_eof``.
- **normal_shell**(``shell_id='default'``):代码主动 ``self.push(...)`` 走这(子类业务编排).

body_shell done -> ``on_body_shell_done`` -> ``request_stop`` 让子类 run 自然退出.
on_stopped 只 cancel 本 routine 的 parse_task(子的清退由 kernel 级联).

body 流来源:本 routine 的 ``on_message``.父 / LLM 流式 ``send(rid, {'text':chunk})``
投递,结尾 ``{'_eof':True}`` 终结.mailbox 保证同实例严格按发送序串行到达,
on_message 只入队(start 前早到的 body 排队不丢帧),parse_task 拉取构造 BodyChunk.

模块串并行:Shell ``_Entry._run`` 处理(等冲突左兄弟 stop 释放模块后再 start;不冲突
并行)--对标 Shell 的 ``CanBlockRightSibling``.kernel TryAcquire 是底层正确性兜底.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, TYPE_CHECKING

from routine import Routine, RoutineSource

from ..shell import Shell
from .body_chunk import BodyChunk, BodyChunkKind, TextChunk
from .xml_parser import (
    ChildBody,
    ChildClose,
    ChildOpen,
    Event,
    Leftover,
    SelfClose,
    Text,
    XmlBodyParser,
)

if TYPE_CHECKING:
    from routine import RoutineHandle


class XmlRoutine(Routine):
    """message 驱动的 XML body 编排器.双 shell(body + normal).

    hook:
        - ``on_body_chunk(chunk)``:body 流事件(CHUNK/STREAM_CLOSED/ABORTED),默认喂 parser
        - ``on_body_text(chunk)``:顶层裸文本(TextChunk, segment 聚合 N×False + 1×True)
        - ``on_xml_event(ev)``:XML 结构事件;默认 ChildOpen 走 body_shell.push 拿 handle(存 _cur_handle),
          子类可 override 在 push 后立刻对 handle 做事(设 stopped 回调 / 收集列表)
        - ``on_body_shell_done(shell)``:body_shell done -> request_stop
        - ``on_xml_event(ev)``:XML 结构事件(ChildOpen/ChildBody/ChildClose/...)

    XmlRoutine 不实现 run(abstract),业务子类必须给 run--典型实现等 body_shell done.
    """

    meta = {'description': 'XML body 编排器(message 驱动,双 shell)'}

    # body_shell 的 shell_id(对标 BODY_SHELL_ID='body').normal_shell 用 'default'.
    # on_xml_event 里据 shell_id 区分 body 派生 push vs normal_shell;on_body_shell_done 按 shell_id 过滤.
    XML_BODY_SHELL_ID = 'body'

    def __init__(self) -> None:
        super().__init__()
        # parser 状态在 created 备好(Invariant A:依赖 body 流的 state 不能等 run).
        self._parser: Optional[XmlBodyParser] = None
        # body_shell:XML body 派生的 push 走这(shell_id=BODY_SHELL_ID).done ->
        # on_body_shell_done -> request_stop.normal_shell:代码主动 self.push() 走这
        # (shell_id='default').两者独立 done/interrupt,按 shell_id 区分 normal/body.
        self._body_shell: Optional[Shell] = None
        self._normal_shell: Optional[Shell] = None
        # 当前正开着 body 的子 handle;ChildBody 灌给它,ChildClose 关掉.
        self._cur_handle: Optional['RoutineHandle'] = None
        # parse_task 从 body 队列拉 chunk 构造 BodyChunk 驱动 on_body_chunk.
        self._parse_task: Optional[asyncio.Task] = None
        # body chunk 队列:on_message(mailbox 串行)入队,parse_task 消费.
        # start 前早到的 body 在这排队,不丢帧.
        self._body_q: Optional[asyncio.Queue] = None
        self._stream_done: bool = False
        # 当前子是否自闭合标签(<tag .../>).自闭合不发 _eof:子的 body_shell 不
        # complete → ondone 不触发 → 不 request_stop,让 run() 自然完成.
        # 开闭标签(<tag></tag> / <tag><child/></tag>)才发 _eof.
        self._cur_self_closing: bool = False
        # text segment 聚合状态:连续 Text event 合成一个 segment,
        # N 次 is_last=False + 1 次 is_last=True(_close_text_segment 发聚合尾包).
        self._in_text_segment: bool = False
        self._text_buf: list = []
        self._text_start: Optional[int] = None
        self._text_end: Optional[int] = None
        # 段首空白暂扣:工具标签间的 '\n' 不是语音内容,不转发给 on_body_text;
        # is_first 转嫁给首个非空白 chunk。全空白段(暂扣一直没兑现)整段吞。
        self._text_pended_first: bool = False
        # 看到 SelfClose 后置 True--抑制后续 Leftover warning.
        self._self_closed: bool = False

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._parser = XmlBodyParser(self.name)
        self._body_q = asyncio.Queue()
        # parse_task 不在这起:created→start 间被拒(TryAcquire 冲突)的实例
        # 不该留任何后台 task.reject 清理不走 on_stopped(业务 task 业务自管,
        # 框架不代 cancel),生命周期绑定 started 才是干净边界.
        # body_shell:done 触发 on_body_shell_done -> request_stop 让 run 自然退出.
        # auto_arm=False:created 阶段 push 只 create(父未 started);on_started arm() 后才 start.
        # shell_id=BODY_SHELL_ID:on_xml_event 里据它区分 body 派生 push vs normal_shell.
        self._body_shell = Shell(
            self, shell_id=self.XML_BODY_SHELL_ID, auto_arm=False,
            ondone=self.on_body_shell_done,
        )
        self._normal_shell = Shell(self, auto_arm=False)

    async def on_started(self) -> None:
        # 父已 started(ack_start 在 run 之前发):arm 让两个 Shell 开始 start 已 push 的 handle.
        self._body_shell.arm()
        self._normal_shell.arm()
        # parse_task 从这起:start 前早到的 body 只在 body 队列排队(不丢帧,
        # on_message 只入队),start 后由 parse_task 拉取驱动 on_body_chunk.
        self._parse_task = asyncio.create_task(self._parse_loop())

    async def on_stopped(self, reason: str = 'auto', result: Any = None,
                         detail: str = '') -> None:
        # 只清本 routine 自己的业务:parse_task(后台解析 task).子 routine 的清退
        # 由 kernel 级联负责(m.stop 先停子再停父,父 on_stopped 时子已 done),不在这处理.
        if self._parse_task is not None and not self._parse_task.done():
            self._parse_task.cancel()

    # ------------------------------------------------------------------
    # body hook(对标,子类可 override)
    # ------------------------------------------------------------------

    async def on_body_chunk(self, chunk: BodyChunk) -> None:
        """body 流事件分发:按 ``chunk.kind`` 分发到数据帧 / 流收口 / 打断三条路径.

        默认实现:
            CHUNK -> ``_on_body_data``:喂 parser,分发它产出的 event.
            STREAM_CLOSED -> ``_on_body_stream_closed``:drain parser + 关 text segment.
            ABORTED -> ``_on_body_aborted``:丢 parser + 清 writer 状态.

        zero 内部由 _parse_loop 从 body 队列构造:on_message 收 {text} -> CHUNK;
        {_eof} -> STREAM_CLOSED.ABORTED 触发时机留后续(本次定义+默认实现处理).
        子类 override(如 Act)想拦截 body 事件在此分流,其余 ``await super().on_body_chunk(chunk)``.
        """
        if chunk.kind is BodyChunkKind.CHUNK:
            await self._on_body_data(chunk.text)
        elif chunk.kind is BodyChunkKind.STREAM_CLOSED:
            await self._on_body_stream_closed()
        elif chunk.kind is BodyChunkKind.ABORTED:
            await self._on_body_aborted()

    async def on_body_text(self, chunk: TextChunk) -> None:
        """顶层裸文本事件流.默认 no-op,子类 override 接管.

        同一个 text segment 触发 N+1 次:N 次 ``is_last=False``(parser 连续吐出的文本片段)
        + 1 次 ``is_last=True``(segment 收尾聚合尾包).``start``/``end`` 是字节偏移.

        空白规范化(基类产出点已做,子类看到的流保证干净):
        - 段首空白裁剪:``is_first`` 转嫁给首个非空白 chunk;
        - 全空白段(如工具标签间的 '\n')整段吞,不触发本 hook;
        - 段中空白保留(是内容,TTS 断句).
        """
        pass

    async def on_body_shell_done(self, shell: Shell) -> None:
        """body_shell done -> request_stop 让 run 自然退出.

        body_shell done = body 流终结(_eof)+ 所有 body 派生子完成.此时 routine
        主体已完成,发 request_stop 让 kernel 走正规 stop 流程(级联停 normal_shell 的子
        + cancel parse_task 在 on_stopped 里).

        防御性 shell_id 检查:本回调只对 body_shell 响应;若误注册到别的 shell
        上(理论不会,ondone 只挂 body_shell)直接 return.
        """
        self._logger.info('body_shell: done')
        if shell.shell_id != self.XML_BODY_SHELL_ID:
            return
        await self.ctx.request_stop()

    # ------------------------------------------------------------------
    # message 入口
    # ------------------------------------------------------------------

    async def on_message(self, source: RoutineSource, data: Any) -> None:
        """收 body chunk 直接入 body 队列.

        mailbox 保证同实例严格按发送序串行调用本方法(发送侧单任务逐条
        await send + 传输 FIFO),parser 顺序由框架兜底,无需业务 reorder.
        """
        if self._body_q is None or self._stream_done:
            return
        if isinstance(data, dict):
            self._body_q.put_nowait(data)

    async def push(self, name: str,
                   kwargs: Optional[Dict[str, Any]] = None) -> 'RoutineHandle':
        """代码主动 push 到 normal_shell(对标 self.push = normal shell).

        区别于 ``self.create``(裸 create 不经编排):push 进 normal_shell 享受串并行
        编排(冲突串行,不冲突并行).子类业务编排用本方法,XML body 派生的 push
        走 body_shell(``on_xml_event`` 默认派发,不调本方法).
        """
        return await self._normal_shell.push(name, kwargs)

    # ------------------------------------------------------------------
    # parse_task:body 队列 -> BodyChunk -> on_body_chunk
    # ------------------------------------------------------------------

    async def _parse_loop(self) -> None:
        """body 队列拉 chunk,构造 BodyChunk 驱动 on_body_chunk.

        {text} -> BodyChunk(CHUNK, text);{_eof} -> BodyChunk(STREAM_CLOSED).
        on_body_chunk 默认实现内部 feed parser -> _handle_xml_event -> on_xml_event.
        子类 override on_body_chunk 可拦截(对标框架 wire body.chunk -> on_body_chunk).
        """
        assert self._body_q is not None and self._body_shell is not None
        try:
            while True:
                data = await self._body_q.get()
                if data is None:
                    break
                if data.get('_eof'):
                    # 流终结:STREAM_CLOSED 让 on_body_chunk drain parser + 关 text segment.
                    await self.on_body_chunk(BodyChunk(kind=BodyChunkKind.STREAM_CLOSED))
                    break
                text = data.get('text', '')
                if text:
                    await self.on_body_chunk(
                        BodyChunk(kind=BodyChunkKind.CHUNK, text=text),
                    )
            # 循环靠 _eof 退出;STREAM_CLOSED 已 drain parser.
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception('parse_loop error')
            raise
        finally:
            self._stream_done = True
            # 异常/Cancel 兜底:正常路径已在 _on_body_stream_closed 里 complete
            # (语义:body 流终结).没走到那(异常退出)这里保底,否则 body_shell 永不 done.
            # 幂等--正常路径已 complete 则 no-op.
            self._body_shell.complete()

    # ------------------------------------------------------------------
    # on_body_chunk 默认实现内部(对标 _on_body_data / _on_body_stream_closed / _on_body_aborted)
    # ------------------------------------------------------------------

    async def _on_body_data(self, chunk: str) -> None:
        """CHUNK:喂 parser,分发它产出的事件.

        实现要点:``parser.feed`` 是同步算法,一次性 ``list(...)`` 物化整批 event
        后再 ``await``.不能边 iterate 边 await--``self._parser`` 是实例级共享状态,await
        让出期间若并发改动 parser,后续 event 会被吃掉.
        """
        if self._parser is None:
            self._parser = XmlBodyParser(self.name)
        events = list(self._parser.feed(chunk))
        for ev in events:
            await self._handle_xml_event(ev)

    async def _on_body_stream_closed(self) -> None:
        """STREAM_CLOSED:drain parser 剩余(合成 ChildClose + SelfClose)+ 关 text segment.

        实现要点:``parser.close()`` 先一次性物化成 list,并立即把
        ``self._parser`` 置 ``None`` 释放共享状态,之后再 ``await``.这样 await 让出期间
        若有别的协程再调 ``on_body_chunk(CHUNK)``,它会创建一份新的 parser,不污染旧批 events.
        """
        if self._parser is not None:
            events = list(self._parser.close())
            self._parser = None
            for ev in events:
                await self._handle_xml_event(ev)
            await self._close_text_segment()
        # body 流终结 = 不会再 push 新子 = complete body_shell.触发点放这(语义层),
        # 不放 _parse_loop 控制流--让任何驱动方(走 on_message 的 parse_loop / 直接调
        # on_body_chunk 的 RunXml)只要发 STREAM_CLOSED 就自动 complete._parse_loop
        # finally 仍兜底(异常/Cancel 没走到这时保底,幂等).
        self._body_shell.complete()

    async def _on_body_aborted(self) -> None:
        """ABORTED:丢 parser + 清 text 聚合状态.

        不自动 close body_shell--由 stop 流程的 interrupt 统一负责(对标 Invariant C).
        """
        self._parser = None
        self._in_text_segment = False
        self._text_buf = []
        self._text_start = None
        self._text_end = None
        self._text_pended_first = False
        self._cur_handle = None

    # ------------------------------------------------------------------
    # 默认 XML 解释入口 -- 子类可覆盖
    # ------------------------------------------------------------------

    async def on_xml_event(self, ev: Event) -> None:
        """对 XML 结构事件的解释入口;默认透传 body shell.

        默认(用 send message 灌子 body,不是 BodyWriter--内部实现差异):
            ``ChildOpen`` -> ``body_shell.push`` 拿 handle(存 ``_cur_handle``;子类 override
            本方法可在 super() 后对 handle 设 stopped 回调 / 收集)
            ``ChildBody`` -> ``send`` 给当前子(带 id)
            ``ChildClose`` -> 给当前子发 ``_eof``(带 id)
            ``SelfClose`` / ``Text`` / ``Leftover`` 由 ``_handle_xml_event`` 处理,不到这.

        想要"默认行为 + 额外动作"可 ``await super().on_xml_event(ev)`` 再加.
        """
        if isinstance(ev, ChildOpen):
            # push 返回的 handle.modules 是占用真理源(created 回执带回).
            # 子类 override on_xml_event 可在 super() 后用 self._cur_handle 设回调/收集.
            self._cur_handle = await self._body_shell.push(ev.name, ev.kwargs)
            self._cur_self_closing = ev.is_self_closing
            return
        if isinstance(ev, ChildBody):
            if self._cur_handle is not None:
                await self.send(self._cur_handle.id, {'text': ev.text})
            return
        if isinstance(ev, ChildClose):
            # 给当前子发 eof 让它收尾自然 done.
            # 不在这里 wait--串并行靠 Shell._Entry._run(arm 后才 start/等).
            # 自闭合标签(<tag .../>)不发 _eof:子的 body_shell 不 complete,
            # ondone 不触发,不 request_stop——让 run() 自然完成.
            # 开闭标签(空/非空)才发 _eof.
            if self._cur_handle is not None and not self._cur_self_closing:
                await self.send(self._cur_handle.id, {'_eof': True})
            self._cur_handle = None
            self._cur_self_closing = False
            return

    # ------------------------------------------------------------------
    # internal dispatch(对标 _handle_xml_event + _close_text_segment)
    # ------------------------------------------------------------------

    async def _close_text_segment(self) -> None:
        """聚合的 text segment 收尾:发一次 ``on_body_text(is_last=True)`` 尾包.

        全空白段(段首空白暂扣一直没兑现)整段吞:不发尾包,不通知消费方 --
        工具标签间的 '\n' 不该进 speak,消费方(开 speak 子)不该见到它。
        """
        if not self._in_text_segment:
            return
        text = ''.join(self._text_buf)
        pended_first = self._text_pended_first
        start = self._text_start
        end = self._text_end
        self._in_text_segment = False
        self._text_buf = []
        self._text_start = None
        self._text_end = None
        self._text_pended_first = False
        if pended_first:
            return
        await self.on_body_text(TextChunk(
            text=text, is_first=False, is_last=True, start=start, end=end,
        ))

    async def _handle_xml_event(self, ev: Event) -> None:
        """parser event 分发:text 聚合 + SelfClose/Leftover 处理 + 其余 on_xml_event.

        - 非 Text/Leftover event 先 ``_close_text_segment`` 关掉正在聚合的 text segment.
        - ``Text``:聚合进当前 segment,发 ``on_body_text(is_last=False)``.
        - ``SelfClose``:置 ``_self_closed`` 抑制后续 Leftover.
        - ``Leftover``:有 text 且未 SelfClose 则 warning(典型 LLM 输出乱),否则 debug.
        - 其余(ChildOpen/ChildBody/ChildClose):交 ``on_xml_event``.
        """
        if not isinstance(ev, (Text, Leftover)):
            await self._close_text_segment()

        if isinstance(ev, Text):
            is_first = not self._in_text_segment
            self._in_text_segment = True
            if is_first:
                self._text_start = ev.start
            self._text_buf.append(ev.text)
            self._text_end = ev.end
            # 段首空白暂扣:裁掉(is_first 转嫁给首个非空白 chunk);段中空白
            # 是内容('好\n你' 的 '\n' 是 TTS 断句),原样转发。
            if (is_first or self._text_pended_first) and not ev.text.strip():
                self._text_pended_first = True
                return
            if self._text_pended_first:
                self._text_pended_first = False
                is_first = True
            await self.on_body_text(TextChunk(
                text=ev.text, is_first=is_first, is_last=False,
                start=ev.start, end=ev.end,
            ))
            return

        if isinstance(ev, SelfClose):
            # 实时流里 parser 看到自己的 </SELF> 标签;标记抑制后续 Leftover.
            # body shell close / on_body_chunk(STREAM_CLOSED) 由 _parse_loop 在 _eof 时触发.
            self._self_closed = True
            return

        if isinstance(ev, Leftover):
            if ev.text and not self._self_closed:
                self._logger.warning(
                    'leftover bytes before </%s>: %r (dropped)', self.name, ev.text,
                )
            elif ev.text:
                self._logger.debug(
                    'leftover after </%s>: %r (dropped)', self.name, ev.text,
                )
            return

        await self.on_xml_event(ev)
