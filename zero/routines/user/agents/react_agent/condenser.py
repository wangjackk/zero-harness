"""ReactCondenserAgent — react_agent 专用上下文压缩 routine.

基于 Memory (sqlite, schema 不同于 store), 跟 reactor/prime 的 CondenserAgent 平级:
- 读: Memory.load_history → project_with_summary 投影 → _msg_to_item 转 items
- 写: Memory.add_summary_message (kind='summary', 跟普通消息同表)

复用 BaseCondenserRoutine 模板方法 (trigger/策略/covered_from_to 全共享).
策略包 (BasicCondenser/AgenticCondenser/HybridCondenser) 也共享.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict
from uuid import uuid4

from routine.logger import setup_logger

from ._condenser.base_routine import (
    BaseCondenserRoutine,
    CondenseInput,
    CondenseLoadResult,
    CondenseOutput,
    CondenseResult,
)

from .memory import get_memory

_log = setup_logger('react_agent.condenser')


# ──────────────────────────────────────────────────────────────────────────────
# 投影: 用 summary 的 covered_to 定位 tail, 正确保留 [summary, tail, new_dialogue]
# ──────────────────────────────────────────────────────────────────────────────


def project_with_summary(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """投影: 找最后一条 summary, 返回 (projected, summary_idx, new_dialogue_start).

    summary 在 messages 表末尾 (id 最大, 后写入), 但语义上替代 head.
    用 extend_data.covered_to 定位 head/tail 边界:
    - head = covered_to 及之前 (被 summary 替代, 不发 LLM)
    - tail = covered_to 之后, summary 之前 (压缩时保留的最近消息)
    - new_dialogue = summary 之后 (压缩后新对话)

    返回 projected = [summary, *tail, *new_dialogue].
    - summary_idx = projected 中 summary 的位置 (0); 无 summary 时返回 -1.
    - new_dialogue_start = projected 中 new_dialogue 的起始索引
      (summary 之后的消息, response_id 缓存里含 summary, 可安全复用).
      tail 里的 response_id 缓存里没有 summary, 不能用.

    agent._build_messages 用 new_dialogue_start 判断从哪里找 previous_response_id.
    """
    summary_idx = -1
    summary_msg: dict[str, Any] | None = None
    for i in range(len(history) - 1, -1, -1):
        if history[i].get('kind') == 'summary':
            summary_idx = i
            summary_msg = history[i]
            break
    if summary_idx < 0 or summary_msg is None:
        return list(history), -1, 0

    extend = summary_msg.get('extend_data') or {}
    covered_to = str(extend.get('covered_to') or '')

    # 在 history 里找 covered_to 的位置
    covered_to_idx = -1
    for i, m in enumerate(history):
        if m.get('message_id') == covered_to:
            covered_to_idx = i
            break

    if covered_to_idx < 0:
        # covered_to 没找到 (异常: 消息被删?), 回退: 只取 summary + 之后
        tail: list[dict[str, Any]] = []
    else:
        tail = history[covered_to_idx + 1:summary_idx]

    new_dialogue = history[summary_idx + 1:]
    projected = [summary_msg, *tail, *new_dialogue]
    # summary 占 projected[0], tail 占 projected[1 : 1+len(tail)],
    # new_dialogue 从 projected[1+len(tail)] 开始
    new_dialogue_start = 1 + len(tail)
    return projected, 0, new_dialogue_start


# ──────────────────────────────────────────────────────────────────────────────
# react Memory message -> strategy item 转换
# ──────────────────────────────────────────────────────────────────────────────


def _msg_to_item(msg: dict[str, Any]) -> dict[str, Any] | None:
    """react Memory message -> strategy item.

    react message: {message_id, role, content, interrupted, feedback, response_id, kind}
    strategy item: {role, content} (find_cut_index 按 content 算 token)

    只跳过 ``kind='summary'`` 旧 summary (避免摘要的摘要; 投影时已被前一条
    summary 截断, 这里跳过防止它混入 head/tail).

    ``[feedback]`` 伪 user 消息 (子 routine 结果) **保留**: fork 模式下它已在
    服务端缓存里 (主对话发过), {conversation} 序列化保持一致; 降级模式
    (fork_response_id=None) 下它是唯一输入来源, 跳过会让 LLM 看不到 tool output.
    """
    if msg.get('kind') == 'summary':
        return None
    role = msg.get('role') or ''
    content = msg.get('content') or ''
    return {'role': role, 'content': content}


# ──────────────────────────────────────────────────────────────────────────────
# ReactCondenserAgent
# ──────────────────────────────────────────────────────────────────────────────


class ReactCondenserAgent(BaseCondenserRoutine):
    """react_agent 上下文压缩 routine (基于 Memory).

    读 react Memory (history, 含旧 summary 行) -> 投影 (找最后一条 summary 切) ->
    判断 trigger -> 执行策略 -> 写新 summary 到 messages 表 (kind='summary').

    共享 BaseCondenserRoutine.run() 模板方法, 只实现 _load_items + _write_summary.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'tool': False,
        'readonly': False,
        'input_schema': CondenseInput.model_json_schema(),
        'output_schema': CondenseOutput.model_json_schema(),
        'description': (
            'react_agent 上下文压缩 routine. 读 react Memory, 应用压缩策略, '
            '写 summary 到 messages 表 (kind=summary). 跟 reactor/prime '
            'CondenserAgent 平级, 各写各的.'
        ),
    }

    async def _load_items(self, inp: CondenseInput) -> CondenseLoadResult:
        """读 react Memory + 投影 + 转 items (带 message_id + response_id)."""
        mem = get_memory()
        history = mem.load_history(inp.session_id)
        if not history:
            return CondenseLoadResult()

        # 投影: 用 covered_to 定位 tail, 正确保留 [summary, tail, new_dialogue]
        projected, _, _ = project_with_summary(history)

        # 转 strategy items (跳过旧 summary)
        items: list[dict[str, Any]] = []
        items_message_ids: list[str] = []
        items_response_ids: list[str | None] = []
        for m in projected:
            item = _msg_to_item(m)
            if item is not None:
                items.append(item)
                items_message_ids.append(m.get('message_id') or '')
                items_response_ids.append(m.get('response_id') or None)

        _log.info(
            'load: agent=%s session=%s items=%d',
            inp.agent_id, inp.session_id, len(items),
        )
        return CondenseLoadResult(
            items=items,
            items_message_ids=items_message_ids,
            items_response_ids=items_response_ids,
        )

    async def _write_summary(
        self,
        inp: CondenseInput,
        result: CondenseResult,
        covered_from: str,
        covered_to: str,
        tokens_before: int,
    ) -> None:
        """写 summary 到 react Memory messages 表 (kind='summary')."""
        mem = get_memory()
        summary_message_id = f'summary-{uuid4().hex[:8]}'
        mem.add_summary_message(
            inp.session_id,
            message_id=summary_message_id,
            summary=result.summary,
            covered_from=covered_from,
            covered_to=covered_to,
            strategy=inp.strategy,
            tokens_before=tokens_before,
            tokens_after=result.tokens_after,
        )
        mem.flush()
