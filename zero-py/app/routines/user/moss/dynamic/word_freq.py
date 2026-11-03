from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine


class WordFreqInput(BaseModel):
    text: str = Field(description='要统计的文本')
    top: int = Field(default=10, description='返回词频最高的前多少个词')


class WordFreq(Routine):
    name = 'word_freq'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '统计一段文本的词频, 返回 Top N 词及出现次数',
        'input_schema': WordFreqInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        import re
        from collections import Counter

        inp = WordFreqInput.model_validate(kwargs)
        words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", inp.text.lower())
        counter = Counter(words)
        return {
            'content': {
                'total': len(words),
                'unique': len(counter),
                'top': [{'word': w, 'count': c} for w, c in counter.most_common(max(0, inp.top))],
            }
        }

    async def stop(self) -> None:
        pass
