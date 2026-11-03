from typing import Any, ClassVar, Dict
from pydantic import BaseModel, Field
from routine import Routine


class TextStatsInput(BaseModel):
    text: str = Field(description='要统计的文本')


class TextStats(Routine):
    name = 'text_stats'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '统计一段文本的字符数、词数、行数',
        'input_schema': TextStatsInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = TextStatsInput.model_validate(kwargs)
        t = inp.text
        return {
            'content': {
                'chars': len(t),
                'chars_no_space': len(''.join(t.split())),
                'words': len(t.split()),
                'lines': len(t.splitlines()) if t else 0,
            }
        }

    async def stop(self) -> None:
        pass
