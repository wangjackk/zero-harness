"""nucleus —— 感知源的治理单元(原版 ``Nucleus`` 契约的塌缩版).

原版 ``core/blueprint/mindflow.py:Nucleus`` 的定义是:

    并行 感知/思考/决策 单元的统一抽象 ... 属于"单生产者-单消费者"的
    有界缓冲区 ... 它接受输入信号, 返回动机 ... 理想情况下它不应该包含
    调度逻辑, 而只作为通讯调度层.

配套接口是 ``name() / description() / status() / signals() / clear() /
add_signal() / with_bus(fire_impulse) / suppress()``, 加上 ``NucleusMeta``
(声明 + IoC factory) 做类注册, Matrix 扫描装配. 原版 8 个默认核各自
持有一个缓冲和一条消费循环, mindflow 每巡 ``peek()`` 群比.

**mossv2 不恢复那个形态, 但恢复这个身份.** 分界线是时间轴:

    原版                       mossv2
    name/description/status    同名类属性 + ``status()``
    signals()                  核名 = ``declare`` 的 ``source``
    add_signal() 内部治理      ``govern()``: 同步治理一次申报
    with_bus(fire_impulse)     由 ``declare`` 内联调用(核没有自己的循环)
    suppress()                 ``on_suppress()``: 输了之后的处置
    NucleusMeta + IoC factory  ``NUCLEI`` 注册表(类注册 + 按名构建)

核仍然是被动对象: 只在 ``declare`` 这一刻被问一次(默认值 → 治理 →
进检录缓冲), 拉取/群比/抢占全部留在裁判循环里. 所以"可插拔感知单元"
回来了, "每个核自己跑一个循环"没有回来 —— 后者会把 rank 群比退化成
分布式一致性问题(见 docs/decisions.md "核类生态永不恢复").

为什么现在需要它: 源多了以后(终端 / 语音 / 反射弧 / 未来的视觉·机身·
定时器), 每个源都在自己的 routine 里手拼 ``declare`` 字段 —— 源的
缺省参数、聚合策略、失败处置、体检信息散落在调用方, mindflow 只认得
一个 ``source`` 字符串. 核把这个"翻译层"收回成一等公民: 新源 = 写一个
核类 + 注册, 不碰 mindflow 的分派逻辑.
"""
from __future__ import annotations

import time
from typing import (TYPE_CHECKING, Any, ClassVar, Dict, List, Optional,
                    Type)

from routine.logger import setup_logger

if TYPE_CHECKING:   # 仅类型标注; 运行期避让与 mindflow 的循环导入
    from .mindflow import Impulse

_log = setup_logger('moss.nucleus')


class Nucleus:
    """一个感知源的声明 + 治理单元.

    子类只需声明 ``name`` 与 ``description``; 想接管治理就覆写
    ``defaults`` / ``govern`` / ``on_suppress``. 实例是**单例**
    (一源一个), 由 ``NUCLEI.build()`` 在 mindflow 装配期创建, 状态
    (计数/冷却) 跟着实例走 —— 裁判循环单线程 asyncio, 无需加锁.
    """

    name: ClassVar[str] = ''
    description: ClassVar[str] = ''

    #: 同源多包是否折成一拍(原版 input 核聚合). False = 每包独立候选,
    #: 适合"一次一条、按最新覆盖"的源(内部引信/纯打断)。
    aggregate: ClassVar[bool] = True

    #: 输了之后的冷却秒数(原版 ``BufferNucleus.suppress_seconds``):
    #: 冷却期内该源的新申报直接 noop(静默丢), 用于高频源降频.
    #: 0 = 不冷却 —— 输了立刻回检录缓冲重赛(mossv2 的历史行为).
    suppress_seconds: ClassVar[float] = 0.0

    def __init__(self, **overrides: Any) -> None:
        for key, value in overrides.items():
            setattr(self, key, value)
        self._suppress_until = 0.0
        self._accepted = 0
        self._dropped = 0
        self._suppressed = 0

    # --- 声明面 ---------------------------------------------------------

    def defaults(self) -> Dict[str, Any]:
        """该源的缺省申报字段: ``declare`` 里没给(或给了 None)的键用它.

        字段全集见 ``Impulse``(priority/strength/complete/mode/...).
        """
        return {}

    # --- 治理面 ---------------------------------------------------------

    def govern(self, imp: 'Impulse') -> Optional['Impulse']:
        """把申报治理成冲动(原版 ``add_signal`` 的同步核).

        **必须同步且不耗时** —— 原版明确要求"不执行任何耗时或异步
        操作". 需要小模型/网络的治理(如判停分类器)由源 routine 在
        申报**之前**做完, 这里只做静态纠偏(裁剪/脱敏/加权/丢弃).

        返回 ``None`` = 丢弃这条申报(等价原版"没东西可宣告").
        """
        return imp

    def cooling(self) -> bool:
        """是否在抑制冷却期(原版 ``_suppress_until``)."""
        return time.monotonic() < self._suppress_until

    def accepting(self) -> bool:
        """是否受理新申报(冷却期检查)."""
        return not self.cooling()

    def on_attended(self, imp: 'Impulse') -> Optional['Impulse']:
        """仲裁赢了之后的处置(原版 ``Nucleus.attended(impulse)``).

        默认不动(返回 None = 按原样开链). 子类可返回改写后的冲动 ——
        mindflow 用它建 attention, 典型用法是**降级回运行档**(原版
        listener: 起话包的 150 强度 / WARNING 优先级只在挑战时有效,
        赢到 attention 后立刻回到 INFO + 100, 否则钉在高档下个包进
        不来). 注意这是**赢家自己**的治权, 与 ``on_suppress`` 对称.
        """
        return None

    def on_suppress(self, imp: 'Impulse', by: Optional['Impulse']) -> None:
        """仲裁输了之后的处置(原版 ``suppress(suppress_by, suppressed)``).

        默认只记一次冷却(mindflow 随后统一回检录缓冲重赛 —— 不丢).
        子类可在此降权/记审计; **不要**把消息丢掉, 丢弃语义已由
        ``mode=knock`` 显式表达.

        冷却**只在窗口开启时设一次**: mossv2 的输家回检录缓冲重赛
        (原版是核自己缓冲 + beat loop 重试), 若每次重赛都续期, 只要有
        事件唤醒就会把冷却无限延长 —— 冷却的语义是"从第一次输起 N 秒
        内降频", 不是"一直输就一直封".
        """
        self._suppressed += 1
        if self.suppress_seconds > 0 and not self.cooling():
            self._suppress_until = time.monotonic() + self.suppress_seconds

    # --- 计数/自解释 ----------------------------------------------------

    def note(self, kind: str) -> None:
        """mindflow 记一笔受理结果(accepted / dropped)."""
        if kind == 'accepted':
            self._accepted += 1
        elif kind == 'dropped':
            self._dropped += 1

    def status(self) -> str:
        """一句体检(原版 ``status()``: IM 红点式, 空则忽略)."""
        bits: List[str] = []
        if self._accepted:
            bits.append(f'ok {self._accepted}')
        if self._dropped:
            bits.append(f'dropped {self._dropped}')
        if self._suppressed:
            bits.append(f'suppressed {self._suppressed}')
        left = self._suppress_until - time.monotonic()
        if left > 0:
            bits.append(f'cooling {left:.1f}s')
        return ', '.join(bits)

    def snapshot(self) -> Dict[str, Any]:
        """结构快照(``req nuclei`` 自解释面)."""
        return {
            'name': self.name,
            'description': self.description,
            'aggregate': bool(self.aggregate),
            'suppress_seconds': float(self.suppress_seconds),
            'defaults': dict(self.defaults()),
            'status': self.status(),
        }


# --- 出厂核 -------------------------------------------------------------
#
# 四个默认源各一个核. 它们的 defaults 与 mindflow 的历史硬编码默认值
# 一致(NOTICE / strength=100 / complete=True), 所以注册与否行为相同 ——
# 核带来的首先是**自解释面**, 其次才是按源覆写的入口.


class UserInputNucleus(Nucleus):
    """终端/IM 的文字输入: 同源聚合(连打两行 = 一拍)."""

    name = 'user'
    description = '用户文字输入(终端/IM): 同源聚合, 连打两行一拍'

    def defaults(self) -> Dict[str, Any]:
        return {'priority': 1, 'strength': 100, 'complete': True}


class ListenerNucleus(Nucleus):
    """语音输入(耳朵): 起话占坑 + 交付填坑, 走礼仪层判停.

    原版 ``ListenerNucleus`` 在"映射两个包"之外还带着四条与起话专属的
    规矩, 这里一并移植 —— 它们都只作用于**起话包**, 交付包一律豁免
    (内容不能丢):

    - **首包提权**: ``complete=False`` 的包强度钉成 ``first_strength``
      (150), 用于同级强度仲裁时抢占(与 ``source_escalation`` ×1.1
      配合); 交付包用 ``normal_strength``(100).
    - **上台降级**: 赢到 attention 后立刻降回 INFO + 100
      (``on_attended``) —— 否则占坑包以 150 钉在台上, 同源下一个包
      再也进不来.
    - **两层冷却**: 输了进 ``onset_suppress_seconds``(0.5s 大冷却),
      赢了进 ``onset_attended_seconds``(0.1s 小冷却, 防"抢到后立刻被
      自己下一个包抢"). 冷却期内起话包不发射 —— 防抢占风暴.
    - **强度 TTL**: 起话包的 ``strength_decay_seconds`` 用 120s(原版
      ``ListenerNucleus.strength_decay_seconds``), 而不是通用默认 20s.
    """

    name = 'asr'
    description = ('语音输入(流式识别 + 礼仪判停): 起话占坑、交付同 id '
                   '填坑, 一轮只算一拍')
    # 一句话由礼仪层切成一轮, 首尾包靠同 id 配对 —— 聚合仍要开
    # (配对逻辑在 _fold_source 里, 关掉聚合会让首包与尾包各自成候选).
    aggregate = True

    #: 首包强度 — 同级强度仲裁时用于抢占(原版 first_strength)
    first_strength: int = 150
    #: 上台降级后的运行强度(原版 normal_strength)
    normal_strength: int = 100
    #: 输掉仲裁后的大冷却(只拦起话包)
    onset_suppress_seconds: float = 0.5
    #: 抢到 attention 后的小冷却(只拦起话包)
    onset_attended_seconds: float = 0.1
    #: 起话包的强度衰减 TTL(原版 120s; 通用默认 20s)
    strength_decay_seconds: float = 120.0

    def __init__(self, **overrides: Any) -> None:
        super().__init__(**overrides)
        self._onset_suppress_until = 0.0
        self._onset_attended_until = 0.0

    # --- 声明面 ---------------------------------------------------------

    def defaults(self) -> Dict[str, Any]:
        return {'complete': True,
                'strength_decay_seconds': self.strength_decay_seconds}

    # --- 治理面 ---------------------------------------------------------

    def govern(self, imp: 'Impulse') -> Optional['Impulse']:
        """起话/交付两套映射(原版 ``_on_onset`` / ``_on_deliver``).

        起话包在冷却期内直接丢(返回 None); 交付包永不受冷却约束.
        """
        if not imp.complete:
            if self._onset_cooling():
                return None
            if imp.strength != self.first_strength:
                return imp.model_copy(
                    update={'strength': self.first_strength})
            return imp
        if imp.strength == self.first_strength:
            # 起话专用的提权值不该漏进交付包(尾包用运行强度)
            return imp.model_copy(
                update={'strength': self.normal_strength})
        return imp

    def on_attended(self, imp: 'Impulse') -> Optional['Impulse']:
        """抢到 attention: 进小冷却 + 降回运行档(原版 ``attended``).

        原版的无条件降级是有理由的: 运行中的 listener attention 停在
        最低一档, 别的包(同 INFO 级)才靠强度仲裁进得来.
        """
        from .mindflow import Priority   # 运行期 import, 避让循环依赖

        if self.onset_attended_seconds > 0:
            self._onset_attended_until = (
                time.monotonic() + self.onset_attended_seconds)
        if (imp.priority != Priority.INFO
                or imp.strength != self.normal_strength):
            return imp.model_copy(
                update={'priority': Priority.INFO,
                        'strength': self.normal_strength})
        return None

    def on_suppress(self, imp: 'Impulse', by: Optional['Impulse']) -> None:
        """仲裁输了: 起话包进大冷却, 交付包只记数. """
        if (not imp.complete and self.onset_suppress_seconds > 0
                and not self._onset_cooling()):
            self._onset_suppress_until = (
                time.monotonic() + self.onset_suppress_seconds)
        super().on_suppress(imp, by)

    def _onset_cooling(self) -> bool:
        """起话冷却期(原版 ``_in_cooldown``): 两层任一未过期即冷却.

        注意它与 ``Nucleus.cooling``(高频源整体降频)不是一回事 ——
        后者拦该源的**全部**申报, 这里只拦起话包.
        """
        now = time.monotonic()
        return (now < self._onset_suppress_until
                or now < self._onset_attended_until)

    # --- 自解释 ---------------------------------------------------------

    def status(self) -> str:
        base = super().status()
        left = max(self._onset_suppress_until,
                   self._onset_attended_until) - time.monotonic()
        if left > 0:
            return (f'{base}, onset cooling {left:.1f}s'
                    if base else f'onset cooling {left:.1f}s')
        return base

    def snapshot(self) -> Dict[str, Any]:
        snap = super().snapshot()
        snap['onset'] = {
            'first_strength': int(self.first_strength),
            'normal_strength': int(self.normal_strength),
            'suppress_seconds': float(self.onset_suppress_seconds),
            'attended_seconds': float(self.onset_attended_seconds),
            'strength_decay_seconds': float(self.strength_decay_seconds),
            'cooling': self._onset_cooling(),
        }
        return snap


class CommandNucleus(Nucleus):
    """反射弧: CTML 直投身体, 不经思考(原版 CommandNucleus)."""

    name = 'command'
    description = '反射弧: CTML 直投身体执行, 不经思考(急停类)'

    def defaults(self) -> Dict[str, Any]:
        return {'priority': 1, 'strength': 100}


class MindflowNucleus(Nucleus):
    """内部引信: 点灯补拍 / 空闲瞬态消化合成出来的空正文冲动.

    不是外界输入, 不该和邻居聚合, 也不该参与"谁最强"的比喻.
    """

    name = 'mindflow'
    description = '内部拍引信(回声补拍/瞬态消化): 无正文, 不聚合'
    aggregate = False


class NotifyNucleus(Nucleus):
    """拍外通知: 外部回调 / 定时器 / 构建结束送回来的事实.

    它的两个缺省就是它的身份:

    - ``priority=BACKGROUND``: **永不抢擂** —— 有现任时一定输, 输了挂账
      送达下一拍. "外面有结果回来了"不构成打断它说话的理由.
    - ``aggregate=False``: 每条通知是一个**独立事件**(两条构建结果不该拼
      成一句话), 各自成候选、各自成拍.

    不抢不等于可丢: 输了走 ``mode='notify'`` 的挂账路(``_dispatch`` 里
    与 BACKGROUND 同一分支), 链终由 ``_safe_chain`` 的 finally 消化.
    """

    name = 'notify'
    description = '拍外通知(外部回调/定时器/构建结束): 不打断现在, 必送达下一拍'
    aggregate = False

    def defaults(self) -> Dict[str, Any]:
        from .mindflow import Priority   # 运行期 import, 避让循环依赖

        return {'priority': int(Priority.BACKGROUND), 'strength': 100,
                'mode': 'notify', 'complete': True}


# --- 注册表 -------------------------------------------------------------


class NucleusRegistry:
    """核的类注册表(原版 ``NucleusMeta`` 的替身: 声明 + 按名构建).

    构建放在 mindflow 装配期(``on_created``), 但**类注册是模块级**的:
    所以跨进程 hub 里 mindflow 所在进程必须能 import 到核类. 第三方
    源在自己的模块 import 时 ``NUCLEI.register(MyNucleus)`` 即可.
    """

    def __init__(self) -> None:
        self._classes: Dict[str, Type[Nucleus]] = {}
        self._defaults: List[str] = []

    def register(self, cls: Type[Nucleus], *, default: bool = True
                 ) -> Type[Nucleus]:
        name = cls.name
        if not name:
            raise ValueError(f'{cls.__name__} 必须声明 Nucleus.name')
        self._classes[name] = cls
        if default and name not in self._defaults:
            self._defaults.append(name)
        return cls

    def unregister(self, name: str) -> None:
        self._classes.pop(name, None)
        if name in self._defaults:
            self._defaults.remove(name)

    def names(self) -> List[str]:
        return list(self._classes)

    def build(self, names: Optional[List[str]] = None
              ) -> Dict[str, Nucleus]:
        """按名构建; ``names`` 为空 = 全部默认核."""
        picked = list(names) if names else list(self._defaults)
        out: Dict[str, Nucleus] = {}
        for name in picked:
            cls = self._classes.get(name)
            if cls is None:
                _log.warning('nucleus %r 未注册, 跳过', name)
                continue
            out[name] = cls()
        return out


NUCLEI = NucleusRegistry()

for _cls in (UserInputNucleus, ListenerNucleus, CommandNucleus,
             MindflowNucleus, NotifyNucleus):
    NUCLEI.register(_cls)

__all__ = [
    'Nucleus', 'NucleusRegistry', 'NUCLEI',
    'UserInputNucleus', 'ListenerNucleus', 'CommandNucleus',
    'MindflowNucleus', 'NotifyNucleus',
]
