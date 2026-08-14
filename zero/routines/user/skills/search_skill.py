"""search_skill — 搜索 skills.sh / hermes-index 上的公开 skill.

设计参考 hermes 金字塔模型, 简化为两层:
  - 默认: 拉 hermes-index JSON (https://hermes-agent.nousresearch.com/docs/api/skills-index.json)
    覆盖 ~9 万条, 6h 缓存, 5 级评分搜索
  - 兜底: hermes-index 拉不到 (网络故障 / 索引文件 404) 时, 走 skills.sh /api/search?q=

返回字段含 install_url, LLM 拿到直接传给 install_skill 装.

通用层 routine: 通过 ctx.req 反向获取 agent_state.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, List
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from routine import Routine

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

from .install_skill import _http_get_text  # 复用 install_skill 的 stdlib HTTP helper


HERMES_INDEX_URL = 'https://hermes-agent.nousresearch.com/docs/api/skills-index.json'
HERMES_INDEX_TTL = 6 * 3600  # 6 小时, 跟 hermes 一致
SKILLS_SH_SEARCH_URL = 'https://skills.sh/api/search'

# Trust 排序权重 (builtin > trusted > community)
_TRUST_RANK = {'builtin': 2, 'trusted': 1, 'community': 0, 'official': 2}


class SearchSkillInput(BaseModel):
    query: str = Field(
        description='Search keyword (e.g. "frontend", "git", "pdf"). '
                    'Matches skill name / description / tags.',
    )
    limit: int = Field(
        25,
        ge=1, le=100,
        description='Max results to return (1-100, default 25).',
    )


class SearchSkillOutput(BaseModel):
    query: str
    results: List[Dict[str, str]]
    source: str  # 'hermes-index' / 'skills-sh' / 'fallback'
    cached: bool = False


def do_search(query: str, limit: int, cache_dir: str | None) -> Dict[str, Any]:
    """纯函数 (sync): 搜 hermes-index / skills.sh.

    ``cache_dir`` 可选; 传入则把 hermes-index JSON 缓存到 ``<cache_dir>/skills-index.json``.
    """
    query = (query or '').strip()
    if not query:
        return {'error': 'query must not be empty'}

    cache_path: Path | None = Path(cache_dir) if cache_dir else None
    if cache_path:
        cache_path.mkdir(parents=True, exist_ok=True)

    # 优先走 hermes-index (覆盖广, 一次 HTTP 拿全量, 缓存 6h)
    try:
        results, cached = _search_hermes_index(query, limit, cache_path)
        if results:
            top = results[:limit]
            return {
                'query': query,
                'results': top,
                'source': 'hermes-index',
                'cached': cached,
                'count': len(top),
                'for_llm': _format_for_llm(query, top, 'hermes-index', cached),
            }
    except Exception:
        pass

    # 兜底: skills.sh /api/search
    try:
        results = _search_skills_sh(query, limit)
    except Exception as exc:
        return {
            'error': f'skill search failed: hermes-index unavailable and skills.sh search failed: {exc!r}',
        }

    if not results:
        return {
            'query': query,
            'results': [],
            'source': 'skills-sh',
            'cached': False,
            'count': 0,
            'for_llm': f'No skills found matching {query!r}. Try different keywords.',
        }

    top = results[:limit]
    return {
        'query': query,
        'results': top,
        'source': 'skills-sh',
        'cached': False,
        'count': len(top),
        'for_llm': _format_for_llm(query, top, 'skills-sh', False),
    }


class SearchSkill(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            'Search public skill marketplaces (hermes-index aggregator, ~90k skills; '
            'falls back to skills.sh /api/search). Returns name + description + install_url. '
            'Pass install_url to install_skill to install. Use load_skill to load after install.'
        ),
        'input_schema': SearchSkillInput.model_json_schema(),
        'output_schema': SearchSkillOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        cache_dir = state.get('skill_index_cache_dir')
        inp = SearchSkillInput.model_validate(kwargs)
        # do_search 是 sync + 内部有 HTTP IO, 扔到线程池避免阻塞事件循环.
        return await asyncio.to_thread(do_search, inp.query, inp.limit, cache_dir)


def _format_for_llm(query: str, results: List[Dict[str, str]], source: str, cached: bool) -> str:
    """生成给 LLM 看的文本摘要, 必须包含 name + install_url (否则 LLM 无法决策)."""
    lines = [f'Found {len(results)} skill(s) matching {query!r} ({source}, cache={"hit" if cached else "miss"}):']
    for i, r in enumerate(results, 1):
        name = r.get('name', '?')
        url = r.get('install_url', '')
        desc = (r.get('description') or '')[:80]
        trust = r.get('trust_level', '')
        lines.append(f'  {i}. {name} [{trust}] - {url}')
        if desc:
            lines.append(f'      {desc}')
    lines.append('')
    lines.append('To install, call install_skill with source="url" and url=<install_url> from above.')
    return '\n'.join(lines)


# ----------------------------------------------------------------------
# hermes-index 搜索
# ----------------------------------------------------------------------

def _search_hermes_index(
    query: str, limit: int, cache_dir: Path | None,
) -> tuple[List[Dict[str, str]], bool]:
    """拉 hermes-index JSON (带缓存), 5 级评分搜索.

    返回 (results, cached). cached=True 表示用了本地缓存 (未触发 HTTP).
    """
    cache_file = cache_dir / 'skills-index.json' if cache_dir else None
    index_data = _load_hermes_index(cache_file)
    cached = index_data.get('_cached', False)
    skills = index_data.get('skills', [])
    if not skills:
        return [], cached

    query_lower = query.lower()
    scored: List[tuple[int, int, Dict[str, Any]]] = []  # (score, original_idx, skill)

    for idx, skill in enumerate(skills):
        name = (skill.get('name') or '').lower()
        description = (skill.get('description') or '').lower()
        tags = skill.get('tags') or []
        if not isinstance(tags, list):
            tags = []
        tags_str = ' '.join(str(t) for t in tags).lower()
        identifier = (skill.get('identifier') or '').lower()
        provider = ((skill.get('extra') or {}).get('provider') or '').lower()

        # haystack 预过滤 (任意字段子串命中)
        haystack = f'{name} {description} {tags_str} {identifier} {provider}'
        if query_lower not in haystack:
            continue

        # 5 级评分 (越小越优), 跟 hermes HermesIndexSource 一致
        if name == query_lower:
            score = 0
        elif name.startswith(query_lower):
            score = 1
        elif provider == query_lower:
            score = 2
        elif query_lower in name.split() or query_lower in provider.split():
            score = 3
        elif query_lower in name:
            score = 4
        else:
            score = 5

        scored.append((score, idx, skill))

    # 稳定排序: 先按 score, 再按原索引 (保留索引内顺序, 通常是 trust desc)
    scored.sort(key=lambda x: (x[0], x[1]))

    results: List[Dict[str, str]] = []
    for _, _, skill in scored[:limit * 3]:  # 多取 3 倍做 trust 排序后取 top limit
        install_url = _build_install_url(skill)
        if not install_url:
            continue
        entry: Dict[str, str] = {
            'name': skill.get('name') or '',
            'description': (skill.get('description') or '')[:200],
            'source': skill.get('source') or '',
            'trust_level': skill.get('trust_level') or 'community',
            'install_url': install_url,
        }
        if skill.get('tags'):
            entry['tags'] = ','.join(str(t) for t in skill['tags'] if t)
        results.append(entry)
        if len(results) >= limit:
            break
    return results, cached


def _load_hermes_index(cache_file: Path | None) -> Dict[str, Any]:
    """加载 hermes-index JSON, 带缓存 (TTL 6h). 失败时用过期缓存兜底.

    返回的 dict 里加 `_cached: True/False` 标记是否用本地缓存.
    """
    if cache_file and cache_file.is_file():
        try:
            age = time.time() - cache_file.stat().st_mtime
            if age < HERMES_INDEX_TTL:
                data = json.loads(cache_file.read_text(encoding='utf-8'))
                data['_cached'] = True
                return data
        except Exception:
            pass  # 缓存损坏, 重新下载

    # 拉远程
    text = _http_get_text(HERMES_INDEX_URL, timeout=60)
    data = json.loads(text)

    # 写缓存 (best-effort)
    if cache_file:
        try:
            cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    data['_cached'] = False
    return data


def _build_install_url(skill: Dict[str, Any]) -> str | None:
    """从 hermes-index 的 skill 条目推导 install_skill 能识别的 URL.

    hermes-index 每条字段: {name, description, source, identifier, trust_level, repo, path, tags, extra}
    - source=github: 拼 https://github.com/{repo}[/tree/main/{path}]
    - source=skills-sh: 用 extra.detail_url 或拼 https://www.skills.sh/{owner}/{repo}/{skill}
    - source=well-known/lobehub/browse-sh/clawhub: 跳过 (装包路径各异, 暂不支持)
    """
    src = skill.get('source') or ''
    repo = skill.get('repo') or ''
    path = skill.get('path') or ''
    extra = skill.get('extra') or {}
    identifier = skill.get('identifier') or ''

    if src == 'github' and repo:
        # repo 格式 "owner/name"
        url = f'https://github.com/{repo}'
        if path:
            # path 是 skill 目录 (相对 repo 根)
            url += f'/tree/main/{path}'
        return url

    if src in ('skills-sh', 'skills.sh'):
        # skills.sh 详情页国内访问常不稳 (SSL EOF), 优先用 extra.repo_url 走 GitHub 直连
        repo_url = extra.get('repo_url')
        if repo_url and repo and repo in repo_url:
            # repo_url 通常是 github.com/owner/repo (裸 repo URL)
            # 拿 identifier 末尾段当 path: "skills-sh:owner/repo/skill-name" -> skill-name
            # 但 identifier 末尾段是 skill name (不是 repo 内路径), 直接给裸 repo URL
            # install_skill 会自动在 repo 里找 SKILL.md
            return repo_url
        # 没有 repo_url -> 回退 detail_url (走 skills.sh 详情页)
        detail = extra.get('detail_url')
        if detail:
            return detail
        # 兜底: 从 identifier "skills-sh:owner/repo/skill" 拼 skills.sh URL
        if identifier.startswith('skills-sh:'):
            tail = identifier.split(':', 1)[1]
            return f'https://www.skills.sh/{tail}'
        if identifier.startswith('skills.sh:'):
            tail = identifier.split(':', 1)[1]
            return f'https://www.skills.sh/{tail}'

    # 其他 source 暂不支持直接装 (用户可以自己上网站找 URL 再装)
    return None


# ----------------------------------------------------------------------
# skills.sh /api/search 兜底
# ----------------------------------------------------------------------

def _search_skills_sh(query: str, limit: int) -> List[Dict[str, str]]:
    """直接调 skills.sh 搜索 API (无缓存, 实时)."""
    from urllib.parse import urlencode
    url = f'{SKILLS_SH_SEARCH_URL}?{urlencode({"q": query, "limit": limit})}'
    text = _http_get_text(url, timeout=20)
    data = json.loads(text)
    skills = data.get('skills', []) if isinstance(data, dict) else data
    if not isinstance(skills, list):
        return []

    results: List[Dict[str, str]] = []
    for item in skills:
        if not isinstance(item, dict):
            continue
        # skills.sh /api/search 返回字段: name, description, owner, repo, skill, url 等
        name = item.get('skill') or item.get('name') or ''
        desc = item.get('description') or ''
        owner = item.get('owner') or ''
        repo = item.get('repo') or ''
        # skills.sh 详情页 URL
        if owner and repo and name:
            install_url = f'https://www.skills.sh/{owner}/{repo}/{name}'
        elif item.get('url'):
            install_url = item['url']
        else:
            continue
        results.append({
            'name': name,
            'description': desc[:200],
            'source': 'skills-sh',
            'trust_level': 'community',
            'install_url': install_url,
        })
        if len(results) >= limit:
            break
    return results
