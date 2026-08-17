"""SendMessage -- 单向异步给指定 agent 发一条消息.

万物皆 routine:agent 间聊天走 req (不经 manager 转发):

  1. call get_agent_rid 查目标 agent 的 rid
  2. req 目标 agent ``chat_message`` {message, from_agent} -- 只触发 _on_input, 立即返回, 不等回复

接收方 agent 处理完后会主动调 send_message 回复, 形成异步往返.
LLM 可当 tool 调用 (act body_shell 派发), 也可代码层 ctx.call.

发送方身份: tool routine 被 agent push 时框架注入 ``AGENT_ID_KEY``
(调用方 agent 的 agent_id), 直接作为 ``from`` 传给接收方. 非 agent 上下文
调用 (无注入) 不传.
"""
from __future__ import annotations

import time
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

from zero.routines.user.agents._core.messaging import PRIORITY_LOW
from zero.routines.user.agents._core.paths import AGENT_ID_KEY

_log = setup_logger('send_message')


class SendMessageInput(BaseModel):
    model_config = {'populate_by_name': True}
    to: str = Field(description='目标 agent 的 id (如 prime_1)')
    message: str = Field(description='要发给目标 agent 的消息文本')
    from_: str | None = Field(
        default=None,
        alias='from',
        json_schema_extra={'x-hidden': True},
        description='发送方 agent_id (框架注入, LLM 不填).',
    )


class SendMessageOutput(BaseModel):
    ok: bool = Field(description='是否发送成功')
    epoch: int = Field(description='目标 agent 本轮的 epoch')


class SendMessage(Routine):
    """单向异步给指定 agent 发消息.

    用法::

        # 代码:
        result = await self.call('send_message', kwargs={
            'to': 'prime_1', 'message': '你好',
        })
        if result['ok']:
            print('sent, epoch:', result['epoch'])
    """

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '单向异步给指定 agent 发一条消息, 立即返回, 不等回复. '
            '接收方 agent 处理完后会主动调本 routine 回复. '
            'to 指定目标 agent_id (如 prime_N).'
        ),
        'input_schema': SendMessageInput.model_json_schema(),
        'output_schema': SendMessageOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        t0 = time.perf_counter()
        # 显式 from 优先; 未显式传时回退到框架注入的 from_agent_id.
        if not kwargs.get('from'):
            from_agent_id = kwargs.get(AGENT_ID_KEY)
            if from_agent_id:
                kwargs['from'] = from_agent_id
        inp = SendMessageInput.model_validate(kwargs)

        # 1. 查目标 agent rid
        from zero.routines.user.agents._core.rid import resolve_agent_rid
        target_rid, _agent_type = await resolve_agent_rid(self, inp.to)
        if not target_rid:
            return {'ok': False, 'error': f'agent {inp.to} not live or not found'}
        t1 = time.perf_counter()

        # 2. req 目标 agent chat_message (单向: 只触发 _on_input, 立即返回)
        # priority: user 发的立即触发 (高), agent 发的排队等 idle (低, 不打断).
        from zero.routines.user.agents._core.messaging import PRIORITY_HIGH
        priority = PRIORITY_HIGH if inp.from_ == 'user' else PRIORITY_LOW
        payload: dict[str, Any] = {'message': inp.message, 'priority': priority}
        if inp.from_:
            payload['from'] = inp.from_
        try:
            result = await self.req(
                target_rid, 'chat_message', payload, timeout=10.0,
            )
        except Exception as exc:
            _log.warning('req agent %s failed: %r', target_rid, exc)
            return {'ok': False, 'error': str(exc)}
        t2 = time.perf_counter()

        if not result.get('ok'):
            return {'ok': False, 'error': result.get('error') or 'agent error'}
        _log.info(
            '[latency] send_message from=%s to=%s lookup=%.1fms req=%.1fms total=%.1fms epoch=%s',
            inp.from_ or '?', inp.to,
            (t1 - t0) * 1000, (t2 - t1) * 1000, (t2 - t0) * 1000,
            result.get('epoch'),
        )
        # 发送方广播:消息已投递,供订阅者(特效/审计/转发)实时感知.
        await self.publish('message_sent', {
            'from': inp.from_ or 'user',
            'to': inp.to,
            'message': inp.message,
            'epoch': result.get('epoch') or 0,
        }, namespace=inp.to)
        return {'ok': True, 'epoch': result.get('epoch') or 0}
