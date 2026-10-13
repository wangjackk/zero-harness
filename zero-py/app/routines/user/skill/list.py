"""skill/list — 列出本 agent workspace 里的 skill (名称 + 简介, 轻量元数据).

只看自己的 (skill_dir); repo 是本地池子, 不强加给 agent ——
看池子用 skill/repo/list, 需要哪个用 skill/repo/pull 下载.

通用层 routine: 通过 agent/state 反向获取 agent_state.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel
from routine import Routine

from app.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import SkillRegistry

__all__ = ['SkillsList']


class SkillsListInput(BaseModel):
    pass


class SkillsListOutput(BaseModel):
    skills: List[Dict[str, str]]


def do_list(skill_dir: str | None) -> Dict[str, Any]:
    """纯函数: 列出 workspace 的 skill 元数据."""
    reg = SkillRegistry(dirs=[Path(skill_dir)] if skill_dir else [])
    reg.rescan()
    skills = []
    for s in reg.list_skills():
        entry: Dict[str, str] = {'name': s.name, 'description': s.description}
        if s.version:
            entry['version'] = s.version
        skills.append(entry)
    if not skills:
        return {
            'skills': [],
            'message': 'No skills installed. Use skill/repo/list to see the shared '
                       'library, then skill/repo/pull to install one.',
        }
    return {'skills': skills}


class SkillsList(Routine):
    name = 'skill/list'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'List this agent\'s installed skills (name + short description). '
                       'The shared library is NOT included here: use skill/repo/list '
                       'to browse it and skill/repo/pull to install from it. '
                       'Use skill/load to load a skill\'s full instructions.',
        'input_schema': SkillsListInput.model_json_schema(),
        'output_schema': SkillsListOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('agent/state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        return do_list(skill_dir)
