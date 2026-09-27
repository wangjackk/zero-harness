"""skill/repo/pull — 从共享库拉取 skill 到本 agent 的 workspace (用的时候下载).

repo 是本地池子, agent 只装自己需要的子集:
  - skill/repo/list 看池子里有什么
  - skill/repo/pull(name) 下载到自己的 skill_dir, 立即可 skill/list / skill/load
  - 版本一致默认跳过 (保护本地改动), force=True 强制覆盖

发布 (workspace -> repo) 是 skill/repo/publish; 网络装包 (URL -> repo) 是
skill/fetch.

通用层 routine: 通过 agent/state 反向获取 agent_state.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from app.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import (
    SKILL_COPY_IGNORE,
    USER_SKILLS_DIR,
    _VALID_NAME_RE,
    list_library_skills,
    read_skill_version,
)


class SkillsRepoPullInput(BaseModel):
    name: str = Field(description='Skill name in the shared library (see skill/repo/list).')
    force: bool = Field(
        False,
        description='Overwrite the workspace copy even if the version is unchanged '
                    '(default skips same-version to protect local edits).',
    )


class SkillsRepoPullOutput(BaseModel):
    name: str
    path: str
    version: str | None = None
    updated: bool = True


def do_repo_pull(
    skill_dir: str | None, name: str, force: bool,
) -> Dict[str, Any]:
    """纯函数: USER_SKILLS_DIR/<name> -> skill_dir/<name>."""
    name = (name or '').strip()
    if not name:
        return {'error': 'name is required'}
    if not _VALID_NAME_RE.match(name):
        return {'error': f'invalid skill name: {name!r} (kebab-case only: '
                         f'lowercase letters, digits and hyphens)'}
    if not skill_dir:
        return {'error': 'skill_dir is required (should be injected by agent)'}

    src = USER_SKILLS_DIR / name
    if not (src / 'SKILL.md').is_file():
        available = [s['name'] for s in list_library_skills()]
        return {'error': f'skill not found in library: {name!r}. Library skills: {available}'}

    dst = Path(skill_dir) / name
    src_ver = read_skill_version(src / 'SKILL.md')
    if dst.exists() and not force:
        dst_ver = read_skill_version(dst / 'SKILL.md') if (dst / 'SKILL.md').is_file() else None
        if src_ver is not None and src_ver == dst_ver:
            return {
                'for_llm': f'Skill "{name}" already at version {src_ver} '
                           f'(use force=True to overwrite local edits).',
                'name': name,
                'path': str(dst),
                'version': src_ver,
                'updated': False,
            }

    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=SKILL_COPY_IGNORE)
    return {
        'for_llm': f'Skill "{name}" pulled from the library into your workspace.',
        'name': name,
        'path': str(dst),
        'version': src_ver,
        'updated': True,
    }


class SkillsRepoPull(Routine):
    name = 'skill/repo/pull'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Pull (download) a skill from the shared library (~/.zero/skills) '
                       'into this agent\'s workspace. Use skill/repo/list to see what the '
                       'library has. After pulling, the skill is visible to '
                       'skill/list / skill/load.',
        'input_schema': SkillsRepoPullInput.model_json_schema(),
        'output_schema': SkillsRepoPullOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('agent/state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        inp = SkillsRepoPullInput.model_validate(kwargs)
        result = do_repo_pull(skill_dir, inp.name, inp.force)
        if result.get('name'):
            self._logger.info('skill/repo/pull: %s -> %s (updated=%s)',
                              result['name'], result.get('path'),
                              result.get('updated'))
        return result
