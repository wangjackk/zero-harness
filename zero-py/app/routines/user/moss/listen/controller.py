"""ListenerController —— 聆听礼仪状态机（原版 ``host/listener/controller.py`` 移植）。

原版 1308 行里，真正的"策略"约一半，另一半是 ghoshell 宿主的装线（Signal /
Session / topic / channel / config store / ASR 参数 / corpus）。mossv2 这版只搬
策略，装线换成三根针：

- ``emit(**fields)``：把一条 impulse 字段交给 ``mindflow.declare``（原版
  ``new_listener_signal`` → session.add_signal）；
- ``feed(event)``：吃耳朵事件（原版 ``on_recognition_result``）；
- ``ScoreCaller``：判停分类器（可选，不挂就退化成 silence/keywords）。

于是原版的三层照旧：**起话（onset）→ 判停（stop）→ 交付（deliver）**。

与原版的三处刻意差异（都有理由，不是省事）：

1. **段（segment）由本层切**，不靠 ASR 的负序号 commit。豆包的 utterance 是
   服务端 VAD 定的"句"，而"一轮"应该由礼仪判停决定（一句话可能由多个分句构成，
   也可能一句就完）。所以这里自己维持 ``segment_id``，``StopJudge`` 的 commit
   就是切段点 —— 与原版"commit 切段"同一语义，只是 commit 的发起方在本地。
2. **TAIL 是合成事件**，不是识别层给的（原版也是 commit 产生的，不是 VAD）。
3. **没有 topic / audio sample 装线**：那些是宿主的可观测面（ClauseTopic 给
   UI、AudioSampleTopic 给示波器），不属于策略。

半双工不在这里：说话期丢麦是器官（``asr/ear.py``）的事，控制器看不到那些帧。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional
from uuid import uuid4

from routine.logger import setup_logger

from ..mindflow import Priority
from .etiquette import EtiquetteConfig, EtiquetteSpec
from .events import Clause, Event, Phase, Segment, Word
from .segment_buffer import SegmentBuffer
from .stop_judge import ScoreCaller, StopJudge, StopScore

__all__ = ['EmitFn', 'LevelFn', 'ListenerController', 'WitnessFn']

_log = setup_logger('moss.asr')

#: 申报出口：吃 impulse 字段，返回 declare 回执（verdict）。
EmitFn = Callable[..., Awaitable[Dict[str, Any]]]

#: 围观针：``(本段全文, 是否定稿)`` —— **只旁听不改判**（UI 流式渲染用）。
#: 同步调用：策略层（尤其 ``_commit`` 是判停回调）不能被渲染拖慢。
WitnessFn = Callable[[str, bool], None]

#: 响度探针：``() -> 最近一块像人声的 dBFS``（器官给，``None`` = 还没量到）。
#: 本层只在**收到文本事件**时采一次样 —— 那一刻人一定在说话，所以采到的是
#: "这句话多响"，不是"此刻多响"。原版是逐帧累加（``_on_audio_level``），
#: 差别只在采样密度，口径一样：段内均值，段尾结算。
LevelFn = Callable[[], Optional[float]]

#: 起话包强度（原版 ListenerNucleus.first_strength）；交付包用常态 100。
#: 权威在 ``nucleus.ListenerNucleus.govern``（核会按包类型钉强度）；
#: 这里给一份只为核未注册时行为不变。
_ONSET_STRENGTH = 150


def _overlap_tail(head: str, tail: str) -> int:
    """``head`` 的后缀与 ``tail`` 的前缀最长重叠长度（相加时别念两遍）。"""
    for k in range(min(len(head), len(tail)), 0, -1):
        if head.endswith(tail[:k]):
            return k
    return 0


def _common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class ListenerController:
    """礼仪驱动状态机：耳朵事件进 → (起话/判停/交付) → declare 出。

    ``emit`` 的签名与 ``mindflow.declare`` 的字段面一致（id/source/text/
    priority/strength/complete/mode/interrupt/low_conf/db/description），所以
    中间不需要翻译层。

    **两个听侧才有的读数在这里算，不往上游推**：响度（``level`` 探针采样，
    段内均值，段尾结算）与低置信字（逐字 ``conf`` 低于礼仪阈值的那些）。
    它们和"这包该多强"一样，是**现场才有的东西** —— 交给核时已经是数，
    核不重算（这两个数现在到不了 prompt，渲染层只印 paused；采集照旧，
    排障还看得到）。

    ``config`` 是礼仪表（默认取 11 个开箱坐标，激活 ``always``）；运行期可用
    :meth:`set_etiquette` 切到别的坐标，用 :meth:`upsert_etiquette` 增改。

    ``witness`` 是可选的**旁观针**（``(全文, 定稿?)``）：本层每推进一次
    文本就叫一声，给交互面做流式渲染。它不参与判停、不阻塞申报 ——
    与 ``emit``（真申报）是两条路：围观的可以没有，申报的不能少。
    """

    def __init__(
            self,
            emit: EmitFn,
            *,
            config: Optional[EtiquetteConfig] = None,
            caller: Optional[ScoreCaller] = None,
            witness: Optional[WitnessFn] = None,
            level: Optional[LevelFn] = None,
            source: str = 'asr',
            strength: int = 100,
            priority: Optional[int] = None,
            manual_boundary: bool = False,
    ) -> None:
        self._emit = emit
        self._cfg = config or EtiquetteConfig()
        self._caller = caller
        self._witness = witness
        self._level = level
        self._source = source
        self._strength = strength
        # 交付包优先级覆盖：None = 听礼仪的（always 是 INFO）
        self._priority = priority
        # 段边界由人类显式给（ptt 的按下/松开）还是由本层猜（静音/打分）
        self._manual = bool(manual_boundary)

        spec = self._cfg.active()
        if spec is None:                       # 配置被清空时退回 always
            spec = self._cfg.get('always')
            self._cfg.activate('always')
        assert spec is not None
        self._spec: EtiquetteSpec = spec
        self._buffer = SegmentBuffer(history=spec.deliver.history)

        # 当前段
        self._seg_id: str = ''
        self._stream_id: str = ''
        self._clauses: list[Clause] = []
        self._text: str = ''
        self._first_sent: bool = False
        self._judge: Optional[StopJudge] = None

        # 诊断
        self._onsets = 0
        self._delivers = 0
        self._commits = 0
        self._last_score: Optional[StopScore] = None
        self._last_activity = time.monotonic()

        # 本段响度(采样累加; 段尾结算并清零 —— 原版 _segment_db 同口径)
        self._level_sum = 0.0
        self._level_count = 0
        self._last_db: Optional[float] = None

        # 异步出口的任务（emit 是 async，commit 回调是同步）
        self._bg: set[asyncio.Task] = set()
        self._closed = False

    # ── 事件入口 ──

    async def feed(self, ev: Any) -> None:
        """吃一条耳朵事件（``EarEvent``：partial / utterance / state）。

        只做相位映射：``partial`` → FIRST（首次非空）/ PARTIAL；
        ``utterance``（服务端 VAD 定稿的一句）→ CLAUSE。段边界由本层判停决定。

        顺带在这里采一次响度：有字就说明刚才有人在说话，那一刻器官手里
        的读数就是"这句话多响"。**不在定时器里采** —— 静音期的读数会把
        整段的均值拉低（原版逐帧累加时同样跳过 is_silent 帧）。
        """
        kind = getattr(ev, 'kind', '')
        if kind == 'state':
            _log.info('listener state: %s', getattr(ev, 'detail', ''))
            return
        text = (getattr(ev, 'text', '') or '').strip()
        if not text:
            return
        self._last_activity = time.monotonic()
        self._sample_level()
        if kind == 'partial':
            await self._on_partial(text)
        elif kind == 'utterance':
            await self._on_clause(text, list(getattr(ev, 'words', None) or []))

    async def _on_partial(self, text: str) -> None:
        """说话中：把"本段全文"并进累计；首次非空发起话包。"""
        if self._closed:
            return
        self._ensure_segment()
        self._absorb(text)
        # 围观针先叫：判停可能同步 commit（关键词/分类器立刻判停），
        # 若先 feed_judge 再叫 partial，UI 会先收"定稿"再收"还在说"，
        # 把定稿行重新激活成半截话。
        self._notify(self._text, False)
        await self._note_first(self._text)
        self._buffer.on_event(self._event(Phase.PARTIAL, self._text))
        await self._feed_judge(Phase.PARTIAL, self._text)

    def _absorb(self, text: str) -> None:
        """把服务端送来的"本段全文"并进 ``_text``。

        partial 名义上是本段全文，但**识别会话重启**（半双工静音、断线重连、
        服务端 VAD 重开）之后它从断点重新开始，只覆盖后半句 —— 照抄
        full-replace 会把已经说过的前半句抹掉。实况（2026-10-20 22:11）：
        ``'你可以同时'`` 之后来了 ``'说话，然后。'``，交付只剩后者。

        判定按"像不像同一句"分三档：

        - 新的以旧的为前缀（在长）→ 以新的为准；
        - 旧的以新的为前缀（服务端回退）→ 保留长的；
        - 旧的还很短（<4 字，几乎全是改字现场）→ 以新的为准；
        - 共同前缀够长（>= 较短者一半，如 ``今天天汽``→``今天天气怎么样``）
          → 同一句在改字，以新的为准；
        - 其余 → 断点续接，接在后面，与尾巴重叠的那段不重复。
        """
        if not text:
            return
        if not self._text:
            self._text = text
            return
        if text.startswith(self._text):
            self._text = text
            return
        if self._text.startswith(text):
            return
        if len(self._text) < 4:
            self._text = text
            return
        shorter = min(len(self._text), len(text))
        if _common_prefix(self._text, text) >= max(2, shorter // 2):
            self._text = text
            return
        self._text = self._text + text[_overlap_tail(self._text, text):]

    async def _on_clause(self, text: str, words: Optional[list] = None) -> None:
        """一句定稿：累积分句、更新全文、喂判停。

        ``words`` 是引擎给的词级明细（``[{'text', 'conf'}]``，见
        ``asr/ear._words_of``）—— 这里**存下来不筛**：阈值属于礼仪，
        而礼仪在交付那一刻才知道是哪一个（可能中途被切）。

        两句的文本关系有三种，``text`` 是"这一句"、``_text`` 是"这一段累计"：

        - partial 已把这一句累计进去（``_text`` 以它结尾）→ 保持累计；
        - 这一句比 partial 更完整（partial 是它的前缀）→ 覆盖；
        - 这一句是新的下一句（partial 还没到）→ 追加。
        """
        if self._closed:
            return
        self._ensure_segment()
        clause = Clause(text=text, words=[
            Word(text=str(w.get('text') or ''), conf=w.get('conf'))
            for w in (words or []) if isinstance(w, dict)])
        self._clauses.append(clause)
        if not self._text.endswith(text):
            if text.startswith(self._text):
                self._text = text
            else:
                # 还有一种: 分句以**最近一条 partial 的尾巴**开头(服务端把
                # 中间态补全成一句, 前面已经念过的字又出现在这句里)。
                # 直接相加会念两遍 —— 只补不重叠的那一段。
                self._text = (self._text
                              + text[_overlap_tail(self._text, text):])
        self._notify(self._text, False)
        self._buffer.on_event(self._event(Phase.CLAUSE, self._text, clause))
        await self._note_first(self._text)
        await self._feed_judge(Phase.CLAUSE, self._text, clause)

    def _notify(self, text: str, final: bool) -> None:
        """叫一声围观针（无针即无操作）；异常吞掉 —— 渲染坏了不能拖垮耳朵。"""
        if self._witness is None or not text.strip():
            return
        try:
            self._witness(text, final)
        except Exception:
            _log.exception('listener witness failed')

    async def _note_first(self, text: str) -> None:
        """本段首次有文本：发一条起话包（complete=False，占坑不填）。

        短句可能没有 partial 直接定稿，所以 CLAUSE 入口也要走这里 —— 原版
        ``FIRST`` 是"识别器有字了"而不是"收到 partial"，口径一致。

        这里不看回执：起话包的冷却（原版 ``ListenerNucleus`` 的
        suppress/attended 两层）由核在 ``declare`` 里判，丢了就丢了 ——
        本段已经开口过一次，不补发；交付包永不受冷却约束。
        """
        if self._first_sent:
            return
        self._first_sent = True
        spec = self._spec.onset
        if not spec.emit:
            return
        self._onsets += 1
        await self._emit(
            id=self._seg_id,              # 与交付包同 id → mindflow 同 id 吸收
            source=self._source,
            text=text,
            priority=int(spec.priority),
            strength=_ONSET_STRENGTH,
            complete=False,
            mode='',                      # 起话输则 suppress（原版 onset 默认）
            interrupt=spec.interrupt,
            # 原版两轴正交: interrupt 只管"停身体"(barge-in 停掉在途
            # 回复), 答不答是 effort. 起话包必须显式 normal —— 否则
            # declare 会按 interrupt primitive 补成 effort='none',
            # 整句话被当成"停而不答"的空拍(旧 bug 的根)
            effort='normal',
            description='listener:onset',
        )

    async def _feed_judge(self, phase: Phase, text: str,
                          clause: Optional[Clause] = None) -> None:
        judge = self._judge
        if judge is None:
            return
        await judge.feed(self._event(phase, text, clause))

    def _event(self, phase: Phase, text: str,
               clause: Optional[Clause] = None) -> Event:
        return Event(stream_id=self._stream_id, segment_id=self._seg_id,
                     phase=phase, text=text, clause=clause)

    # ── 段与判停 ──

    def _ensure_segment(self) -> None:
        """开新段：新 id + 新判停循环（旧的已 commit/关闭）。"""
        if self._seg_id:
            return
        self._seg_id = uuid4().hex
        if not self._stream_id:
            self._stream_id = uuid4().hex
        self._clauses = []
        self._text = ''
        self._first_sent = False
        self._open_judge()

    def _open_judge(self) -> None:
        stop = self._spec.stop
        clf = stop.classifier
        if self._manual:
            # ptt: 边界是"松手", 不是"猜". 判停三把刀（静音定时器 / 分类器评分 /
            # 关键词端点）全部让位 —— 否则按住期间停顿一下就被切段, 一句话
            # 变成两条交付, 后一条还带着前一条的前缀.
            seg_vad, caller, keywords = float('inf'), None, []
        else:
            seg_vad = stop.silence
            caller = self._caller if clf is not None else None
            keywords = stop.keywords
        self._judge = StopJudge(
            commit=self._commit,
            caller=caller,
            threshold=clf.threshold if clf else 7,
            segment_vad=seg_vad,
            judge_delay=clf.delay if clf else 0.3,
            keywords=keywords,
            context=clf.context if clf else '',
            on_score=self._on_score,
        )

    def set_manual_boundary(self, on: bool) -> None:
        """段边界交给人类（``True`` = ptt 的按下/松开；``False`` = 本层判停）.

        切换不丢字：文本与分句在 controller 自己手里，这里只换当前段的判停
        策略（旧循环拆掉、按新策略重开）。唯一的出口差异 —— 手动边界下
        ``send_now()``（松手）是**唯一**能关段的地方。
        """
        on = bool(on)
        if on == self._manual:
            return
        self._manual = on
        if self._judge is not None:
            self._judge.close()
        if self._seg_id:
            self._open_judge()
        # 一行日志是这一刀唯一的现场证据: 出"按住还是被切段"时先看它有没有打
        _log.info('listener boundary -> %s (silence/classifier/keywords %s)',
                  'manual' if on else 'judged',
                  'off' if on else 'on')

    def _on_score(self, score: StopScore) -> None:
        self._last_score = score

    def _commit(self) -> None:
        """判停：切段 → 入留存 → （emit=True 时）发交付包。同步回调。"""
        seg_id, text, clauses = self._seg_id, self._text, list(self._clauses)
        # 响度/低置信字**必须在 _close_segment 之前结账**: 结算读的是本段
        # 累积的采样, 而清账就发生在结算里 —— 先切段就读不到了(会静默变成
        # "这一段没有 db", 不是报错).
        db, low_conf = self._close_segment_loudness(clauses)
        self._close_segment()
        if not text.strip():
            return
        self._commits += 1
        _log.info('listener commit: %d clauses, %r', len(clauses), text[:120])
        # 定稿先于交付：交付要等 declare 回执，UI 不该陪着等
        self._notify(text, True)
        seg = Segment(id=seg_id, stream_id=self._stream_id,
                      text=text, clauses=clauses)
        self._buffer.on_segment(seg)
        if self._spec.deliver.emit:
            self._delivers += 1
            self._spawn(self._deliver(seg, db, low_conf))

    async def _deliver(self, seg: Segment, db: Optional[float],
                       low_conf: str) -> None:
        spec = self._spec.deliver
        prio = self._priority if self._priority is not None else int(spec.priority)
        # 两个数在 _commit 里就结好了(段已切走, 这里只看账): 这一整段多响 +
        # 哪几个字可能听错. 只有听侧有, 所以在这里随包发出 —— 核不重算,
        # 脑子只读属性.
        await self._emit(
            id=seg.id,                    # 与起话包同 id → 吸收填坑
            source=self._source,
            text=seg.text,
            priority=prio,
            strength=self._strength,
            complete=True,
            mode=spec.mode,
            interrupt=spec.interrupt,
            effort='normal',              # 交付永远要被回应(见 onset 处注释)
            low_conf=low_conf,
            db=db,
            # 交付包 = 这一段的收尾(原版 ``paused=True``); 中间的 partial
            # 不上行, 所以现在只有"说完了"这一种。留着这个字段是因为它
            # 是**边界信息**而不是废话: 哪天中间态也进脑子, 模型得能分辨
            # "他还在说, 别抢答"。
            paused=True,
            description='listener:deliver',
        )

    # ── 听侧专有的两个读数 ──

    def _sample_level(self) -> None:
        """采一次响度(段内累加; 探针没挂/没读到就跳过)."""
        if self._level is None:
            return
        try:
            db = self._level()
        except Exception:              # 探针坏了不能拖垮耳朵
            _log.exception('listener level probe failed')
            return
        if db is None:
            return
        self._level_sum += float(db)
        self._level_count += 1

    def _segment_db(self) -> Optional[float]:
        """本段到此刻的平均响度(dB); 还没量到声音 → ``None``(不是 0)."""
        if not self._level_count:
            return None
        return round(self._level_sum / self._level_count, 1)

    def _low_confidence_chars(self, threshold: Optional[float],
                              clauses: Optional[list] = None) -> str:
        """词级置信度低于 ``threshold`` 的字(拼成一串; 阈值 None → 空)。

        **只列字不列分数**: 模型要的是"这里可能是同音字, 该猜就猜",
        不是一份置信度报表。分数给它反而会诱发"这句我不确定所以问一句"
        这种没必要的回问。

        ``clauses`` 给定时读它(交付包传这一段的分句); 不给则读当前累积的
        —— 交付发生在 ``_commit`` 之后, 那时 ``_clauses`` 已经清空了,
        所以必须由调用方把这一段带进来。
        """
        if threshold is None:
            return ''
        chars: list = []
        for clause in (self._clauses if clauses is None else clauses):
            for w in clause.words:
                # conf 为 None = 引擎没报, 不是低分 —— 不许当可疑字列出来
                if w.conf is not None and w.conf < threshold:
                    chars.append(w.text)
        return ''.join(chars)

    def _close_segment_loudness(
            self, clauses: Optional[list] = None) -> tuple[Optional[float], str]:
        """段尾结算: 响度 + 低置信字, 然后清零(下一段从零攒)。

        尾包是唯一带 ``db`` 的包 —— 分句只说"现在多响", 整段多响属于段尾
        (原版 ``_close_segment_loudness`` 同语义)。清零放在这里而不是
        ``_close_segment``: 结算与清账是同一件事, 分开放会出现"读了没清"
        或"清了没读"的半截状态。
        """
        db = self._segment_db()
        low_conf = self._low_confidence_chars(
            self._spec.deliver.low_confidence, clauses)
        self._level_sum = 0.0
        self._level_count = 0
        self._last_db = db
        if db is not None:
            _log.info('listener loudness db=%.1f (low_conf=%r)', db, low_conf)
        return db, low_conf

    def _close_segment(self) -> None:
        if self._judge is not None:
            self._judge.close()
        self._judge = None
        self._seg_id = ''
        self._clauses = []
        self._text = ''
        self._first_sent = False

    # ── 运行期控制面 ──

    def active(self) -> EtiquetteSpec:
        return self._spec

    def config(self) -> EtiquetteConfig:
        return self._cfg

    def set_etiquette(self, name: str) -> str:
        """切礼仪：拆掉当前未交付的段（原版切礼仪 = cancel session），换配置。"""
        spec = self._cfg.get(name)
        if spec is None:
            return f'etiquette {name!r} not defined'
        self._close_segment()
        self._cfg.activate(name)
        self._spec = spec
        self._buffer.resize(spec.deliver.history)
        _log.info('etiquette -> %s (onset=%s stop.silence=%s deliver=%s)',
                  name, spec.onset.emit, spec.stop.silence,
                  'push' if spec.deliver.emit else 'retain')
        return name

    def upsert_etiquette(self, spec_json: str) -> str:
        """按 JSON 增/改一种礼仪（模型 ``set_etiquette_spec`` 的同构面）。"""
        spec = EtiquetteSpec.model_validate_json(spec_json)
        self._cfg.upsert(spec)
        return spec.name

    def transcript(self, n: int = 0) -> Dict[str, Any]:
        """拉读留存槽：``enabled`` = 当前礼仪不自动交付（observer 类）。"""
        cur = self._buffer.peek_current()
        recent = self._buffer.peek_recent(n or self._spec.deliver.history)
        return {
            'enabled': not self._spec.deliver.emit,
            'current': cur.to_dict() if cur else None,
            'recent': [s.to_dict() for s in recent],
            'forgotten': self._buffer.forgotten(),
        }

    async def send_now(self) -> str:
        """人类强发送（输入法语义）。

        - 自动交付礼仪：立刻 force commit 当前段（走正常交付路径）；
        - 留存礼仪：把留存全部取走，作为一条交付包发出去（空 = 无信号）。
        """
        if self._spec.deliver.emit:
            if self._judge is not None:
                self._judge.commit_now()
                return 'committed'
            return 'nothing to commit'
        items = self._buffer.drain()
        if not items:
            return 'buffer empty'
        text = ''.join(s.text for s in items)
        # 留存礼仪没有判停那次 _commit, 定稿要在这里补 —— 否则 UI 的
        # "你:" 行永远停在 partial 状态
        self._notify(text, True)
        # 结账口径与自动交付一致: 解冻的这几段当作一段, 响度取这段期间的
        # 采样均值, 低置信字扫这几段的分句.
        db, low_conf = self._close_segment_loudness(
            [c for s in items for c in s.clauses])
        await self._emit(id=uuid4().hex, source=self._source, text=text,
                         priority=int(Priority.INFO), strength=self._strength,
                         complete=True, mode='notify', interrupt=False,
                         low_conf=low_conf, db=db, paused=True,
                         description='listener:send-now')
        return f'sent {len(items)} segments'

    def snapshot(self) -> Dict[str, Any]:
        """状态快照（进 notice / req status）。"""
        return {
            'etiquette': self._spec.name,
            'listening': not self._closed,
            'segment': self._seg_id or None,
            'onsets': self._onsets,
            'delivers': self._delivers,
            'commits': self._commits,
            'last_score': (self._last_score.score
                           if self._last_score is not None else None),
            # 上一段的响度(dB, 段尾结算那一个); None = 还没结过
            'db': self._last_db,
            'forgotten': self._buffer.forgotten(),
            'idle_s': round(time.monotonic() - self._last_activity, 1),
        }

    # ── 收摊 ──

    def stop(self) -> None:
        self._closed = True
        self._close_segment()
        for t in list(self._bg):
            t.cancel()
        self._bg.clear()

    async def aclose(self) -> None:
        """停 + 收尸（cancel 后必须 await，否则 loop 关闭时报 pending）。"""
        self.stop()
        tasks = list(self._bg)
        self._bg.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ── 内部 ──

    def _spawn(self, coro: Awaitable[Any]) -> None:
        if self._closed:
            if asyncio.iscoroutine(coro):
                coro.close()
            return
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

        def _report(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                _log.error('listener emit failed: %r', exc)

        task.add_done_callback(_report)
