"""工具执行: 模型产生 function call 后立即并发调用.

不做 Shell 编排, 也不按 modules 或输出顺序排队. 每个 call 独立经过
tool_pre_execute waterfall → create → start → wait; 失败只转成该 call
的错误结果回传给模型, 不阻塞其他 call 或整个轮次.

interrupt() 供 agent abort 时停止在途工具.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from .wire import AGENT_ID_KEY

if TYPE_CHECKING:
    from .agent import FlowAgent
    from .phase import Abort


@dataclass
class ToolOutcome:
    """单个工具调用的落定结果 (模型顺序回传)."""

    call_id: str
    name: str
    input: dict
    rid: str | None = None
    result: Any = None          # 值 / 异常对象 / StartError
    output: str = ''            # LLM 可读格式化 (content 约定)


class ToolScheduler:
    """function call 即到即并发执行; agent 常驻复用."""

    def __init__(self, agent: 'FlowAgent') -> None:
        self._a = agent
        self._handles: set[Any] = set()

    async def interrupt(self) -> None:
        """停止在途工具, 撤掉尚未 start 的 (abort / agent stop 用)."""
        for handle in list(self._handles):
            if handle.is_done():
                continue
            if handle.is_started():
                await handle.stop(fire=True)
            else:
                await handle.destroy(fire=True)

    async def execute(self, fc_calls: list, abort: 'Abort',
                      *, epoch: int, message_id: str) -> list[ToolOutcome]:
        async def run(fc) -> ToolOutcome:
            abort.throw_if_aborted()
            args = _parse_args(fc)
            try:
                return await self._execute_one(
                    fc, args, epoch=epoch, message_id=message_id)
            except Exception as exc:
                # 工具及其扩展点失败只汇报给模型, 不弄垮整个轮次.
                return _error_outcome(fc, args, exc)

        # 每个 call 立即起自己的执行链, 不串行等待前一个.
        tasks = [asyncio.create_task(run(fc)) for fc in fc_calls]
        try:
            return list(await asyncio.gather(*tasks))
        except asyncio.CancelledError:
            await self.interrupt()
            raise

    async def _execute_one(self, fc, args: dict, *, epoch: int,
                           message_id: str) -> ToolOutcome:
        rejected = await self._admit(fc)
        if rejected is not None:
            return rejected

        tool_args = dict(args)
        tool_args[AGENT_ID_KEY] = self._a._agent_id
        handle = await self._a.ctx.create(fc.name, tool_args)
        self._handles.add(handle)
        try:
            handle.on_started_handler = self._a.make_started_handler(
                fc.name, args, fc.call_id, handle.id,
                epoch=epoch, message_id=message_id)
            await handle.start()
            result = await handle
        except Exception as exc:
            return _error_outcome(fc, args, exc, rid=handle.id)
        finally:
            self._handles.discard(handle)

        output = _format_result(result)
        # post_execute: 结果定稿前可改写 LLM 可见 output (dsh tools/post-execute)
        output = await self._a.hooks.waterfall(
            'tool_post_execute',
            {'name': fc.name, 'call_id': fc.call_id, 'input': args,
             'result': result, 'output': output},
            output)
        await self._a.hooks.serial('tool_result', {
            'name': fc.name, 'call_id': fc.call_id,
            'input': args, 'result': result,
        })
        return ToolOutcome(call_id=fc.call_id, name=fc.name, input=args,
                           rid=handle.id, result=result, output=output or '')

    async def _admit(self, fc) -> ToolOutcome | None:
        """unknown tool / pre_execute 拒绝 → 错误 outcome; 放行返回 None."""
        args = _parse_args(fc)
        if self._a._tool_map.get(fc.name) is None:
            return _error_outcome(fc, args, RuntimeError(f'unknown tool: {fc.name}'))
        decision = await self._a.hooks.waterfall('tool_pre_execute', {
            'name': fc.name, 'input': args, 'call_id': fc.call_id,
        }, {'kind': 'enter'})
        if decision.get('kind') != 'enter':
            reason = str(decision.get('error') or f'rejected by policy: {fc.name}')
            return _error_outcome(fc, args, RuntimeError(reason))
        return None


def _error_outcome(fc, args: dict, exc: BaseException,
                   rid: str | None = None) -> ToolOutcome:
    return ToolOutcome(call_id=fc.call_id, name=fc.name, input=args, rid=rid,
                       result=exc, output=_format_error(exc))


def _parse_args(fc) -> dict:
    try:
        return json.loads(fc.arguments) if fc.arguments else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ── 结果格式化 (content 约定, 对齐 prime ToolExecutor) ──

def _format_error(exc: BaseException) -> str:
    return json.dumps(
        {'error': str(exc) or type(exc).__name__}, ensure_ascii=False)


def _format_result(result: Any) -> str:
    if result is None:
        return ''
    if isinstance(result, BaseException):
        return _format_error(result)
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if 'content' in result:
            fl = result['content']
            if fl is None:
                return ''
            if isinstance(fl, str):
                return fl
            return json.dumps(fl, ensure_ascii=False, default=str)
        return json.dumps(result, ensure_ascii=False, default=str)
    return json.dumps(result, ensure_ascii=False, default=str)
