"""skill/repo/list — 共享 skill 库 (~/.zero/skills) 清单.

库 CRUD 的 L. 每条含 name/description/version?/agents (空 = 通用,
非空 = 专属受众如 ['prime']); 前端 create agent 选项也用它, 自行按
agents 过滤. (agent 视角的合并清单是 skill/list.)
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field
from routine import Routine

from .registry import list_library_skills

__all__ = ['SkillsRepoList']


class SkillsRepoListOutput(BaseModel):
    ok: bool
    skills: List[Dict[str, Any]] = Field(
        description='[{name, description, version?, agents}] — agents 空 = 通用')


class SkillsRepoList(Routine):
    name = 'skill/repo/list'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'List all skills in the shared library (~/.zero/skills), with '
                       'version and audience (agents) metadata.',
        'output_schema': SkillsRepoListOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {
            'ok': True,
            'skills': list_library_skills(),
        }
