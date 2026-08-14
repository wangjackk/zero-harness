"""routines.yaml 配置驱动加载 + 引导 routine.

yaml 声明"启用哪些模块", 两种条目粒度:
  - 文件条目 (xxx.py): import 该模块, 注册其命名空间内的 Routine 子类
  - 目录条目 (pkg/):   import 该包 ``__init__.py``, 注册其 re-export 的
                       Routine 类 ---- ``__init__`` 即包的 manifest, re-export
                       什么就注册什么(子包经由 ``__init__`` 的 import 链自然
                       引入, loader 不主动递归扫子目录)
两种条目形态:
  - 纯字符串: ``- routines/user/ask.py``
  - dict (带 kwargs): passive routine 的启动配置. 注册时 kwargs 注入类的
    ``is_passive``(dict 形态 = passive + auto-start 默认入参), 随 catalog
    推给 kernel; auto-start 时 ``Execute(name, kwargs)`` 带参拉起, ``run(kwargs)``
    自然收到 ---- 配置随注册一次流动, routine 内无需回头读 yaml.

name/schema/doc 以 Routine 类声明为唯一事实源.

引导: ``RoutinesLoader`` 是唯一静态注册的 passive routine, kernel 自动拉起后
运行时注册 yaml 里的其余全部 routine, 注册完成即自然退出(passive 无监督重拉,
kernel 不会循环重启).
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set, Type

import yaml

from routine import Routine, Routines
from routine.errors import RegisterError
from routine.logger import setup_logger

_log = setup_logger('routines_loader')

# zero 包根 (…/zero-harness/zero)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
# 本模块完整名 zero.routines.loader → 包根模块名 zero
_ROOT_PACKAGE = __name__.split('.')[0]


def _parse_entries(data: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    """yaml "routines" 列表 → [(path, kwargs)].

    纯字符串条目 → (path, {}); dict 条目 → (path, kwargs), 缺 path / kwargs 非
    mapping 报 ValueError.
    """
    out: List[tuple[str, Dict[str, Any]]] = []
    for e in (data.get('routines') or []):
        if isinstance(e, dict):
            rel = str(e.get('path') or '').strip().replace('\\', '/')
            if not rel:
                raise ValueError('routines.yaml: dict 条目缺 path')
            kw = e.get('kwargs') or {}
            if not isinstance(kw, dict):
                raise ValueError(f'{rel}: kwargs 必须是 mapping')
            out.append((rel, kw))
        else:
            out.append((str(e).strip().replace('\\', '/'), {}))
    return out


def _read_yaml(path: Path | str | None = None) -> List[tuple[str, Dict[str, Any]]]:
    yaml_path = Path(path) if path else _PACKAGE_ROOT / 'routines.yaml'
    entries = _parse_entries(yaml.safe_load(yaml_path.read_text(encoding='utf-8')) or {})
    if not entries:
        raise ValueError(f'{yaml_path}: "routines" 列表为空')
    return entries


def _entry_classes(rel: str, seen: Set[Type[Routine]]) -> List[Type[Routine]]:
    """条目 path → 该条目暴露的 Routine 类(import + collect).

    目录条目只 import 该包 ``__init__.py``(import 链自然牵出 re-export 的
    子包 routine); 文件条目只收模块内定义的类.
    """
    target = _PACKAGE_ROOT / rel
    if target.is_dir():
        dotted, imported_ok = _to_dotted(rel), True
    elif target.is_file() and rel.endswith('.py'):
        dotted, imported_ok = _to_dotted(rel), False
    else:
        raise ValueError(f'条目既非存在的 .py 文件也非目录: {rel}')
    return _collect(importlib.import_module(dotted), seen, imported_ok)


def _inject_passive_kwargs(classes: List[Type[Routine]], kw: Dict[str, Any]) -> None:
    """yaml 条目 kwargs 覆盖式注入 passive 类的 ``is_passive``(dict 形态).

    覆盖不叠加: yaml kwargs 是部署配置的完整声明. 类代码里的 dict 默认仅在
    条目没写 kwargs 时保留. 非 passive 类忽略(手动 submit 传参, 与 yaml 无关).
    """
    if not kw:
        return
    for cls in classes:
        if cls.is_passive:
            cls.is_passive = dict(kw)


def load_entries(entries: List[tuple[str, Dict[str, Any]]]) -> Routines:
    """已解析 yaml 条目 → import 模块 → 收集 Routine 子类 → 注入 kwargs → 注册.

    与 ``_read_yaml`` 分离: 热路径(watcher)已持有解析结果, 直接传入不重读文件.
    """
    rs = Routines()
    seen: Set[Type[Routine]] = set()
    for rel, kw in entries:
        classes = _entry_classes(rel, seen)
        if not classes:
            raise ValueError(f'{rel}: 未暴露任何 Routine 子类')
        _inject_passive_kwargs(classes, kw)
        rs.register(*classes)
    return rs


def load_from_yaml(path: Path | str | None = None) -> Routines:
    """读 routines.yaml 一次 → ``load_entries``. yaml 全生命周期唯一读盘点."""
    return load_entries(_read_yaml(path))


def apply_yaml_kwargs(entries: List[tuple[str, Dict[str, Any]]]) -> None:
    """重注入已解析条目的 kwargs 进对应 passive 类(watcher 热路径用).

    接受 ``_read_yaml`` 的解析结果(不重读文件): ``.py`` reload 后类是重新
    定义的干净声明 / kwargs 变更后需换新值 ---- 覆盖式重注入(幂等), 随后
    reload 让新 kwargs 随 catalog 推给 kernel.
    """
    for rel, kw in entries:
        _inject_passive_kwargs(_entry_classes(rel, set()), kw)


def _to_dotted(rel: str) -> str:
    """routines/user/ask.py → zero.routines.user.ask."""
    rel = rel[:-3] if rel.endswith('.py') else rel
    if rel.endswith('/__init__'):
        rel = rel[: -len('/__init__')]
    return f'{_ROOT_PACKAGE}.{rel.replace("/", ".")}'


def _collect(module, seen: Set[Type[Routine]], imported_ok: bool) -> List[Type[Routine]]:
    """收集 module 内的 Routine 子类.

    优先认 ``__all__``(声明了就只收声明项 ---- manifest 语义, import 链带上
    来的基类/辅助类不误注册); 未声明退回 ``dir()`` 全命名空间.
    imported_ok=True: 不比 ``__module__`` ---- 目录条目, re-export 什么收什么.
    imported_ok=False: 只收模块内**定义**的类(比 ``__module__``) ---- 文件条目,
                       防止 import 链上的基类/引擎类被误注册.
    排除 Routine 基类、_ 前缀私有类、抽象基类; seen 全局去重.
    """
    exported = getattr(module, '__all__', None)
    names: List[str] = list(exported) if exported is not None else dir(module)
    out: List[Type[Routine]] = []
    for name in names:
        cls = getattr(module, name, None)
        if not isinstance(cls, type) or not issubclass(cls, Routine):
            continue
        if cls is Routine or cls in seen:
            continue
        if not imported_ok and cls.__module__ != module.__name__:
            continue
        if name.startswith('_') or getattr(cls, '__abstractmethods__', None):
            continue
        seen.add(cls)
        out.append(cls)
    return out


class RoutinesLoader(Routine):
    """引导 routine: 被自动拉起后按 routines.yaml 注册其余全部 routine.

    唯一静态注册的 routine(引导扇区); yaml 不列自己 元层不进清单.
    注册完成 run() 自然返回 ---- 实例 auto 停止, kernel 不重拉(passiveStarted
    去重), 后续热更新由 watcher 接管.
    """

    name = 'routines_loader'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '引导 routine: 拉起后按 routines.yaml 运行时注册其余全部 routine.',
    }

    async def run(self, kwargs: Dict[str, Any]):
        """load_from_yaml → 逐个 hub.register_routine(等 kernel 回执) → 退出.

        同名冲突(重连场景 kernel 路由已在,重连 push 已兜底)跳过不视为错误.
        passive routine 注册成功由 kernel 同步 auto-start(catalog.go handleCatalogRegister).
        """
        try:
            rs = load_from_yaml()
        except Exception as exc:
            _log.error('load routines.yaml failed: %r', exc)
            return
        hub = self.ctx.hub
        if hub is None:
            _log.error('no RoutineHub on ctx.hub')
            return

        registered: List[str] = []
        skipped: List[str] = []
        for cls in rs.get_routines():
            try:
                await hub.register_routine(cls)
                registered.append(cls.name)
            except RegisterError:
                skipped.append(cls.name)
        _log.info('loader: registered %d routines, skipped %d (already in kernel): %s',
                  len(registered), len(skipped), skipped)
