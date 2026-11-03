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
    ``is_passive``(PassiveConfig = passive + auto-start 默认入参), 随 catalog
    推给 kernel; auto-start 时 ``Execute(name, kwargs)`` 带参拉起, ``run(kwargs)``
    自然收到 ---- 配置随注册一次流动, routine 内无需回头读 yaml.

name/schema/doc 以 Routine 类声明为唯一事实源.

启动: **清单路径是进程级的一次性事实源**. ``main.py`` 拿命令行位置参数
(或 ``ZERO_ROUTINES_YAML``) 调 ``set_yaml_path`` 定一次, loader 与 watcher 都
从 ``yaml_path()`` 取 ---- 两个读盘点必须同源, 否则热重载盯着另一份文件, 改了
清单什么都不会发生而且不报错. 相对路径按**当前目录**解析(命令行惯例), 而 yaml
条目自身仍相对 app/ 解析: 两套基准各管一件事(清单在哪 / routine 在哪).

``check_yaml`` 在 kernel 拉起前预检(文件在 / 能解析 / 条目非空 / 路径存在),
不通过就退出 ---- 一个没有任何 routine 的空壳进程比启动失败更难查.

清单可选带 ``modules:`` 段 = **kernel 的模块树**(见 ``yaml_modules``): 只有
自成一体的装配需要它(moss 的 ``code`` 在 app 的 tree.json 里没有节点), 不写就
用入口的 tree.json. 树里没有的模块 start 时直接 KeyError, 所以"换一套身体"必须
连树一起换.

引导: ``RoutinesLoader`` 是唯一静态注册的 passive routine, kernel 自动拉起后
运行时注册 yaml 里的其余全部 routine, 注册完成即自然退出(passive 无监督重拉,
kernel 不会循环重启).
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Set, Type

import yaml

from routine import PassiveConfig, Routine, Routines
from routine.errors import RegisterError
from routine.logger import setup_logger

_log = setup_logger('routines_loader')

# app 包根 (zero/) ---- 条目 path (routines/user/...) 与 routines.yaml 的解析基准
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
# 本模块完整名 app.routines.loader → 包根模块名 app
_ROOT_PACKAGE = __name__.split('.')[0]

_DEFAULT_YAML = _PACKAGE_ROOT / 'routines.yaml'
# 本进程读哪份清单: 启动时定一次, 之后只读. 不散落第二份常量 ----
# watcher 若自己记一份路径, 就会出现"改了清单没人管"的静默坏行为.
_yaml_path: Path = _DEFAULT_YAML


def set_yaml_path(path: str | Path | None = None) -> Path:
    """定下本次进程的清单路径, 返回解析后的绝对路径(启动时调一次).

    优先级: 显式参数(命令行位置参数) > ``ZERO_ROUTINES_YAML`` > app/routines.yaml.
    相对路径按**当前工作目录**解析 ---- 与 shell 惯例一致; 环境变量那条同样.
    """
    global _yaml_path
    raw = str(path or '').strip() or os.environ.get('ZERO_ROUTINES_YAML', '').strip()
    if raw:
        p = Path(raw).expanduser()
        _yaml_path = p if p.is_absolute() else (Path.cwd() / p)
    else:
        _yaml_path = _DEFAULT_YAML
    return _yaml_path


def yaml_path() -> Path:
    """本次进程的清单路径 ---- loader 与 watcher 的唯一读盘基准."""
    return _yaml_path


def check_yaml(path: Path | str | None = None) -> List[tuple[str, Dict[str, Any]]]:
    """启动前预检, 通过则返回条目.

    只查结构不 import: 预检跑在 kernel 拉起之前, 不该触发业务模块的导入
    副作用(那些留在 ``load_entries`` 里). 四条判据 ---- 文件在 / 能解析 /
    条目非空 / 每个条目路径存在; 任一不过就抛 ``ValueError``, 由入口决定
    退出而不是带着空注册表继续跑.
    """
    p = Path(path) if path else _yaml_path
    if not p.is_file():
        raise ValueError(f'清单不存在: {p}')
    entries = _read_yaml(p)          # 解析失败/条目为空在这里抛
    missing = [rel for rel, _kw in entries if not _entry_exists(rel)]
    if missing:
        raise ValueError(f'清单条目路径不存在(基准 {_PACKAGE_ROOT}): '
                         + ', '.join(missing))
    return entries


def _parse_entries(data: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    """yaml "routines" 列表 → [(path, kwargs)].

    纯字符串条目 → (path, {}); dict 条目 → (path, kwargs), 缺 path / kwargs 非
    mapping 报 ValueError.
    """
    if not isinstance(data, dict):
        # 顶层写成列表/标量时不要让它冒 AttributeError: 预检只接 ValueError,
        # 而"清单结构写错"是最常见的启动失败.
        raise ValueError('顶层必须是 mapping(形如 routines: [...]), '
                         f'实际是 {type(data).__name__}')
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


def yaml_modules(path: Path | str | None = None) -> Dict[str, Dict[str, Any]]:
    """清单可选的 ``modules:`` 段 ---- kernel 的模块树(没写返回 ``{}``).

    只有"自成一体的装配"需要它: moss 的身体部件(code)在 app 的 tree.json 里
    没有节点, 树里没有的模块 start 时直接 KeyError. 空 dict = 清单没声明,
    入口照旧用 tree.json.
    """
    p = Path(path) if path else _yaml_path
    data = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    mods = data.get('modules') or {}
    if not isinstance(mods, dict):
        raise ValueError(f'{p}: modules 段必须是 {{id: {{...}}}} 映射')
    out: Dict[str, Dict[str, Any]] = {}
    for mid, node in mods.items():
        if node is None:
            node = {}
        if not isinstance(node, dict):
            raise ValueError(f'{p}: modules.{mid} 必须是 mapping')
        out[str(mid)] = node
    return out


def _entry_exists(rel: str) -> bool:
    """条目是存在的 .py 文件或目录(基准 app/)."""
    target = _PACKAGE_ROOT / rel
    return target.is_dir() or (target.is_file() and rel.endswith('.py'))


def _read_yaml(path: Path | str | None = None) -> List[tuple[str, Dict[str, Any]]]:
    yaml_path_ = Path(path) if path else _yaml_path
    entries = _parse_entries(
        yaml.safe_load(yaml_path_.read_text(encoding='utf-8')) or {})
    if not entries:
        raise ValueError(f'{yaml_path_}: "routines" 列表为空')
    return entries


def _entry_classes(rel: str, seen: Set[Type[Routine]]) -> List[Type[Routine]]:
    """条目 path → 该条目暴露的 Routine 类(import + collect).

    目录条目 import 该包 ``__init__.py``(import 链自然牵出 re-export 的
    子包 routine); 文件条目 import 该模块. 收集一律只认 ``__all__``.
    """
    if not _entry_exists(rel):
        raise ValueError(f'条目既非存在的 .py 文件也非目录: {rel}')
    return _collect(importlib.import_module(_to_dotted(rel)), seen)


def _inject_passive_kwargs(classes: List[Type[Routine]], kw: Dict[str, Any]) -> None:
    """yaml 条目 kwargs 覆盖式注入 passive 类的 ``is_passive``(PassiveConfig).

    覆盖不叠加: yaml kwargs 是部署配置的完整声明. 类代码里的 PassiveConfig
    默认仅在条目没写 kwargs 时保留. 非 passive 类忽略(手动 create 传参, 与
    yaml 无关). 注入保持 ``PassiveConfig(kwargs={...})`` 形态.
    """
    if not kw:
        return
    for cls in classes:
        if cls.is_passive:
            cls.is_passive = PassiveConfig(kwargs=dict(kw))


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
    """routines/user/ask.py → app.routines.user.ask."""
    rel = rel[:-3] if rel.endswith('.py') else rel
    if rel.endswith('/__init__'):
        rel = rel[: -len('/__init__')]
    return f'{_ROOT_PACKAGE}.{rel.replace("/", ".")}'


def _collect(module, seen: Set[Type[Routine]]) -> List[Type[Routine]]:
    """只认 ``__all__``(manifest 语义): 声明什么注册什么.

    注册清单的唯一事实源是显式声明,没有静默 fallback ---- 未声明
    ``__all__`` 立即报错; manifest 里的非 Routine 项(pydantic 类型等
    公共导出)跳过不注册. 排除 Routine 基类、_ 前缀私有名、抽象基类;
    seen 全局去重.
    """
    exported = getattr(module, '__all__', None)
    if exported is None:
        raise ValueError(f'{module.__name__}: 缺少 __all__ manifest')
    out: List[Type[Routine]] = []
    for name in exported:
        cls = getattr(module, name, None)
        if not isinstance(cls, type) or not issubclass(cls, Routine):
            # manifest 里的非 Routine 项(pydantic 类型等公共导出)跳过
            continue
        if cls is Routine or cls in seen:
            continue
        if name.startswith('_') or getattr(cls, '__abstractmethods__', None):
            continue
        if not cls.name:
            # 没有名字的不是 routine, 是基类(chan 族/容器/黑板笔的公共父类):
            # 包 __all__ 导出它们是为了 import, 不是为了让它们进注册表.
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
        path = yaml_path()
        try:
            rs = load_from_yaml(path)
        except Exception as exc:
            # 带 traceback: %r 只留异常字符串, 双加载类错误(如 C 扩展
            # PyInit 两次)看不出肇事模块.
            _log.error('load %s failed: %r', path, exc, exc_info=exc)
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
                _log.info('✅ %s', cls.name)
            except RegisterError:
                skipped.append(cls.name)
        _log.info('loader: %s → registered %d, skipped %d (already in kernel): %s',
                  path.name, len(registered), len(skipped), skipped)
