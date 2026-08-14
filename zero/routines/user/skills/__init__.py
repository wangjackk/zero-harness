"""通用 skill routines ---- 公共层, 供各 agent 专用 wrapper 通过 ``self.call`` 调用.

本 ``__init__`` 即包 manifest: re-export 的 Routine 类在目录条目整包加载时注册.
"""
from .list_skills import ListSkills
from .load_skill import LoadSkill
from .install_skill import InstallSkill
from .uninstall_skill import UninstallSkill
from .search_skill import SearchSkill

__all__ = ['ListSkills', 'LoadSkill', 'InstallSkill', 'UninstallSkill', 'SearchSkill']
