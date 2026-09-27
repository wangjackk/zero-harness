"""FlowLoop ---- turn/step 主循环驱动器.

对齐 dsh ReactLoopAgent 的轮次模型:

- 轮次 (turn): 在领取首条输入之前打开, 不再欠任何工作时关闭.
  "欠债" = 工具结果待模型查看 (还有 function_call_output 未被
  下一次请求消化) 或 next-step inbox 有新输入.
- 步骤 (step): 一次模型请求 + 它调用的工具. 轮次边界领取
  next-step 全部 + 一条 next-turn; 步骤之间只领 next-step.
- pre_step 拒绝 → 关闭不含步骤的空轮次 (留痕).
- 插件失败结束轮次, 不结束循环 (driver 存活, 下次唤醒再来).

与 prime ReactLoop 的本质差异: 输入不打断在途轮次 (followup 排队),
中断走 Abort 协作信号, 轮次边界有 turn_stopping 观察点.
"""
from __future__ import annotations

import json
import time
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from .llm import LlmConnError as LLMConnectionError
from .phase import Aborted, Running

if TYPE_CHECKING:
    from .agent import FlowAgent

# step 完成原因: None = 还欠债 (工具结果待消化), 'completed' / 'max-steps'
StepEnd = dict | None


class FlowLoop:
    """驱动一个 Running 相位穿越轮次与步骤边界, 直到无欠债或被中断."""

    def __init__(self, agent: 'FlowAgent') -> None:
        self._a = agent
        self._ev = agent.events   # EventLog: 事件单写, 表面派生

    async def run(self, running: Running) -> None:
        """driver 入口: 只要有 pending 输入就开新轮 (对齐 dsh kick)."""
        while True:
            running.abort.throw_if_aborted()
            if not self._a.inbox_pending:
                break
            running.step = 0
            await self._turn(running)
            running.abort.throw_if_aborted()

    # ── 轮次 ──

    async def _turn(self, running: Running) -> bool:
        """一个轮次. 返回是否还有待处理 (driver 决定是否继续开轮)."""
        abort = running.abort
        turn = running.turn + 1
        running.turn = turn
        self._ev.append('turn/start', turn=turn)

        message_id = uuid4().hex
        running.message_id = message_id  # 挂到 running: _drive 兜底收尾用
        turn_end: dict | None = None
        target = 'next-turn'
        try:
            while True:
                abort.throw_if_aborted()
                step = running.step + 1
                decision = await self._pre_step(running, target, turn, step)

                if decision['kind'] == 'reject':
                    # pre_step 拒绝: 已领取批次保持删除, 关空轮次留痕
                    turn_end = {'kind': 'blocked'}
                    return False

                messages = decision.get('messages') or []
                if running.step == 0 and not messages:
                    # 空领取 (消息被清/改写为空): 占轮次边界但不花模型调用
                    turn_end = {'kind': 'empty'}
                    return False

                abort.throw_if_aborted()
                self._ev.append('step/start', turn=turn, step=step)
                running.step = step
                try:
                    for msg in messages:
                        self._ev.append_user(msg.text, turn=turn, step=step)
                    # skill 注入点 (dsh pre-step 监听器): 目录/手势
                    # 落在领取的用户消息之后, 离模型回答最近
                    await self._a.inject_context(messages)
                    step_end = await self._step(running, turn, step,
                                                message_id)
                    # max-steps 粘性: 后续正常步骤不降级轮次结果
                    if turn_end is None or turn_end.get('kind') != 'max-steps':
                        turn_end = step_end
                finally:
                    self._ev.append('step/end', turn=turn, step=step)

                abort.throw_if_aborted()
                # 无欠债 (step 收敛 + 无新 steer) → 询问观察点后关轮
                if step_end and not self._a.inbox_next_step_pending:
                    await self._a.hooks.serial('turn_stopping', {
                        'turn': turn, 'step': running.step,
                    })
                    abort.throw_if_aborted()
                if step_end and not self._a.inbox_next_step_pending:
                    break
                target = 'next-step'
        except Aborted:
            turn_end = {'kind': 'aborted', 'reason': abort.reason}
            raise
        except LLMConnectionError as exc:
            turn_end = {'kind': 'error', 'error': str(exc)}
            await self._a.emit_text(f'[LLM 连接失败: {exc}]', epoch=running_epoch(self._a),
                                    message_id=message_id, is_final=True)
        except Exception as exc:
            turn_end = {'kind': 'error', 'error': str(exc)}
            await self._a.emit_text(f'[执行出错: {exc}]', epoch=running_epoch(self._a),
                                    message_id=message_id, is_final=True)
        finally:
            self._ev.append('turn/end', turn=turn,
                            reason=turn_end or {'kind': 'completed'})
            await self._a.emit_turn_end(turn_end or {'kind': 'completed'},
                                        turn=turn, message_id=message_id)
            await self._a.emit_stats()

        return self._a.inbox_pending

    # ── 步骤 ──

    async def _pre_step(self, running: Running, target: str,
                        turn: int, step: int) -> dict:
        """领取输入 + 组装上下文 + pre_step waterfall (可拒绝/改写).

        压缩不在这里直接调用 ---- 它是 pre_step 链的默认第一环 (agent
        注册), 第三方 handler 可 prepend 抢先 (如先做免模型裁剪).
        """
        claimed = await self._a.inbox_claim(target)

        assembly = {
            'turn': turn, 'step': step,
            'messages': claimed,
            'tools': self._a._schemas or [],
        }
        return await self._a.hooks.waterfall(
            'pre_step', assembly,
            {'kind': 'enter', 'messages': claimed})

    async def _step(self, running: Running, turn: int, step: int,
                    message_id: str) -> StepEnd:
        """一次模型请求 + 它的工具调用. 返回 None = 还欠债."""
        a = self._a
        abort = running.abort
        text, fc_calls, response_id, usage, first_ts = \
            await self._call_llm(running, message_id)

        self._ev.append_assistant(text, response_id=response_id, usage=usage,
                                  turn=turn, step=step,
                                  first_token_ts=first_ts)
        a._mark_response(response_id)
        # 增量协议: 正文已按 delta 发出, 终态只发结束信号
        await a.emit_text('', epoch=running_epoch(a), message_id=message_id,
                          is_final=True)
        await a.emit_stats()

        if not fc_calls:
            return {'kind': 'completed'}

        # 有工具调用: 写入 function_call 事件, 交给调度器
        for fc in fc_calls:
            args = _sanitize_args(fc)
            self._ev.append_function_call(fc.name, args, fc.call_id,
                                          turn=turn, step=step)

        outcomes = await a.scheduler.execute(fc_calls, abort,
                                             epoch=running_epoch(a),
                                             message_id=message_id)

        feedback: list[dict] = []
        for out in outcomes:
            raw = out.result if not isinstance(out.result, BaseException) \
                else {'error': str(out.result)}
            self._ev.append_function_output(out.call_id, out.output,
                                            raw_result=raw,
                                            turn=turn, step=step)
            feedback.append({
                'name': out.name, 'input': out.input, 'result': out.output,
                'rid': out.rid, 'call_id': out.call_id, 'status': 'done',
            })
        await a.emit_feedback(content='', results=feedback,
                              epoch=running_epoch(a), message_id=message_id)
        await a.emit_stats()

        if a._max_turns is not None and running.step >= a._max_turns:
            return {'kind': 'max-steps'}
        return None   # 欠债: 工具结果待下一次模型请求消化

    # ── LLM 调用 (request waterfall + request_error waterfall) ──

    async def _call_llm(self, running: Running,
                         message_id: str) -> tuple | None:
        """流式调 LLM. 返回 (text, fc_calls, response_id, usage, first_ts)
        或 None (中断)."""
        a = self._a
        abort = running.abort
        input_items, prev_rid = a.events.to_request()

        # request waterfall: 可改写 effort / tools (仅本次请求生效, 不粘步)
        config = await a.hooks.waterfall('request', {
            'turn': running.turn, 'step': running.step,
            'reasoning_effort': a._llm.reasoning_effort,
            'tools': a._schemas or [],
        }, {'reasoning_effort': a._llm.reasoning_effort, 'tools': a._schemas})
        tools = config.get('tools') or None
        effort = config.get('reasoning_effort')
        # request/header: 冻结请求的事实留痕 (模型可见即已记录;
        # input 从事件派生, effort/tools/model 是不可派生的请求事实)
        self._ev.append('request/header', turn=running.turn,
                        step=running.step, effort=effort or '',
                        model=a._llm.key,
                        tools=len(tools or []),
                        incremental=bool(prev_rid))

        for attempt in (0, 1):
            try:
                return await self._stream_llm(
                    input_items, prev_rid, running, message_id, tools, effort)
            except Aborted:
                raise
            except Exception as exc:
                if attempt == 0:
                    action = await a.hooks.waterfall(
                        'request_error',
                        {'error': str(exc), 'exc': exc,
                         'turn': running.turn, 'step': running.step},
                        None)
                    if isinstance(action, dict) and action.get('kind') == 'retry':
                        # 钩子可能已压缩 (replacement) → 重算请求包络
                        input_items, prev_rid = a.events.to_request()
                        continue
                    if prev_rid is not None and _is_retryable(exc):
                        a.events.invalidate_rid()
                        input_items, prev_rid = a.events.to_request()
                        continue
                raise

    async def _stream_llm(self, input_items: list, prev_rid: str | None,
                          running: Running, message_id: str,
                          tools: list | None,
                          effort: str | None = None) -> tuple:
        a = self._a
        abort = running.abort
        text = ''
        fc_calls: list = []
        completed = False
        response_id = ''
        usage: dict = {}
        first_ts: float | None = None
        epoch = running_epoch(a)

        try:
            async for ev in a._llm.stream(
                input_items,
                instructions=a._instructions,
                tools=tools,
                previous_response_id=prev_rid,
                reasoning_effort=effort,
            ):
                abort.throw_if_aborted()
                kind = type(ev).__name__
                if kind == 'ReasoningDelta':
                    if first_ts is None:
                        first_ts = time.time()
                    await a.emit_text(ev.text, epoch=epoch,
                                      message_id=message_id,
                                      is_final=False, is_thinking=True)
                elif kind == 'TextDelta':
                    if first_ts is None:
                        first_ts = time.time()
                    text += ev.text
                    await a.emit_text(ev.text, epoch=epoch,
                                      message_id=message_id,
                                      is_final=False)
                elif kind == 'Completed':
                    completed = True
                    response_id = ev.response_id
                    usage = ev.usage or {}
                    fc_calls = ev.function_calls
                    await a.emit_usage(
                        usage=usage, max_context=a._llm.max_context,
                        epoch=epoch, message_id=message_id,
                        model_key=a._llm.key,
                        model_name=a._llm.model_id,
                        reasoning_effort=effort)
        except Aborted:
            if text:
                self._ev.append_assistant(text, first_token_ts=first_ts)
            await a.emit_text('', epoch=epoch, message_id=message_id,
                              is_final=True)
            raise
        except Exception:
            # 连接失败等: 半截文字保住, 终态收尾后上抛给 request_error
            if text:
                self._ev.append_assistant(text, first_token_ts=first_ts)
            await a.emit_text('', epoch=epoch, message_id=message_id,
                              is_final=True)
            raise

        if not completed:
            raise LLMConnectionError('stream ended without completion')
        return text, fc_calls, response_id, usage, first_ts


def running_epoch(a: 'FlowAgent') -> int:
    return a._epoch


def _sanitize_args(fc) -> str:
    """校验 LLM 返回的 arguments 是否合法 JSON."""
    args = fc.arguments
    if not args:
        return '{}'
    try:
        json.loads(args)
        return args
    except (json.JSONDecodeError, TypeError):
        return json.dumps(
            {'_error': 'malformed arguments from LLM', '_raw': args[:500]},
            ensure_ascii=False)


def _is_retryable(exc: BaseException) -> bool:
    """可重试判定 (对齐 prime: rid 过期 / 临时网络错误)."""
    s = str(exc)
    if 'PreviousResponseNotFound' in s or 'previous_response_id' in s.lower():
        return True
    if 'InvalidParameter' in s and ("'param': ''" in s or "\"param\":\"\"" in s):
        return True
    import ssl
    if isinstance(exc, (ssl.SSLError, ConnectionError, TimeoutError)):
        return True
    if 'SSL' in s and ('BAD_RECORD_MAC' in s or 'SSLV3_ALERT' in s):
        return True
    return False


# provider 报上下文溢出的常见措辞 (openai context_length_exceeded 等).
# dsh 用 provider 确认的错误码; zero 侧 openai SDK 统一成 BadRequestError,
# 只能靠 message 判定, 保守匹配避免误触发压缩.
_OVERFLOW_MARKERS = (
    'context_length_exceeded',
    'context length exceeded',
    'maximum context length',
    'context window exceeded',
    'input tokens exceed',
    'too many input tokens',
)


def _is_context_overflow(exc: BaseException | None) -> bool:
    """请求包络超出模型上下文窗口判定 (溢出恢复触发条件)."""
    if not isinstance(exc, BaseException):
        return False
    s = str(exc).lower()
    return any(m in s for m in _OVERFLOW_MARKERS)
