"""
RoutinesWatcher ---- routines.yaml + routine 源码变更自动热重载.
检测: mtime 轮询(1s, 零依赖, Windows 稳) + 500ms debounce(编辑器保存常连串写).
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from asyncio import Event
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

import yaml

from routine import Routine, request
from routine.errors import ReloadError, RegisterError
from routine.logger import setup_logger

from .loader import (apply_yaml_kwargs, load_entries, _parse_entries,
                     _to_dotted, _PACKAGE_ROOT)

__all__ = ['RoutinesWatcher']

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
        # dotted -> 上次 reload 错误文本 (失败日志去重 + 恢复上报)
        self._reload_errors: Dict[str, str] = {}
        # 专属单线程 executor: stat/snapshot 不走 asyncio.to_thread 的默认
        # 共享池 ---- 默认池被其他模块占满时 (阻塞任务积压), watcher 的
        # poll 会集体挂起, 表现为热载停摆 + reload 逃生口超时 (实测复现).
        # 单线程够用: poll 串行, stat 走线程只为不阻塞 loop.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix='routines-watcher')

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        self._stop_event = Event()

    async def on_started(self) -> None:
        self._entries = _read_entries()
        self._yaml_mtime = _mtime(_YAML_PATH)
        self._mtimes = {p: _mtime(p) for p in self._watched_files()}
        asyncio.create_task(self._watch_loop())

    async def run(self, kwargs: Dict[str, Any]):
        await self._stop_event.wait()

    @request('reload')
    async def _req_reload(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """手动强制重载(poll 逃生口): ``{'path': <yaml 条目路径>}`` (必填).

        path 精确或前缀匹配 yaml 条目: 目录条目展开为包内全部 .py, 文件条目
        为单文件 ---- 复用 ``_reload_sources`` 同一条链路, 语义与自动热重载
        完全一致(manifest diff deregister + yaml kwargs 重注入 + upsert).
        不支持全量重载: 必须指到具体条目, 防止误杀运行中的会话.
        """
        path = str((data or {}).get('path') or '').strip()
        if not path:
            return {'error': "path is required ('path': <yaml entry path>); "
                             'full reload is not allowed',
                    'reloaded': []}
        target = path.rstrip('/')
        rels = [rel for rel, _ in self._entries
                if rel == target or rel.startswith(target + '/')]
        if not rels:
            return {'error': f'no yaml entry matches {target!r}',
                    'reloaded': []}
        changed: Set[Path] = set()
        for rel in rels:
            t = _PACKAGE_ROOT / rel
            if t.is_dir():
                changed.update(_walk_package_files(t))
            elif t.is_file():
                changed.add(t)
        if not changed:
            return {'error': 'no source files resolved', 'reloaded': []}
        return await self._reload_sources(sorted(changed))

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        self._executor.shutdown(wait=False)

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

    def _snapshot(self) -> Dict[Path, float]:
        """watched 文件 mtime 快照 + 删除墓碑.

        仍处条目 cone 内的已删文件保留 mtime=0 条目 ---- 重建后 mtime≠0
        被 poll 检出为变更, 自动恢复注册("文件重建即恢复"); 条目已移除的
        不再保留.
        """
        snap = {p: _mtime(p) for p in self._watched_files()}
        for p in self._mtimes:
            if p not in snap and _mtime(p) == 0.0 and self._under_entry(p):
                snap[p] = 0.0
        return snap

    def _under_entry(self, p: Path) -> bool:
        """路径是否仍处某个 yaml 条目 cone 内(删除墓碑的保留条件)."""
        for rel, _kw in self._entries:
            t = _PACKAGE_ROOT / rel
            if p == t or t in p.parents:
                return True
        return False

    def _ancestor_dotteds(self, p: Path) -> Set[str]:
        """变更文件在目录条目 cone 内的祖先包 dotted(条目根包到直接父包全链).

        子模块 reload 不刷新包 ``__init__`` 的 re-export 绑定(manifest 在包),
        必须连带 reload 祖先包链 ---- reverse 字典序保证子模块先, 包后.
        文件条目 / 条目外文件返回空集; ``__init__.py`` 自身变更时其所属包
        dotted 已在变更集, 调用方去重.
        """
        out: Set[str] = set()
        for rel, _kw in self._entries:
            t = _PACKAGE_ROOT / rel
            if not t.is_dir() or not (t == p.parent or t in p.parents):
                continue
            for d in p.parents:
                if d == _PACKAGE_ROOT or _PACKAGE_ROOT not in d.parents:
                    break  # 越出 app 包根
                if (d / '__init__.py').exists():
                    out.add(_to_dotted(d.relative_to(_PACKAGE_ROOT).as_posix()))
                if d == t:
                    break  # 条目根即顶
            break
        return out

    # --- 主循环 ---

    async def _watch_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._stop_event and not self._stop_event.is_set():
            try:
                # stat 扫描全部走线程: 单次 stat 冷路径可达数百 ms (杀软/
                # 目录监视句柄竞争), 同步跑在 loop 上会阻塞所有 routine
                # 调用 ---- 这是全局间歇性延迟的根因. 走专属 executor,
                # 不与 asyncio.to_thread 的默认共享池混用.
                m = await loop.run_in_executor(self._executor, _mtime, _YAML_PATH)
                if m != self._yaml_mtime:
                    self._yaml_mtime = m
                    await self._apply_yaml_change()
                    continue

                changed = await loop.run_in_executor(
                    self._executor,
                    lambda: [p for p, old in self._mtimes.items()
                             if _mtime(p) != old])
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

        self._mtimes = await asyncio.get_running_loop().run_in_executor(
            self._executor, self._snapshot)

    async def _reload_sources(self, changed: List[Path]) -> Dict[str, Any]:
        """源码变更: reload 模块链 + manifest 过滤 + 重注入 yaml kwargs +
        hub.reload_routine(新类).

        重载粒度(只认 routine 定义文件与 manifest):
          - routine 的**定义文件**变更 → reload(停旧实例 + auto-start 新实例);
          - ``__init__`` 撤下导出 → deregister; 新增导出 → 注册;
          - 仍导出但定义文件未动 → 不动(改 ``__init__`` 不 stop/start 全包);
          - 其他文件(共享 helper/孤儿) → 不触发 routine 重载 ---- 无依赖图,
            依赖变更走 routine/reload 逃生口.

        - 文件删除: 注销该模块定义的 routine, 不 reload(源没了, 内存版失效);
          同时清 sys.modules 缓存, 防 manifest 重新引用时旧类"复活".
        - reload 顺序: 子模块先, 包 ``__init__`` 后(reverse 字典序) ----
          ``__init__`` 的 re-import 绑到刚 reload 的新类, 避免包命名空间
          留旧对象造成新旧类双注册竞态. 子模块单文件变更时也连带 reload
          条目 cone 内祖先包链(manifest 在包 ``__init__``, 子模块自身无
          ``__all__``, 不连带则改动到不了注册表).
        - import 失败(写到一半/manifest 先于文件/语法错)**不丢变更**: 失败文件
          的 mtime 快照保留旧值, 下轮 poll 自动视为仍有变更重试 ---- 文件与
          manifest 的编辑顺序无关, 两边写齐后 ~1.5s 内自动收敛.

        返回 ``{reloaded, deregistered, failed}`` 摘要 ---- poll 轮忽略,
        ``@request('reload')`` 手动路径透传给调用方.
        """
        hub = self.ctx.hub
        if hub is None:
            return {'error': 'no hub', 'reloaded': []}

        # B3: 文件删除 → 注销该模块定义的 routine(未注册过的静默跳过)
        existing: List[Path] = []
        deregistered: List[str] = []
        for p in sorted(changed):
            if p.exists():
                existing.append(p)
            else:
                deregistered.extend(await self._handle_deleted(p))

        file_dotted: Dict[Path, str] = {}
        for p in existing:
            rel = p.relative_to(_PACKAGE_ROOT).as_posix()
            if p.name == '__init__.py':
                rel = rel.rsplit('/', 1)[0]
            file_dotted[p] = _to_dotted(rel)

        # 只看 routine 定义文件与 manifest: 其他文件(共享 helper/孤儿)变更
        # 不触发 routine 重载 ---- 无依赖图, 依赖变更走 routine/reload 逃生口.
        failed: Set[str] = set()
        reloaded_classes: Set[type] = set()
        added_classes: Set[type] = set()

        changed_dotteds: Set[str] = {file_dotted[p] for p in existing}
        # 祖先包(条目 cone 内, 含条目根包): 子模块 reload 不刷新包 ``__init__``
        # 的 re-export 绑定, 而 manifest 恰在包 ``__init__`` ---- 必须连带
        # reload 祖先包链(re-import 绑到新类), 否则子模块改动永远到不了注册表.
        ancestor_dotteds: Set[str] = set()
        for p in existing:
            ancestor_dotteds |= self._ancestor_dotteds(p)
        ancestor_dotteds -= changed_dotteds

        for dotted in sorted(changed_dotteds | ancestor_dotteds, reverse=True):
            try:
                old = importlib.import_module(dotted)
                # reload 前快照 manifest 导出名(增删 diff 基准). 内存态可能被
                # 上一轮失败 reload 污染(缺 __all__) → 旧清单未知, 记空集:
                # 文件修好后按"新增导出"重新 upsert, 不丢恢复能力.
                try:
                    old_names = {cls.name for cls in _collect_pub(old)}
                except ValueError:
                    old_names = set()
                mod = importlib.reload(old)
                # 缺 __all__ = 非法中间态(编辑进行中) → 按失败处理:
                # 保持旧注册不动, 下轮 poll 自动重试, 修好后收敛.
                pub = _collect_pub(mod)
            except Exception as exc:
                failed.add(dotted)
                self._note_reload_error(dotted, exc)
                continue
            self._note_reload_ok(dotted)
            new_names = {cls.name for cls in pub}
            # reload 目标: 只有定义文件实际变更的 routine ----
            # 仍导出但文件未动的兄弟不动(改 __init__ 不该 stop/start 全包).
            reloaded_classes.update(
                cls for cls in pub if cls.__module__ in changed_dotteds)
            # manifest 新增导出 → 注册(类的定义文件可能没改)
            if diff := new_names - old_names:
                added_classes.update(cls for cls in pub if cls.name in diff)
            # manifest 撤下 / 类被删 → deregister
            # (实验 routine 的卸载路径, 不撤则 kernel 留僵尸路由直到重启)
            for gone in old_names - new_names:
                try:
                    await hub.deregister_routine(gone)
                    deregistered.append(gone)
                    _log.info('🔥 deregistered %s (removed from manifest)', gone)
                except Exception as exc:
                    _log.warning('deregister %s failed: %s', gone, exc)

        # B2: manifest 过滤 ---- 未被条目导出的类不上行注册(防"幽灵注册"),
        # 已注册的撤下(逃生口对僵尸的收敛语义)
        reloaded_classes, strays = self._filter_by_manifest(reloaded_classes)
        for cls in strays:
            try:
                await hub.deregister_routine(cls.name)
                deregistered.append(cls.name)
                _log.info('🔥 deregistered %s (not in manifest)', cls.name)
            except Exception:
                pass  # 未注册过(新文件先于 manifest 的中间态): 静默

        # reload 后类是重新定义的干净声明, yaml kwargs 注入已丢 ----
        # 用持有的 entries 快照重注入(不读文件), 新 kwargs 随 reload 推给 kernel.
        if reloaded_classes or added_classes:
            apply_yaml_kwargs(self._entries)

        reloaded: List[str] = []
        seen_names: Set[str] = set()
        for cls in reloaded_classes:
            try:
                await hub.reload_routine(cls)
                if cls.name not in seen_names:  # 包 + 子模块双路收集去重
                    seen_names.add(cls.name)
                    reloaded.append(cls.name)
                _log.info('🔄 reloaded %s', cls.name)
            except ReloadError as exc:
                _log.warning('reload %s failed: %s', cls.name, exc)
        for cls in added_classes - reloaded_classes:
            try:
                await hub.reload_routine(cls)  # 未注册时即注册(同名覆盖语义)
                reloaded.append(cls.name)
                _log.info('➕ registered %s (manifest add)', cls.name)
            except ReloadError as exc:
                _log.warning('register %s failed: %s', cls.name, exc)

        # mtime 快照: 失败模块的文件保留旧值 ---- 下轮 poll 检测到"仍有变更"
        # 自动重试. 新文件(旧快照没有)用 0.0, 同样触发重试. 已删文件以
        # mtime=0 墓碑保留(重建即恢复注册). (stat 全量扫走线程, 不阻塞 loop)
        snapshot = await asyncio.get_running_loop().run_in_executor(
            self._executor, self._snapshot)
        # 吞变更守卫: debounce+reload 处理窗口内落盘的文件不在本轮 changed
        # 集, _snapshot 会以当前 mtime 吸收从而吞掉变更. 保留旧值, 下轮
        # poll 重新检出 ---- IDE 重构常在百 ms 内连写 rename/__init__ 等
        # 多个文件, 极易命中窗口(表现为注册"迟到"数十秒).
        changed_set = set(changed)
        for p, cur in list(snapshot.items()):
            if p in changed_set or cur == 0.0:
                continue
            old = self._mtimes.get(p)
            if old is not None and old != cur:
                snapshot[p] = old
        for p, dotted in file_dotted.items():
            # 失败判定: 自身模块 reload 失败, 或祖先包 reload 失败(绑定没刷上,
            # 本轮改动等于没生效) ---- 都保留旧 mtime, 下轮 poll 重试.
            if (dotted in failed
                    or any(a in failed for a in self._ancestor_dotteds(p))):
                if p in snapshot:
                    snapshot[p] = self._mtimes.get(p, 0.0)
        self._mtimes = snapshot
        out: Dict[str, Any] = {'reloaded': reloaded,
                               'deregistered': deregistered}
        if failed:
            out['failed'] = sorted(failed)
        return out

    async def _handle_deleted(self, p: Path) -> List[str]:
        """文件删除: 注销该模块**定义**的 routine(源没了).

        只处理本模块定义的类(``__module__`` 精确匹配)---- ``__init__.py``
        被删时包内子模块的 routine 不受牵连(各自文件仍在). 同时清掉
        sys.modules 缓存: 陈旧模块对象会让后续 import(如 manifest 重新
        引用该名字)绑定删除前的旧类 ---- 旧代码"复活". 文件重建后走全新
        import(新类新注册), 恢复语义不变. 未注册过的(已从 manifest 撤下)
        静默跳过.
        """
        rel = p.relative_to(_PACKAGE_ROOT).as_posix()
        if p.name == '__init__.py':
            rel = rel.rsplit('/', 1)[0]
        dotted = _to_dotted(rel)
        mod = sys.modules.get(dotted)
        if mod is None:
            return []
        hub = self.ctx.hub
        out: List[str] = []
        for cls in _collect_pub(mod):
            if cls.__module__ != dotted:
                continue
            try:
                await hub.deregister_routine(cls.name)
                out.append(cls.name)
                _log.info('🔥 deregistered %s (file deleted: %s)',
                          cls.name, rel)
            except Exception:
                pass
        # 清模块缓存: 防 manifest 重新引用时从 sys.modules 复活删除前旧类
        sys.modules.pop(dotted, None)
        return out

    def _filter_by_manifest(self, classes: Set[type]) -> Tuple[Set[type], List[type]]:
        """manifest 过滤: 保留被 yaml 条目导出的类, 撤下未导出的(stray).

        - 目录条目 cone 内(``__module__`` 前缀匹配条目 dotted)的类, 必须在
          条目包 ``__init__`` 当前导出(``__all__`` / 命名空间)里 ---- 文件在
          而 manifest 撤下的 routine 不得注册.
        - 文件条目: 类的 ``__module__`` 即条目 dotted, 天然通过.
        - 不在任何条目 cone 内的类原样保留(watch 集来自条目, 兜底不误杀).
        - 条目包 import 失败(中间态)时跳过该条目过滤, 不误杀.
        """
        entry_dotteds = {_to_dotted(rel) for rel, _ in self._entries}
        allowed_cache: Dict[str, Optional[Set[str]]] = {}

        def _allowed(entry: str) -> Optional[Set[str]]:
            if entry not in allowed_cache:
                try:
                    mod = importlib.import_module(entry)
                except Exception:
                    allowed_cache[entry] = None  # 中间态: 不过滤
                else:
                    allowed_cache[entry] = {cls.name for cls in _collect_pub(mod)}
            return allowed_cache[entry]

        kept: Set[type] = set()
        strays: List[type] = []
        for cls in classes:
            owners = [e for e in entry_dotteds
                      if cls.__module__ == e or cls.__module__.startswith(e + '.')]
            if not owners:
                kept.add(cls)
                continue
            allowed: Set[str] = set()
            broken = False
            for e in owners:
                a = _allowed(e)
                if a is None:
                    broken = True
                    break
                allowed |= a
            if broken or cls.name in allowed:
                kept.add(cls)
            else:
                strays.append(cls)
        return kept, strays

    def _note_reload_error(self, dotted: str, exc: Exception) -> None:
        """记 reload 失败: 同错不重复刷屏(重试每秒跑, 日志只打文本变化时)."""
        text = f'{type(exc).__name__}: {exc}'
        if self._reload_errors.get(dotted) != text:
            _log.warning('reload %s 失败, 每秒自动重试中: %s', dotted, text)
            self._reload_errors[dotted] = text

    def _note_reload_ok(self, dotted: str) -> None:
        if dotted in self._reload_errors:
            _log.info('reload %s 恢复', dotted)
            del self._reload_errors[dotted]

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
    """只认 ``__all__``(与 loader._collect 一致 ---- manifest 声明什么收什么).

    未声明 ``__all__`` 的模块没有可注册的类(返回空) ---- watcher 只对已
    声明的 manifest 变化做热重载.
    """
    from routine import Routine
    exported = getattr(module, '__all__', None)
    out: List[type] = []
    if exported is None:
        return out
    seen: Set[type] = set()
    for name in exported:
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
