"""UserAgent -- user 身份的常驻 agent routine.

HTTP /run 无 X-Agent-Id 时转发给本 routine, 本 routine 内部 call 目标 routine
并注入 from_agent_id='user'. 这样所有 HTTP 调用都强制有 agent_id, user 跟
prime 同级, 是系统的一等公民.

未来可扩展: 鉴权 / 审计 / user 级 session / LLM 路由.
"""
from __future__ import annotations

import asyncio
import time
from asyncio import Event
from typing import Any, ClassVar, Dict

from routine import Routine, request
from routine.logger import setup_logger

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

_log = setup_logger('user_agent')

_USER_AGENT_ID = 'user'
_BRIDGE_NAME = 'web_server'


class UserAgent(Routine):
    """user 身份的常驻 agent. HTTP 无 agent_id 的调用经此转发."""

    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'user 身份常驻 agent. HTTP 无 agent_id 的调用经此转发, 注入 from_agent_id=user.',
    }

    def __init__(self):
        super().__init__()
        self._stop_event: Event | None = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        self._stop_event = Event()

    async def on_started(self) -> None:
        asyncio.create_task(self._register_with_bridge())

    async def _register_with_bridge(self) -> None:
        """重试注册到 WebServer (bridge 可能还没起来)."""
        for attempt in range(20):
            try:
                routines = await self.ctx.get_running_routines()
            except Exception:
                routines = []
            for r in routines:
                if str(r.get('name') or '') == _BRIDGE_NAME:
                    bridge_id = str(r.get('id') or '')
                    if bridge_id:
                        try:
                            await self.ctx.req(bridge_id, 'register_agent', {
                                'agent_id': _USER_AGENT_ID,
                                'namespace': _USER_AGENT_ID,
                                'name': 'User',
                                'routine_id': self.id,
                            }, timeout=2.0)
                            _log.info('user_agent registered to bridge, id=%s', self.id)
                            return
                        except Exception as exc:
                            _log.warning('register attempt %d failed: %r', attempt, exc)
                    break
            await asyncio.sleep(1.0)
        _log.warning('user_agent failed to register after 20 attempts')

    @request('run')
    async def on_run(self, source, data: dict) -> dict:
        """HTTP 转发的 routine 调用. 注入 from_agent_id='user' 后 call 目标."""
        t0 = time.perf_counter()
        target = str(data.get('target') or '')
        kwargs = data.get('kwargs') or {}
        if not target:
            return {'ok': False, 'error': 'missing target routine name'}
        if not isinstance(kwargs, dict):
            return {'ok': False, 'error': 'kwargs must be a dict'}
        kwargs[AGENT_ID_KEY] = _USER_AGENT_ID
        try:
            result = await self.call(target, kwargs)
            t1 = time.perf_counter()
            _log.info('[latency] user_agent.run target=%s call=%.1fms', target, (t1 - t0) * 1000)
            return {'ok': True, 'result': result}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    @request('agent_state')
    async def on_agent_state(self, source, data: dict) -> dict:
        """返回 user agent 的 state."""
        return {
            'agent_id': _USER_AGENT_ID,
            'project_root': None,
            'session_id': _USER_AGENT_ID,
            'skill_dir': None,
            'skill_index_cache_dir': None,
        }

    @request('get_history')
    async def on_get_history(self, source, data: dict) -> dict:
        """user 无对话历史 (消息都在目标 agent 那边), 返回空."""
        return {'ok': True, 'session_id': _USER_AGENT_ID, 'messages': []}

    async def run(self, kwargs: Dict[str, Any]):
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
