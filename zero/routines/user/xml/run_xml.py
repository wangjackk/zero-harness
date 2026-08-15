"""RunXml -- 动态执行 XML 字符串,返回各子 routine 结果.

调试用途:把完整 XML 喂给跟 act 完全相同的 body_shell 流水线执行.XML 当 kwargs
传入,run 里自己喂自己的 on_body_chunk(不经 on_message reorder--单段 XML 无乱序).

用法(LLM / HTTP / 代码都行)::

    <run_xml xml="<print_body>hello</print_body><dance duration=\"2\"/>"/>
    # 代码:
    await self.call('run_xml', {'xml': '<print_body>hi</print_body>'})
    # HTTP:
    curl -XPOST localhost:7780/run/run_xml -H 'Content-Type: application/json' \
        -d '{"xml":"<music duration=\"5\"><dance duration=\"2\"/></music>"}'

跟 act 的区别:act 是 async gen(父 send 流式 XML,yield 每个子结果);RunXml 是
同步收完整 XML 字符串 -> 喂自己 -> 等全部子 done -> 返回结果列表.共用同一条
body_shell -> parser -> on_xml_event 默认派发链路.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import RoutineHandle

from .body_chunk import BodyChunk, BodyChunkKind
from .xml_parser import ChildOpen
from .xml_routine import XmlRoutine


class RunXmlInput(BaseModel):
    xml: str = Field(description='要动态执行的 XML 字符串')


class RunXml(XmlRoutine):
    """收 XML 字符串,走 act 同款 body_shell 流水线,返回各子结果."""

    meta = {
        'description': '动态执行 XML 字符串(调试用,跟 act 同款 body_shell 流水线)',
        'input_schema': RunXmlInput.model_json_schema(),
        'hidden': True,
    }

    def __init__(self) -> None:
        super().__init__()
        self._child_handles: List[RoutineHandle] = []

    async def on_xml_event(self, ev: Any) -> None:
        """ChildOpen -> 走基类默认 push,然后把刚 push 的 body 子 handle 收进列表.

        原靠基类 push 钩子收的活:现在父在 on_xml_event 里 push 后自己接.normal_shell 的
        push(代码主动 self.push)不经本方法,不收--只收 body_shell 派生的工具子.
        """
        await super().on_xml_event(ev)
        if isinstance(ev, ChildOpen) and self._cur_handle is not None:
            self._child_handles.append(self._cur_handle)

    async def run(self, kwargs: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        input_ = RunXmlInput(**kwargs)
        xml = input_.xml.strip()
        if not xml:
            return None
        self._logger.info('\n %s', xml)
        # 跟流式完全相同的路径:CHUNK 喂 parser -> STREAM_CLOSED drain.
        # 不走 on_message reorder(单段 XML 无乱序);_parse_task 阻塞在 reorder_q
        # 空转,不并发踩 parser 状态.on_body_chunk 内部 push 出的 body 子已 arm
        # (run 在 on_started 后跑),立即 start.STREAM_CLOSED 会 complete body_shell
        # (语义:body 流终结),wait_done 等所有 body 子 done 后返回.
        await self.on_body_chunk(BodyChunk(kind=BodyChunkKind.CHUNK, text=xml))
        await self.on_body_chunk(BodyChunk(kind=BodyChunkKind.STREAM_CLOSED))
        await self._body_shell.wait_done()
        self._logger.info('xml done')

        errors: List[str] = []
        collected: List[Dict[str, Any]] = []
        for h in self._child_handles:
            if not h.name:
                continue
            if h.error:
                errors.append(f'{h.name}: {h.error}')
            elif h.result is not None:
                collected.append({'name': h.name, 'result': h.result})
        if errors:
            raise RuntimeError('; '.join(errors))
        return collected or None
