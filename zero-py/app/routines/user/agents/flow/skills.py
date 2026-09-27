"""FlowSkills ---- skill 文件系统 provider routine (dsh skill 插件移植).

dsh 用三个 npm 插件 (registry / filesystem provider / model-facing tool);
zero 生态的载体是 routine ---- 本模块是 registry + provider 层:
长驻服务 (routines.yaml dict 条目 kernel 自启), 扫描本地 skill 根,
解析 frontmatter, 响应 catalog / load 请求. 模型侧工具面是
skill_tool.SkillTool (按名字 call 临时拉起, 与 IPython 同构).

调用协议: 常驻 routine 不能用 call (它会等 run() 返回) ---- 调用方
经 skills_req() 按名字解析 routine id 后 req(id, event, data).

对齐 dsh 的语义:

- 发现根 (rank 升序, 后者覆盖前者同名):
    100 <project>/.zero/skills      (project-zero)
    200 <project>/.agents/skills    (project-agents)
    300 custom_skill_dirs           (custom, yaml kwargs)
    400 ~/.zero/skills              (user-zero)
    500 ~/.agents/skills            (user-agents)
  project root = 最近 .git 祖先, 无则 cwd 本身.
- 格式: <name>/SKILL.md 目录束 或 <name>.md 扁平文件, 只发现一层深.
- frontmatter (YAML): name/description 必填,
  disable-model-invocation / user-invocable 可选 (布尔, 缺省均可).
  name 必须 kebab-case; 非法值整条丢弃 + warn (fail closed).
- 目录与正文分离生命周期: catalog 只解析 frontmatter; 每次 load
  重读文件重解析 ---- 正文编辑无需缓存失效协议.
- 无状态扫描: 每次请求都全量重扫 (dsh pre-step snapshot 语义;
  dsh 的 watcher 只是加速手段, 不是语义的一部分).

零项目基础设施依赖: 只用标准库 + yaml + routine 框架.
"""
from __future__ import annotations

import re
from asyncio import Event
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

import yaml
from pydantic import BaseModel
from routine import Routine, request
from routine.logger import setup_logger

_log = setup_logger('flow.skills')

# kernel 运行名录里的 routine 名 (类名 snake_case; 解析 id 用)
ROUTINE_NAME = 'flow_skills'

# kebab-case 校验 (dsh 同款约束)
_NAME_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')

# frontmatter 布尔可接受形式 (dsh 同款)
_BOOL_WORDS = {
    'true': True, 'yes': True, 'on': True, '1': True,
    'false': False, 'no': False, 'off': False, '0': False,
}


@dataclass
class SkillEntry:
    """目录条目: 只有 name/description 进模型视野."""

    name: str
    description: str
    root: Path          # 所在 skill 根 (resource_base)
    path: Path          # SKILL.md / <name>.md 全路径
    model_invocable: bool = True   # disable-model-invocation: false
    user_invocable: bool = True    # user-invocable: true


class FlowSkillsInput(BaseModel):
    pass


class FlowSkillsOutput(BaseModel):
    pass


class FlowSkills(Routine):
    """长驻 skill 服务: catalog (目录快照) + load (按需读全文)."""

    is_passive = True    # auto-start, 无部署配置

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': FlowSkillsInput.model_json_schema(),
        'output_schema': FlowSkillsOutput.model_json_schema(),
        'description': (
            'Skill registry + filesystem provider (dsh skill plugins port). '
            'Serves catalog snapshots and on-demand skill body loads.'
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._custom_dirs: List[Path] = []
        self._stop_evt = Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        self._logger.info(f'{self.name} started')
        self._custom_dirs = [
            Path(p) for p in (kwargs.get('custom_skill_dirs') or [])
        ]
        await self._stop_evt.wait()

    async def stop(self) -> None:
        self._stop_evt.set()

    # ── 请求面 (agent / skill 工具经 skills_req 调用) ──

    @request('catalog')
    async def on_catalog(self, source, data: dict) -> dict:
        """目录快照: {entries: [{name, description}], digest}.

        只含模型可调用条目 (disable-model-invocation 不进目录,
        它们只有用户手势通道). digest 只覆盖目录条目 ---- 正文
        编辑不变更 digest, 不触发目录重发 (dsh KV-cache 语义).
        """
        skills = self._scan(str((data or {}).get('cwd') or '') or None)
        entries = [
            {'name': s.name, 'description': s.description}
            for s in sorted(skills.values(), key=lambda s: s.name)
            if s.model_invocable
        ]
        return {'entries': entries, 'digest': _digest(entries)}

    @request('load')
    async def on_load(self, source, data: dict) -> dict:
        """按 name 加载正文: 每次重读重解析 (无正文缓存协议).

        channel 语义 (dsh 双通道):
        - 'model' (skill 工具): 未知 → 错误; 禁模型调用 → 错误
        - 'user'  (用户手势):  未知 / 禁用户调用 → skip=True,
          调用方保持沉默 (名字留在原文当普通文本)
        """
        name = str((data or {}).get('name') or '')
        channel = str((data or {}).get('channel') or 'model')
        cwd = str((data or {}).get('cwd') or '') or None
        skills = self._scan(cwd)
        entry = skills.get(name)
        if entry is None:
            if channel == 'user':
                return {'ok': False, 'skip': True}
            return {'ok': False,
                    'error': f'skill "{name}" is unknown or no longer '
                             f'available'}
        if channel == 'model' and not entry.model_invocable:
            return {'ok': False,
                    'error': f'skill "{name}" is not available for '
                             f'model invocation'}
        if channel == 'user' and not entry.user_invocable:
            return {'ok': False, 'skip': True}
        try:
            content = entry.path.read_text(encoding='utf-8')
        except OSError as exc:
            return {'ok': False, 'error': f'read skill failed: {exc}'}
        return {'ok': True, 'name': entry.name,
                'resource_base': str(entry.root),
                'content': _strip_frontmatter(content)}

    # ── 扫描 (无状态, 每次全量) ──

    def _scan(self, cwd: str | None) -> Dict[str, SkillEntry]:
        """按 rank 升序扫全部根, 同名后者覆盖前者. 返回 {name: entry}."""
        found: Dict[str, SkillEntry] = {}
        for root in self._roots(cwd):
            if not root.is_dir():
                continue
            for entry in _scan_root(root):
                found[entry.name] = entry   # rank 高的根后扫 → 覆盖
        return found

    def _roots(self, cwd: str | None) -> List[Path]:
        roots: List[Path] = []
        if cwd:
            proj = _project_root(Path(cwd))
            roots.append(proj / '.zero' / 'skills')
            roots.append(proj / '.agents' / 'skills')
        roots.extend(self._custom_dirs)
        roots.append(Path.home() / '.zero' / 'skills')
        roots.append(Path.home() / '.agents' / 'skills')
        return roots


# ── 调用辅助: 常驻 routine 按 id 请求, 名字 → id 解析 ──

async def skills_req(rt: Routine, event: str, data: dict,
                     timeout: float = 30.0) -> Optional[dict]:
    """对 flow_skills 发 request; 未运行 → None (调用方自行降级).

    req 的 target 是 routine id (kernel 分配), 不是名字 ---- 这里经
    get_running_routines 按名字解析. 每次解析: 一次内核往返, 相对
    LLM 调用可忽略, 换来重启/换 id 后零状态失效协议.
    """
    rid: Optional[str] = None
    try:
        routines = await rt.ctx.get_running_routines()
    except Exception as exc:
        _log.warning('get_running_routines failed: %r', exc)
        return None
    for r in routines or []:
        if str(r.get('name') or '') == ROUTINE_NAME:
            rid = str(r.get('id') or '') or None
            break
    if rid is None:
        return None
    result = await rt.req(rid, event, data, timeout=timeout)
    return result if isinstance(result, dict) else None


# ── 模块级纯函数 (可测试) ──

def _project_root(cwd: Path) -> Path:
    """最近 .git 祖先; 无则 cwd 本身 (dsh 同款)."""
    for p in (cwd, *cwd.parents):
        if (p / '.git').exists():
            return p
    return cwd


def _scan_root(root: Path) -> List[SkillEntry]:
    """扫一个根: <name>/SKILL.md 与 <name>.md, 一层深. 非法条目 warn+跳过."""
    out: List[SkillEntry] = []
    try:
        candidates = sorted(root.iterdir())
    except OSError as exc:
        _log.warning('skill root unreadable %s: %r', root, exc)
        return out
    for item in candidates:
        entry: Optional[SkillEntry] = None
        if item.is_dir():
            skill_md = item / 'SKILL.md'
            if skill_md.is_file():
                entry = _parse_with_fail_closed(skill_md,
                                                fallback_name=item.name)
        elif item.is_file() and item.suffix == '.md':
            entry = _parse_with_fail_closed(item, fallback_name=item.stem)
        if entry is not None:
            out.append(entry)
    return out


def _parse(path: Path, *, fallback_name: str) -> Optional[SkillEntry]:
    """解析一个 skill 文件: frontmatter 校验 (fail closed)."""
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        _log.warning('skill unreadable %s: %r', path, exc)
        return None
    fm, _body = _split_frontmatter(text)

    name = str(fm.get('name') or fallback_name)
    description = str(fm.get('description') or '').strip()
    if not _NAME_RE.match(name):
        _log.warning('skill dropped (name not kebab-case): %s (%s)',
                     name, path)
        return None
    if not description:
        _log.warning('skill dropped (missing description): %s (%s)',
                     name, path)
        return None
    # 双通道 flags: disable-model-invocation 不丢弃条目 ---- 它只是
    # 从目录/工具面退场, 用户手势通道仍可达 (dsh 语义). 两 flag 缺省 true.
    model_invocable = not _parse_bool(
        fm.get('disable-model-invocation'), path, name, default=False)
    user_invocable = _parse_bool(
        fm.get('user-invocable'), path, name, default=True)

    return SkillEntry(name=name, description=description,
                      root=path.parent, path=path,
                      model_invocable=model_invocable,
                      user_invocable=user_invocable)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """拆 '---' frontmatter 块; 无则 ({}, text)."""
    if not text.startswith('---'):
        return {}, text
    parts = text.split('\n---', 2)
    if len(parts) < 2:
        return {}, text
    try:
        fm = yaml.safe_load(parts[0][3:].strip()) or {}
    except yaml.YAMLError:
        return {}, text
    body = parts[1].lstrip('\n') if len(parts) > 1 else ''
    if len(parts) > 2:
        body += '\n---' + parts[2]
    return fm if isinstance(fm, dict) else {}, body


def _parse_bool(raw: Any, path: Path, name: str, default: bool) -> bool:
    """frontmatter 布尔解析: 缺省用 default; 非法值整条丢弃 (fail closed)."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in _BOOL_WORDS:
        return _BOOL_WORDS[raw.strip().lower()]
    _log.warning('skill dropped (non-boolean invocation flag): %s (%s)',
                 name, path)
    raise _SkillRejected(name)


class _SkillRejected(Exception):
    """frontmatter 非法 → 整条丢弃的信号."""


def _digest(entries: List[dict]) -> str:
    """目录 digest: 只覆盖排序后的 (name, description) 对."""
    import hashlib
    import json
    payload = json.dumps(
        [[e['name'], e['description']] for e in entries],
        ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def _strip_frontmatter(text: str) -> str:
    return _split_frontmatter(text)[1]


# ── 渲染 (dsh-skill renderSkillContent 同构) ──

def render_skill_content(name: str, resource_base: str, content: str) -> str:
    """加载结果 → <skill_content> 块 (工具结果与用户手势注入共用)."""
    return '\n'.join([
        f'<skill_content name="{name}">',
        '<skill_resources>',
        f'Base directory for this skill: {resource_base}',
        'Resolve relative paths mentioned by this skill against the '
        'base directory before using them. Load referenced resources '
        'only as needed.',
        '</skill_resources>',
        '',
        '<skill_instructions>',
        content,
        '</skill_instructions>',
        '</skill_content>',
    ])


# ── 用户手势扫描 (dsh SKILL_GESTURE 同款) ──

# /<kebab-name> 记号, 前后须是空白/行首行尾 ---- 命令名是独立词素
SKILL_GESTURE = re.compile(
    r'(^|\s)/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)')


def invoked_skill_names(texts: List[str]) -> List[str]:
    """扫一批用户文本里的 /name 手势, 按出现序去重."""
    names: List[str] = []
    for text in texts:
        for match in SKILL_GESTURE.finditer(text):
            name = match.group(2)
            if name and name not in names:
                names.append(name)
    return names


# _parse 的布尔 fail-closed 信号在 scan 层统一收口为丢弃:
def _parse_with_fail_closed(path: Path, *, fallback_name: str) -> Optional[SkillEntry]:
    try:
        return _parse(path, fallback_name=fallback_name)
    except _SkillRejected:
        return None
