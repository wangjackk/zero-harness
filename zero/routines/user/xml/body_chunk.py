"""XmlRoutine body hook 用的数据类型(对标 routine3.BodyChunk + zero TextChunk).

zero 内部驱动内部实现不同(消息驱动 on_message+reorder,不是 wire body.chunk),
但 hook 签名/类型保持一致--子类 override on_body_chunk/on_body_text 的写法一致.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class BodyChunkKind(Enum):
    """body 流事件种类(三事件合一,对标 BodyChunkKind)."""

    CHUNK = 'chunk'              # 数据帧:喂 parser
    STREAM_CLOSED = 'stream_closed'  # 流终结:drain parser + 关 text segment
    ABORTED = 'aborted'          # 打断:丢 parser(本次触发时机留后续)


@dataclass
class BodyChunk:
    """统一的 body 流帧(对标 routine3.BodyChunk,精简掉 wire 字段 id/is_final).

    zero 内部由 _parse_loop 从 reorder 队列构造:on_message 收 {text,id} -> CHUNK;
    {_eof,id} -> STREAM_CLOSED.子类 override on_body_chunk 按 kind 分发.
    """

    kind: BodyChunkKind
    text: str = ''


@dataclass
class TextChunk:
    """顶层裸文本事件(对标 zero/routines/core/routine.py TextChunk).

    同一个 text segment 触发 N+1 次:N 次 is_last=False(parser 连续吐出的文本片段)+
    1 次 is_last=True(segment 收尾聚合尾包).start/end 是字节偏移(parser 提供).
    """

    text: str
    is_first: bool
    is_last: bool
    start: Optional[int] = None
    end: Optional[int] = None
