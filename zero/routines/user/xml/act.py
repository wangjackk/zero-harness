"""Act -- agent 动作执行管线(XmlRoutine 子类,流式 yield 工具子结果).

zero 重实现:

- agent push 一个 ``act`` 子,LLM 流式输出 XML body 经 ``send`` 喂给 act 的 ``on_message``
  (走 XmlRoutine 基类 reorder -> parser -> body_shell push 工具子).
- 工具子 done 时经 ``handle.on_stopped_handler`` 回调 put 进 queue;``run`` 是 async generator,
  从 queue 拉 yield 给父 agent(``async for res in act_handle``).
- body_shell done(body 流终结 + 全工具子 done)-> run 退出 -> act stopped.

hook 写法对齐:``on_body_chunk``(ABORTED 透传)/``on_xml_event``(ChildOpen 失败 put error)
/``on_xml_event``(ChildOpen 后设 on_stopped_handler)/``on_body_shell_done``(set _body_done).区别于:无
BodyWriter(agent 直接 send body);顶层裸文本 ``on_body_text`` 留 hook(基类 no-op,
push output 子说话依赖 output routine,zero 版子类按需 override).
"""
import asyncio
from typing import Any, Dict

from routine import RoutineHandle

from .body_chunk import BodyChunk, BodyChunkKind, TextChunk
from .xml_routine import XmlRoutine
from .xml_parser import ChildOpen

# wire 契约常量: act 注入调用方 agent_id 到 tool routine kwargs,
# tool 侧 (xml_agent 等调用方) 按同名键读取. 与 xml_agent/agent.py 保持一致.
AGENT_ID_KEY = 'from_agent_id'


def _clean_kwargs(kwargs: Dict[str, Any] | None) -> Dict[str, Any] | None:
    """过滤掉框架注入字段(如 agent_id), 只保留业务参数给前端展示."""
    if not kwargs:
        return None
    cleaned = {k: v for k, v in kwargs.items() if k != AGENT_ID_KEY}
    return cleaned or None


class Act(XmlRoutine):
    """LLM XML body -> body_shell 派发工具子 -> 流式 yield 结果给 agent.

    ``run`` 是 async generator:yield 每个工具子的 ``{id, name, result, error}``.
    agent 用::

        act = await self.push('act')          # normal_shell
        await act.start()
        seq = 0
        async for chunk in llm_stream(...):   # LLM 流式 XML
            await self.send(act.id, {'text': chunk, 'id': seq}); seq += 1
        await self.send(act.id, {'_eof': True, 'id': seq})
        async for res in act:                 # 拿每个工具子结果
            ...
    """

    meta = {'description': 'agent 动作执行管线(XML body -> 工具子 -> 流式 yield 结果)',
            'hidden': True}

    def __init__(self) -> None:
        super().__init__()
        self._done_q: asyncio.Queue = asyncio.Queue()
        self._child_count: int = 0
        self._done_count: int = 0
        self._body_done: asyncio.Event = asyncio.Event()
        # agent_id: agent submit act 时传入, push 工具子前注入到 ev.kwargs.
        # tool routine 通过 get_agent_rid 查 rid 后 ctx.req(rid, 'agent_state') 反向获取 skill_dir 等.
        self._agent_id: str | None = None
        # handle.id -> 输入 kwargs (过滤掉框架注入字段后), 供 _on_child_done 带进 yield 结果.
        self._child_inputs: dict[str, dict] = {}
        # 当前 speak 子 handle + body seq(顶层裸文本逐字显示).
        # on_body_text is_first 时 submit speak, 流式 send text, is_last 时 send _eof + await done.
        self._speak_handle: Any = None
        self._speak_seq: int = 0

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        await super().on_created(rid, kwargs)
        self._agent_id = kwargs.get(AGENT_ID_KEY)

    async def on_body_text(self, chunk: TextChunk) -> None:
        """顶层裸文本 -> body_shell push speak 子逐字显示(0.3s/字).

        跟 on_xml_event 的 ChildOpen/ChildBody/ChildClose 同款路径:
        - is_first=True: body_shell.push('speak') 拿 handle, 重置 seq (Shell 管 start)
        - is_last=False: send 流式 text 给 speak (跟 ChildBody 一致)
        - is_last=True: send _eof (跟 ChildClose 一致, 让 speak 自然 done)

        is_last=True 的 chunk.text 是聚合完整文本(重复), 不 send, 只发 _eof.
        不 wait —— Shell 自己管 start/stop 顺序(对标 on_xml_event 不 wait 的语义).
        """
        if chunk.is_first:
            self._speak_handle = await self._body_shell.push('speak')
            self._speak_seq = 0
        if chunk.is_last:
            await self.send(self._speak_handle.id, {
                '_eof': True, 'id': self._speak_seq,
            })
            self._speak_handle = None
        elif chunk.text:
            await self.send(self._speak_handle.id, {
                'text': chunk.text, 'id': self._speak_seq,
            })
            self._speak_seq += 1

    async def on_body_chunk(self, chunk: BodyChunk) -> None:
        """ABORTED 时清理(本次无 text_writer,no-op),其余透传基类默认分发.

        对标 Act.on_body_chunk:ABORTED 额外关 text_writer(有,zero 版留空).
        """
        if chunk.kind is BodyChunkKind.ABORTED:
            # 无 text_writer(output routine 留后续),这里 no-op;保留 hook 形态对齐.
            pass
        await super().on_body_chunk(chunk)

    async def on_body_shell_done(self, shell) -> None:
        """body_shell done -> 标记 _body_done 让 run 退出 + super 触发 request_stop."""
        await super().on_body_shell_done(shell)
        self._body_done.set()

    async def on_xml_event(self, ev: Any) -> None:
        """ChildOpen -> 走基类默认 push 拿 handle 后设 on_stopped_handler + count;失败 put error.

        参数注入: 在 push 之前把 agent_id 注入 ev.kwargs.
        tool routine 需要的其他信息通过 get_agent_rid 查 rid 后 ctx.req(rid, 'agent_state') 反向获取.

        失败的 child scope(基类 push 抛异常)捕获后 put 一条 error 结果,不让整轮崩--
        对标 Act.on_xml_event 的 try/except.后续 ChildBody/ChildClose 对该标签成 no-op
        (基类 _cur_handle 没设上).
        """
        if isinstance(ev, ChildOpen) and self._agent_id:
            if ev.kwargs is None:
                ev.kwargs = {}
            if AGENT_ID_KEY not in ev.kwargs:
                ev.kwargs[AGENT_ID_KEY] = self._agent_id
        if not isinstance(ev, ChildOpen):
            await super().on_xml_event(ev)
            return
        try:
            await super().on_xml_event(ev)
            # 基类 push 后 _cur_handle 已是刚 push 的 body 子:设 stopped 回调 + count
            # (原靠基类 push 钩子设的活,现在父在 on_xml_event 里 push 后自己接).
            handle = self._cur_handle
            if handle is not None:
                self._child_count += 1
                handle.on_stopped_handler = self._on_child_done
                # 存输入 kwargs (过滤框架注入字段), 供 _on_child_done yield 时带上 input.
                self._child_inputs[handle.id] = _clean_kwargs(ev.kwargs)
        except Exception as exc:
            self._logger.warning(f'ignore failed child scope <{ev.name}>: {exc}')
            self._child_count += 1
            self._done_q.put_nowait({
                'id': '', 'name': ev.name, 'result': None, 'error': str(exc),
                'input': _clean_kwargs(ev.kwargs),
            })
            self._done_count += 1
            # 不 set _body_done (同 _on_child_done, 等 body_shell complete)
            return

    async def _on_child_done(self, handle: RoutineHandle) -> None:
        """async 回调(notify_done 里 spawn 调):put_nowait 结果 + count.

        async 是 handle 生命周期回调的统一签名(handle.on_stopped_handler 约定 async
        callable,notify spawn 不阻塞 reader).本实现体内虽只同步操作(put_nowait/set),
        但保持 async 以符合签名,并允许未来扩展(await send/等子).

        不在这 set _body_done ---- 早期版本在 ``_done_count >= _child_count`` 时
        直接 set, 但 body_shell 可能还没 complete (LLM 流还在继续, 后续标签还没
        被 parser 解析 push). 此时 act.run 检查 ``_body_done.is_set() and
        _done_q.empty()`` 会提前退出, 导致后续标签的 chunk 没被 _parse_task 处理.
        _body_done 只由 on_body_shell_done 回调 set (body_shell complete + 所有
        子 done -> _fire_done -> on_body_shell_done -> _body_done.set()).
        """
        self._done_count += 1
        self._done_q.put_nowait({
            'id': handle.id, 'name': handle.name,
            'result': handle.result, 'error': handle.error,
            'input': self._child_inputs.pop(handle.id, None),
        })

    async def run(self, kwargs: Dict[str, Any]):
        """async generator:yield 每个工具子结果给父 agent.

        body_shell done(_body_done)+ queue drain 后退出.event-driven 等(queue.get 或
        _body_done.wait 哪个先就绪),避免 polling 延迟--对标 _yield_done_handles.

        注意 ``asyncio.wait`` 被 cancel 时**不会**取消内部 task(Python 文档行为),
        需 try/finally 手动 cancel--否则 react 被打断(_cancel_react cancel _run_react)
        时这两个 task 泄漏成 "Task was destroyed but it is pending".
        """
        while True:
            if self._body_done.is_set() and self._done_q.empty():
                return
            get_task = asyncio.ensure_future(self._done_q.get())
            done_task = asyncio.ensure_future(self._body_done.wait())
            try:
                done, pending = await asyncio.wait(
                    {get_task, done_task}, return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                # 取消未完成的 task:FIRST_COMPLETED 时 pending 需 cancel;被 cancel 时
                # asyncio.wait 不替我们 cancel 内部 task,这里统一兜底防泄漏.
                for t in (get_task, done_task):
                    if not t.done():
                        t.cancel()
            if get_task in done:
                yield get_task.result()
