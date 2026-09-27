"""agent_hub/preset/* ---- 中立预制体仓库 (list / save / delete).

预制体 = 预填的 agent 创建参数 ``{kind, params}``. 存储是纯文件
(``~/.zero/presets/<id>.yaml``, env ZERO_PRESETS_DIR 覆盖), 无状态无
hub ---- 形状对 agent 类型中立: 前端创建表单的预填层消费, params 原样
透传给各 manager, 本体系不感知任何 agent 类型语义.

与 skills 同一模式: 事实源在文件系统, 平铺 routine 系列, 一操作一 routine.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, ClassVar, Dict, List

import yaml
from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

__all__ = ['AgentHubPresetList', 'AgentHubPresetSave', 'AgentHubPresetDelete']

_log = setup_logger('agent_hub.presets')

PRESETS_DIR = Path(
    os.environ.get('ZERO_PRESETS_DIR')
    or Path.home() / '.zero' / 'presets',
).expanduser()

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]*$')  # preset id: kebab/snake 风格


class PresetSaveInput(BaseModel):
    id: str = Field(description='preset id (kebab/snake 风格)')
    label: str | None = Field(None, description='显示名 (可选)')
    description: str | None = Field(None, description='说明 (可选)')
    kind: str = Field(description='agent 类型 (agent_hub/types 的 kind)')
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description='预填的创建参数 (model/skills/各 manifest 字段)',
    )


class PresetDeleteInput(BaseModel):
    id: str = Field(description='preset id')


# ----------------------------------------------------------------------


def _list() -> Dict[str, Any]:
    presets: List[Dict[str, Any]] = []
    if PRESETS_DIR.is_dir():
        for f in sorted(PRESETS_DIR.glob('*.yaml')):
            try:
                doc = yaml.safe_load(f.read_text(encoding='utf-8')) or {}
            except Exception as exc:
                _log.warning('preset parse failed (%s): %r', f.name, exc)
                continue
            if not isinstance(doc, dict):
                continue
            presets.append({
                'id': str(doc.get('id') or f.stem),
                'label': str(doc.get('label') or f.stem),
                'description': str(doc.get('description') or ''),
                'kind': str(doc.get('kind') or ''),
                'params': doc.get('params') or {},
            })
    return {'ok': True, 'presets': presets}


def _save(data: PresetSaveInput) -> Dict[str, Any]:
    pid = data.id.strip()
    if not _ID_RE.match(pid):
        return {'ok': False, 'error': f'invalid preset id: {pid!r} '
                                      '(lowercase letters, digits, - and _)'}
    kind = data.kind.strip()
    if not kind:
        return {'ok': False, 'error': 'kind is required (agent type to prefill)'}
    doc = {
        'id': pid,
        'label': (data.label or pid).strip(),
        'description': (data.description or '').strip(),
        'kind': kind,
        'params': data.params,
    }
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    path = PRESETS_DIR / f'{pid}.yaml'
    path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
        encoding='utf-8',
    )
    _log.info('preset saved: %s (kind=%s)', pid, kind)
    return {'ok': True, 'id': pid, 'path': str(path)}


def _delete(data: PresetDeleteInput) -> Dict[str, Any]:
    pid = data.id.strip()
    if not _ID_RE.match(pid):
        return {'ok': False, 'error': f'invalid preset id: {pid!r}'}
    path = PRESETS_DIR / f'{pid}.yaml'
    if not path.is_file():
        return {'ok': False, 'error': f'preset not found: {pid}'}
    path.unlink()
    _log.info('preset deleted: %s', pid)
    return {'ok': True, 'id': pid}


# ----------------------------------------------------------------------


class _PresetRoutine(Routine):
    """平铺 routine 基座: 校验输入后分发到模块函数."""

    _op: ClassVar[str]

    async def run(self, kwargs):
        if self._op == 'save':
            return _save(PresetSaveInput.model_validate(kwargs))
        if self._op == 'delete':
            return _delete(PresetDeleteInput.model_validate(kwargs))
        return _list()


class AgentHubPresetList(_PresetRoutine):
    """列全部预制体."""

    _op = 'list'
    name = 'agent_hub/preset/list'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Neutral agent presets (prefilled create params): list all',
    }


class AgentHubPresetSave(_PresetRoutine):
    """保存/覆盖预制体."""

    _op = 'save'
    name = 'agent_hub/preset/save'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Neutral agent presets: save (upsert)',
        'input_schema': PresetSaveInput.model_json_schema(),
    }


class AgentHubPresetDelete(_PresetRoutine):
    """删除预制体."""

    _op = 'delete'
    name = 'agent_hub/preset/delete'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Neutral agent presets: delete by id',
        'input_schema': PresetDeleteInput.model_json_schema(),
    }
