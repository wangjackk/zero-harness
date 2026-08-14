"""WorldAgent -- world 身份的常驻 agent routine.

代表"客观世界"作为消息来源: UI 事件、定时器、文件变化、外部 webhook 等.
跟 user / agent 同级, 是系统的第三种身份.

链路: 客观事件 → HTTP /agents/world/run/send_message {to, message}
      → world_agent → send_message routine → 目标 agent (from='world')
"""
from __future__ import annotations

import asyncio
import time
from asyncio import Event
from typing import Any, ClassVar, Dict

from routine import Routine, request
from routine.logger import setup_logger

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

_log = setup_logger('world_agent')

_WORLD_AGENT_ID = 'world'
_BRIDGE_NAME = 'web_server'


class WorldAgent(Routine):
    """world 身份的常驻 agent. 客观事件经此转发, 注入 from_agent_id='world'."""

    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'world 身份常驻 agent. 客观事件 (UI/定时器/webhook) 经此转发, 注入 from_agent_id=world.',
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
                                'agent_id': _WORLD_AGENT_ID,
                                'namespace': _WORLD_AGENT_ID,
                                'name': 'World',
                                'routine_id': self.id,
                            }, timeout=2.0)
                            _log.info('world_agent registered to bridge, id=%s', self.id)
                            return
                        except Exception as exc:
                            _log.warning('register attempt %d failed: %r', attempt, exc)
                    break
            await asyncio.sleep(1.0)
        _log.warning('world_agent failed to register after 20 attempts')

    @request('run')
    async def on_run(self, source, data: dict) -> dict:
        """HTTP 转发的 routine 调用. 注入 from_agent_id='world' 后 call 目标."""
        t0 = time.perf_counter()
        target = str(data.get('target') or '')
        kwargs = data.get('kwargs') or {}
        if not target:
            return {'ok': False, 'error': 'missing target routine name'}
        if not isinstance(kwargs, dict):
            return {'ok': False, 'error': 'kwargs must be a dict'}
        kwargs[AGENT_ID_KEY] = _WORLD_AGENT_ID
        try:
            result = await self.call(target, kwargs)
            t1 = time.perf_counter()
            _log.info('[latency] world_agent.run target=%s call=%.1fms', target, (t1 - t0) * 1000)
            return {'ok': True, 'result': result}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    @request('agent_state')
    async def on_agent_state(self, source, data: dict) -> dict:
        """返回 world agent 的 state."""
        return {
            'agent_id': _WORLD_AGENT_ID,
            'project_root': None,
            'session_id': _WORLD_AGENT_ID,
            'skill_dir': None,
            'skill_index_cache_dir': None,
        }

    @request('get_history')
    async def on_get_history(self, source, data: dict) -> dict:
        """world 无对话历史 (消息都在目标 agent 那边), 返回空."""
        return {'ok': True, 'session_id': _WORLD_AGENT_ID, 'messages': []}

    async def run(self, kwargs: Dict[str, Any]):
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
