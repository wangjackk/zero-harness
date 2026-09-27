"""skill/repo/delete — 从共享 skill 库删除 skill.

只删库内副本 (USER_SKILLS_DIR/<name>/); agent workspace 里的草稿副本
属于 agent 自己的文件, 不在本 routine 的职责内.
  - 库里不存在时报错 (附库内可用清单)
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from .registry import USER_SKILLS_DIR, list_library_skills


class SkillsRepoDeleteInput(BaseModel):
    name: str = Field(description='Skill name to delete from the shared library.')


class SkillsRepoDeleteOutput(BaseModel):
    name: str
    removed: bool


def do_repo_delete(name: str) -> Dict[str, Any]:
    """纯函数: 从用户共享库删除指定 skill."""
    name = (name or '').strip()
    if not name:
        return {'error': 'name is required'}

    target = (USER_SKILLS_DIR / name).resolve()
    if not target.is_dir():
        available = [s['name'] for s in list_library_skills()]
        return {
            'error': f'skill not found in library: {name!r}. Library skills: {available}',
        }
    if target.parent != USER_SKILLS_DIR.resolve():
        return {'error': f'invalid skill name: {name!r}'}
    shutil.rmtree(target)
    return {
        'for_llm': f'Skill "{name}" deleted from the shared library.',
        'name': name,
        'removed': True,
    }


class SkillsRepoDelete(Routine):
    name = 'skill/repo/delete'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Delete a skill from the shared library (~/.zero/skills). '
                       'Workspace draft copies are not affected.',
        'input_schema': SkillsRepoDeleteInput.model_json_schema(),
        'output_schema': SkillsRepoDeleteOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        name = (kwargs.get('name') or '').strip()
        result = do_repo_delete(name)
        if result.get('name'):
            self._logger.info('skill/repo/delete: %s (removed=%s)', name, True)
        return result
