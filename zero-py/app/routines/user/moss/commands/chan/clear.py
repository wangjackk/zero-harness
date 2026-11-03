"""chan_clear —— 清一条轨: 丢掉排队的, 停下在跑的(轨本身还在).

族内四个动作都针对一条具体的轨, 差别在后果: 开轨改拓扑 / 看轨只读 /
清轨丢活 / 删轨回收实例. 所以 ``clear`` 的老名字连同它的无参形态一起
收掉: 无参 = 清全部轨, 那不是"某条轨的操作", 它是 ``<interrupt/>``.

清空必须是即刻的: 声明 ``call_soon``, 不排在它要清的那些动作后面 ——
排在后面就什么也清不掉了(``<clear/>`` 当初为这事才带的 ``call_soon``,
取消原语的 ``interrupt_command`` 同参).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field, ValidationError

from .base import ChanCommand

__all__ = ['ChanClear']


class ChanClearInput(BaseModel):
    """清轨参数."""

    name: str = Field(description='要清空的轨道名')


class ChanClear(ChanCommand):
    """清一条轨: req shell clear(chans=[name])."""

    name = 'chan_clear'
    op = 'clear'

    meta: ClassVar[Dict[str, Any]] = {
        **ChanCommand.meta,
        # 中断类不点灯: 清完就清完了, 没有"答案"要等 —— 与 interrupt 同判.
        'observe': False,
        'description': ('清一条轨: 丢掉该轨上排队中的 routine、停下正在跑的'
                        '(轨本身还在, 之后照常可投); 全停所有轨用 interrupt'),
        # 备注(跟着能力走): 只在 chan_clear 进词表时出现.
        'example': (
            '丢掉 b 排队的活, 停下正在跑的; 轨本身不会消失(那是 chan_delete),\n'
            '要一次停所有轨用 interrupt. 清空是即刻的, 不排在它要清的那些\n'
            'routine 后面.'
        ),
        'call_soon': True,
        'input_schema': ChanClearInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        # name 是必填: 签名里必须看得出来(渲染器把"有 default"读成可选), 所以
        # 声明成 required; 校验失败换成能照着做的话(pydantic 原文太干).
        try:
            inp = ChanClearInput.model_validate(kwargs)
        except ValidationError:
            raise RuntimeError(
                'chan_clear needs name; to stop every lane use interrupt')
        r = await self._ctl('clear', {'chans': [inp.name]})
        cleared = list(r.get('cleared') or [])
        return {'content': (f'清了 {", ".join(cleared)}' if cleared
                            else f'{inp.name} 本来就是空的'),
                'details': {'cleared': cleared}}
