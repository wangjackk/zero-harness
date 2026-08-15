"""text_reverse ---- 文本倒叙 routine.

支持三种粒度 (mode):
  - 'chars' (默认): 按字符 (码点) 倒序
  - 'words': 按空白分词单位倒序 (词序)
  - 'lines': 按行倒序 (行序)
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field

from routine import Routine


class TextReverseInput(BaseModel):
    text: str = Field(description='要倒叙的文本')
    mode: Literal['chars', 'words', 'lines'] = Field(
        default='chars', description='倒叙粒度: chars (默认) / words / lines',
    )


class TextReverseOutput(BaseModel):
    text: str = Field(description='倒叙后的文本')
    mode: str = Field(description='实际使用的粒度')
    length: int = Field(description='结果长度')


class TextReverse(Routine):
    """文本倒叙: 按字符 / 单词 / 行粒度反转文本."""

    name = 'text_reverse'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '文本倒叙. mode=chars 按字符倒序 (默认); '
                       'mode=words 按词倒序; mode=lines 按行倒序',
        'input_schema': TextReverseInput.model_json_schema(),
        'output_schema': TextReverseOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = TextReverseInput.model_validate(kwargs)
        if inp.mode == 'chars':
            reversed_text = inp.text[::-1]
        elif inp.mode == 'words':
            reversed_text = ' '.join(inp.text.split()[::-1])
        else:  # lines
            reversed_text = '\n'.join(inp.text.splitlines()[::-1])
        return TextReverseOutput(
            text=reversed_text, mode=inp.mode, length=len(reversed_text),
        ).model_dump()
