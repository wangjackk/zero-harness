"""PigLatin ---- 把英文文本转成 Pig Latin 文字游戏.

规则 (经典版):
  - 辅音开头的单词: 把开头辅音串移到词尾加 "ay"  -> happy -> appyhay
  - 元音开头的单词: 直接加 "way"                  -> apple -> appleway
  - 保留首字母大小写, 非字母字符原样通过
"""
from __future__ import annotations

import re
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine

_WORD_RE = re.compile(r"[A-Za-z]+")
_VOWELS = set('aeiouAEIOU')


def _translate_word(word: str) -> str:
    if word[0] in _VOWELS:
        out = word + 'way'
    else:
        m = re.match(r'[^aeiouAEIOU]+', word)
        cut = m.end() if m else 1
        out = word[cut:] + word[:cut] + 'ay'
    # 保留首字母大写风格
    if word[0].isupper():
        out = out[:1].upper() + out[1:].lower()
        # 若原词全大写, 结果也全大写
        if word.isupper():
            out = out.upper()
    return out


class PigLatinInput(BaseModel):
    text: str = Field(description='要翻译的英文文本')


class PigLatinOutput(BaseModel):
    translated: str = Field(description='Pig Latin 结果')
    word_count: int = Field(description='被翻译的单词数')


class PigLatin(Routine):
    """英文 -> Pig Latin 翻译器, 演示 dynamic routine 完整闭环."""

    name = 'pig_latin'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '把英文文本翻译成 Pig Latin 文字游戏',
        'input_schema': PigLatinInput.model_json_schema(),
        'output_schema': PigLatinOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = PigLatinInput.model_validate(kwargs)
        count = 0

        def repl(m: re.Match) -> str:
            nonlocal count
            count += 1
            return _translate_word(m.group(0))

        translated = _WORD_RE.sub(repl, inp.text)
        return PigLatinOutput(
            translated=translated, word_count=count,
        ).model_dump()
