"""install_skill — 安装 skill 到 skill_dir.

最小实现: 支持两种 source
  - local: 从本地目录拷贝 (用户已 clone / 手写)
  - url:   从 HTTP(S) URL 拉取单个 SKILL.md (可选 references/ 等子文件)

安装目标: ``<skill_dir>/<name>/``  (单一来源, 立即生效)
  - 同名 skill 默认拒绝, force=True 覆盖
  - 安装后自动 rescan, LLM 下次 list_skills 即可见

不走隔离区/安全扫描/lockfile — 单用户本地开发场景, 过度安全设计反而碍事.

通用层 routine: 通过 ctx.req 反向获取 agent_state.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from routine import Routine

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import build_registry

_FM_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n?', re.DOTALL)


class InstallSkillInput(BaseModel):
    source: str = Field(
        description='Skill source type: "local" or "url".',
    )
    path: str | None = Field(
        None,
        description='Local source directory (for source="local"). '
                    'Must contain SKILL.md. Relative paths resolve against project root.',
    )
    url: str | None = Field(
        None,
        description='HTTP(S) URL to install from (for source="url"). Accepts: '
                    '(1) skills.sh detail page (https://www.skills.sh/owner/repo/skill); '
                    '(2) github.com tree/blob URL (https://github.com/owner/repo/tree/<branch>/path); '
                    '(3) raw SKILL.md URL (raw.githubusercontent.com / any URL returning markdown). '
                    'GitHub API uses GITHUB_TOKEN / GH_TOKEN env if set (anonymous: 60/hrs).',
    )
    name: str | None = Field(
        None,
        description='Override skill name. If omitted, derive from frontmatter "name" field, '
                    'or fall back to source directory name / URL basename.',
    )
    force: bool = Field(
        False,
        description='Overwrite if a skill with the same name already exists.',
    )


class InstallSkillOutput(BaseModel):
    name: str
    path: str
    overwritten: bool = False


async def do_install(
    skill_dir: str | None,
    source: str,
    path: str | None,
    url: str | None,
    name_override: str | None,
    force: bool,
) -> Dict[str, Any]:
    """纯函数 (async): 安装 skill 到 skill_dir."""
    if not skill_dir:
        return {'error': 'skill_dir is required (should be injected by agent)'}
    if source not in ('local', 'url'):
        return {'error': f'unsupported source: {source!r} (expected "local" or "url")'}

    base = Path(skill_dir)
    base.mkdir(parents=True, exist_ok=True)

    # 解析 name + 拉取源文件
    if source == 'local':
        if not path:
            return {'error': 'path is required for source="local"'}
        src_dir = Path(path).resolve()
        if not src_dir.is_absolute():
            # 相对路径兜底: 用 cwd (project_root 已被 BashTool 解析, 这里直接 Path)
            src_dir = (Path.cwd() / path).resolve()
        if not src_dir.is_dir():
            return {'error': f'source directory not found: {src_dir}'}
        skill_md = src_dir / 'SKILL.md'
        if not skill_md.is_file():
            return {'error': f'SKILL.md not found in source directory: {src_dir}'}
        name = name_override or _derive_name_from_local(skill_md, src_dir)
        files = _collect_local_files(src_dir)
    else:  # url
        if not url:
            return {'error': 'url is required for source="url"'}
        try:
            name, files = await _fetch_url_skill(url, name_override)
        except Exception as exc:
            return {'error': f'failed to fetch skill from URL: {exc}'}

    if not name:
        return {'error': 'could not derive skill name; provide name= explicitly'}
    if not _valid_skill_name(name):
        return {'error': f'invalid skill name: {name!r} (letters, digits, _, - only)'}

    dst = base / name
    overwritten = dst.exists()
    if overwritten and not force:
        return {'error': f'skill already exists: {name!r}. Use force=True to overwrite.'}

    if overwritten:
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    # If user provided an explicit name override, rewrite the frontmatter
    # `name:` field in the copied SKILL.md so the registry (which keys on
    # frontmatter name) sees the override as the skill's identity. Without
    # this, installing the same source twice with different name overrides
    # would produce two directories whose SKILL.md both carry the original
    # frontmatter name -> registry dedupes -> second install is invisible.
    if name_override and 'SKILL.md' in files and isinstance(files['SKILL.md'], str):
        files['SKILL.md'] = _rewrite_frontmatter_name(files['SKILL.md'], name)

    for rel, content in files.items():
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding='utf-8')

    # rescan 让 list_skills 立即看到新 skill
    reg = build_registry(skill_dir)
    reg.rescan()
    return {
        'for_llm': f'Skill "{name}" installed successfully at {dst}.',
        'name': name,
        'path': str(dst),
        'overwritten': overwritten,
    }


class InstallSkill(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Install a skill from a local directory or URL. '
                       'Installed skills are immediately visible to list_skills / load_skill.',
        'input_schema': InstallSkillInput.model_json_schema(),
        'output_schema': InstallSkillOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        inp = InstallSkillInput.model_validate(kwargs)
        result = await do_install(
            skill_dir, inp.source, inp.path, inp.url, inp.name, inp.force,
        )
        if result.get('name'):
            self._logger.info('install_skill: %s -> %s (overwritten=%s)',
                              result['name'], result.get('path'), result.get('overwritten'))
        return result


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

_VALID_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_-]*$')
_ALLOWED_SUPPORT_DIRS = {
    'references', 'templates', 'scripts', 'assets', 'examples',
}


def _valid_skill_name(name: str) -> bool:
    return bool(_VALID_NAME_RE.match(name))


def _derive_name_from_local(skill_md: Path, src_dir: Path) -> str:
    """从 SKILL.md frontmatter 提取 name, fallback 到目录名."""
    try:
        text = skill_md.read_text(encoding='utf-8-sig')
        m = _FM_RE.match(text)
        if m:
            import yaml
            fm = yaml.safe_load(m.group(1)) or {}
            n = fm.get('name')
            if isinstance(n, str) and n.strip():
                return n.strip()
    except Exception:
        pass
    return src_dir.name


def _rewrite_frontmatter_name(skill_md_text: str, new_name: str) -> str:
    """Rewrite the `name:` field in SKILL.md frontmatter to new_name.

    If the text has no frontmatter or no `name:` key, leave it alone (the
    registry will fall back to the directory name, which is already `new_name`
    because install_skill created the dst dir with that name).
    """
    m = _FM_RE.match(skill_md_text)
    if not m:
        return skill_md_text
    fm_raw = m.group(1)
    try:
        import yaml
        fm = yaml.safe_load(fm_raw) or {}
    except Exception:
        # malformed frontmatter -> don't risk corrupting it, leave as-is.
        return skill_md_text
    if not isinstance(fm, dict) or 'name' not in fm:
        return skill_md_text
    fm['name'] = new_name
    new_fm = yaml.safe_dump(fm, default_flow_style=False, sort_keys=False).strip()
    return f'---\n{new_fm}\n---\n' + skill_md_text[m.end():]


def _collect_local_files(src_dir: Path) -> Dict[str, str | bytes]:
    """收集 src_dir 下所有文件, 返回相对路径 -> 内容的映射.
    顶层允许 SKILL.md + _ALLOWED_SUPPORT_DIRS 下的任意文件.
    """
    files: Dict[str, str | bytes] = {}
    for entry in sorted(src_dir.iterdir()):
        if entry.is_file():
            if entry.name == 'SKILL.md':
                files['SKILL.md'] = entry.read_text(encoding='utf-8-sig')
            # 顶层其他文件忽略 (只取 SKILL.md + 支持目录)
        elif entry.is_dir() and entry.name in _ALLOWED_SUPPORT_DIRS:
            for child in sorted(entry.rglob('*')):
                if child.is_file():
                    rel = child.relative_to(src_dir).as_posix()
                    try:
                        files[rel] = child.read_text(encoding='utf-8-sig')
                    except UnicodeDecodeError:
                        files[rel] = child.read_bytes()
    if 'SKILL.md' not in files:
        # 兜底: 至少要有 SKILL.md (前面已校验, 这里防御性)
        files['SKILL.md'] = (src_dir / 'SKILL.md').read_text(encoding='utf-8-sig')
    return files


async def _fetch_url_skill(url: str, name_override: str | None) -> tuple[str, Dict[str, str | bytes]]:
    """从 URL 拉取 skill, 按 URL 形态智能分流 (不强制 .md 后缀).

    支持的 URL 形态:
      1. skills.sh 详情页 (https://www.skills.sh/owner/repo/skill)
         -> GET HTML, 正则抽 `npx skills add <github-url>` -> 走 GitHub API
      2. github.com 仓库/tree/blob URL -> 走 GitHub API (Trees API 拿全树 + raw 拉文件)
      3. 其他 URL (raw.githubusercontent.com / 任意 .md / 任意返回 markdown 的 URL)
         -> GET 当 SKILL.md 单文件

    无 GITHUB_TOKEN 时匿名调 GitHub API (60/小时限流); 设了 GITHUB_TOKEN / GH_TOKEN
    自动带上 Authorization 头.

    用 stdlib urllib (via asyncio.to_thread) 避免引入 aiohttp 依赖.
    """
    parsed = urlparse(url)
    if not parsed.scheme.startswith('http'):
        raise ValueError(f'URL must be http(s): {url}')

    # ---- 分流 1: skills.sh 详情页 ----
    if 'skills.sh' in parsed.netloc:
        return await _fetch_skills_sh(url, name_override)

    # ---- 分流 2: github.com 任意仓库 URL (含 /tree/ /blob/ 或裸 repo URL) ----
    if parsed.netloc == 'github.com':
        return await _fetch_github(url, name_override)

    # ---- 分流 3: 当 markdown 直接 GET ----
    skill_md_text = await asyncio.to_thread(_http_get_text, url)
    # 如果返回的是 HTML 但不是 skills.sh, 提示用户
    if '<html' in skill_md_text[:500].lower() or '<!doctype html' in skill_md_text[:500].lower():
        raise ValueError(
            f'URL returned HTML, not markdown. '
            f'For skills.sh skills, pass the skills.sh detail page URL; '
            f'for GitHub skills, pass the github.com tree/blob URL. '
            f'Got: {url}'
        )

    name = name_override or _derive_name_from_md(skill_md_text, url)
    return name, {'SKILL.md': skill_md_text}


# ----------------------------------------------------------------------
# skills.sh 分流
# ----------------------------------------------------------------------

# 匹配 skills.sh 详情页里的 `npx skills add https://github.com/...` 命令
_SKILLS_SH_INSTALL_RE = re.compile(
    r'npx\s+skills(?:-sh)?\s+add\s+(https?://github\.com/[^\s"\'<>]+)',
    re.IGNORECASE,
)


async def _fetch_skills_sh(url: str, name_override: str | None) -> tuple[str, Dict[str, str | bytes]]:
    """skills.sh 详情页 -> 解析出真实 GitHub URL -> 委托 _fetch_github.

    skills.sh 详情页 HTML 里有 `npx skills add https://github.com/owner/repo/...`
    命令, 抽出真实 GitHub URL 再走 GitHub API.
    """
    html = await asyncio.to_thread(_http_get_text, url)
    m = _SKILLS_SH_INSTALL_RE.search(html)
    if not m:
        raise ValueError(
            f'skills.sh page did not contain a `npx skills add <github-url>` command: {url}'
        )
    github_url = m.group(1).rstrip('`.,)')
    return await _fetch_github(github_url, name_override)


# ----------------------------------------------------------------------
# GitHub 分流
# ----------------------------------------------------------------------

# 匹配 github.com/owner/repo, github.com/owner/repo/tree/branch/path,
# github.com/owner/repo/blob/branch/path/SKILL.md
_GITHUB_URL_RE = re.compile(
    r'^https?://github\.com/'
    r'(?P<owner>[^/]+)/'
    r'(?P<repo>[^/]+?)(?:\.git)?'
    r'(?:/(?P<kind>tree|blob)/(?P<branch>[^/]+)(?:/(?P<path>.+))?)?'
    r'/?$'
)


async def _fetch_github(github_url: str, name_override: str | None) -> tuple[str, Dict[str, str | bytes]]:
    """从 GitHub URL 拉取 skill 包.

    支持的 URL 形态:
      - https://github.com/owner/repo
        (拿 default branch, 在仓库里找 SKILL.md)
      - https://github.com/owner/repo/tree/<branch>/path/to/skill_dir
        (在指定 path 下找 SKILL.md + 支持文件)
      - https://github.com/owner/repo/blob/<branch>/path/to/SKILL.md
        (单文件, 转 raw URL 直接 GET)
    """
    m = _GITHUB_URL_RE.match(github_url)
    if not m:
        raise ValueError(f'invalid GitHub URL: {github_url}')
    owner = m.group('owner')
    repo = m.group('repo')
    kind = m.group('kind')  # 'tree' / 'blob' / None
    branch = m.group('branch')
    path = m.group('path') or ''

    # blob URL: 单文件, 直接转 raw 拉
    if kind == 'blob':
        raw_url = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}'
        text = await asyncio.to_thread(_http_get_text, raw_url)
        name = name_override or _derive_name_from_md(text, github_url)
        return name, {'SKILL.md': text}

    # tree URL 或裸 repo URL: 用 Trees API 拿全树
    if not branch:
        repo_info = await asyncio.to_thread(_gh_api_get, f'/repos/{owner}/{repo}')
        branch = repo_info.get('default_branch') or 'main'

    tree_data = await asyncio.to_thread(
        _gh_api_get,
        f'/repos/{owner}/{repo}/git/trees/{branch}?recursive=1',
    )
    if tree_data.get('truncated'):
        raise ValueError(
            f'GitHub tree is truncated (>100k entries); cannot reliably find SKILL.md. '
            f'Pass a more specific URL with /tree/<branch>/<path>.'
        )
    tree_paths = [
        item['path'] for item in tree_data.get('tree', [])
        if item.get('type') == 'blob'
    ]

    # 找 SKILL.md:
    # - path 以 SKILL.md 结尾 -> 直接用 (URL 指向具体文件)
    # - path 是目录 -> 优先 path/SKILL.md, 找不到则 path 子树最浅的 SKILL.md
    # - path 为空 -> 优先根 SKILL.md / skills/*/SKILL.md / .agents/skills/*/SKILL.md / .claude/skills/*/SKILL.md,
    #   都没有再取全树最浅的
    skill_md_path: str | None = None
    if path:
        if path.endswith('SKILL.md'):
            skill_md_path = path
        else:
            prefix = path.rstrip('/') + '/'
            direct = prefix + 'SKILL.md'
            if direct in tree_paths:
                skill_md_path = direct
            else:
                # path 子树里最浅的 SKILL.md (按路径深度排序, 同深度按字典序)
                candidates = [
                    p for p in tree_paths
                    if p.startswith(prefix) and p.endswith('/SKILL.md')
                ]
                if candidates:
                    skill_md_path = min(candidates, key=lambda p: (p.count('/'), p))
                else:
                    raise ValueError(
                        f'no SKILL.md found under {prefix!r} in {owner}/{repo}@{branch}. '
                        f'Pass a more specific URL ending in /SKILL.md, or a /tree/<branch>/<path> '
                        f'directory that contains SKILL.md.'
                    )
    else:
        # path 为空: 4 标准候选根
        all_skill_mds = [p for p in tree_paths if p == 'SKILL.md' or p.endswith('/SKILL.md')]
        if not all_skill_mds:
            raise ValueError(f'no SKILL.md found in {owner}/{repo}@{branch}')
        # 优先根 SKILL.md
        if 'SKILL.md' in all_skill_mds:
            skill_md_path = 'SKILL.md'
        else:
            # 4 标准子目录: skills/<name>/SKILL.md / .agents/skills/<name>/SKILL.md / .claude/skills/<name>/SKILL.md
            for sub in ('skills/', '.agents/skills/', '.claude/skills/'):
                matches = [
                    p for p in all_skill_mds
                    if p.startswith(sub) and p.count('/') == 1
                ]
                if matches:
                    skill_md_path = matches[0]
                    break
        if not skill_md_path:
            # 兜底: 全树最浅
            skill_md_path = min(all_skill_mds, key=lambda p: (p.count('/'), p))

    # skill 目录 = SKILL.md 所在目录
    parts = skill_md_path.split('/')
    skill_dir = '/'.join(parts[:-1])  # '' 表示根
    default_name = parts[-2] if len(parts) >= 2 else repo
    name = name_override or default_name

    # 拉 SKILL.md + skill_dir 下 _ALLOWED_SUPPORT_DIRS 子目录的文件
    files: Dict[str, str | bytes] = {}
    files['SKILL.md'] = await asyncio.to_thread(
        _gh_get_raw, owner, repo, branch, skill_md_path,
    )

    for p in tree_paths:
        if p == skill_md_path:
            continue
        if skill_dir and not p.startswith(skill_dir + '/'):
            continue
        rel = p[len(skill_dir) + 1:] if skill_dir else p
        # 只拉 _ALLOWED_SUPPORT_DIRS 下的 (顶层 SKILL.md 同级的其他 .md 不拉)
        first_seg = rel.split('/')[0] if '/' in rel else None
        if first_seg in _ALLOWED_SUPPORT_DIRS:
            files[rel] = await asyncio.to_thread(_gh_get_raw, owner, repo, branch, p)

    return name, files


# ----------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ----------------------------------------------------------------------

def _http_get_text(url: str, timeout: int = 30) -> str:
    """GET URL 返回 text."""
    import urllib.request
    req = urllib.request.Request(url, headers={
        'User-Agent': 'zero-install_skill/1.0',
        'Accept': 'text/markdown, text/plain, text/html;q=0.9, */*;q=0.1',
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    return data.decode('utf-8', errors='replace')


def _gh_api_get(path: str, timeout: int = 30):
    """GitHub REST API GET (anonymous or with GITHUB_TOKEN / GH_TOKEN)."""
    import json
    import os
    import urllib.request
    url = f'https://api.github.com{path}'
    headers = {
        'User-Agent': 'zero-install_skill/1.0',
        'Accept': 'application/vnd.github+json',
    }
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _gh_get_raw(owner: str, repo: str, branch: str, path: str, timeout: int = 30):
    """从 raw.githubusercontent.com 拉单文件, 失败时 fallback 到 Contents API.

    raw.githubusercontent.com 在国内网络常不稳 (SSL EOF / 连接重置),
    所以: 先试 raw 3 次 (退避 0.5/1/2s), 全失败再走 api.github.com Contents API
    (解 base64, 但 api.github.com 通常更可达).
    """
    import base64
    import time
    import urllib.request

    raw_url = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}'
    last_exc: Exception | None = None
    for attempt, backoff in enumerate([0.5, 1.0, 2.0], start=1):
        try:
            req = urllib.request.Request(raw_url, headers={
                'User-Agent': 'zero-install_skill/1.0',
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            try:
                return data.decode('utf-8')
            except UnicodeDecodeError:
                return data  # bytes (二进制资源如图片)
        except Exception as exc:
            last_exc = exc
            if attempt < 3:
                time.sleep(backoff)

    # raw 全失败 -> fallback 到 Contents API (api.github.com 更稳)
    # path 里的 / 要 URL-encode, 但简单 path 直接用即可
    api_path = f'/repos/{owner}/{repo}/contents/{path}?ref={branch}'
    try:
        item = _gh_api_get(api_path, timeout=timeout)
        content_b64 = item.get('content') or ''
        content = base64.b64decode(content_b64)
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError:
            return content
    except Exception as exc:
        raise ValueError(
            f'failed to fetch {owner}/{repo}/{branch}/{path}: '
            f'raw.githubusercontent.com failed ({last_exc!r}); '
            f'api.github.com fallback also failed ({exc!r})'
        )


def _derive_name_from_md(skill_md_text: str, url: str) -> str:
    """从 SKILL.md frontmatter name 字段 / URL 末尾段推导 skill name."""
    m = _FM_RE.match(skill_md_text)
    if m:
        try:
            import yaml
            fm = yaml.safe_load(m.group(1)) or {}
            n = fm.get('name')
            if isinstance(n, str) and n.strip():
                return n.strip()
        except Exception:
            pass
    # fallback: URL 末尾段去掉扩展名
    path = urlparse(url).path
    last = path.rsplit('/', 1)[-1]
    if '.' in last:
        last = last.rsplit('.', 1)[0]
    return last or 'skill'
