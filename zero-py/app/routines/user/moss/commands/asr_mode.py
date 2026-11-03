"""AsrMode —— 切耳朵的听音模式: ptt(按住按钮才听, 装配默认) / always(麦常开).

一条普通指令, **自己不持有状态**: 它把模式和按钮名写给耳朵(asr), 由
耳朵记着 —— 指令退出后模式仍然有效. 这跟 `req etiquette` 是同一处真理
源, 只是给模型一个标签面:

    <asr_mode mode="ptt" button="f9"/>   # 按住 F9 说话, 松开即交付
    <asr_mode mode="always"/>            # 回到麦常开
    <asr_mode/>                          # 只查当前模式

ptt 的"按下/松开"不从这里进 —— 那是高频事件, 由按钮源(键盘 routine /
GPIO / UI)直接 ``req(asr, 'press'|'release', {'button': 名})``, 名字对
不上会被耳朵忽略(不是错误). 指令面只管"设成哪个模式".
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine

__all__ = ['AsrMode', 'AsrModeInput']

_log = setup_logger('moss.asr_mode')


class AsrModeInput(BaseModel):
    """asr_mode 入参: 两项都可省(省 = 不改)."""

    mode: str = Field(
        default='',
        description='always(麦常开) | ptt(按住按钮才听); 空 = 只查不改')

    button: str = Field(
        default='',
        description='ptt 的按钮名(press/release 要与它配对); 空 = 不改')


class AsrMode(Routine):
    """切耳朵听音模式(状态在耳朵里, 本实例只做转发)."""

    name = 'asr_mode'

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '切耳朵听音模式: mode="ptt" + button(默认 f9) = 按住才听、'
            '松开即交付(装配默认就是这个); mode="always" = 麦常开; '
            '不给参数 = 只查当前模式. 模式活在耳朵里, 切完一直有效(不跨重启)'),
        'input_schema': AsrModeInput.model_json_schema(),
        # 出厂声明 observe: 这条的两种用法都以回执为结果 —— 查模式(不给参数)
        # 就是为了看它, 切模式也要确认真切成了. 不声明等于叫模型每次多写一遍
        # observe="true", 而"切完到底在哪个模式"是它下一步说话的前提.
        'observe': True,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = AsrModeInput.model_validate(kwargs or {})
        rid = await self._asr_rid()
        if rid is None:
            # 门做不到它声称的事(切模式) → 抛. 信封改成 error 一样能看见,
            # 而且还带注意债 —— 回执里说"没起来"是把故障伪装成事实.
            raise RuntimeError('asr not running —— 耳朵没起, 模式切不了')
        body: Dict[str, Any] = {}
        if inp.mode:
            body['mode'] = inp.mode
        if inp.button:
            body['button'] = inp.button
        try:
            r = await self.req(rid, 'mode', body, timeout=10) or {}
        except Exception as exc:
            raise RuntimeError(f'set mode failed: {exc}') from None
        if not r.get('ok', True):
            raise RuntimeError(str(r.get('error') or 'mode was refused'))
        mode, button = r.get('mode'), r.get('button')
        if mode == 'ptt':
            content = 'mode=ptt' + (f', 按住 {button} 才听' if button else '')
        else:
            content = f'mode={mode}, 麦常开'
        return {'content': content, 'details': dict(r)}

    async def _asr_rid(self) -> Optional[str]:
        """按名找耳朵(常驻唯一). 指令是短命实例, 不缓存."""
        try:
            for r in await self.get_running_routines():
                if r.get('name') == 'asr':
                    return str(r['id'])
        except Exception:
            _log.exception('asr lookup failed')
        return None
