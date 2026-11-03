"""听的控制层 —— 原版 ``host/listener`` 的逐层移植。

- ``events``：识别事件契约（text axis 四相位）；
- ``etiquette``：三个正交旋钮 + 11 个开箱坐标；
- ``stop_judge``：判停循环（静音 / 关键词 / 分类器）；
- ``segment_buffer``：留存槽（拉模式读）。

上层（controller：起话/交付/降级/故障自愈）与器官（``asr/``）分开：
本包只有策略，不碰麦克风、不碰网络、不碰 routine。
"""
from .controller import EmitFn, LevelFn, ListenerController
from .etiquette import (
    ClassifierSpec,
    DeliverMode,
    DeliverSpec,
    DetectSpec,
    EtiquetteConfig,
    EtiquetteSpec,
    OnsetSpec,
    StopSpec,
)
from .events import Clause, Event, Phase, Segment, Word
from .segment_buffer import HeardSegment, SegmentBuffer
from .stop_judge import ScoreCaller, StopJudge, StopScore, parse_stop_score

__all__ = [
    'Phase', 'Word', 'Clause', 'Event', 'Segment',
    'OnsetSpec', 'DeliverSpec', 'DeliverMode', 'ClassifierSpec', 'DetectSpec',
    'StopSpec', 'EtiquetteSpec', 'EtiquetteConfig',
    'ScoreCaller', 'StopJudge', 'StopScore', 'parse_stop_score',
    'HeardSegment', 'SegmentBuffer', 'EmitFn', 'LevelFn',
    'ListenerController',
]
