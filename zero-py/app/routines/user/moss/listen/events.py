"""听侧识别事件契约（text axis）—— 原版 ``contracts/asr.py`` 的保真移植。

只保留文本轴，不带 audio 存档（原版 ``RecognitionSegment.audio`` 是给
``precise_cut`` 回切音频用的，mossv2 不做音频回放）。

相位语义与原版逐条同构：

- ``FIRST``：文本首次非空的第一个包。**不是**段/turn 标记，只说明"识别器
  有字了"。引擎不产它，由识别层自己推导，一段最多一次。
- ``PARTIAL``：说话中的中间态（``definite=false``），full-replace。仅在文本
  真的变了才发 —— 引擎每个音频包都会重发累计文本，相同文本不重复发。
- ``CLAUSE``：定稿分句（``definite=true``），每句只发一次，不被 partial
  去重挡掉。
- ``TAIL``：段尾（帧级 ``is_last_package=true``），标记一段结束并触发切段。

两个不变量（原版文档明确钉死）：

1. ``CLAUSE`` 是引擎的**协议义务** —— 静音够 ``end_window_size`` 就必须把
   当前句定稿为 definite，由 VAD 决定；
2. ``TAIL`` **不是** VAD 决定的 —— 它只由 ``commit()``（发送侧负序号）或音频
   输入关闭产生。光有静音永远不产生 TAIL。

``definite`` 只说明"这句稳定了"，不表示流结束；流结束是另一个事实。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

__all__ = ['Phase', 'Word', 'Clause', 'Event', 'Segment']


class Phase(str, Enum):
    """识别流的文本相位（值与火山 SAUC 语义对齐，见模块 docstring）。"""

    FIRST = 'first'
    PARTIAL = 'partial'
    CLAUSE = 'clause'
    TAIL = 'tail'


@dataclass
class Word:
    """一个"字"及其词级置信度。

    ``conf`` 是引擎给的逐字置信度；``None`` = 引擎没报（按"未知"处理，
    不能当"低置信"）。字段名 ``conf`` 与火山 ASR ``utterances[].words[].conf``
    一致（说侧 TTS 字幕那边叫 ``confidence``，两侧各持其名，不强行统一）。
    """

    text: str = ''
    conf: float | None = None


@dataclass
class Clause:
    """引擎 VAD 定稿的一句（text axis）。

    ``text`` 是**这一句自己的文本**，不是累计全文（累计全文在 ``Event.text``）。
    ``additional`` 是引擎原始 ``additions``（说话人/情绪/音量/语速/语种…），
    原样透传，下游想用就有。
    """

    text: str = ''
    start_ms: int = 0
    end_ms: int = 0
    additional: dict = field(default_factory=dict)
    words: list[Word] = field(default_factory=list)
    created: float = field(default_factory=time.time)


@dataclass
class Event:
    """识别流文本轴上的一个事件。

    ``text`` 是累计全文，full-replace，跨相位口径一致（FIRST 就是流里第一段
    有效文本）。``clause`` 只在 CLAUSE 相位有值。``segment_id`` 指向该段的
    ``Segment.id``。
    """

    stream_id: str
    segment_id: str
    phase: Phase
    text: str
    clause: Clause | None = None
    error: str = ''
    created: float = field(default_factory=time.time)


@dataclass
class Segment:
    """一段（一个 turn）的文本存档 —— 每次 TAIL 切一段。

    ``clauses`` 是该段的分句明细（就是 text axis 发出去的那些 ``Clause``
    实例），这样定稿后仍然查得到逐句文本与引擎附加信息。
    """

    id: str
    stream_id: str
    text: str = ''
    start_ms: int = 0
    end_ms: int = 0
    clauses: list[Clause] = field(default_factory=list)
    created: float = field(default_factory=time.time)
