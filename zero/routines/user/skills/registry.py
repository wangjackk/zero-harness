"""Skill registry — 扫描 skill 目录 (通用层, 不耦合 agent workspace).

Pattern: Progressive Disclosure (Anthropic-style).
- list_skills  -> returns lightweight metadata (name + description)
- load_skill   -> returns full SKILL.md body as tool result
- No session state, no system prompt mutation.

通用层只认 ``skill_dir`` (一个具体的 skill 目录路径, 内含 <name>/SKILL.md).
不认 workspace / agent_id / project_root —— 那是 agent 专用层的概念,
agent 在自己的 skill routine wrapper 里把 workspace/skills/ 作为 skill_dir 传入.

builtin 来源 (zero 包内置, seed 时使用, 运行时不读):
  - skills/builtin/   ← 唯一 skill 库 (npx skills 装这里);
    frontmatter ``agents: [prime]`` 声明专属受众, 缺省所有 agent.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# registry.py is at skills/registry.py -> parents[0] = skills/.
_SKILLS_DIR = Path(__file__).resolve().parent
# builtin 仓库: zero 包内置, seed 时作为来源.
BUILTIN_SKILLS_DIR = _SKILLS_DIR / 'builtin'

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
    """Single-directory skill scanner.

    只扫一个目录: ``skill_dir`` (内含 <name>/SKILL.md).
    agent 专用层 wrapper 把 ``<workspace>/skills/`` 作为 skill_dir 传入;
    通用层不碰 workspace 拼路径的逻辑.
    """

    def __init__(self, dirs: Sequence[Path] | None = None) -> None:
        if dirs is None:
            dirs = [BUILTIN_SKILLS_DIR]
        self._dirs = [d for d in dirs if d]
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
                meta = self._parse_skill_md(skill_md)
                if meta is not None and meta.name not in self._cache:
                    self._cache[meta.name] = meta

    def _parse_skill_md(self, path: Path) -> Optional[SkillMeta]:
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
            source='workspace',
            agents=agents,
        )

    def list_skills(self) -> List[SkillMeta]:
        if not self._cache:
            self.rescan()
        return list(self._cache.values())

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


def build_registry(skill_dir: str | Path | None = None) -> SkillRegistry:
    """构建 registry: 扫 ``skill_dir`` (一个具体的 skill 目录, 内含 <name>/SKILL.md).

    通用层只认 skill_dir, 不认 workspace —— agent 专用层 wrapper 负责把
    ``<workspace>/skills/`` 作为 skill_dir 传入. builtin skill 在 agent 初始化
    时已通过 seed_workspace_skills() 拷贝到 skill_dir; 运行时只读这里.
    """
    if skill_dir is None:
        # 兼容无 skill_dir 场景 (测试/早期开发): 回退到 builtin.
        return SkillRegistry(dirs=[BUILTIN_SKILLS_DIR])
    return SkillRegistry(dirs=[Path(skill_dir)])


def seed_workspace_skills(workspace: str | Path, profile: str | None = None) -> int:
    """初始化时把 builtin skill 拷贝到 agent workspace 的 skills/ 子目录.

    通用层不认 workspace, 但 seed 是初始化期操作 (agent 专用层调),
    接收 workspace 路径, 拷贝到 ``<workspace>/skills/``.

    受众过滤: skill frontmatter ``agents: [prime]`` 声明专属受众;
    无 ``agents`` = 所有 agent. ``profile`` 是 agent 自己的画像
    (None = 仅通用 skill, 'prime' = 通用 + prime 专属).

    project 私有 skill 由用户直接放到 ``<workspace>/skills/``,
    agent 运行时自动扫到, 不参与 seed. 注意: 同名 skill 会被 builtin 覆盖,
    用户私有 skill 应避免与 builtin 同名.

    拷贝后 ``<workspace>/skills/`` 成为 skill_dir, 跟 builtin 解耦.
    已存在的同名 skill 目录会被覆盖 (重新 seed 时同步最新 builtin).
    用户手动放到 skill_dir 的非同名 skill 不受影响.

    返回拷贝的 skill 数量.
    """
    ws = Path(workspace)
    target = ws / 'skills'
    target.mkdir(parents=True, exist_ok=True)

    src = BUILTIN_SKILLS_DIR
    if not src.is_dir():
        return 0

    reg = SkillRegistry(dirs=[src])
    reg.rescan()
    count = 0
    for meta in reg.list_skills():
        if meta.agents and profile not in meta.agents:
            continue
        child = meta.skill_path.parent
        dst = target / child.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(child, dst)
        count += 1
    return count


def list_builtin_skills() -> List[Dict[str, object]]:
    """列 builtin 可用 skill (给前端 preload_skills 选项用, 不依赖 skill_dir).

    返回 [{name, description, version?, agents}] — ``agents`` 空列表 = 通用,
    非空 = 专属受众 (如 ['prime']).
    """
    reg = SkillRegistry(dirs=[BUILTIN_SKILLS_DIR])
    reg.rescan()
    out: List[Dict[str, object]] = []
    for meta in reg.list_skills():
        entry: Dict[str, object] = {
            'name': meta.name,
            'description': meta.description,
            'agents': list(meta.agents),
        }
        if meta.version:
            entry['version'] = meta.version
        out.append(entry)
    return out


def list_prime_skills() -> List[Dict[str, object]]:
    """列 prime 专属 skill (builtin 里 ``agents`` 含 prime 的).

    返回 [{name, description, version?}].
    """
    return [
        {
            'name': e['name'],
            'description': e['description'],
            **({'version': e['version']} if 'version' in e else {}),
        }
        for e in list_builtin_skills()
        if 'prime' in e.get('agents', [])
    ]
