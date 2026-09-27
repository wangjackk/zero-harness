"""工具执行: 常驻 Shell 编排 (cone 串并行) + pre_execute/result 扩展点.

串并行判定不靠 meta 标志位 (concurrency_safe 不消费): 复用业务侧
Shell (zero xml 包) ---- push 尽快 submit, start 是调度点, 与左兄弟
的 modules cone 交集冲突则串行, 不冲突则并行. modules 是 kernel 解析
的占用真理源 (工具 on_created 返回), 与 TryAcquire 同源 ---- 工具要
串行就声明 modules, 天然并行就不声明.

每 step 一批 function call: 逐个过 tool_pre_execute waterfall (拒绝
的不 push, 合成错误结果) → push 全部 → complete → join (push 序结果,
失败收成对象不抛) → reset (Shell 复用下一批). interrupt() 供 agent
abort 时 top-down 排水在跑的工具.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from ...shell import Shell
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
    output: str = ''            # LLM 可读格式化 (for_llm 约定)


class ToolScheduler:
    """每 step 一批 function call 的 Shell 执行器. agent 常驻复用."""

    def __init__(self, agent: 'FlowAgent') -> None:
        self._a = agent
        self._shell = Shell(agent)

    async def interrupt(self) -> None:
        """top-down 排水: 停在跑的工具, 撤未 start 的 (abort / agent stop 用)."""
        await self._shell.interrupt()
        self._shell.reset()

    async def execute(self, fc_calls: list, abort: 'Abort',
                      *, epoch: int, message_id: str) -> list[ToolOutcome]:
        outcomes: list[ToolOutcome | None] = [None] * len(fc_calls)
        pushed: list[tuple[int, Any, dict, Any]] = []

        for i, fc in enumerate(fc_calls):
            abort.throw_if_aborted()
            outcomes[i] = await self._admit(fc)
            if outcomes[i] is not None:      # unknown / 被拒: 不 push
                continue
            args = _parse_args(fc)
            tool_args = dict(args)
            tool_args[AGENT_ID_KEY] = self._a._agent_id
            try:
                handle = await self._shell.push(fc.name, tool_args)
            except Exception as exc:
                outcomes[i] = _error_outcome(fc, args, exc)
                continue
            handle.on_started_handler = self._a.make_started_handler(
                fc.name, args, fc.call_id, handle.id,
                epoch=epoch, message_id=message_id)
            pushed.append((i, fc, args, handle))

        self._shell.complete()
        try:
            results = await self._shell.join()
        except (asyncio.CancelledError, Exception):
            await self.interrupt()
            raise

        for (i, fc, args, handle), result in zip(pushed, results):
            output = _format_result(result)
            # post_execute: 结果定稿前可改写 LLM 可见 output (dsh tools/post-execute)
            output = await self._a.hooks.waterfall(
                'tool_post_execute',
                {'name': fc.name, 'call_id': fc.call_id, 'input': args,
                 'result': result, 'output': output},
                output)
            out = ToolOutcome(call_id=fc.call_id, name=fc.name, input=args,
                              rid=handle.id, result=result,
                              output=output or '')
            outcomes[i] = out
            await self._a.hooks.serial('tool_result', {
                'name': fc.name, 'call_id': fc.call_id,
                'input': args, 'result': result,
            })

        self._shell.reset()
        return [o for o in outcomes if o is not None]

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


def _error_outcome(fc, args: dict, exc: BaseException) -> ToolOutcome:
    return ToolOutcome(call_id=fc.call_id, name=fc.name, input=args,
                       result=exc, output=_format_error(exc))


def _parse_args(fc) -> dict:
    try:
        return json.loads(fc.arguments) if fc.arguments else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ── 结果格式化 (for_llm 约定, 对齐 prime ToolExecutor) ──

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
        if 'for_llm' in result:
            fl = result['for_llm']
            if fl is None:
                return ''
            if isinstance(fl, str):
                return fl
            return json.dumps(fl, ensure_ascii=False, default=str)
        return json.dumps(result, ensure_ascii=False, default=str)
    return json.dumps(result, ensure_ascii=False, default=str)
