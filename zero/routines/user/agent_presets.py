"""AgentPreset -- agent preset 仓库操作 routine (list / copy / delete).

preset 仓库实现在 ``agents/_core/presets.py`` (纯文件操作, 无状态),
这里是 routine 壳: 一个 routine 一个 ``op`` 参数分发三种操作,
任何 agent 都能调用, 走 kernel 路由, 不依赖 prime manager 是否在运行.

用法::

    run_routine('agent_preset')                                          # list
    run_routine('agent_preset', kwargs={'op': 'copy', 'from': 'prime',
                                        'id': 'reviewer', 'name': 'Reviewer'})
    run_routine('agent_preset', kwargs={'op': 'delete', 'id': 'reviewer'})
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

from .agents._core.presets import copy_preset, delete_preset, list_presets

_log = setup_logger('agent_preset')


class AgentPresetInput(BaseModel):
    op: Literal['list', 'copy', 'delete'] = Field(
        'list',
        description='操作类型:list(列全部, 默认) / copy(复制到用户根, copy-only '
                    '唯一创建入口) / delete(删用户根 preset, 随附只读拒删)',
    )
    from_: str | None = Field(
        None,
        alias='from',
        description='[copy] 来源 preset id (随附或用户根均可)',
    )
    id: str | None = Field(
        None,
        description='[copy] 新 preset id (小写字母/数字/_, 字母开头, 不得与任一'
                    '根重名); [delete] 要删的用户根 preset id',
    )
    name: str | None = Field(
        None,
        description='[copy] 新显示名 (可选, 缺省沿用来源)',
    )


class AgentPresetOutput(BaseModel):
    ok: bool
    presets: List[Dict[str, Any]] | None = Field(
        None,
        description='[list] 全部 preset. 每个 item: {id, name, description, '
                    'source, path, extra_instructions}. source: '
                    'shipped(随附只读) | user(用户根可写)',
    )
    id: str | None = Field(None, description='[copy] 新 preset id')
    path: str | None = Field(None, description='[copy] 新 preset 目录绝对路径')
    error: str | None = None
    for_llm: str = Field(
        default='',
        description='[list] LLM-friendly 摘要, 每行: "id (source) name -- description"',
    )


class AgentPreset(Routine):
    """agent preset 仓库操作: list / copy / delete (copy-only 创作)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': 'agent preset 仓库操作. op=list 列全部 (随附只读 + 用户根'
                       '可写); op=copy 复制到用户根 (copy-only: 没有空白新建, 改'
                       '副本 preset.yaml 即改定义); op=delete 删用户根 (随附拒删).',
        'input_schema': AgentPresetInput.model_json_schema(),
        'output_schema': AgentPresetOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = AgentPresetInput.model_validate(kwargs)
        if inp.op == 'list':
            return self._list()
        if inp.op == 'copy':
            return self._copy(inp)
        return self._delete(inp)

    @staticmethod
    def _list() -> Dict[str, Any]:
        presets = list_presets()
        _log.info('agent_preset list: %d presets', len(presets))
        lines = [
            f"{p['id']} ({p['source']}) {p['name']} -- {p['description'] or '(no description)'}"
            for p in presets
        ]
        return {'ok': True, 'presets': presets, 'for_llm': '\n'.join(lines)}

    @staticmethod
    def _copy(inp: AgentPresetInput) -> Dict[str, Any]:
        try:
            result = copy_preset(inp.from_ or '', inp.id or '', name=inp.name)
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True, **result}

    @staticmethod
    def _delete(inp: AgentPresetInput) -> Dict[str, Any]:
        try:
            delete_preset(inp.id or '')
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True}
