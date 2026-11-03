"""DAG 示例工作流(``dags/*.yaml``)依赖的演示 routine.

这些不是产品功能,而是为了让 ``dags/`` 下的示例 DAG 能端到端跑起来,
覆盖各项特性(数据流 / retry / trigger_rule / 失败聚合)的最小 fixture:

    Echo            通用回显,原样返回 kwargs
    MergeWeather    合并两路结果(演示多上游 -> 单节点的数据流)
    SummaryWeather  对合并结果做最终汇总(演示链式数据流)
    FlakyTask       前 N 次故意失败,用于演示节点级 retry
    AlwaysFail      永远失败,用于演示 trigger_rule(all_done / none_failed...)

真实业务节点应是各自领域的 routine;删除示例 DAG 时这些 fixture 可一并移除.

新框架适配:``start(kwargs)`` -> ``run(kwargs)``(返回值经 lifecycle 自动成 result);
``routine3`` -> ``routine``;``routine3.logger`` -> ``routine.logger``.
"""
from __future__ import annotations
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

logger = setup_logger('dag')


class EchoInput(BaseModel):
    pass


class EchoOutput(BaseModel):
    items: dict = Field(default_factory=dict, description='原样返回的入参字典')


class Echo(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '接收任意 kwargs,打印后原样返回(通用测试用).',
        'input_schema': EchoInput.model_json_schema(),
        'output_schema': EchoOutput.model_json_schema(),
    }
    """接收任意 kwargs,打印后原样返回(通用测试用)."""

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        logger.info('Echo: %s', kwargs)
        return kwargs


class MergeWeatherInput(BaseModel):
    a: str = Field(default='', description='北京天气描述')
    b: str = Field(default='', description='上海天气描述')


class MergeWeatherOutput(BaseModel):
    text: str = Field(description='合并后的天气句子')


class MergeWeather(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '把两个城市天气合并成一句话.',
        'input_schema': MergeWeatherInput.model_json_schema(),
        'output_schema': MergeWeatherOutput.model_json_schema(),
    }
    """把两个城市天气合并成一句话."""

    async def run(self, kwargs: Dict[str, Any]) -> str:
        a = kwargs.get('a', '')
        b = kwargs.get('b', '')
        result = f'北京天气:{a} / 上海天气:{b}'
        logger.info('MergeWeather: %s', result)
        return result


class SummaryWeatherInput(BaseModel):
    data: str = Field(default='', description='上游戏节点的合并结果文本')


class SummaryWeatherOutput(BaseModel):
    text: str = Field(description='汇总后的文本')


class SummaryWeather(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '对合并结果做最终汇总.',
        'input_schema': SummaryWeatherInput.model_json_schema(),
        'output_schema': SummaryWeatherOutput.model_json_schema(),
    }
    """对合并结果做最终汇总."""

    async def run(self, kwargs: Dict[str, Any]) -> str:
        data = kwargs.get('data', '')
        result = f'[汇总] {data}'
        logger.info('SummaryWeather: %s', result)
        return result


# 模块级计数器:让 FlakyTask 的失败次数跨"重试时的多次新实例"累计.
# 注意:重试是每次 push 新建实例(实例属性不保留),所以状态必须放模块级.
# 进程重启后归零--重新演示 retry 时需重启服务.
_flaky_state: dict[str, int] = {}


class FlakyTaskInput(BaseModel):
    fail_times: int = Field(default=1, description='前 N 次调用故意失败')
    tag: str = Field(default='default', description='区分不同节点计数器的标签')


class FlakyTaskOutput(BaseModel):
    text: str = Field(description='成功或失败的状态描述')


class FlakyTask(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '前 fail_times 次调用故意失败,之后成功--用于测试节点级 retry.',
        'input_schema': FlakyTaskInput.model_json_schema(),
        'output_schema': FlakyTaskOutput.model_json_schema(),
    }
    """前 ``fail_times`` 次调用故意失败,之后成功--用于测试节点级 retry.

    tag 用来区分不同节点的计数器;同名 tag 共享一个计数.
    """

    async def run(self, kwargs: Dict[str, Any]) -> str:
        fail_times = kwargs.get('fail_times', 1)
        tag = kwargs.get('tag', 'default')
        _flaky_state[tag] = _flaky_state.get(tag, 0) + 1
        n = _flaky_state[tag]
        logger.info('FlakyTask[%s]: 第 %d 次调用 (fail_times=%d)', tag, n, fail_times)
        if n <= fail_times:
            raise RuntimeError(f'FlakyTask[{tag}] 第 {n} 次故意失败')
        return f'FlakyTask[{tag}] 第 {n} 次成功'


class AlwaysFailInput(BaseModel):
    reason: str = Field(default='故意失败', description='失败原因')


class AlwaysFailOutput(BaseModel):
    pass


class AlwaysFail(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '总是抛错--用于测试 trigger_rule(all_done / none_failed...)与失败聚合.',
        'input_schema': AlwaysFailInput.model_json_schema(),
        'output_schema': AlwaysFailOutput.model_json_schema(),
    }
    """总是抛错--用于测试 trigger_rule(all_done / none_failed...)与失败聚合."""

    async def run(self, kwargs: Dict[str, Any]) -> str:
        reason = kwargs.get('reason', '故意失败')
        logger.info('AlwaysFail: %s', reason)
        raise RuntimeError(reason)
