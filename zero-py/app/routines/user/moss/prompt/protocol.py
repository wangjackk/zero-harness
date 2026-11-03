"""protocol —— 协议面片段: 教脑子 CTML(语法 + 排序 + 边界 + 观测).

文本在同目录 ``protocol.md``, 装配时读一次 —— 改教学不改代码.
稳定段, 排 persona 后 capability 前.

**这里只放与具体能力无关的东西**: 标签形态/轨道/观测回灌/红线. 某个
routine 怎么用(示例、陷阱、参数)是它自己的 ``meta['example']`` ——
"提示词跟能力在一起", 能力没进词表时它那段也不出现.

**用语只有一个**: 词表里的名字都是 routine("万物皆 routine"), 所以本文
只说"叫一个 routine", 不引入"动作/命令"这第二种名词 —— 一旦出现两种
名词, 模型就会以为存在两种调用方式.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict

from routine.routine import Routine

__all__ = ['Protocol']


class Protocol(Routine):
    """一次性 prompt routine: CTML 协议教学(读 protocol.md)."""

    name = 'prompt/protocol'

    meta: ClassVar[Dict[str, Any]] = {
        'description': 'prompt 片段: CTML 协议(语法/轨道/边界/观测)',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        return Path(__file__).with_name('protocol.md').read_text(
            encoding='utf-8')

    async def stop(self) -> None:
        pass
