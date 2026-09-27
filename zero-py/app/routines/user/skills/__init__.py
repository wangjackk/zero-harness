"""skill 家族 routines.

命名约定: 层在前缀里 (local / repo / web 来源).
  - skill/list / skill/load: agent 有效视图 (local + repo 合并, 渐进加载)
  - skill/repo/*: 共享库 CRUD (repo_list / repo_get / repo_pull / repo_publish / repo_delete)
  - skill/search / skill/fetch: 从网络找 skill / 装 skill (skill 市场领域,
    不是通用 web search —— 那是另一个领域的东西)
  - local 层无 routine: workspace 只是个目录 (读走合并视图, 写草稿是文件操作)
"""
from .fetch import SkillsFetch
from .list import SkillsList
from .load import SkillsLoad
from .repo_delete import SkillsRepoDelete
from .repo_get import SkillsRepoGet
from .repo_list import SkillsRepoList
from .repo_publish import SkillsRepoPublish
from .repo_pull import SkillsRepoPull
from .search import SkillsSearch

__all__ = [
    'SkillsFetch',
    'SkillsList',
    'SkillsLoad',
    'SkillsRepoDelete',
    'SkillsRepoGet',
    'SkillsRepoList',
    'SkillsRepoPublish',
    'SkillsRepoPull',
    'SkillsSearch',
]
