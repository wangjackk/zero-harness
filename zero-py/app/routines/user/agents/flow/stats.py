"""Stats ---- 会话统计的事件流派生 (dsh session-stats / token-meter 移植).

dsh 用 projection 逐事件消费 session 事件; flow 的对应物: 同一
EventLog 的纯函数派生 ---- live 与 resume 走同一条路, 天然一致.

映射 (对齐 dsh session-stats projection):
- turns/steps: step/end 计数; turn 号变化时 turns+1 (step/end 在
  finally 里恰好一次, 覆盖完成/失败/取消全部情形)
- llm_ms:      Σ ts(assistant/message) − ts(step/start)
- tool_ms:     Σ ts(function_call_output) − ts(function_call)  (call_id 配对)
- ttft:        Σ first_token_ts − ts(step/start), 除以有首 token 的步数
- decode:      Σ ts(assistant/message) − first_token_ts;
               decode_tokens = Σ usage.output_tokens (仅携带 usage 的步)

token 四桶 (对齐 dsh token-meter, disjoint):
- cache_read = usage.input_tokens_details.cached_tokens
- uncached   = input_tokens − cached
- cache_write = 0 (provider 不报写缓存)
- output     = output_tokens (已含 reasoning tokens)
"""
from __future__ import annotations

from typing import Any


def derive_stats(events: list[dict[str, Any]]) -> dict[str, int]:
    """事件流 → 统计视图. 无状态全量派生, 每次调用重放."""
    turns = steps = 0
    llm_ms = tool_ms = 0.0
    ttft_ms = decode_ms = 0.0
    ttft_steps = decode_tokens = 0
    uncached = cache_read = output_tokens = 0
    last_turn: Any = None
    start_ts: float | None = None
    pending: dict[str, float] = {}

    for ev in events:
        t = ev.get('type')
        ts = float(ev.get('ts') or 0)
        if t == 'step/start':
            start_ts = ts
        elif t == 'assistant/message':
            u = ev.get('usage')
            u = u if isinstance(u, dict) else {}
            if start_ts is not None:
                llm_ms += max(0.0, (ts - start_ts) * 1000)
                ft = ev.get('first_token_ts')
                if ft:
                    ft = float(ft)
                    ttft_ms += max(0.0, (ft - start_ts) * 1000)
                    ttft_steps += 1
                    decode_ms += max(0.0, (ts - ft) * 1000)
                    decode_tokens += _int(u.get('output_tokens'))
            it = _int(u.get('input_tokens'))
            det = u.get('input_tokens_details')
            cached = _int(det.get('cached_tokens')) \
                if isinstance(det, dict) else 0
            uncached += max(0, it - cached)
            cache_read += cached
            output_tokens += _int(u.get('output_tokens'))
        elif t == 'function_call':
            cid = str(ev.get('call_id') or '')
            if cid:
                pending[cid] = ts
        elif t == 'function_call_output':
            cid = str(ev.get('call_id') or '')
            if cid in pending:
                tool_ms += max(0.0, (ts - pending.pop(cid)) * 1000)
        elif t == 'step/end':
            steps += 1
            turn = ev.get('turn')
            if turn != last_turn:
                turns += 1
                last_turn = turn
            start_ts = None
        elif t == 'turn/end':
            # 取消/失败的轮次: 丢弃未配对的工具调用计时
            pending.clear()

    return {
        'turns': turns, 'steps': steps,
        'llm_ms': round(llm_ms), 'tool_ms': round(tool_ms),
        'ttft_ms': round(ttft_ms), 'ttft_steps': ttft_steps,
        'decode_ms': round(decode_ms), 'decode_tokens': decode_tokens,
        'uncached_input': uncached, 'cache_read': cache_read,
        'cache_write': 0, 'output_tokens': output_tokens,
    }


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0
