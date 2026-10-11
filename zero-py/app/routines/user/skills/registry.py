"""Skill registry — 扫描 skill 目录 (通用层, 不耦合 agent workspace).

Pattern: Progressive Disclosure (Anthropic-style).
- skill/list  -> returns lightweight metadata (name + description)
- skill/load  -> returns full SKILL.md body as tool result
- No session state, no system prompt mutation.

通用层只认 ``skill_dir`` (一个具体的 skill 目录路径, 内含 <name>/SKILL.md).
不认 workspace / agent_id / project_root —— 那是 agent 专用层的概念,
agent 在自己的 skill routine wrapper 里把 workspace/skills/ 作为 skill_dir 传入.

skill 库 (USER_SKILLS_DIR, 本地池子):
  - 跨 agent / 跨 session / 跨机共享的单一真源, 纯池子不绑定 agent 类型;
  - skill/fetch(URL) 拉进池子, skill/repo/publish 版本化发布,
    skill/repo/pull 从池子下载到 agent workspace (按需取用).
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# skill 库: 唯一库 (发布/拉取/共享都指向这里), env ZERO_SKILLS_DIR 覆盖.
USER_SKILLS_DIR = Path(
    os.environ.get('ZERO_SKILLS_DIR')
    or Path.home() / '.zero' / 'skills',
).expanduser()

_FM_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    skill_path: Path
    version: Optional[str] = None
    source: str = ''  # 'workspace' (agent 自己的副本)
    # 受众声明 (frontmatter ``agents: [prime]``); 空 = 所有 agent.
    agents: tuple[str, ...] = ()


class SkillRegistry:
    """多目录 skill 扫描器.

    dirs first-wins 同名去重. ``profile`` 是 agent 受众画像:
    frontmatter 声明 ``agents`` 且不含 profile 的 skill 从 list_skills
    里过滤掉 (get/invoke 仍可显式取, LLM 明确点名时放行).
    """

    def __init__(
        self, dirs: Sequence[Path] | None = None,
        profile: str | None = None,
    ) -> None:
        if dirs is None:
            dirs = [USER_SKILLS_DIR]
        self._dirs = [d for d in dirs if d]
        self._profile = profile
        self._cache: Dict[str, SkillMeta] = {}

    def rescan(self) -> None:
        self._cache.clear()
        for scan_dir in self._dirs:
            if not scan_dir.is_dir():
                continue
            for child in sorted(scan_dir.iterdir()):
                if not child.is_dir():
                    continue
                if child.name.startswith('_') or child.name.startswith('.'):
                    continue
                skill_md = child / 'SKILL.md'
                if not skill_md.is_file():
                    continue
                src = 'repo' if scan_dir == USER_SKILLS_DIR else 'workspace'
                meta = self._parse_skill_md(skill_md, src)
                if meta is not None and meta.name not in self._cache:
                    self._cache[meta.name] = meta

    def _parse_skill_md(self, path: Path, source: str = 'workspace') -> Optional[SkillMeta]:
        try:
            text = path.read_text(encoding='utf-8-sig')
        except Exception:
            return None
        name = path.parent.name
        description = ''
        version = None
        agents: tuple[str, ...] = ()
        m = _FM_RE.match(text)
        if m:
            try:
                import yaml
                fm = yaml.safe_load(m.group(1)) or {}
                name = str(fm.get('name', name))
                description = str(fm.get('description', '') or '').strip()
                version = fm.get('version')
                if version is not None:
                    version = str(version)
                raw_agents = fm.get('agents')
                if isinstance(raw_agents, str):
                    agents = (raw_agents,)
                elif isinstance(raw_agents, list):
                    agents = tuple(str(a) for a in raw_agents)
            except Exception:
                pass
        if not description:
            body = m.group(0) if m else text
            first_line = next(
                (ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith('#')),
                '',
            )
            description = first_line[:200]
        return SkillMeta(
            name=name,
            description=description,
            skill_path=path,
            version=version,
            source=source,
            agents=agents,
        )

    def all_skills(self) -> List[SkillMeta]:
        """全量清单 (不做 profile 过滤, 选项列表/版本比对用)."""
        if not self._cache:
            self.rescan()
        return list(self._cache.values())

    def list_skills(self) -> List[SkillMeta]:
        if not self._cache:
            self.rescan()
        if self._profile is None:
            return [m for m in self._cache.values() if not m.agents]
        return [
            m for m in self._cache.values()
            if not m.agents or self._profile in m.agents
        ]

    def get(self, name: str) -> Optional[SkillMeta]:
        if not self._cache:
            self.rescan()
        return self._cache.get(name)

    def invoke(self, name: str) -> str:
        """返回 skill 完整正文(tool result 用,不含目录信息)."""
        meta = self.get(name)
        if meta is None:
            raise KeyError(f'skill not found: {name}')
        return self._render_body(meta)

    def invoke_for_preload(self, name: str) -> str:
        """返回预加载格式(三段式: IMPORTANT 标记 + 正文 + 目录信息)."""
        meta = self.get(name)
        if meta is None:
            raise KeyError(f'skill not found: {name}')
        body = self._render_body(meta)
        skill_dir = meta.skill_path.parent
        return (
            f'[IMPORTANT: The "{meta.name}" skill is preloaded for this session. '
            f'Treat its instructions as active guidance for the duration of this '
            f'session unless the user overrides them.]\n\n'
            f'{body}\n\n'
            f'[Skill directory: {skill_dir}]\n'
            f'Relative paths in this skill can be resolved against this directory. '
            f'Supporting files (if any) can be read with the Read tool using absolute paths.'
        )

    def _render_body(self, meta: SkillMeta) -> str:
        text = meta.skill_path.read_text(encoding='utf-8-sig')
        m = _FM_RE.match(text)
        body = text[m.end():] if m else text
        lines: List[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith('Base directory for this skill:'):
                continue
            if stripped.startswith('Supporting files can be referenced using relative paths'):
                continue
            if 'Use FilePathResolver.resolve_path(' in stripped:
                continue
            lines.append(line)
        result = '\n'.join(lines).strip()
        version_str = f' (v{meta.version})' if meta.version else ''
        return f'# Skill: {meta.name}{version_str}\n\n{result}'


def build_registry(
    skill_dir: str | Path | None = None,
    profile: str | None = None,
) -> SkillRegistry:
    """构建 registry: 多目录读, first-wins 同名去重.

    目录优先级: ``[skill_dir, USER_SKILLS_DIR]``
      - skill_dir: agent workspace (项目私有 skill)
      - USER_SKILLS_DIR: skill 库 —— publish/install 后新 skill 无需
        任何同步即对 live agent 的 list/load 可见.

    ``profile`` 是 agent 受众画像 (prime/pi/None), 见 SkillRegistry.
    """
    if skill_dir is None:
        return SkillRegistry(dirs=[USER_SKILLS_DIR], profile=profile)
    return SkillRegistry(
        dirs=[Path(skill_dir), USER_SKILLS_DIR], profile=profile,
    )


def list_library_skills() -> List[Dict[str, object]]:
    """列 skill 库全部 skill (skill/repo/list / 前端 skill 选项用).

    返回 [{name, description, version?, agents}] — ``agents`` 空列表 = 通用,
    非空 = 专属受众 (如 ['prime']).
    """
    reg = SkillRegistry(dirs=[USER_SKILLS_DIR])
    reg.rescan()
    out: List[Dict[str, object]] = []
    for meta in reg.all_skills():
        entry: Dict[str, object] = {
            'name': meta.name,
            'description': meta.description,
            'agents': list(meta.agents),
        }
        if meta.version:
            entry['version'] = meta.version
        out.append(entry)
    return out


# ----------------------------------------------------------------------
# 装入共享库 (skill/fetch 远程下载的落盘; 本地手工放也直接拷目录)
# ----------------------------------------------------------------------

_VALID_NAME_RE = re.compile(r'^[a-z0-9-]+$')  # skill 规范: kebab-case

# 目录拷贝时剔除的无关内容 (pyc 缓存 / VCS / 依赖目录)
SKILL_COPY_IGNORE = shutil.ignore_patterns(
    '__pycache__', '*.pyc', '.git', 'node_modules',
)


def read_skill_version(skill_md: Path) -> Optional[str]:
    """读 SKILL.md frontmatter 的 version 字段 (无则 None)."""
    try:
        text = skill_md.read_text(encoding='utf-8-sig')
    except Exception:
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
        v = fm.get('version')
        return str(v) if v is not None else None
    except Exception:
        return None


def install_to_library(
    files: Dict[str, Any],
    derived_name: str,
    name_override: str | None = None,
    force: bool = False,
) -> Dict[str, Any]:
    """把 files (相对路径 -> 内容) 落盘进共享库 ``<name>/``.

    纯文件操作, 不管来源 (本地拷贝 / URL 下载由各自 routine 备好 files).
    name 取 name_override 优先, 其次 derived_name (frontmatter / 目录名 /
    URL 段); override 时改写 SKILL.md frontmatter 的 name 字段保持身份
    一致. 同名默认拒绝, force=True 覆盖.
    """
    name = name_override or derived_name
    if not name:
        return {'error': 'could not derive skill name; provide name= explicitly'}
    if not _VALID_NAME_RE.match(name):
        return {'error': f'invalid skill name: {name!r} (kebab-case only: '
                         f'lowercase letters, digits and hyphens, e.g. my-skill)'}

    if name_override and isinstance(files.get('SKILL.md'), str):
        files = {**files, 'SKILL.md': _rewrite_frontmatter_name(files['SKILL.md'], name)}

    dst = USER_SKILLS_DIR / name
    overwritten = dst.exists()
    if overwritten and not force:
        return {'error': f'skill already exists: {name!r}. Use force=True to overwrite.'}

    USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if overwritten:
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding='utf-8')
    return {
        'content': f'Skill "{name}" installed to the shared library.',
        'name': name,
        'path': str(dst),
        'overwritten': overwritten,
    }


def _rewrite_frontmatter_name(skill_md_text: str, new_name: str) -> str:
    """改写 SKILL.md frontmatter 的 ``name:`` 字段为 new_name.

    无 frontmatter 或无 ``name`` 键时不动 (registry 会 fallback 到目录名,
    目录名已是 new_name)."""
    m = _FM_RE.match(skill_md_text)
    if not m:
        return skill_md_text
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        # malformed frontmatter -> don't risk corrupting it, leave as-is.
        return skill_md_text
    if not isinstance(fm, dict) or 'name' not in fm:
        return skill_md_text
    fm['name'] = new_name
    new_fm = yaml.safe_dump(fm, default_flow_style=False, sort_keys=False,
                            allow_unicode=True).strip()
    return f'---\n{new_fm}\n---\n' + skill_md_text[m.end():]
