"""流式 XML body 源 -- 模拟 LLM 流式输出 XML,随机分块喂自己(self-driven XmlRoutine).

MockXmlSource 继承 :class:`XmlRoutine`,自己就是 XML body 编排器.区别:body 不是外部
send 进来,而是自己用 :func:`generate_xml` 流式生成,直送自己的 ``on_message``(模拟
LLM 在 routine 内部生成 body 的场景).复用 XmlRoutine 完整的 on_message -> reorder
-> parser -> body_shell 编排链路,测:
  1. 随机分块(片段切断标签)parser 仍能流式拼装;
  2. self-close 子 ``<print_body/>`` 走 ChildOpen + ChildClose 的 eof 通路正常 done;
  3. 带 body 子 ``<print_body>hello</print_body>`` 拼出 "hello".

XML 子标签用已注册的 :class:`PrintBody`(test_xml_message 已验),不依赖 sing/dance
这类未实现的 routine.``chunk_min``/``chunk_max`` 控制分块随机范围(默认 2-8).
"""
from typing import Any, AsyncIterator, Dict

import asyncio

from routine import RoutineSource

from .xml_routine import XmlRoutine

_XML = '<print_body>hello</print_body><print_body/>'


async def generate_xml(body: str = _XML, *,
                       chunk_min: int = 2, chunk_max: int = 8) -> AsyncIterator[str]:
    """流式随机分块 yield XML 片段(模拟 LLM 流式输出).

    片段长度在 [chunk_min, chunk_max] 随机取,边界可能切断标签--验 parser 的
    跨片段拼装能力.最后一片后,调用方需自行发 ``_eof`` 终结 body 流.
    """
    import random
    if chunk_min < 1 or chunk_max < chunk_min:
        raise ValueError(f'invalid chunk range: [{chunk_min}, {chunk_max}]')
    pos = 0
    while pos < len(body):
        n = random.randint(chunk_min, chunk_max)
        yield body[pos:pos + n]
        pos += n


class MockXmlSource(XmlRoutine):
    """self-driven XmlRoutine:自己生成 XML body 流式喂回自己.

    继承 XmlRoutine,override :meth:`on_started`(arm 后 spawn 生成 task 流式喂自己)
    + :meth:`on_body_text`(接顶层裸文本).``run`` 等待 body_shell done--body_shell
    done = body 流终结 + 所有派生子完成,触发 request_stop 让 run 退出.``on_stopped``
    cancel 本 routine 的 _gen_task(parse_task 由父类 on_stopped 清).

    body 直送自己的 ``on_message``(不经 kernel 回环--自->自不必 wire 中转),走完整
    reorder -> parser -> body_shell 链路.arm 后(子能 start)才开始喂--on_started 里起
    生成 task.
    """

    meta = {'description': 'self-driven XmlRoutine(流式随机分块 + self-close 子)'}
    # is_passive = True

    def __init__(self) -> None:
        super().__init__()
        self._body_kwargs: Dict[str, Any] = {}
        self._gen_task = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        await super().on_created(rid, kwargs)
        # 记住分块参数,on_started 里起生成 task 时用
        self._body_kwargs = {
            'body': kwargs.get('body') or _XML,
            'chunk_min': int(kwargs.get('chunk_min', 2)),
            'chunk_max': int(kwargs.get('chunk_max', 8)),
        }

    async def on_stopped(self, reason: str = 'auto', result: Any = None,
                         detail: str = '') -> None:
        # 清本 routine 自己的 _gen_task;parse_task 由父类 on_stopped 清.
        if self._gen_task is not None and not self._gen_task.done():
            self._gen_task.cancel()
        await super().on_stopped(reason=reason, result=result, detail=detail)

    async def _generate_and_send(self) -> None:
        """流式生成 XML body,直送自己的 on_message(reorder -> parser -> body_shell).

        自->自不经 kernel 回环:直接调 ``on_message`` 走 reorder 入队,parse_task 拉取
        构造 BodyChunk 驱动 on_body_chunk.比 ``self.send(self.id, ...)`` 少一圈
        wire -> kernel -> delivered 中转(自->自本就不该绕远).
        """
        body = self._body_kwargs['body']
        chunk_min = self._body_kwargs['chunk_min']
        chunk_max = self._body_kwargs['chunk_max']
        src = RoutineSource(id=self.id, name=self.name)
        try:
            seq = 0
            async for chunk in generate_xml(body, chunk_min=chunk_min, chunk_max=chunk_max):
                self._logger.info(f'on message:{chunk}')
                await self.on_message(src, {'text': chunk, 'id': seq})
                seq += 1
            # 流终结:发 _eof(带 id,走自己的 reorder)
            await self.on_message(src, {'_eof': True, 'id': seq})

        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception('MockXmlSource generate_and_send error')
            raise

    async def on_body_text(self, chunk) -> None:
        """顶层裸文本(非子标签内容).默认 no-op,子类 override 接管.

        接 ``TextChunk``:``chunk.text`` 是文本,``is_last`` 标 segment 收尾.
        MockXmlSource 不处理顶层文本,留 no-op.
        """
        pass

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """等 body_shell done(body 流终结 + 所有派生子完成)-> request_stop 让 run 退出."""
        self._gen_task = asyncio.create_task(self._generate_and_send())
        await self._body_shell.wait_done()
