"""
RoutinesWatcher ---- routines.yaml + routine 源码变更自动热重载.
检测: mtime 轮询(1s, 零依赖, Windows 稳) + 500ms debounce(编辑器保存常连串写).
"""
from __future__ import annotations

import asyncio
import importlib
from asyncio import Event
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set

import yaml

from routine import Routine
from routine.errors import ReloadError, RegisterError
from routine.logger import setup_logger

from .loader import (apply_yaml_kwargs, load_entries, _parse_entries,
                     _to_dotted, _PACKAGE_ROOT)

_log = setup_logger('routines_watcher')

_POLL_INTERVAL = 1.0
_DEBOUNCE = 0.5
_YAML_PATH = _PACKAGE_ROOT / 'routines.yaml'


class RoutinesWatcher(Routine):
    """yaml + 源码变更 → 自动 register/reload/deregister."""

    name = 'routines_watcher'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '监控 routines.yaml 与 routine 源码变更, 自动热重载 (HMR).',
    }

    def __init__(self):
        super().__init__()
        self._stop_event: Event | None = None
        self._mtimes: Dict[Path, float] = {}
        self._yaml_mtime: float = 0.0
        # [(path, kwargs)] ---- yaml 条目快照 (diff 基准)
        self._entries: List[tuple[str, Dict[str, Any]]] = []

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        self._stop_event = Event()

    async def on_started(self) -> None:
        self._entries = _read_entries()
        self._yaml_mtime = _mtime(_YAML_PATH)
        self._mtimes = {p: _mtime(p) for p in self._watched_files()}
        asyncio.create_task(self._watch_loop())

    async def run(self, kwargs: Dict[str, Any]):
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    # --- 监控集 ---

    def _watched_files(self) -> Set[Path]:
        """yaml 条目展开成 .py 集合(目录条目含包内全部 .py 与各层 __init__)."""
        files: Set[Path] = {_YAML_PATH}
        for rel, _kw in self._entries:
            target = _PACKAGE_ROOT / rel
            if target.is_dir():
                files.update(_walk_package_files(target))
            elif target.is_file():
                files.add(target)
        return files

    # --- 主循环 ---

    async def _watch_loop(self) -> None:
        while self._stop_event and not self._stop_event.is_set():
            try:
                m = _mtime(_YAML_PATH)
                if m != self._yaml_mtime:
                    self._yaml_mtime = m
                    await self._apply_yaml_change()
                    continue

                changed = [p for p, old in self._mtimes.items() if _mtime(p) != old]
                if changed:
                    await asyncio.sleep(_DEBOUNCE)
                    await self._reload_sources(changed)
            except Exception:
                _log.exception('watch loop error')
            await asyncio.sleep(_POLL_INTERVAL)

    async def _apply_yaml_change(self) -> None:
        """yaml 变更: 读一次 → diff 条目 → 新增 register / 移除 deregister / kwargs 变化 reload."""
        new_entries = _read_entries()
        old_map = dict(self._entries)
        new_map = dict(new_entries)
        added = [p for p in new_map if p not in old_map]
        removed = [p for p in old_map if p not in new_map]
        kw_changed = [p for p in new_map if p in old_map and new_map[p] != old_map[p]]
        self._entries = new_entries

        # kwargs 变化: 用刚读的 entries 重注入类(不重读文件) + reload 受影响
        # routine ---- kernel reload 覆盖路由(带新 passive_kwargs) + 停老实例
        # + auto-start 新实例, run(kwargs) 拿到新值.
        if kw_changed:
            try:
                apply_yaml_kwargs(new_entries)
                hub = self.ctx.hub
                if hub is not None:
                    for rel in kw_changed:
                        for dotted in self._entries_to_dotteds([rel]):
                            for cls in _collect_pub(importlib.import_module(dotted)):
                                try:
                                    await hub.reload_routine(cls)
                                    _log.info('yaml~: %s kwargs updated, reloaded %s',
                                              rel, cls.name)
                                except ReloadError as exc:
                                    _log.warning('yaml~ reload %s failed: %s', cls.name, exc)
            except Exception as exc:
                _log.warning('yaml~ apply kwargs failed: %r', exc)

        hub = self.ctx.hub
        if hub is None:
            return

        if added:
            rs = load_entries(new_entries)
            for dotted in self._entries_to_dotteds(added):
                for cls in rs.get_routines():
                    if cls.__module__ == dotted or cls.__module__.startswith(dotted + '.'):
                        try:
                            await hub.register_routine(cls)
                            _log.info('yaml+: registered %s', cls.name)
                        except RegisterError as exc:
                            _log.warning('yaml+ register %s failed: %s', cls.name, exc)

        for rel in removed:
            for dotted in self._entries_to_dotteds([rel]):
                for cls in _collect_pub(importlib.import_module(dotted)):
                    try:
                        await hub.deregister_routine(cls.name)
                        _log.info('yaml-: deregistered %s', cls.name)
                    except Exception as exc:
                        _log.warning('yaml- deregister %s failed: %s', cls.name, exc)

        self._mtimes = {p: _mtime(p) for p in self._watched_files()}

    async def _reload_sources(self, changed: List[Path]) -> None:
        """源码变更: reload 模块链 + 重注入 yaml kwargs + hub.reload_routine(新类)."""
        hub = self.ctx.hub
        if hub is None:
            return

        dotted_changed: Set[str] = set()
        for p in changed:
            if p.name == '__init__.py':
                dotted_changed.add(_to_dotted(
                    p.relative_to(_PACKAGE_ROOT).as_posix().rsplit('/', 1)[0]))
            else:
                dotted_changed.add(_to_dotted(p.relative_to(_PACKAGE_ROOT).as_posix()))

        reloaded_classes: Set[type] = set()
        for dotted in dotted_changed:
            try:
                mod = importlib.reload(importlib.import_module(dotted))
            except Exception:
                _log.exception('reload module %s failed', dotted)
                continue
            for cls in _collect_pub(mod):
                if cls.__module__ == mod.__name__:   # 只 reload 本模块定义的类
                    reloaded_classes.add(cls)

        # reload 后类是重新定义的干净声明, yaml kwargs 注入已丢 ----
        # 用持有的 entries 快照重注入(不读文件), 新 kwargs 随 reload 推给 kernel.
        if reloaded_classes:
            apply_yaml_kwargs(self._entries)

        for cls in reloaded_classes:
            try:
                await hub.reload_routine(cls)
                _log.info('🔄 reloaded %s', cls.name)
            except ReloadError as exc:
                _log.warning('reload %s failed: %s', cls.name, exc)

        self._mtimes = {p: _mtime(p) for p in self._watched_files()}

    def _entries_to_dotteds(self, entries: List[str]) -> List[str]:
        """条目 path → 模块 dotted. 目录条目就是该包 ``__init__.py`` 一个
        dotted ---- 与 loader 一致(manifest 语义), 不递归扫子包."""
        return [_to_dotted(rel) for rel in entries]


def _read_entries() -> List[tuple[str, Dict[str, Any]]]:
    """yaml → [(path, kwargs)] (dict 复用 loader 解析, 保证两边语义一致)."""
    data = yaml.safe_load(_YAML_PATH.read_text(encoding='utf-8')) or {}
    return _parse_entries(data)


def _walk_package_files(pkg_dir: Path) -> Set[Path]:
    """目录条目展开: 包内各层 .py(含 __init__; 跳过 _ 前缀与 test)."""
    files: Set[Path] = {pkg_dir / '__init__.py'}
    stack = [pkg_dir]
    while stack:
        d = stack.pop()
        for child in sorted(d.iterdir()):
            if child.is_dir():
                if child.name == '__pycache__' or child.name.startswith('_'):
                    continue
                if (child / '__init__.py').exists():
                    stack.append(child)
            elif child.suffix == '.py':
                if child.name.startswith('test_') or child.name.endswith('_test.py'):
                    continue
                files.add(child)
    return files


def _collect_pub(module) -> List[type]:
    """收集模块内的公开 Routine 子类(不比 __module__).

    优先认 ``__all__``(与 loader._collect 一致 ---- manifest 声明什么收什么),
    未声明退回 ``dir()``.
    """
    from routine import Routine
    exported = getattr(module, '__all__', None)
    names: List[str] = list(exported) if exported is not None else dir(module)
    out: List[type] = []
    seen: Set[type] = set()
    for name in names:
        cls = getattr(module, name, None)
        if not isinstance(cls, type) or not issubclass(cls, Routine):
            continue
        if cls is Routine or cls in seen or name.startswith('_'):
            continue
        if getattr(cls, '__abstractmethods__', None):
            continue
        seen.add(cls)
        out.append(cls)
    return out


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0
