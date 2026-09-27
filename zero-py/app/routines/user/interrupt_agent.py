"""InterruptAgent -- 打断指定 agent 的当前轮次.

万物皆 routine:打断与发消息同为 agent 输入,统一走 routine 入口
(与 send_message 对偶: send_message 投递, interrupt_agent 打断):

  1. resolve_agent_rid 动态查目标 agent 的 rid (不依赖易失注册表)
  2. req 目标 agent ``interrupt`` -- agent 侧 bump epoch + abort 在飞轮次
     (LLM 流 / 工具调用立即中止), 补发空 final 帧让前端停止流式状态

前端 WS / voice / agent 间代码都可调用.
"""
from __future__ import annotations

import time
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

__all__ = ['InterruptAgent']

_log = setup_logger('interrupt_agent')


class InterruptAgentInput(BaseModel):
    to: str = Field(description='目标 agent 的 id (如 prime_1)')


class InterruptAgentOutput(BaseModel):
    ok: bool = Field(description='是否已触发打断')


class InterruptAgent(Routine):
    """打断指定 agent 的当前轮次.

    用法::

        # 代码:
        result = await self.call('interrupt_agent', kwargs={'to': 'prime_1'})
        if result['ok']:
            print('interrupted')
    """

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '打断指定 agent 的当前轮次: 在飞 LLM 流 / 工具调用立即中止, '
            'agent 补发空 final 帧让前端停止流式状态. '
            'to 指定目标 agent_id (如 prime_N).'
        ),
        'input_schema': InterruptAgentInput.model_json_schema(),
        'output_schema': InterruptAgentOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        inp = InterruptAgentInput.model_validate(kwargs)
        t0 = time.monotonic()

        # 动态解析: manager live_agents 两跳查询, web_server/agent 重载后依然可用
        from app.routines.user.agents._core.rid import resolve_agent_rid
        target_rid, agent_type = await resolve_agent_rid(self, inp.to)
        if not target_rid:
            _log.warning('interrupt %s: not live or not found', inp.to)
            return {'ok': False, 'error': f'agent {inp.to} not live or not found'}
        _log.info('interrupt %s: resolved rid=%s type=%s (%.0fms)',
                  inp.to, target_rid, agent_type,
                  (time.monotonic() - t0) * 1000)

        try:
            result = await self.req(target_rid, 'interrupt', {}, timeout=10.0)
        except Exception as exc:
            _log.warning('interrupt %s: req failed (%.0fms): %r', inp.to,
                         (time.monotonic() - t0) * 1000, exc)
            return {'ok': False, 'error': str(exc)}

        dt = (time.monotonic() - t0) * 1000
        if not result.get('ok'):
            _log.warning('interrupt %s: agent returned error (%.0fms): %s',
                         inp.to, dt, result.get('error'))
            return {'ok': False, 'error': result.get('error') or 'agent error'}
        _log.info('interrupt %s: done (%.0fms) -> %s', inp.to, dt, result)
        return {'ok': True}
