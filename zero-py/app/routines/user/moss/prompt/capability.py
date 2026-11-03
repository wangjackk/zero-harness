"""capability —— 能力面片段: 词表快照按身体分支分组渲染.

数据源两处, 都是只读快照: Shell 全局词表(registry list) + kernel
路由表的 modules/meta 声明 —— **一份声明喂三张嘴**(kernel 互斥 +
模型词表教学 + 参数签名). 装配时渲染一次, 热注册不自动刷新(要新
前缀就显式重组).

**提示词跟能力在一起**: 渲染只遍历**当前词表**, 每行由那个 routine 自己的
meta 拼出(description / input_schema / body / example). 所以能力没注入时,
它那一段说明也不会出现在前缀里 —— 不存在"全局文档讲了一堆本机没有的
名字"这种事; protocol 只讲与具体能力无关的语法与机制.

**用语只有一个**: 词表里的一行就是一个 **routine**(万物皆 routine). 这里
没有"动作/命令"这第二种东西 —— `commands/` 目录也只是分类, 标记"这些是给
moss 用的 routine", 不是另一个概念. 教学文本里出现第二种名词, 模型就会以为
存在两种调用方式, 于是去猜.

分组键 = 模块树顶层分支(身体部位); 声明跨多个分支的 routine(左手+尾巴式)
单独"跨模块"段; 不占模块的平铺"通用"段.

**两级披露**: 装配在清单里声明 ``detail:``(详列名单) —— 名单内每项 = 签名
(必填裸名, 可选带默认) + 参数说明子行(有 description 才占行) + " — 说明";
名单外**只印名字**(同组末尾一行). 理由: 前缀是每拍都在的常驻成本, 而真正
每拍都用得上的只有语法与轨(say/_/all/code/interrupt…) —— 车身能力一次对话
摸不到两次, 它的参数说明留在 ``<routine/doc name="…"/>`` 里按需取, 数据源还是
routine 自己的 meta(一处声明, 没有第二份文档).

**不印标记**. 曾经在这两处挂过 ``⟨body⟩`` / ``⟨observe⟩`` 括注, 想去通告
"内容写在哪 / 结果会不会回来"两件机制. 但机制的两头都不在清单上: body 的
写法在它自己 description 里(作者写的"body = …"), 而 observe 到底认不认,
由标签上的 ``observe="true"`` 现决定(shell 三侧同名, 见 ``shell._route``).
括注于是只是一句重复的话, 却要 20 多个名字每个都背一个 —— 名字行的职责
就只剩一件事: **让模型知道这个词存在**.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Tuple

from routine.logger import setup_logger
from routine.routine import Routine

__all__ = ['Capability']

_log = setup_logger('moss.prompt.capability')

# 词表名 → 标签面写法(只有"名字与写法不同"的才需要): flow 的实例名是
# __scope__, 标签面写 `_`; 清单里应该印 `_`, 否则模型不会想到那种写法.
_ALIAS = {'__scope__': '_'}

__all__ = ['Capability', 'canon', 'entry', 'name_only', 'param_lines',
           'signature']


def canon(name: str) -> str:
    """清单里写的名字(标签面) → 词表注册名: ``_`` → ``__scope__``.

    装配的 ``detail:`` 是给人写给人读的, 写的应该是**标签面**那个名字
    (``_``); 词表与 kernel 认的是注册名. 别名只有这一处映射, 别处都借它.
    """
    name = str(name).strip()
    for reg, shown in _ALIAS.items():
        if shown == name:
            return reg
    return name


def signature(v: str, meta: Dict[str, Any]) -> str:
    """签名 + 说明: wave(hand) — 挥手 / say — 说一句话(要说的话写在中间).

    必填参数裸名, 可选参数带默认值(None 默认裸名), 无 schema 只出名.
    **参数的单位/取值/跨字段约束不在这里** —— 那是 ``param_lines`` 的
    子行(签名一行装不下, 也不该被撑成一段).

    **body 与 observe 不在这里标**. 有没有 body 是 ``meta['body']`` 的机制
    旗(``CtmlRoutine`` 自己盖, 见 ``ctml``), 而"body 要写什么"是作者在
    ``description`` 里那句话("body = 要说的话")—— 同一个事实只说一次.
    observe 更是每次调用现决定(标签上的 ``observe="true"``), 印在常驻
    前缀里只会让模型把"出厂声明"和"这次要不要"混成一件事.
    """
    schema = meta.get('input_schema') or {}
    props = schema.get('properties') or {}
    required = set(schema.get('required') or [])
    parts = []
    for k, p in props.items():
        p = p if isinstance(p, dict) else {}
        if k in required or p.get('default') is None:
            parts.append(k)
        else:
            parts.append(f'{k}={p["default"]!r}')
    sig = f'{_ALIAS.get(v, v)}({", ".join(parts)})' if parts else \
        _ALIAS.get(v, v)
    desc = (meta.get('description') or '').strip()
    tail = f' — {desc}' if desc else ''
    return f'{sig}{tail}'


def param_lines(meta: Dict[str, Any]) -> List[str]:
    """参数说明子行(缩进在签名下): 单位 / 取值 / 跨字段约束.

    数据源是 ``input_schema`` 里每个参数的 ``description`` —— 写它的
    时候就是给"要填这个参数的人"看的(``Field(description=…)``).
    ``protocol.md`` 那句"routine 怎么用(参数/body/注意事项)看清单里它
    那一行", 落点就在这几行: 签名只印名字与默认值, 装不下的语义在这.

    只印**有**说明的参数(没写就不占行); 说明折成一行(作者可能写多行).
    """
    props = (meta.get('input_schema') or {}).get('properties') or {}
    out: List[str] = []
    for k, p in props.items():
        desc = (p or {}).get('description') if isinstance(p, dict) else ''
        desc = ' '.join(str(desc or '').split())
        if desc:
            out.append(f'  {k}: {desc}')
    return out


def example_block(meta: Dict[str, Any]) -> str:
    """示例块(缩进两格, 空行隔开): 作者写的私有提示词, 没有就是空串."""
    example = str(meta.get('example') or '').strip('\n').rstrip()
    if not example:
        return ''
    return '\n'.join(('  ' + ln) if ln.strip() else ''
                     for ln in example.splitlines())


def entry(v: str, meta: Dict[str, Any], suffix: str = '',
          seen: set | None = None) -> str:
    """一个 routine 的**完整一项**: 签名行 + 参数子行 + 示例块.

    能力面(详列项)与 ``routine/doc``(按需取)共用这一处 —— 两处各写一遍就会
    漂移: 清单里看到的和它回来的不走同一段话. ``suffix`` 是能力面
    才有的模块标注(`` (wheels)``); ``seen`` 非空时同一段 example 只印一次
    (容器族把共享结构声明在各自 meta 里, 同一份文本).
    """
    blocks = [f'- {signature(v, meta)}{suffix}']
    params = param_lines(meta)
    blocks += params
    ex = example_block(meta)
    if ex and (seen is None or ex not in seen):
        if seen is not None:
            seen.add(ex)
        if params:
            # 空行分行: 参数子行与示例块同缩进, 不隔开时"at 是基线…"
            # 那种示例首行会被读成上一个参数的续写
            blocks.append('')
        blocks.append(ex)
    return '\n'.join(blocks)


def name_only(v: str, meta: Dict[str, Any]) -> str:
    """只印名字: 签名 / 参数 / 示例都留在 ``routine/doc`` 里按需取.

    "另有: …" 那一行是**索引**, 不是说明 —— 它要回答的只有"这个词存在吗".
    一个名字后面挂一串括注, 二十个名字就变成二十段读不完的句子, 而真正
    每拍都要用的语法(``say``/``_``/``all``/``code``)反而被淹掉. 名字对上
    不认得词的时候, 正确的下一步是 ``<routine/doc name="…"/>``, 不是从括注
    里猜签名. (``meta`` 留参是为签名稳定: 调用点不该关心它用不用得上.)
    """
    return _ALIAS.get(v, v)


class Capability(Routine):
    """一次性 prompt routine: 词表快照按锥分组(易变段, 排前缀尾部)."""

    name = 'prompt/capability'

    meta: ClassVar[Dict[str, Any]] = {
        'description': 'prompt 片段: 能力面(词表快照按身体分组, 装配时渲染)',
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        rid = await self._rid('shell')
        r = await self.req(rid, 'registry', {'op': 'list'})
        verbs: List[str] = list(r.get('verbs') or [])
        if not verbs:
            return '你现在没有任何可用的 routine.'
        # 详列名单由装配给(清单的 detail:). 空 = 没配 —— 全详列, 但要说一声:
        # 悄悄全裸名比多花 token 更坏(模型不知道该传什么, 只能靠报错试)
        detail = {canon(n) for n in (r.get('detail') or [])}
        if not detail:
            _log.warning('清单没声明 detail(详列名单) —— 本拍全详列; '
                         '要缩短常驻前缀就在 moss.yaml 的 detail: 里点名')
            detail = set(verbs)
        # kernel 路由表: name → (modules 声明, meta) —— 注册期静态预测同源
        info = {e.get('name'): (list(e.get('modules') or []),
                                e.get('meta') or {})
                for e in await self.get_routines()}
        tree = await self.get_module_tree()
        top = tree.children(tree.root_id)   # 顶层分支 = 身体部位
        groups: Dict[str, List[Tuple[str, List[str]]]] = {}
        cross: List[Tuple[str, List[str]]] = []
        plain: List[Tuple[str, List[str]]] = []
        missing: List[str] = []      # 词表有名字、catalog 没注册(哑巴行)
        for v in sorted(verbs):
            mods = (info.get(v) or ([], {}))[0]
            if v not in info:
                # 清单上会只剩一行裸名字(不报错, 只是哑巴) —— 装配缺口,
                # 至少留个响, 但别每个名字刷一条.
                missing.append(v)
                plain.append((v, mods))
                continue
            if not mods:
                plain.append((v, mods))
                continue
            # 该声明的锥落在哪些顶层分支(锥含祖先, 分支必在其中)
            branches = sorted(
                {b for m in mods for b in top if b in tree.cone(m)})
            if len(branches) == 1:
                groups.setdefault(branches[0], []).append((v, mods))
            else:
                cross.append((v, mods))   # 跨分支(或树上找不到)
        lines = ['可调用的 routine(CTML 标签):']
        if missing:
            _log.warning('verbs without routine registration (naked lines): '
                         '%s', ', '.join(missing))
        rest_total = [v for v in verbs if v not in detail]
        if rest_total and 'routine/doc' in verbs:
            # 提示只在能真的查到时出现: 指向一个词表外的名字比不提示更坏
            lines.append('(只列名字的那些: 参数与示例用 <routine/doc name="…"/> 取)')
        seen: set = set()      # 同一段 example 只印一次(容器族共享结构说明)

        def meta_of(v: str) -> Dict[str, Any]:
            return (info.get(v) or ([], {}))[1]

        def names(vs: List[str]) -> str:
            return ' '.join(name_only(v, meta_of(v)) for v in vs)

        def block(title: str, items: List[Tuple[str, List[str]]]) -> List[str]:
            """一组: 详列项各占一块, 名字项收在末尾一行.

            整组都没点名时不留一个空组头(标题与名字并成一行) —— 车身那种
            整族都裸名的情况很常见, 两行说一件事是白花.
            """
            detailed = [(v, m) for v, m in items if v in detail]
            rest = [v for v, _ in items if v not in detail]
            if not detailed:
                return [f'{title} {names(rest)}']
            out = [f'{title}:']
            out += [entry(v, meta_of(v), f' ({" + ".join(m)})' if m else '',
                         seen)
                    for v, m in detailed]
            if rest:
                out.append('  另有: ' + names(rest))
            return out

        for b in top:
            items = groups.get(b)
            if items:
                lines += block(b, items)
        if cross:
            lines += block('跨模块(原子占用)', cross)
        if plain:
            lines += block('通用(不占模块)', plain)
        return '\n'.join(lines)

    @staticmethod
    def _item(v: str, mods: List[str],
              info: Dict[str, Tuple[List[str], Dict[str, Any]]],
              seen: set) -> str:
        """一项 = 签名行 + 参数说明子行 + 示例块(装配侧的薄壳, 供测试)."""
        _, meta = info.get(v) or ([], {})
        return entry(v, meta, f' ({" + ".join(mods)})' if mods else '', seen)

    @staticmethod
    def _param_lines(meta: Dict[str, Any]) -> List[str]:
        """参数子行(模块函数 ``param_lines`` 的薄壳: 老调用点/测试在用)."""
        return param_lines(meta)

    @staticmethod
    def _sig_line(v: str, meta: Dict[str, Any]) -> str:
        """签名行(模块函数 ``signature`` 的薄壳: 老调用点/测试在用)."""
        return signature(v, meta)


    async def _rid(self, name: str) -> str:
        """按名找运行中的 routine 实例 id(片段自解析依赖, 不走注入)."""
        for r in await self.get_running_routines():
            if r.get('name') == name:
                return r['id']
        raise RuntimeError(f'routine not running: {name}')

    async def stop(self) -> None:
        pass
