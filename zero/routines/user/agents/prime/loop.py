"""ReactLoop + ToolExecutor ---- react 主循环和工具执行.

ReactLoop: 驱动 LLM 调用 → 工具执行 → 下一轮的循环.
ToolExecutor: 工具执行编排 (校验 → push → join → 写回 → feedback).
"""
from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any, TYPE_CHECKING

from routine.logger import setup_logger

from .._core.llm import (
    LLMClient, LLMConnectionError,
    TextDelta, ReasoningDelta, Completed,
)

if TYPE_CHECKING:
    from .reactor import ReactorAgent

_log = setup_logger('reactor.loop')


class ReactInterrupted(Exception):
    """epoch 过期, react 应退出."""


# ──────────────────────────────────────────────────────────────────────────────
# ReactLoop
# ──────────────────────────────────────────────────────────────────────────────

class ReactLoop:
    """react 主循环: 每次用户输入触发一次 run()."""

    def __init__(self, agent: 'ReactorAgent') -> None:
        self._a = agent
        self._llm: LLMClient = agent._llm
        self._ctx = agent._ctx
        self._tracker = agent._session.tracker
        self._executor = ToolExecutor(agent)

    async def run(self, epoch: int, message_id: str) -> None:
        schemas = self._build_schemas()
        await self._a.emit_sys_prompt(epoch=epoch, message_id=message_id,
                                      messages=self._a.conv_to_prompt_messages())

        text_output = ''
        turn_count = 0
        max_turns = self._a._max_turns

        try:
            while max_turns is None or turn_count < max_turns:
                self._a.check_epoch(epoch)
                turn_count += 1

                await self._a.maybe_condense(epoch)

                result = await self._call_llm_with_retry(epoch, message_id, schemas, text_output)
                if result is None:
                    break  # 被中断或连接失败

                text_output, fc_calls, response_id, usage = result

                # 无工具调用 → 完成
                if not fc_calls:
                    if text_output:
                        self._ctx.append_assistant(text_output)
                    self._a._mark_response(response_id, text_output, usage)
                    await self._a.emit_text(text_output, epoch=epoch,
                                            message_id=message_id, is_final=True)
                    break

                # 有工具调用 → 写入 + 执行
                if text_output:
                    self._ctx.append_assistant(text_output)
                for fc in fc_calls:
                    args = self._sanitize_args(fc)
                    self._ctx.append_function_call(fc.name, args, fc.call_id)
                self._a._mark_response(response_id, text_output, usage)

                await self._executor.execute(fc_calls, epoch, message_id)

        except ReactInterrupted:
            if text_output:
                await self._a.emit_text(text_output, epoch=epoch,
                                        message_id=message_id, is_final=True)
            else:
                await self._a.emit_text('', epoch=epoch,
                                        message_id=message_id, is_final=True)
        except asyncio.CancelledError:
            # CancelledError 可能发生在两处:
            # 1. _stream_llm 期间: _stream_llm 已保存半截文字 + emit is_final
            # 2. 工具执行期间: text_output 已在 _mark_response 之前保存
            # 两种情况都不需要再 append_assistant(text_output), 否则重复保存.
            # 停止 agent 场景下前端会被 resume 时的 session_changed 重置, 无需 emit.
            raise
        except LLMConnectionError as exc:
            msg = f'[LLM 连接失败: {exc}]'
            self._ctx.append_assistant(msg)
            await self._a.emit_text(msg, epoch=epoch, message_id=message_id, is_final=True)
        except Exception as exc:
            _log.error('react crashed: %s', exc, exc_info=True)
            msg = f'[执行出错: {exc}]'
            self._ctx.append_assistant(msg)
            await self._a.emit_text(msg, epoch=epoch, message_id=message_id, is_final=True)

    def _build_schemas(self) -> list[dict]:
        return self._a.build_tools()

    async def _call_llm_with_retry(
        self, epoch: int, message_id: str,
        schemas: list[dict], text_output: str,
    ) -> tuple[str, list, str, dict] | None:
        """调 LLM stream, 处理重试. 返回 (text, fc_calls, response_id, usage) 或 None."""
        input_items, prev_rid = self._tracker.to_request(self._ctx.items())

        for attempt in (0, 1):
            try:
                return await self._stream_llm(
                    input_items, prev_rid, epoch, message_id, schemas, text_output,
                )
            except Exception as exc:
                if attempt == 0 and prev_rid is not None and _is_retryable(exc):
                    self._tracker.invalidate_last()
                    input_items = self._ctx.items()
                    prev_rid = None
                    continue
                raise

    async def _stream_llm(
        self, input_items: list[dict], prev_rid: str | None,
        epoch: int, message_id: str,
        schemas: list[dict], text_output: str,
    ) -> tuple[str, list, str, dict]:
        """流式调用 LLM, 返回 (text, fc_calls, response_id, usage)."""
        text = ''
        fc_calls: list = []
        response_id = ''
        usage: dict = {}
        completed = False

        try:
            async for ev in self._llm.stream(
                input_items,
                instructions=self._a._instructions,
                tools=schemas or None,
                previous_response_id=prev_rid,
            ):
                if isinstance(ev, ReasoningDelta):
                    await self._a.emit_text(ev.text, epoch=epoch, message_id=message_id,
                                            is_final=False, is_thinking=True)
                elif isinstance(ev, TextDelta):
                    text += ev.text
                    await self._a.emit_text(text, epoch=epoch, message_id=message_id, is_final=False)
                elif isinstance(ev, Completed):
                    completed = True
                    response_id = ev.response_id
                    usage = ev.usage or {}
                    fc_calls = ev.function_calls
                    await self._a.emit_usage(
                        usage=ev.usage,
                        max_context=self._llm.max_context,
                        epoch=epoch, message_id=message_id,
                        trigger_ratio=self._a.trigger_ratio,
                        model_key=self._llm.model_key,
                        model_name=self._llm.model_name,
                        reasoning_effort=self._llm.reasoning_effort,
                    )
        except asyncio.CancelledError:
            # 用户打断: 半截 text 写进 ctx + 发 is_final 收尾前端.
            if text:
                self._ctx.append_assistant(text)
            await self._a.emit_text(text, epoch=epoch, message_id=message_id, is_final=True)
            raise

        if not completed:
            if text:
                self._ctx.append_assistant(text)
            await self._a.emit_text(text, epoch=epoch, message_id=message_id, is_final=True)
            raise ReactInterrupted()

        return text, fc_calls, response_id, usage

    @staticmethod
    def _sanitize_args(fc) -> str:
        """校验 LLM 返回的 function_call arguments 是否合法 JSON."""
        args = fc.arguments
        if not args:
            return '{}'
        try:
            json.loads(args)
            return args
        except (json.JSONDecodeError, TypeError):
            return json.dumps(
                {'_error': 'malformed arguments from LLM', '_raw': args[:500]},
                ensure_ascii=False,
            )


# ──────────────────────────────────────────────────────────────────────────────
# ToolExecutor
# ──────────────────────────────────────────────────────────────────────────────

class ToolExecutor:
    """工具执行编排: 校验 → submit+start → gather → 写回 → feedback."""

    def __init__(self, agent: 'ReactorAgent') -> None:
        self._a = agent
        self._ctx = agent._ctx   # LocalContextProvider: 写回 function output 用

    async def execute(
        self, fc_calls: list,
        epoch: int, message_id: str,
    ) -> None:
        tool_map = self._a._tool_map

        handles: dict[str, Any] = {}
        known: list = []
        results_by_call_id: dict[str, Any] = {}

        # 分流: known / unknown
        for fc in fc_calls:
            if tool_map.get(fc.name) is None:
                err = {'error': f'unknown tool: {fc.name}'}
                results_by_call_id[fc.call_id] = err
            else:
                known.append(fc)

        # submit known tools + 并发 start (工具 routine 不声明 modules, 无冲突全并行)
        from zero.routines.user.agents._core.paths import AGENT_ID_KEY
        tasks: list[asyncio.Task] = []
        for fc in known:
            self._a.check_epoch(epoch)
            args = self._parse_args(fc)
            tool_args = dict(args)
            tool_args[AGENT_ID_KEY] = self._a._agent_id
            h = await self._a.ctx.submit(fc.name, tool_args)
            handles[fc.call_id] = h
            h.on_started_handler = self._make_started_handler(fc, args, epoch, message_id)
            tasks.append(asyncio.create_task(self._run_handle(h)))

        if known:
            try:
                # gather 保序: results[i] 对应 known[i], 异常已收成对象(_run_handle)
                results = await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                # epoch 切换被打断: 停掉全部 handles 再放行 cancel
                for h in handles.values():
                    coro = h.stop(fire=True) if h.is_started() else h.unsubmit(fire=True)
                    try:
                        await coro
                    except Exception:
                        pass
                raise
            for fc, result in zip(known, results):
                results_by_call_id[fc.call_id] = result

        # 写回 ctx + 收集 feedback
        feedback_results: list[dict] = []
        for fc in fc_calls:
            result = results_by_call_id.get(fc.call_id)
            output = self._format_result(result)
            raw = result if not isinstance(result, BaseException) else {'error': str(result)}
            self._ctx.append_function_output(fc.call_id, output, raw_result=raw)

            feedback_results.append({
                'name': fc.name,
                'input': self._parse_args(fc) if fc.name in tool_map else None,
                'result': output,
                'rid': handles.get(fc.call_id, type('', (), {'id': None})()).id
                       if fc.call_id in handles else None,
                'call_id': fc.call_id,
                'status': 'done',
            })

        await self._a.emit_feedback(
            content='', results=feedback_results,
            epoch=epoch, message_id=message_id,
        )

    async def _run_handle(self, h: Any) -> Any:
        """start + 等单个 handle, 异常收成对象不抛 (对齐原 join 的收集语义)."""
        try:
            err = await h.start()
            if err:
                return err
            return await h
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return exc

    def _parse_args(self, fc) -> dict[str, Any]:
        """解析 fc.arguments 为 dict."""
        try:
            return json.loads(fc.arguments) if fc.arguments else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _format_result(result: Any) -> str:
        """格式化工具结果为 LLM 可读字符串 (for_llm 约定)."""
        if result is None:
            return ''
        if isinstance(result, BaseException):
            return json.dumps({'error': str(result) or type(result).__name__},
                              ensure_ascii=False)
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            if 'for_llm' in result:
                fl = result['for_llm']
                if fl is None:
                    return ''
                if isinstance(fl, str):
                    return fl
                return json.dumps(fl, ensure_ascii=False, default=str)
            return json.dumps(result, ensure_ascii=False, default=str)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _make_started_handler(self, fc, args: dict, epoch: int, message_id: str):
        async def on_started(_handle: Any = None) -> None:
            await self._a.emit_feedback(
                content='',
                results=[{
                    'name': fc.name,
                    'input': args,
                    'result': None,
                    'rid': getattr(_handle, 'id', None),
                    'call_id': fc.call_id,
                    'status': 'running',
                }],
                epoch=epoch, message_id=message_id,
            )
        return on_started


# ──────────────────────────────────────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────────────────────────────────────

def _is_retryable(exc: BaseException) -> bool:
    """判断是否可重试 (previous_response_id 过期 / 临时网络错误)."""
    s = str(exc)
    if 'PreviousResponseNotFound' in s or 'previous_response_id' in s.lower():
        return True
    if 'InvalidParameter' in s and ("'param': ''" in s or "\"param\":\"\"" in s):
        return True
    # SSL/网络层临时错误: BAD_RECORD_MAC, connection reset, timeout 等.
    # 重试会建新连接, 大概率能恢复.
    if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
        return True
    if 'SSL' in s and ('BAD_RECORD_MAC' in s or 'SSLV3_ALERT' in s):
        return True
    return False
