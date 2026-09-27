"""SysPrompt -- 拉 agent 的 system prompt 全量快照.

web_server ``@subscribe('sys_prompt')`` 缓存快照 (WS 只广播轻量通知),
本 routine 经 req 读 web_server 的 ``'sys_prompt'`` handler.
前端收到 WS 通知后调 ``POST /run_routine {name: sys_prompt, kwargs: {agent_id}}`` 拉全量.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

__all__ = ['SysPrompt']

_log = setup_logger('sys_prompt')

_BRIDGE_NAME = 'web_server'


class SysPromptInput(BaseModel):
    agent_id: str = Field(description='要拉快照的 agent_id')


class SysPromptOutput(BaseModel):
    agent_id: str
    epoch: int | None = None
    message_id: str | None = None
    messages: list[Dict[str, Any]] = []


class SysPrompt(Routine):
    """拉 agent 的 system prompt 全量快照 (web_server 缓存)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '拉 agent 的 system prompt 全量快照 (web_server 缓存). '
                       'WS sys_prompt 通知到达后调本 routine 拉全量.',
        'input_schema': SysPromptInput.model_json_schema(),
        'output_schema': SysPromptOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = SysPromptInput.model_validate(kwargs)
        empty = {'agent_id': inp.agent_id, 'messages': []}
        # 查 web_server rid (跨进程正确, 不走 inproc 直引)
        bridge_id = ''
        try:
            routines = await self.get_running_routines()
        except Exception:
            routines = []   # kernel 查询失败按无缓存处理
        for r in routines:
            if str((r or {}).get('name') or '') == _BRIDGE_NAME:
                bridge_id = str(r.get('id') or '').strip()
                break
        if not bridge_id:
            return empty
        try:
            return await self.req(bridge_id, 'sys_prompt',
                                  {'agent_id': inp.agent_id}, timeout=10.0)
        except Exception:
            return empty
