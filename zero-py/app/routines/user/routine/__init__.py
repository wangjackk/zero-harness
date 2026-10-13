"""routine 命名空间 ---- routine 系统自身的运维口(目录条目 manifest).

- routine/list: 全量清单(前端 Routine 面板与 skill 库软依赖)
- routine/doc: 取单个 routine 的 doc/schema(skill 库 routine-creator 教
  LLM 调用的软依赖)
- routine/check_name: 注册名规范校验(创建前静态检查)
- routine/reload: 手动触发热重载(watcher 之外的显式入口)

re-export 的 Routine 类经 routines.yaml 目录条目(``- routines/user/
routine``)注册; ``__all__`` 即 manifest.
"""
from .list import ListRoutines
from .doc import RoutineDoc
from .check_name import CheckRoutineName
from .reload import ReloadRoutines

__all__ = ['ListRoutines', 'RoutineDoc', 'CheckRoutineName', 'ReloadRoutines']
