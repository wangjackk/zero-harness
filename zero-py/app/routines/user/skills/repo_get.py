"""skill/repo/get — 读共享库单个 skill 的内容详情.

库 CRUD 的深度 R: repo_list 给清单, 这个给 SKILL.md 全文 + 目录文件列表
(前端 create agent 的"查看详情"用它). 只读库 (USER_SKILLS_DIR), 不碰
agent workspace 副本; 拉副本是 skill/repo/pull.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field
from routine import Routine

from .registry import USER_SKILLS_DIR, SkillRegistry, _FM_RE, _VALID_NAME_RE

__all__ = ['SkillsRepoGet']


class SkillsRepoGetInput(BaseModel):
    name: str = Field(description='Skill name in the shared library (see skill/repo/list).')


class SkillsRepoGetOutput(BaseModel):
    ok: bool
    name: str
    description: str
    version: str | None = None
    agents: List[str] = Field(default_factory=list, description='受众 (空 = 通用)')
    files: List[str] = Field(description='skill 目录内全部文件的相对路径')
    body: str = Field(description='SKILL.md 正文 (已剥 frontmatter, 摘要在 description)')


class SkillsRepoGet(Routine):
    name = 'skill/repo/get'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Read one skill from the shared library (~/.zero/skills): full '
                       'SKILL.md content plus its directory file list. Read-only; use '
                       'skill/repo/pull to copy it into an agent workspace.',
        'input_schema': SkillsRepoGetInput.model_json_schema(),
        'output_schema': SkillsRepoGetOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = SkillsRepoGetInput.model_validate(kwargs)
        name = inp.name.strip()
        if not name:
            return {'error': 'name is required'}
        if not _VALID_NAME_RE.match(name):
            return {'error': f'invalid skill name: {name!r} (kebab-case only: '
                             f'lowercase letters, digits and hyphens)'}

        reg = SkillRegistry(dirs=[USER_SKILLS_DIR])
        reg.rescan()
        meta = reg.get(name)
        if meta is None:
            return {'error': f'skill not found in library: {name!r}'}

        skill_dir = meta.skill_path.parent
        text = meta.skill_path.read_text(encoding='utf-8-sig')
        m = _FM_RE.match(text)
        body = text[m.end():].strip() if m else text.strip()
        files = sorted(
            p.relative_to(skill_dir).as_posix()
            for p in skill_dir.rglob('*')
            if p.is_file()
        )
        self._logger.info('skill/repo/get: %s (files=%d)', meta.name, len(files))
        return {
            'ok': True,
            'name': meta.name,
            'description': meta.description,
            'version': meta.version,
            'agents': list(meta.agents),
            'files': files,
            'body': body,
        }
