"""list_skills — 列出所有可用 skill 的名称和简介(轻量元数据,不加载完整内容)

通用层 routine: 通过 ctx.req 反向获取 agent_state.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field
from routine import Routine

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import build_registry


class ListSkillsInput(BaseModel):
    pass


class ListSkillsOutput(BaseModel):
    skills: List[Dict[str, str]]


def do_list(skill_dir: str | None) -> Dict[str, Any]:
    """纯函数: 列出 skill_dir 下所有 skill 的元数据."""
    reg = build_registry(skill_dir)
    reg.rescan()
    skills = []
    for s in reg.list_skills():
        entry: Dict[str, str] = {'name': s.name, 'description': s.description}
        if s.version:
            entry['version'] = s.version
        if s.source:
            entry['source'] = s.source
        skills.append(entry)
    if not skills:
        return {'skills': [], 'message': 'No skills found.'}
    return {'skills': skills}


class ListSkills(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'List all available skills (name + short description). Use load_skill to load a skill\'s full instructions.',
        'input_schema': ListSkillsInput.model_json_schema(),
        'output_schema': ListSkillsOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        return do_list(skill_dir)
