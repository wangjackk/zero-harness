"""list_routines ---- 列出 routine.

kind=registered(默认):所有已注册 routine(发现可调 routine 用).
kind=running:当前运行中的 routine 实例(按 name 找对端 id 用).

调用示例:
    curl -XPOST localhost:7780/run/list_routines -d '{}'
    curl -XPOST localhost:7780/run/list_routines -d '{"kind":"running"}'
    run_routine({name: 'list_routines', kwargs: {}})
    run_routine({name: 'list_routines', kwargs: {kind: 'running'}})
"""
from enum import Enum
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine import Routine


class RoutineKind(str, Enum):
    """list_routines 的 kind 参数:str-enum 让 pydantic 生成 JSON schema 的 enum."""

    REGISTERED = 'registered'
    RUNNING = 'running'


class ListRoutinesInput(BaseModel):
    """list_routines 输入:kind 控制返回已注册 routine 还是运行中实例."""

    kind: RoutineKind = Field(
        RoutineKind.REGISTERED,
        description='registered=已注册 routine(默认); running=运行中实例',
    )


class ListRoutinesOutput(BaseModel):
    routines: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='routine 列表(原始). kind=registered 返回 '
                    '[{name, hub_id, is_passive}, ...]; '
                    'kind=running 返回 [{name, id}, ...]. '
                    'hub_id 是进程级身份(如 "zero"/"one")',
    )
    for_llm: str = Field(
        default='',
        description='LLM-friendly text summary: 按 hub 聚合的 routine 列表 + 数量统计. '
                    '格式如 "5 routines, 2 hubs:\\n  zero(3, 1p): a, b, c*\\n  one(2): d, e". '
                    '(* = passive).适合直接喂给 LLM.',
    )


class ListRoutines(Routine):
    """列出 routine(已注册 / 运行中)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '列出 routine(已注册 / 运行中).kind=registered(默认)|running',
        'input_schema': ListRoutinesInput.model_json_schema(),
        'output_schema': ListRoutinesOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = ListRoutinesInput.model_validate(kwargs)
        if inp.kind is RoutineKind.RUNNING:
            return await self._list_running()
        return await self._list_registered()

    async def _list_registered(self) -> Dict[str, Any]:
        try:
            routines = await self.get_routines()
        except NotImplementedError:
            return {
                'error': 'get_routines not implemented for this transport',
                'routines': [],
                'for_llm': '',
            }
        except Exception as exc:
            self._logger.warning('list_routines(registered) failed: %s', exc)
            return {
                'error': f'{type(exc).__name__}: {exc}',
                'routines': [],
                'for_llm': '',
            }
        self._logger.info('list_routines(registered): %d routines', len(routines))
        return {'routines': routines, 'for_llm': _build_llm_summary(routines)}

    async def _list_running(self) -> Dict[str, Any]:
        try:
            routines = await self.get_running_routines()
        except NotImplementedError:
            return {
                'error': 'get_running_routines not implemented for this transport',
                'routines': [],
                'for_llm': '',
            }
        except Exception as exc:
            self._logger.warning('list_routines(running) failed: %s', exc)
            return {
                'error': f'{type(exc).__name__}: {exc}',
                'routines': [],
                'for_llm': '',
            }
        self._logger.info('list_routines(running): %d routines', len(routines))
        return {'routines': routines, 'for_llm': _build_running_summary(routines)}


def _build_llm_summary(routines: List[Dict[str, Any]]) -> str:
    """把已注册 routine 列表按 hub 聚合成文本摘要(给 LLM 看).

    格式示例::

        5 routines, 2 hubs:
          zero(3, 1p): list_routines, register_routine, web_server*
          one(2): echo, dag_translator

    (* = passive; Np = N 个 passive routine)
    """
    if not routines:
        return '0 routines'

    # 按 hub 聚合(name 列表 + passive 计数)
    hubs: Dict[str, List[str]] = {}
    passive_counts: Dict[str, int] = {}
    for r in routines:
        hub = r.get('hub_id', '') or '?'
        name = r.get('name', '')
        is_passive = bool((r.get('is_passive') or {}).get('flag', False))
        hubs.setdefault(hub, []).append(name + ('*' if is_passive else ''))
        passive_counts[hub] = passive_counts.get(hub, 0) + (1 if is_passive else 0)

    total = len(routines)
    lines = [f'{total} routines, {len(hubs)} hubs:']
    for hub, names in hubs.items():
        pc = passive_counts[hub]
        ptag = f', {pc}p' if pc > 0 else ''
        lines.append(f'  {hub}({len(names)}{ptag}): {", ".join(names)}')

    return '\n'.join(lines)


def _build_running_summary(routines: List[Dict[str, Any]]) -> str:
    """把运行中 routine 实例聚合成文本摘要(给 LLM 看).

    格式示例::

        3 running routines:
          act#42
          agent#7
          web_server#3
    """
    if not routines:
        return '0 running routines'

    total = len(routines)
    lines = [f'{total} running routines:']
    for r in routines:
        name = r.get('name', '')
        rid = r.get('id', '')
        lines.append(f'  {name}#{rid}' if rid else f'  {name}')

    return '\n'.join(lines)
