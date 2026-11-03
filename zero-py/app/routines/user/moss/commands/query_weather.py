"""query_weather —— 查天气动作(假数据桩).

演示"查询类动作出厂声明观测"的样板: meta 里 ``observe=True``
(原版 ``CommandMeta.always_observe`` 同构, 我们三侧同名 observe) —— 模型调它不必写
``observe="true"``, 结果一到就自带注意债, 迟到了也会补一拍把回执
交回模型眼前. 2s 模拟 API 延迟(够长以演示"拍已收、结果才到"),
真实实现换 HTTP 调用即可, 契约不变.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine.routine import Routine

__all__ = ['QueryWeather']


class QueryWeatherInput(BaseModel):
    """查天气参数."""

    city: str = Field(description='城市名, 如 "北京"')


class QueryWeather(Routine):
    """查天气(假桩): 2s 模拟延迟, 回固定假数据."""

    name = 'query_weather'

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '查询指定城市当前天气(约 2 秒; 结果会自动回到你眼前, '
            '不必再续一拍)'),
        'input_schema': QueryWeatherInput.model_json_schema(),
        # 查询类动作的出厂声明(原版 CommandMeta.always_observe 同构, 键名与
        # 标签属性同名 observe): 结果天生该被看 —— shell 装配期扫到, 自带注意债
        'observe': True,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = QueryWeatherInput.model_validate(kwargs)
        await asyncio.sleep(2.0)   # 模拟 API 往返(够长以演示"漂着")
        w = {
            'city': inp.city,
            'temp': '12℃',
            'weather': '晴',
            'wind': '北风 2 级',
            'aqi': '良',
        }
        return {'content': (f'{w["city"]}: {w["weather"]} {w["temp"]}, '
                            f'{w["wind"]}, 空气{w["aqi"]}'),
                'details': w}
