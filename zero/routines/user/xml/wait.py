"""Wait -- 延时 / barrier routine(对标 ``wait`` 命令).

``duration`` 秒后返回.无 duration 或 0 = 立即
返回(纯 barrier).不占模块.

在 ``Shell`` 编排里是**双向全局同步点**(对标 ``wait`` 命令):等所有
左兄弟完成后自己跑,自己完成后再放行右兄弟----把编排切成"wait 前 / wait 后"
两段.Shell 识别 ``wait`` name 给特殊待遇(无条件,不论 modules 是否冲突)::

    shell.push('a')
    shell.push('wait', {'duration': 0.5})  # wait 等 a 完成,再 sleep 0.5s
    shell.push('b')                         # b 等 wait 完成(无条件)
"""
import asyncio
from typing import Any, Dict

from pydantic import BaseModel, Field

from routine import Routine


def parse_duration(value) -> float:
    """解析 duration(秒) 支持 number/string,
    负数取绝对值;None / 0 / 解析失败 → 0(立即返回,纯 barrier)."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return abs(float(value))
    if isinstance(value, str):
        try:
            return abs(float(value))
        except ValueError:
            return 0.0
    return 0.0


class WaitInput(BaseModel):
    """wait input: duration seconds then return. 0/omit = pure barrier,
    returns immediately after prior siblings finish."""

    duration: float = Field(
        0.0, description='前面动作完成后需要等待多少秒',
    )


class Wait(Routine):
    """wait routine:延时 / barrier 节点."""

    meta = {
        'description': 'wait(延时/barrier)',
        'input_schema': WaitInput.model_json_schema(),
    }

    name = 'wait'

    async def run(self, kwargs: Dict[str, Any]):
        duration = parse_duration(kwargs.get('duration', 0))
        if duration > 0:
            self._logger.info('wait %s start (duration=%ss)', self.id, duration)
            await asyncio.sleep(duration)
        else:
            self._logger.info('wait %s start (barrier, no duration)', self.id)
