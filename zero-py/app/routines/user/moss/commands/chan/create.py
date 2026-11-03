"""chan_create —— 开轨动作: 模型的显式并行决策.

默认只有 main 一条轨(全串行). 想并行? 先发本动作开一条新轨,
回执确认后(下一轮)可用 —— 轨道是身体的并行结构, 建轨是真实
动作(普通动作一个, 挂任意已开的轨排队执行), 不是随取名免费的
语法糖.

标签第一槽的 lane 闸的是 shell 轨表, 第二槽的 verb 闸的是全局词表:
两道门, 两张嘴(词表 = registry 控制面, 轨表 = chan 控制面).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from .base import ChanCommand

__all__ = ['ChanCreate']


class ChanCreateInput(BaseModel):
    """开轨参数."""

    name: str = Field(description='新轨道名, 之后的标签第一槽可用它')


class ChanCreate(ChanCommand):
    """开轨: req shell chan(create) 建轨, 回执带全量轨名."""

    name = 'chan_create'
    op = 'create'

    meta: ClassVar[Dict[str, Any]] = {
        **ChanCommand.meta,
        'description': '开一条新轨道(默认只有 main 串行; 开轨回执确认后, '
                       '新轨上的 routine 可与 main 并行)',
        # 备注(跟着能力走): 只在 chan_create 进词表时出现.
        'example': (
            '回执会立刻叫你一拍, 那一拍 b 轨才可用(回执里带全部轨名;\n'
            '重复开同一条轨无妨, 幂等).'
        ),
        'input_schema': ChanCreateInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = ChanCreateInput.model_validate(kwargs)
        r = await self._chan({'name': inp.name})
        lanes = ', '.join(r.get('lanes') or [])
        return {'content': f'轨 {inp.name} 就绪; 现有 {lanes}', 'details': r}
