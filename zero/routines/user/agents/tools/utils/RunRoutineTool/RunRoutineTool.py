"""RunRoutine ---- 通用 routine 执行器,跨 conn 调任意已注册 routine.

底层是 submit+start+wait_for(timeout):kernel 路由表查 name 所属 conn,跨 conn wire
转发给目标 host 执行,agent 不感知 host 边界.超时后 stop 挂住的 routine.

设计要点:
- 不限制目标 host:name 在 kernel 路由表里查到哪个 conn 就转发到哪.
- 失败(not found / routine 抛异常 / start 被拒 / 超时)以 ``error`` 字段返回,不抛异常
  打断 agent.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field, ValidationError
from routine import Routine, SubmitError

from zero.routines.user.agents._core.paths import AGENT_ID_KEY
from .prompt import DESCRIPTION


class RunRoutineInput(BaseModel):
    name: str = Field(
        description=(
            'Name of the registered routine to invoke. Must match a routine name. '
        ),
    )
    kwargs: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            'Input dict for the routine. Must match the routine input_schema if '
            'declared in its meta. Default empty dict for no-arg routines.'
        ),
    )


class RunRoutineOutput(BaseModel):
    result: Dict[str, Any] = Field(
        default_factory=dict,
        description='Routine return value (whatever the routine run() returned).',
    )


class RunRoutine(Routine):
    """Invoke any registered routine by name (cross-conn via kernel router).

    submit + start + wait_for(timeout): kernel routes by name → owning conn,
    returns the routine result, or {error: ...} on failure / timeout.
    does not raise: failures surfaced as error field for the LLM.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': RunRoutineInput.model_json_schema(),
        'output_schema': RunRoutineOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    # routine 执行超时(秒).超时后 stop 挂住的 routine,返 error 给 LLM.
    # 防止 test_echo 等设计为"block until stop"的 routine 通过 run_routine 调用后
    # 永不返回,导致 shell.join() 卡死 -> React 循环停摆 -> 前端永远 running.
    _TIMEOUT = 300

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        name = kwargs.get('name')
        try:
            inp = RunRoutineInput(**kwargs)
            call_kwargs = dict(inp.kwargs)
            agent_id = kwargs.pop(AGENT_ID_KEY, None)
            if agent_id:
                call_kwargs[AGENT_ID_KEY] = agent_id
            # 用 submit+start+wait_for 替代无超时的 self.call(),
            # 超时后 handle.stop(fire=True) 清理挂住的 routine.
            handle = await self.submit(inp.name, call_kwargs)
            await handle.start()
            try:
                result = await asyncio.wait_for(handle, timeout=self._TIMEOUT)
            except asyncio.TimeoutError:
                await handle.stop(fire=True)
                self._logger.warning('run_routine: %s timed out (%ds)', name, self._TIMEOUT)
                return {
                    'error': f'timeout: routine {name!r} did not complete within {self._TIMEOUT}s',
                    'for_llm': f'routine {name!r} 执行超时({self._TIMEOUT}s),已停止.',
                }
        except ValidationError as exc:
            self._logger.warning('run_routine: %s validation failed: %s', name, exc)
            return {
                'error': f'ValidationError: {exc}',
                'for_llm': f'参数校验失败: {exc}. 请检查 name 和 kwargs 是否符合 RunRoutineInput schema (name 必填, kwargs 为 dict).',
            }
        except SubmitError as exc:
            self._logger.warning('run_routine: %s submit failed: %s', name, exc)
            return {
                'error': f'SubmitError: {exc}',
                'for_llm': f'routine {name!r} 未注册或被 kernel 拒绝 submit: {exc}. 请检查 routine 名称拼写, 或确认 routine 已加载.',
            }
        except Exception as exc:
            self._logger.warning('run_routine: %s failed: %s', name, exc, exc_info=True)
            return {
                'error': f'{type(exc).__name__}: {exc}',
                'for_llm': f'routine {name!r} 执行报错: {type(exc).__name__}: {exc}.',
            }
        self._logger.info('run_routine: %s ok', inp.name)
        return result
