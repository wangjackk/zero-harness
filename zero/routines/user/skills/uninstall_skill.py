"""uninstall_skill — 从 skill_dir 卸载 skill.

最小实现: 删除 ``<skill_dir>/<name>/`` 目录.
  - 不存在的 skill 报错
  - 删除后自动 rescan, LLM 下次 list_skills 不再可见

只删 skill_dir 副本, 不碰 builtin 源 (重新 seed 时会拷回来).

通用层 routine: 通过 ctx.req 反向获取 agent_state.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import build_registry


class UninstallSkillInput(BaseModel):
    name: str = Field(description='Skill name to uninstall (from list_skills output).')


class UninstallSkillOutput(BaseModel):
    name: str
    removed: bool


def do_uninstall(skill_dir: str | None, name: str) -> Dict[str, Any]:
    """纯函数: 从 skill_dir 删除指定 skill."""
    name = (name or '').strip()
    if not name:
        return {'error': 'name is required'}

    if not skill_dir:
        return {'error': 'skill_dir is required (should be injected by agent)'}

    base = Path(skill_dir)
    target = base / name
    if not target.is_dir():
        reg = build_registry(skill_dir)
        available = [s.name for s in reg.list_skills()]
        return {
            'error': f'skill not found: {name!r}. Installed skills: {available}',
        }

    # 防御: 只删 <skill_dir>/<name>/, 拒绝 .. 遍历 / 符号链接
    try:
        real_target = target.resolve()
        real_base = base.resolve()
        if real_target.parent != real_base:
            return {'error': f'invalid target path (must be direct child of skill_dir): {target}'}
        if real_target.is_symlink():
            return {'error': f'refusing to remove symlink: {target}'}
    except Exception as exc:
        return {'error': f'path validation failed: {exc}'}

    shutil.rmtree(target)
    reg = build_registry(skill_dir)
    reg.rescan()
    return {
        'for_llm': f'Skill "{name}" uninstalled.',
        'name': name,
        'removed': True,
    }


class UninstallSkill(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Uninstall a skill. Only removes the installed copy; '
                       'builtin sources are unaffected.',
        'input_schema': UninstallSkillInput.model_json_schema(),
        'output_schema': UninstallSkillOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        name = (kwargs.get('name') or '').strip()
        result = do_uninstall(skill_dir, name)
        if result.get('removed'):
            self._logger.info('uninstall_skill: %s (removed=%s)', name, True)
        return result
