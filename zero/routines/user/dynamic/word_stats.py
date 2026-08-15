"""WordStats ---- 文本统计 routine: 字符/单词/行数 + 词频 Top-N.

演示 routine-creator 闭环: dynamic 实验场 -> watcher 热注册 -> 自验.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine import Routine


class WordStatsInput(BaseModel):
    text: str = Field(description='要统计的文本')
    top_n: int = Field(default=5, description='词频榜显示前 N 个')


class WordStatsOutput(BaseModel):
    chars: int = Field(description='字符数 (含空白)')
    chars_no_space: int = Field(description='字符数 (不含空白)')
    words: int = Field(description='单词数 (按字母数字切分)')
    lines: int = Field(description='行数')
    top_words: List[Dict[str, Any]] = Field(description='词频 Top-N: [{word, count}]')


class WordStats(Routine):
    """统计文本的字符/单词/行数与词频 Top-N."""

    name = 'word_stats'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '统计文本字符/单词/行数及词频 Top-N.',
        'input_schema': WordStatsInput.model_json_schema(),
        'output_schema': WordStatsOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = WordStatsInput.model_validate(kwargs)
        words = re.findall(r"\w+", inp.text.lower(), flags=re.UNICODE)
        top = Counter(words).most_common(max(inp.top_n, 0))
        return WordStatsOutput(
            chars=len(inp.text),
            chars_no_space=len(re.sub(r'\s', '', inp.text)),
            words=len(words),
            lines=inp.text.count('\n') + (1 if inp.text else 0),
            top_words=[{'word': w, 'count': c} for w, c in top],
        ).model_dump()
