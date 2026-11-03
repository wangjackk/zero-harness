"""StopJudge —— 每段一个判停循环（原版 ``host/listener/stop_judge.py`` 移植）。

把识别事件流翻译成 commit 决策：

- ``CLAUSE``：累积分句、起一个防抖的分类器任务、给静音定时器续期；
- ``FIRST`` / ``PARTIAL``：取消在途分类器（这句被新文本取代了）；
- 分类器打分 >= 阈值 → 提前 commit；静音到期 → 兜底 commit；
- 关键词命中 → 立刻 commit（显式端点，不打分）。

两条路径赛跑，谁先到谁 commit，且**只 commit 一次** —— 一个 ``_committed``
闩锁 + 互相 cancel，置位与 commit 之间没有 ``await``（不会被打断）。

``feed`` 是 async 但**绝不内联等打分**：它只起任务就返回。原版这条注释值得
原样保留：``on_event_creating`` 是 inline-await 的挂载点，在这里阻塞就是把
接收循环堵死。

与原版的唯一差异：``caller`` 从 ``MossLLMCaller.run_messages(list[Message])``
收窄成 ``async (prompt: str) -> str``，不依赖 ghoshell 的消息类型。
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from routine.logger import setup_logger

from .events import Event, Phase

__all__ = ['parse_stop_score', 'StopScore', 'StopJudge', 'ScoreCaller']

_log = setup_logger('moss.asr')

#: 判停打分器：吃拼好的 prompt，吐原始文本（第一个数字即分值）。
ScoreCaller = Callable[[str], Awaitable[str]]


def parse_stop_score(raw: str) -> int | None:
    """从原始输出里取 0-9 的判停分；没有数字则 None。"""
    for ch in (raw or '').strip():
        if ch.isdigit():
            return int(ch)
    return None


@dataclass
class StopScore:
    """一次判停打分的观测 —— 请求（分句）+ 结果（分值 + 原始输出）。

    供旁路监控（测试/日志）看判停器实际发了什么、模型回了什么分。
    """

    clauses: list[str]
    score: int | None
    raw: str = ''


class StopJudge:
    """每段一个判停循环 —— 静音定时器与分类器互斥，先到先 commit。

    两条 commit 路径：

    - ``silence`` 定时器：最后一个分句之后 ``segment_vad`` 秒 commit（每个新分句
      展期一次）。这是基线兜底。
    - ``classifier``：被 ``judge_delay`` 防抖（安全期）—— 窗口内被下一句取代的
      调用一次都不会花；分数 >= ``threshold`` 时提前 commit。

    新一段会拆掉旧循环（取消两个任务、清空累积）。
    """

    def __init__(
            self,
            *,
            commit: Callable[[], None],
            caller: Optional[ScoreCaller] = None,
            judge: bool = True,
            threshold: int = 7,
            segment_vad: float = 3.0,
            judge_delay: float = 0.3,
            keywords: Optional[Sequence[str]] = None,
            context: str = '',
            on_score: Optional[Callable[[StopScore], None]] = None,
    ) -> None:
        self._commit = commit
        self._caller = caller
        self._judge = judge and caller is not None
        self._threshold = threshold
        self._segment_vad = segment_vad
        self._judge_delay = judge_delay
        self._keywords = list(keywords) if keywords else []
        # 易变提示（etiquette.classifier.context）放在最前面：同一段内它是稳定的
        # 前缀，累计的分句跟在后面能吃到 prompt 缓存。
        self._context = context
        self._on_score = on_score
        self._segment_id: str | None = None
        self._clauses: list[str] = []
        self._committed = False
        self._last_clause_at: float | None = None
        self._judge_task: asyncio.Task | None = None
        self._vad_task: asyncio.Task | None = None
        self._epoch = 0

    # ── 驱动 ──

    async def feed(self, event: Event) -> None:
        """用一条识别事件驱动本循环（FIRST/PARTIAL/CLAUSE）。不阻塞。"""
        if event.segment_id != self._segment_id:
            self._teardown()
            self._segment_id = event.segment_id
            self._clauses = []
            self._committed = False
            self._last_clause_at = None
        if self._committed:
            return
        if event.phase == Phase.CLAUSE:
            self._on_clause(event)
        elif event.phase in (Phase.FIRST, Phase.PARTIAL):
            self._cancel_judge()
            # ``partial`` 是"人还在说"的直接证据 —— 静音计时必须跟着展期.
            # 只认 CLAUSE 就会这样: 服务端迟迟不吐新的 ``utterance``(它自己
            # 的 VAD 还没判停), 而人一直在往下说, 我们这边的 ``silence`` 到点
            # 就把半句话交出去, 后半句另起一段 —— 一句话被切成两条.
            if self._last_clause_at is not None:
                self._last_clause_at = time.monotonic()

    def close(self) -> None:
        """取消在途判停任务（会话收摊）。"""
        self._teardown()

    def committed(self) -> bool:
        return self._committed

    def commit_now(self) -> None:
        """外部强制 commit（人类强发送 / 测试）：走与其他路径同一条出口。"""
        self._try_commit()

    # ── commit 路径 ──

    def _on_clause(self, event: Event) -> None:
        text = event.clause.text if event.clause else event.text
        if self._keywords and any(k in text for k in self._keywords):
            self._try_commit()
            return
        self._clauses.append(text)
        if self._segment_vad <= 0:
            self._try_commit()   # 0 = 首个分句即端点，不起定时器
            return
        self._last_clause_at = time.monotonic()
        # ``segment_vad=inf`` = 本层不做时间判停（ptt: 边界由"松手"给）。
        # 起一个永远不到期的定时器只是白烧一个 20Hz 空转任务。
        if math.isfinite(self._segment_vad):
            self._start_vad()
        if self._judge:
            self._start_judge()

    def _start_vad(self) -> None:
        self._cancel_vad()
        self._vad_task = asyncio.create_task(self._vad_loop())

    async def _vad_loop(self) -> None:
        # deadline = last_clause_at + segment_vad；每个新分句把它往后推（展期）。
        while True:
            await asyncio.sleep(0.05)
            if self._committed or self._last_clause_at is None:
                return
            if time.monotonic() - self._last_clause_at >= self._segment_vad:
                self._try_commit()
                return

    def _start_judge(self) -> None:
        self._cancel_judge()
        epoch = self._epoch
        self._judge_task = asyncio.create_task(
            self._judge_loop(list(self._clauses), epoch))

    async def _judge_loop(self, clauses: list[str], epoch: int) -> None:
        try:
            # 安全期：在这段时间内被新分句取代 → 任务被 cancel，这次调用不花钱。
            await asyncio.sleep(self._judge_delay)
            raw = await self._caller(self._build_prompt(clauses))
            score = parse_stop_score(raw)
            _log.info('stop judge scored %s over %d clauses', score, len(clauses))
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception('stop judge failed')
            self._emit_score(clauses, None, '')   # 失败也要让观测面看见
            return
        self._emit_score(clauses, score, raw)
        if epoch != self._epoch or self._committed:
            return
        if score is not None and score >= self._threshold:
            self._try_commit()

    def _emit_score(self, clauses: list[str], score: int | None, raw: str) -> None:
        """发一次打分观测；观测者抛异常不能把 commit 带崩。"""
        if self._on_score is None:
            return
        try:
            self._on_score(StopScore(clauses=list(clauses), score=score, raw=raw))
        except Exception:
            _log.exception('stop judge on_score observer failed')

    def _try_commit(self) -> None:
        if self._committed:
            return
        self._committed = True
        self._cancel_vad()
        self._cancel_judge()
        self._commit()

    # ── 内部 ──

    def _cancel_judge(self) -> None:
        if self._judge_task is not None and not self._judge_task.done():
            self._judge_task.cancel()
        self._judge_task = None
        self._epoch += 1

    def _cancel_vad(self) -> None:
        if self._vad_task is not None and not self._vad_task.done():
            self._vad_task.cancel()
        self._vad_task = None

    def _teardown(self) -> None:
        self._cancel_vad()
        self._cancel_judge()

    def _build_prompt(self, clauses: list[str]) -> str:
        """每句一个块；``<context>`` 在前，与分句一起构成稳定的缓存前缀。"""
        parts: list[str] = []
        if self._context:
            parts.append(f'<context>\n{self._context}\n</context>')
        parts.extend(clauses)
        return '\n'.join(parts)
