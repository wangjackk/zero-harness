"""text_stat ---- CHALLENGE textstat 的 zero 侧正式 routine.

契约见 .bridge/CHALLENGE.md: lines/words/chars/chars_no_ws/unique_words/top5/avg_line_len.
归一化: split() -> lower() -> 仅去首尾 ASCII string.punctuation -> 空 token 丢弃.
"""
from __future__ import annotations

import string
from collections import Counter
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine import Routine


class TextStatInput(BaseModel):
    text: str = Field(description='要统计的文本')


class TextStatOutput(BaseModel):
    lines: int = Field(description='len(text.splitlines())')
    words: int = Field(description='按空白分词的原始 token 数')
    chars: int = Field(description='字符数 (含空白)')
    chars_no_ws: int = Field(description='去掉所有空白字符后的长度')
    unique_words: int = Field(description='归一化后不同词的数量')
    top5: List[List[Any]] = Field(description='词频 Top5: [[word, count], ...] count 降序, 同频按词升序')
    avg_line_len: float = Field(description='chars/lines 四舍五入 2 位; 空文本为 0')


def _normalize_tokens(text: str) -> List[str]:
    out = []
    for tok in text.split():
        w = tok.lower().strip(string.punctuation)
        if w:
            out.append(w)
    return out


class TextStat(Routine):
    """按 CHALLENGE 契约统计文本: 行数/词数/字符/唯一词/词频 Top5/平均行长."""

    name = 'text_stat'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '文本统计 (lines/words/chars/unique/top5/avg_line_len), 归一化仅去 ASCII 标点.',
        'input_schema': TextStatInput.model_json_schema(),
        'output_schema': TextStatOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = TextStatInput.model_validate(kwargs)
        text = inp.text
        lines = len(text.splitlines())
        chars = len(text)
        norm = _normalize_tokens(text)
        counter = Counter(norm)
        top5 = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        return TextStatOutput(
            lines=lines,
            words=len(text.split()),
            chars=chars,
            chars_no_ws=sum(1 for c in text if not c.isspace()),
            unique_words=len(counter),
            top5=[[w, c] for w, c in top5],
            avg_line_len=round(chars / lines, 2) if lines else 0,
        ).model_dump()
