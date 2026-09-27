"""EventLog ---- 仅追加会话事件日志 + 表面派生 (dsh session log 移植).

flow 的唯一存储: 一个 session 一个 JSONL 文件, 只追加不修改.

事件词汇 (对齐 dsh):
- 结构: agent/start, turn/start, turn/end, step/start, step/end
- 表面 (surface, 进模型派生): user/message, assistant/message,
  function_call, function_call_output
- 表面变更: compaction/replacement (summary + shadowed 表面区间)

核心不变量 (dsh "模型可见即已记录"): 发给模型的每个字节都能从
事件流派生出来 ---- derive_items() 就是下次请求的 input.

表面区间协议 (dsh shadowedRange): replacement 记录**压缩时刻的表面
位置区间** [start, end). 派发线性扫描维护表面, 遇 replacement 做
surface[:start] + [summary] + surface[end:] ---- 嵌套替换天然正确.

prev_rid (Responses API 增量链): assistant/message 事件携带
response_id; 有效性是内存标志 (压缩/重试作废, resume 后保守全量).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from routine.logger import setup_logger

_log = setup_logger('flow.eventlog')

# 表面事件类型 (derive_items 消费)
SURFACE_TYPES = frozenset({
    'user/message', 'assistant/message',
    'function_call', 'function_call_output',
    'skill/catalog', 'skill/invoke',
})


class EventLog:
    """一个 session 的仅追加事件日志.

    兼容旧 ctx 接口 (append_user/items/flush), loop 无感切换.
    单线程事件循环内使用, 无锁.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[dict[str, Any]] = []
        self._seq = 0
        self._rid_valid = False          # prev_rid 有效性 (内存态)
        self._load()
        self._fh = open(self._path, 'a', encoding='utf-8')

    # ── 恢复 (resume = 重读事件流) ──

    def _load(self) -> None:
        if not self._path.exists():
            return
        with open(self._path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    _log.warning('skip malformed log line: %.60s', line)
                    continue
                self._events.append(ev)
                self._seq = max(self._seq, int(ev.get('seq', 0)))
        if self._events:
            _log.info('resumed event log: %d events (%s)',
                      len(self._events), self._path.name)

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    # ── 写 (仅追加, 即时 flush) ──

    def append(self, type_: str, **payload: Any) -> dict[str, Any]:
        """追加一个事件. 自动补 seq/ts, 返回完整事件."""
        self._seq += 1
        ev = {'seq': self._seq, 'ts': time.time(), 'type': type_, **payload}
        self._events.append(ev)
        self._fh.write(json.dumps(ev, ensure_ascii=False,
                                  default=str) + '\n')
        self._fh.flush()
        return ev

    # ── 读 ──

    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    @property
    def length(self) -> int:
        return len(self._events)

    def last_turn(self) -> int:
        """最大 turn 号 (resume 后轮次续号)."""
        best = 0
        for ev in self._events:
            t = ev.get('turn')
            if isinstance(t, int) and t > best:
                best = t
        return best

    def last_response_id(self) -> str | None:
        """最后一条带 response_id 的 assistant/message."""
        for ev in reversed(self._events):
            if ev.get('type') == 'assistant/message':
                rid = ev.get('response_id')
                if rid:
                    return str(rid)
        return None

    # ── prev_rid 有效性 (Responses API 增量链) ──

    def invalidate_rid(self) -> None:
        """作废增量链: 下次请求全量重发 (重试/压缩/resume 后)."""
        self._rid_valid = False

    def rid_available(self) -> bool:
        return self._rid_valid and self.last_response_id() is not None

    def to_request(self) -> tuple[list[dict[str, Any]], str | None]:
        """派生请求包络: (input_items, previous_response_id|None)."""
        items = self.derive_items()
        rid = self.last_response_id() if self._rid_valid else None
        return items, rid

    def mark_response(self, response_id: str) -> None:
        """一次成功请求落定 → 增量链恢复可用."""
        if response_id:
            self._rid_valid = True

    # ── 表面派生 (给模型的 input) ──

    def derive_surface_events(self) -> list[dict[str, Any]]:
        """派生表面事件序列: replacement / prune 已应用的 surface 事件列表.

        空文本 assistant/message 不占表面位置 (与 apply_replacement
        记录区间时的表面状态一致). items / history 都从这一路径转出.

        prune 事件在流的尾部而目标 fco 在它之前 ---- 先收集全部 edits
        再派生 (call_id 唯一, 后剪覆盖先剪, 对整个表面生效).
        """
        pruned: dict[str, str] = {}          # call_id -> 裁剪后 output
        for ev in self._events:
            if ev.get('type') == 'compaction/prune':
                pruned.update(ev.get('edits') or {})

        surface: list[dict[str, Any]] = []
        for ev in self._events:
            t = ev.get('type')
            if t == 'assistant/message' and not (ev.get('text') or ''):
                continue
            if t == 'function_call_output' and ev.get('call_id') in pruned:
                surface.append({**ev, 'output': pruned[ev['call_id']]})
            elif t in SURFACE_TYPES:
                surface.append(ev)
            elif t == 'compaction/replacement':
                start, end = ev.get('shadowed', (0, 0))
                surface = surface[:start] + [ev] + surface[end:]
        return surface

    def derive_items(self) -> list[dict[str, Any]]:
        """从事件流派生模型 input items. 每次请求都重放 (dsh deriveMessages)."""
        items: list[dict[str, Any]] = []
        for ev in self.derive_surface_events():
            t = ev.get('type')
            if t == 'user/message':
                items.append({'role': 'user', 'content': ev.get('text', '')})
            elif t == 'assistant/message':
                items.append({'role': 'assistant', 'content': ev.get('text', '')})
            elif t == 'compaction/replacement':
                items.append({
                    'role': 'user',
                    'content': f"Context summary:\n\n{ev.get('summary', '')}",
                })
            elif t == 'function_call':
                items.append({
                    'type': 'function_call',
                    'name': ev.get('name', ''),
                    'arguments': ev.get('arguments', '{}'),
                    'call_id': ev.get('call_id', ''),
                })
            elif t == 'function_call_output':
                items.append({
                    'type': 'function_call_output',
                    'call_id': ev.get('call_id', ''),
                    'output': ev.get('output', ''),
                })
            elif t == 'skill/catalog':
                items.append({
                    'role': 'user',
                    'content': render_catalog(ev.get('entries') or []),
                })
            elif t == 'skill/invoke':
                # 用户手势注入: 正文即渲染好的 <skill_content> 块
                items.append({
                    'role': 'user',
                    'content': str(ev.get('content') or ''),
                })
        return items

    def items(self) -> list[dict[str, Any]]:
        """ctx 接口别名."""
        return self.derive_items()

    # ── 压缩表面替换 (dsh user/message + compactCheckpointSource) ──

    def apply_replacement(self, summary: str, cut: int, *,
                          tokens_before: int = 0,
                          tokens_after: int = 0) -> None:
        """压缩落定: 记 replacement 事件, 作废增量链.

        cut 是压缩时表面上的切割点 (表面位置区间 [0, cut) 被 summary
        替代) ---- 派生重放时在同一表面状态上重算, 区间必然对齐.
        """
        self.append(
            'compaction/replacement',
            summary=summary, shadowed=[0, cut],
            tokens_before=tokens_before, tokens_after=tokens_after,
        )
        self._rid_valid = False

    def apply_prune(self, edits: dict[str, str]) -> None:
        """免模型裁剪落定 (dsh tool-result-pruner): 旧工具结果替换.

        edits: {call_id: new_output}. 不改变条目数/表面位置 (replacement
        区间不受影响); 派生时 fco 的 output 按映射替换.
        """
        if not edits:
            return
        self.append('compaction/prune', edits=dict(edits))
        self._rid_valid = False

    # ── ctx 兼容写接口 (loop 经 agent 调用) ──

    def append_user(self, text: str, *, turn: int = 0, step: int = 0) -> None:
        self.append('user/message', turn=turn, step=step, text=text)

    def append_assistant(self, text: str, *, response_id: str = '',
                         usage: dict | None = None,
                         turn: int = 0, step: int = 0,
                         first_token_ts: float | None = None) -> None:
        """assistant 锚点事件. text 可为空 (纯工具调用步) ---- rid 仍记录.

        first_token_ts: 流式首个 delta 到达时刻 (统计派生用; 中断/失败
        的半截消息可能没有).
        """
        self.append('assistant/message', turn=turn, step=step, text=text,
                    response_id=response_id, usage=usage or {},
                    first_token_ts=first_token_ts)

    def append_function_call(self, name: str, arguments: str, call_id: str, *,
                             turn: int = 0, step: int = 0) -> None:
        self.append('function_call', turn=turn, step=step, name=name,
                    arguments=arguments, call_id=call_id)

    def append_function_output(self, call_id: str, output: str,
                               raw_result: Any = None, *,
                               turn: int = 0, step: int = 0) -> None:
        self.append('function_call_output', turn=turn, step=step,
                    call_id=call_id, output=output, raw_result=raw_result)

    def flush(self) -> None:
        """ctx 接口兼容: 事件即时落盘, no-op."""
        if not self._fh.closed:
            self._fh.flush()

    # ── UI history 派生 (get_history / session_changed 契约) ──

    def derive_history(self) -> list[dict[str, Any]]:
        """事件流 → 前端消息列表 (user/assistant/tool 三类, FC burst 分组)."""
        return derive_history(self.derive_surface_events())


def load_events(path: Path) -> list[dict[str, Any]]:
    """只读加载事件流 (hub 为已停止 agent 派生历史用, 不落任何写)."""
    events: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return events
    with open(p, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def derive_surface_from(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """表面事件派生的纯函数形态 (与 EventLog.derive_surface_events 同一规则)."""
    pruned: dict[str, str] = {}
    for ev in events:
        if ev.get('type') == 'compaction/prune':
            pruned.update(ev.get('edits') or {})

    surface: list[dict[str, Any]] = []
    for ev in events:
        t = ev.get('type')
        if t == 'assistant/message' and not (ev.get('text') or ''):
            continue
        if t == 'function_call_output' and ev.get('call_id') in pruned:
            surface.append({**ev, 'output': pruned[ev['call_id']]})
        elif t in SURFACE_TYPES:
            surface.append(ev)
        elif t == 'compaction/replacement':
            start, end = ev.get('shadowed', (0, 0))
            surface = surface[:start] + [ev] + surface[end:]
    return surface


def derive_history(surface: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """表面事件 → 前端消息列表 (user/assistant/tool 三类, FC burst 分组)."""
    out: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    def flush() -> None:
        if not order:
            return
        results = []
        for cid in order:
            fc = pending.get(cid)
            if fc is None:
                continue
            results.append({
                'name': fc.get('name', ''),
                'input': _safe_json(fc.get('arguments')),
                'result': fc.get('raw_result', fc.get('output')),
                'call_id': cid,
                'status': 'done',
            })
        if results:
            out.append({
                'id': f"tool-{results[0].get('call_id') or len(out)}",
                'role': 'tool', 'text': '', 'results': results,
                'final': True,
            })
        pending.clear()
        order.clear()

    for ev in surface:
        t = ev.get('type')
        if t == 'user/message':
            flush()
            out.append({'id': f'hist-user-{len(out)}', 'role': 'user',
                        'text': ev.get('text', ''), 'final': True})
        elif t == 'assistant/message':
            flush()
            out.append({'id': f'hist-assistant-{len(out)}',
                        'role': 'assistant',
                        'text': ev.get('text', ''), 'final': True})
        elif t == 'compaction/replacement':
            flush()
            out.append({'id': f'hist-summary-{len(out)}', 'role': 'user',
                        'text': f"Context summary:\n\n{ev.get('summary', '')}",
                        'final': True})
        elif t == 'function_call':
            cid = str(ev.get('call_id') or '')
            if not cid:
                continue
            pending[cid] = {'name': ev.get('name', ''),
                            'arguments': ev.get('arguments', '')}
            order.append(cid)
        elif t == 'function_call_output':
            cid = str(ev.get('call_id') or '')
            fc = pending.get(cid)
            if fc is not None:
                fc['output'] = ev.get('output', '')
                if ev.get('raw_result') is not None:
                    fc['raw_result'] = ev.get('raw_result')
    flush()
    return out


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ── skill 目录渲染 (dsh tool-skill catalog 模板移植) ──

_CATALOG_DESCRIPTION_MAX = 500


def render_catalog(entries: list[dict[str, Any]]) -> str:
    """目录条目 → user 角色目录消息 (替换语义: 以最新目录为准).

    dsh 语义: digest 变化才追加 replacement ---- 旧目录仍在历史
    (append-only, KV cache 友好), 模板明示以最新目录为准.
    """
    lines = [
        '- `{name}`: {desc}'.format(
            name=str(e.get('name') or ''),
            desc=_cap(str(e.get('description') or '')))
        for e in entries
    ]
    listing = '\n'.join(lines) if lines else '(none)'
    retire = ('\nThis catalog replaces all earlier skill listings; '
              'do not use names from older listings.'
              if lines else
              '\nNo skills are available now; do not use older names.')
    return (
        '<system-reminder>\n'
        'A skill is a reusable set of task-specific instructions. '
        'The following skills are available in this session:\n\n'
        '<available_skills>\n'
        f'{listing}\n'
        '</available_skills>\n\n'
        'If the user names a skill, or the task clearly matches a '
        "skill's description, call the `skill` tool with the exact skill "
        'name before taking task actions. Load all applicable skills, '
        'then follow their full instructions. This catalog contains '
        'summaries only; do not infer or follow a skill\'s instructions '
        'until it has been loaded.\n'
        'A user may also invoke a skill directly; its <skill_content> '
        'block then appears in this conversation. Follow it, and do not '
        'call the `skill` tool again for that skill.'
        f'{retire}\n'
        '</system-reminder>'
    )


def _cap(desc: str) -> str:
    if len(desc) <= _CATALOG_DESCRIPTION_MAX:
        return desc
    return desc[:_CATALOG_DESCRIPTION_MAX - 1] + '…'
