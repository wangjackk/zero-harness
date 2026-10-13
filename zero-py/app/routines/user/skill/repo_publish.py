"""skill/repo/publish — 把 agent workspace 里的 skill 版本化发布到共享 skill 库.

发布契约:
  - version 必填, 且必须严格大于库内当前版本 (首次发布无此要求);
  - 发布即入库 (USER_SKILLS_DIR), 所有 agent / session 立即可见
    (registry 多目录直读, 无需重 seed);
  - 输入 version 会写进库内副本的 SKILL.md frontmatter (单一真源).

通用层 routine: 通过 agent/state 反向获取 agent_state.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict, Tuple

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from app.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import SKILL_COPY_IGNORE, USER_SKILLS_DIR, _VALID_NAME_RE, read_skill_version

_log = setup_logger('skill/repo/publish')

_FM_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)
_VERSION_RE = re.compile(r'^\d+(\.\d+)*$')


class SkillsRepoPublishInput(BaseModel):
    name: str = Field(
        description='Skill name to publish (directory name under the agent skill_dir).',
    )
    version: str = Field(
        description='Version to publish, dot-separated integers (e.g. "1.2.0"). '
                    'Must be strictly greater than the version currently in the library.',
    )


class SkillsRepoPublishOutput(BaseModel):
    name: str
    path: str
    version: str
    previous_version: str | None = None


def _parse_version(s: str) -> Tuple[int, ...]:
    """'1.2.0' -> (1, 2, 0); 非点分整数直接报错."""
    s = s.strip()
    if not _VERSION_RE.match(s):
        raise ValueError(
            f'invalid version: {s!r} (expected dot-separated integers, e.g. "1.2.0")'
        )
    return tuple(int(p) for p in s.split('.'))


def _version_key(v: Tuple[int, ...]) -> Tuple[int, ...]:
    """去尾部 0, 使 1.2 == 1.2.0."""
    parts = list(v)
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _set_frontmatter_version(text: str, version: str) -> str:
    """把 version 写进 SKILL.md frontmatter (无 frontmatter 则创建)."""
    m = _FM_RE.match(text)
    if not m:
        return f'---\nversion: {version}\n---\n\n{text}'
    import yaml
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        fm = {}
    if not isinstance(fm, dict):
        fm = {}
    fm['version'] = version
    new_fm = yaml.safe_dump(fm, default_flow_style=False, sort_keys=False,
                            allow_unicode=True).strip()
    return f'---\n{new_fm}\n---\n' + text[m.end():]


def do_repo_publish(
    skill_dir: str | None, name: str, version: str,
) -> Dict[str, Any]:
    """纯函数: skill_dir/<name> -> USER_SKILLS_DIR/<name> (版本递增强制)."""
    if not skill_dir:
        return {'error': 'skill_dir is required (should be injected by agent)'}

    name = (name or '').strip()
    if not name:
        return {'error': 'name is required'}
    if not _VALID_NAME_RE.match(name):
        return {'error': f'invalid skill name: {name!r} (kebab-case only: '
                         f'lowercase letters, digits and hyphens)'}

    try:
        new_v = _parse_version(version)
    except ValueError as exc:
        return {'error': str(exc)}

    src = Path(skill_dir) / name
    src_md = src / 'SKILL.md'
    if not src_md.is_file():
        return {'error': f'skill not found in workspace: {src}'}

    dst = USER_SKILLS_DIR / name
    prev = read_skill_version(dst / 'SKILL.md') if (dst / 'SKILL.md').is_file() else None
    if prev is not None:
        try:
            prev_v = _parse_version(prev)
        except ValueError:
            return {'error': f'library version is malformed: {prev!r}'}
        if _version_key(new_v) <= _version_key(prev_v):
            return {
                'error': (
                    f'version must be greater than the current library version: '
                    f'{version!r} <= {prev!r}'
                ),
            }

    # 入库: 整目录拷贝 + SKILL.md frontmatter 盖章 version
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=SKILL_COPY_IGNORE)
    (dst / 'SKILL.md').write_text(
        _set_frontmatter_version(
            (dst / 'SKILL.md').read_text(encoding='utf-8-sig'), version,
        ),
        encoding='utf-8',
    )
    return {
        'content': f'Skill "{name}" v{version} published to the shared library.',
        'name': name,
        'path': str(dst),
        'version': version,
        'previous_version': prev,
    }


class SkillsRepoPublish(Routine):
    name = 'skill/repo/publish'
    """Publish a skill from the agent workspace to the shared user skill library."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            'Publish a skill from the current agent workspace to the shared user '
            'skill library (~/.zero/skills). Requires an explicit version strictly '
            'greater than the version currently in the library. Published skills '
            'are immediately visible to all agents and sessions.'
        ),
        'input_schema': SkillsRepoPublishInput.model_json_schema(),
        'output_schema': SkillsRepoPublishOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('agent/state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        inp = SkillsRepoPublishInput.model_validate(kwargs)

        result = do_repo_publish(skill_dir, inp.name, inp.version)
        if result.get('name'):
            _log.info('skill/repo/publish: %s v%s -> %s', result['name'],
                      result.get('version'), result.get('path'))
        return result
