"""Mindflow —— 桥梁: 思考与行动之间的闭环调度器.

原版 mindflow 同构: 何时想(事件驱动, 无刺激不思考) / 想几拍
(回声帧循环) / 一拍何时结束(等观测收束) / 拍链可中断(一拍抛错 =
收链留痕, 泵不崩) / 没事就歇(裁判循环空搏动, idle 屏障可观测).

多源仲裁(照抄原版两场结构, 拉取时序): 原版是"rank 预选赛 +
challenge 决赛"——核各自缓冲, 裁判循环每巡 peek 群比选头号挑战者,
再挑战现任 attention. 这里同构塌缩:

- 检录缓冲(``_pending``)替八核缓冲, 信号线(``_has_signal``)替
  ``_has_impulse_event``; declare = 申报登记(原版外界入口 add_signal/
  on_signal 同构: signal.__state__ dispatched/ignored ↔ verdict
  accepted/ignored; 仲裁结果调用方不可见, 只回注册级回执 ——
  fire-and-forget 返回 None 同构).
- 裁判循环(原版 ``_on_impulse_consuming_loop`` 同构): 信号即醒 +
  0.5s 空搏动保活(原版同参 —— 原版超时裸 continue 不打 rank, tick
  只让循环响应退出/pause; 重赛由事件驱动: 新 declare 点灯或链终
  点灯 —— 原版空缺期 thinking 循环 1s 扫描的替身).
- rank 群比(原版 ``_rank_best_impulse_from_nuclei`` 同构): 同源
  聚合成候选(连打两行 = 一拍, 原版 input 核聚合), interrupt/command
  各自成候选取最新(原版两核 last-impulse cache), 尾包快车道(原版
  ``_AWAITED_IMPULSE_LANE``: attention 挂起在等的同 id 尾包无视
  优先级直接胜出), 字典序群比(先 priority 后 strength, 原版合成
  标量 priority_strength 的字典序等价). **单源成链**: 一拍由一个
  源驱动(原版一巡只出一个 impulse), 跨源不并拍.
- 失败处置(照抄原版失败侧): default → suppress 回检录缓冲(原版
  ``nucleus.suppress`` 缓冲重试同构) —— 不挂账不陪葬, 下届独立成
  链(下个事件点灯的 rank 顺带重比, 原版"新信号顺带扫冷却核"同构);
  notify → 挂账(送达下一拍, 永不丢);
  next → 挂账 + 插队(强制当前链下一拍必看); knock/command → 丢;
  BACKGROUND → 挂账(观众席永不抢擂).
- 感知源核(``nucleus.py``): 源从"一个字符串"回到"一个对象" ——
  一源一核, 持缺省参数/聚合策略/抑制冷却/自解释(原版 ``Nucleus``
  的声明面与治理面). 但**核没有自己的循环**: 只在 declare 这一刻被
  问一次(缺省 → 治理 → 检录), 群比仍在裁判循环单点打. 原版"每核
  一个缓冲 + peek 群比"的形态不恢复 —— 那会让 rank 退化成分布式
  一致性问题. ``req nuclei`` 是这批核的自解释面.
- 决赛照抄原版 AbsAttention 分支序(保护期/衰减/同源提权/吸收).

拍链(原版 need_observe 回声帧循环同构): 仲裁赢家开一拍; 每拍的
产物是观测, 但 **obs 只记账不驱动** —— 指令结果的到达不是激活源
(原版默认命令完成不点 need_observe 同构; 回执入史, 下一届缝合缝
被动读到). 自续唯一驱动 = next 模式强制续一拍(显式插队声明); 无
它 → 收链.

中断(原版 attention.abort 同构): 一拍抛错(脑子挂了/等观测超时/
被抢占) = 拍链断 —— 泵不崩, reason 留灵魂记忆(下一拍的底), 收链.
抢占走同一通路: 裁判判挑战者胜 → 在途拍在检查点抛错(reason=
'interrupted') → 留痕收链 → 挑战者回检录缓冲头等下届.

**抢占的判据是"有东西可停"**(原版没有这一问, 因为原版没有首字延迟
到八九秒的脑子): 在途拍还没开口(字流一个字都没投)时, 新输入不抢占
—— 它排队, 等这一拍说完自己成下一届. 显式 ``interrupt`` / FATAL 不受
此限(那是"停"的语义承诺, 不是竞争).

    Mindflow(何时想/想几拍/谁说话算数) → Ghost(怎么想: 记忆+一段流)
    → Shell/Channel(做) → 观测回灌 Mindflow(闭环)
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum, IntEnum
from typing import Any, ClassVar, Dict, List, Optional, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine, request

from .nucleus import NUCLEI, Nucleus

__all__ = ['Mindflow', 'Priority', 'ChallengeMode', 'Impulse', 'Attention']

_log = setup_logger('moss.mindflow')


class Priority(IntEnum):
    """输入优先级: 越大越高, FATAL 永远抢占, BACKGROUND 永不抢占."""

    BACKGROUND = -1
    INFO = 0
    NOTICE = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4
    FATAL = 5


class ChallengeMode(str, Enum):
    """仲裁处置模式.

    沿"抢占成功 vs 失败"双轴对称: default 抢占成功开新链/失败 suppress
    回缓冲重赛; aside 只送达不接管(注册即挂账, 不参与竞争 —— 原版
    aside 同构); notify 失败也不丢消息(挂账); next 失败时插队(强制
    当前链下一拍必看); knock 失败即丢(原版 knock nucleus: 唯一可丢
    的感知入口 —— 门前请求, 忙时没听见就没听见, 端侧自己持有内容,
    门没敲开就再敲一次).
    """

    DEFAULT = ''
    ASIDE = 'aside'
    NOTIFY = 'notify'
    NEXT = 'next'
    KNOCK = 'knock'


class Impulse(BaseModel):
    """冲动: 感知源治理后的调度信号 ——
    多模态各源(终端/语音/机身)的输入统一起来仲裁.

    首包-尾包: ``complete=False`` 的输入开链后挂起不想(占住注意力),
    等**同 id** 的尾包(``complete=True``)吸收才开拍 —— 粘包归源(尾包
    自带全量文本, 原版同语义: nucleus 定义内部协议, attention 不做拼接).
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    source: str = Field(default='user', description='感知源名(同源挑战提权)')
    text: str = Field(default='', description='输入正文')
    priority: Priority = Field(default=Priority.NOTICE)
    strength: int = Field(
        default=100, ge=0, le=1000,
        description='初始强度; 0 = 绝不竞争(只占位)')
    complete: bool = Field(
        default=True, description='False = 首包(占链挂起, 等同 id 尾包)')
    mode: ChallengeMode = Field(default=ChallengeMode.DEFAULT)
    interrupt: bool = Field(
        default=False,
        description='停身体(原版 ``Impulse.interrupt`` 字段契约): 赢到'
                    '注意力后先 shell.clear() 停在途拍的旧 logos. **只管'
                    '停不管答** —— 答不答由 ``effort`` 决定(原版两轴正'
                    '交). 沿用 interrupt primitive 的副作用: 优先级强制 '
                    'FATAL(语义承诺不可降级)')
    effort: str = Field(
        default='normal',
        description="思考强度(原版 ``thinking_effort``): 'normal' 正常"
                    "开拍思考; 'none' 不思考不出声 —— 只开一帧空拍留"
                    "痕(effort='none' + interrupt=True 才是原版 interrupt "
                    "primitive 的全貌: 停而不答). declare 裸写 "
                    "interrupt=True 默认按 primitive 补 effort='none'")
    # ── 听侧专有的三个读数(原版 ListenerSignal 同名同义) ──
    # 它们不是"输入正文", 是**关于这段输入的元信息**: 脑子靠它们判断
    # "这句我是不是听岔了 / 他是不是说完了". 由听的控制层算好送来 ——
    # 核与脑子都不重算(响度与逐字置信度只有那一层有).
    # 三个都送到渲染层(brain._listen), 但**只有 paused 真的印上去**:
    # low_conf/db 暂不印(2026-10-25), 数据仍在走, 别看见它们没进 prompt
    # 就当链路断了.
    low_conf: str = Field(
        default='',
        description='词级置信度低于礼仪阈值的那些字(只列字不列分数); '
                    '空 = 这句没有可疑字。数据面用, 渲染层暂不印出')
    db: float | None = Field(
        default=None,
        description='这一段话的平均响度(dBFS), 段尾结算; None = 没量到。'
                    '数据面用, 渲染层暂不印出 —— 是测量, 不是对谁的判断')
    paused: bool | None = Field(
        default=None,
        description='这包是不是一段的收尾(原版语义: 信息不是许可 —— '
                    '说完了也由模型自己决定接不接); None = 不说')
    command: bool = Field(
        default=False,
        description='反射弧(原版 ImpulsePrimitive.command_only): text = '
                    'logos(CTML) 直投身体不经思考; last-wins(新指令覆盖'
                    '未消费的旧指令), 失败即丢(指令无重试语义)')
    transient: bool = Field(
        default=False,
        description='True = 状态快照(原版 dynamic_context/percepts 同源'
                    '覆盖): 文本不进记忆正文, 同源新包覆盖旧包 —— '
                    '感知源(电量/位置/ASR 渐精包)永远只留一份"现在"')
    protection_time: float = Field(
        default=0.0, description='同级保护期(秒), 防抖')
    strength_decay_seconds: float = Field(
        default=20.0, description='强度衰减约定时长(无动静最迟此时归零)')
    stale_timeout: float = Field(
        default=0.0, description='过期丢弃(0 = 永不过期)')
    created_at: float = Field(default_factory=time.monotonic)

    def is_stale(self) -> bool:
        if self.stale_timeout <= 0:
            return False
        return time.monotonic() - self.created_at > self.stale_timeout


class Attention:
    """拍链仲裁体(照抄原版 AbsAttention + BaseAttention): 挑战裁决 +
    强度衰减曲线. 一条拍链一个实例, 只持仲裁状态, 不管拍怎么跑."""

    SOURCE_ESCALATION = 1.1    # 同源挑战提权(原版 source_escalation)
    MAX_PROTECTION_TIME = 3.0  # 保护期上限
    PROTECTION_RATIO = 0.2     # 保护期 = 衰减时长 × 0.2(原版同参)

    def __init__(self, impulse: Impulse):
        self._draw_from = impulse
        self._current = impulse
        self._decay_time = max(impulse.strength_decay_seconds, 1.0)
        if not impulse.complete:
            # 原版 ``_attention.py:70-71``: 不完整的包(起话/占坑)这一帧
            # 永不思考 —— 只占注意力, 不对着半句话开脑子. 这是**机制**
            # 保证, 不靠调用方记得给 effort='none' (原版 onset 恰好显式
            # 给了, 但那是 redundant 的一层).
            impulse.effort = 'none'
        self.absorb_impulse(impulse)   # 播种强度/保护期(照抄)

    def impulse(self) -> Impulse:
        return self._current

    def is_protected(self) -> bool:
        return self._protected_until > time.monotonic()

    def can_absorb(self, challenger: Impulse) -> bool:
        """同 id 且当前未 complete: 挑战者是这条链在等的尾包.
        仲裁方在保护期之前先问这个 —— 挂起链等的正是它, 被保护期
        挡住就永挂(原版靠 nucleus 缓冲重试兜底, 无 nucleus 时吸收
        提前就是替身)."""
        return (challenger.id == self._draw_from.id
                and not self._current.complete)

    def challenge(self, challenger: Impulse) -> str:
        """照抄原版 AbsAttention.challenge 分支序: stale 输 / 同 id 未
        complete 吸收 / FATAL 或更高赢 / 更低输 / 保护期输 / 强度仲裁."""
        if challenger.is_stale():
            return 'lose'
        if challenger.id == self._draw_from.id and not self._current.complete:
            # 只吸收一次: 尾包已到(complete)后, 同 id 再来走正常仲裁
            return 'absorb'
        if (challenger.priority == Priority.FATAL
                or challenger.priority > self._priority):
            return 'win'
        if challenger.priority < self._priority:
            return 'lose'
        if self.is_protected():
            return 'lose'
        return 'win' if self._arbit_by_strength(challenger) else 'lose'

    def absorb_impulse(self, impulse: Impulse) -> Optional[Impulse]:
        """照抄原版: 同 id 折进(更新强度/优先级/保护期/complete),
        异 id 交还调用方路由."""
        self._strength_start = impulse.strength
        self._refreshed_at = time.monotonic()
        self._priority = impulse.priority
        self._protected_until = self._refreshed_at + impulse.protection_time
        if impulse.id == self._draw_from.id:
            self._current = impulse
            return None
        return impulse

    def _arbit_by_strength(self, challenger: Impulse) -> bool:
        s = challenger.strength
        if challenger.source == self._draw_from.source:
            s = int(s * self.SOURCE_ESCALATION)
        return self.current_strength() < s

    def current_strength(self) -> int:
        """照抄原版 BaseAttention: 保护期内保持起始强度(×提权), 之后
        线性衰减到 0(incomplete 衰减 ×1.5, 占着不走衰减更快)."""
        elapsed = time.monotonic() - self._refreshed_at
        protection = min(self._decay_time * self.PROTECTION_RATIO,
                         self.MAX_PROTECTION_TIME)
        if elapsed < protection:
            return int(self._strength_start * self.SOURCE_ESCALATION)
        progress = min((elapsed - protection)
                       / (self._decay_time - protection), 1.0)
        factor = 1.0 if self._current.complete else 1.5
        return int(max(self._strength_start * (1.0 - progress * factor), 0))


class MindflowInput(BaseModel):
    """mindflow 入参: Moss 组装时注入."""

    ghost_id: str = Field(description='灵魂(Ghost)实例 id, 拍的思考对象')

    nuclei: List[str] = Field(
        default_factory=list,
        description='启用的感知源核名(``nucleus.py`` 注册表). 空 = 全部'
                    '默认核(user/asr/command/mindflow); 没注册核的源仍'
                    '可裸参数申报(向后兼容), 只是没有按源覆写的默认值.')


class Mindflow(Routine):
    """桥梁: 裁判循环 + 多源仲裁. 灵魂只管想, 身体只管做."""

    name = 'mindflow'

    MAX_INJECT: ClassVar[int] = 100   # 挂账上限(超限丢最旧)
    INTERRUPT_QUIET: ClassVar[float] = 0.5
    # 打断冷静期(原版 InterruptNucleus.suppress_seconds=0.5 同参):
    # 胜利侧设, 冷静期内静默丢 —— 打断无累积语义, 多次等价一次
    TICK: ClassVar[float] = 0.5
    # 裁判空搏动(原版消费循环 wait_for 0.5s 同参): 保活心跳 —— 原版
    # 超时裸 continue 不打 rank, tick 只让循环响应退出/pause. 重赛
    # 由事件驱动(新 declare 点灯 / 链终点灯), 执行期专注性绝对.

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '桥梁: 思考与行动间的闭环调度 + 多源仲裁(拉取时序, 原版 '
            'rank+challenge 两场结构同构). declare {text} = 申报登记('
            'fire-and-forget: 仲裁在裁判循环拉取时打, verdict 只有 '
            '注册级回执 accepted/dropped/noop/ignored); 可选申报参数: '
            'priority(默认 NOTICE, FATAL 抢占/BACKGROUND 让路), '
            'strength(默认 100, 0 绝不竞争), mode(default/aside/notify/'
            'next/knock — default 失败回检录缓冲重赛, knock 失败即丢), '
            'id + complete=False(首包占链, 尾包 rank 快车道吸收), '
            'transient=True(状态快照: 不进记忆正文, 同源覆盖刷槽, '
            '不参与竞争), interrupt=True(停身体: FATAL 抢占 + 停在途拍'
            '旧动作, 不管答不答), effort(normal=正常开拍思考 / none=只'
            '开空拍留痕不思考不出声; interrupt 裸写默认 none, 即原版 '
            'interrupt primitive 的停而不答; 起话类 barge-in 传 '
            'effort=normal 停而回答), protection_time, stale_timeout. '
            'command {text} = '
            '反射弧: CTML 直投身体不经思考(原版 command_only, '
            'priority=5 即 fatal_command). idle = 静默屏障(等无在途链 '
            '+ 检录缓冲空 + 无挂账, 测试/收摊用). nuclei = 感知源清单 '
            '(每源的缺省参数/聚合策略/冷却/受理计数; source 即核名, '
            '没注册核的源仍可裸参数申报). 拍 = 叫 ghost think '
            '一段流 + 等观测; obs 只记账不驱动回声(指令结果不触发: '
            '回执入史, 下一届缝合缝读到), 自续唯一驱动 = next 插队; 拍 '
            '抛错或被抢占 = 中断(收链留痕, 泵不崩); 裁判循环信号即醒 '
            '+ 0.5s 空搏动(原版同参).'),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = MindflowInput.model_validate(kwargs or {})
        self._ghost_id = inp.ghost_id
        # 灵魂掉线只报一次(见 _think): 每拍都刷同一条 ERROR 会把别的账淹掉,
        # 一声不说又让人对着"打字没反应"猜.
        self._ghost_lost_warned = False
        # 感知源核(原版 Matrix 扫描 NucleusMeta 装配的塌缩): 一源一实例,
        # 持默认参数/聚合策略/冷却. 没注册核的源照样能裸参数申报 ——
        # 核是"按源覆写"的入口, 不是准入门槛.
        self._nuclei: Dict[str, Nucleus] = NUCLEI.build(inp.nuclei or None)
        _log.info('nuclei: %s',
                  ', '.join(sorted(self._nuclei)) or '(none)')
        # 检录缓冲 + 信号线(原版 nuclei buffer + _has_impulse_event
        # 同构): declare 只登记点亮, 拉取权 100% 在裁判循环手里
        self._pending: List[Impulse] = []
        self._has_signal = asyncio.Event()
        self._chain_task: Optional[asyncio.Task] = None
        # 拍链仲裁状态(原版 mindflow._current_attention 同构)
        self._attention: Optional[Attention] = None
        self._chain_alive = False
        self._impulse_event: Optional[asyncio.Event] = None   # 尾包唤醒首包挂起
        # 仲裁失败系的消息挂账(原版 percepts buffer 同构, 按源记):
        # 本链下一拍优先看, 链收尾则下链首拍看 —— 消息不丢
        self._pending_inject: List[tuple[str, str]] = []
        # 瞬态槽(原版 nucleus 状态消息进 percepts buffer, 下一帧仍进
        # moment 同构): 同源覆盖天然有界(源数有限)
        self._pending_transients: Dict[str, str] = {}
        # "下一拍"灯(原版 need_observe 闩锁, 不是计数器): 手动 next 与
        # 观测回声共用 —— 见 _request_beat
        self._need_beat = False
        self._preempted = False     # 抢占: 当前链判死
        self._interrupt_quiet_until = 0.0   # 打断冷静期(胜利侧)
        self._idle_event = asyncio.Event()   # 静默探针(idle 用)
        self._stopped = asyncio.Event()      # 裁判循环的退出灯(stop 里点)

    # --- 裁判循环(原版 _on_impulse_consuming_loop 同构) ---

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """裁判循环: 信号即醒, 0.5s 空搏动保活(原版同参 —— 原版超时
        是裸 continue 不打 rank, tick 只让循环响应退出/pause; 重赛
        由事件驱动: 新 declare 点灯或链终点灯). 泵不崩: 循环只在 stop
        点灯时退出(等的是那盏灯, 不是框架 3s 后的兜底 cancel)."""
        while not self._stopped.is_set():
            woken = True
            try:
                await asyncio.wait_for(self._has_signal.wait(), self.TICK)
            except asyncio.TimeoutError:
                woken = False   # 空搏动: 保活心跳, 不打 rank(原版同构)
            self._has_signal.clear()
            if self._stopped.is_set():
                break
            try:
                await self._arbitrate(woken)
            except Exception:
                _log.exception('arbitrate failed')

    async def _arbitrate(self, woken: bool) -> None:
        """裁判一巡(原版消费循环一轮: rank + challenge + 处置).
        ``woken``: 信号唤醒(True)还是空搏动(False). 空搏动不打
        rank(原版裸 continue 同构)—— 执行期专注性绝对: 翻盘只经
        新 declare 点灯或链终点灯(原版空缺期扫描的替身); 空搏动
        只保留 idle 判定(原版在 thinking 循环 1s tick 里做, mossv2
        无此循环, 挪到这里). 信号唤醒时的空闲瞬态即时消化拍 /
        正文挂账消化(链终)语义不变."""
        if not woken:
            self._maybe_idle()
            return
        if (not self._chain_alive and not self._pending
                and self._pending_transients):
            # 空闲瞬态: 合成纯瞬态拍(正文空, 只刷快照) —— 链在场时
            # 下一拍自带快照, 不合成
            self._pending.append(Impulse(source='mindflow'))
        if not self._pending:
            self._maybe_idle()
            return
        candidates, self._pending = [e for e in self._pending
                                     if not e.is_stale()], []
        winner, percepts, losers = self._rank(candidates)
        for imp in losers:
            self._dispatch(imp, by=winner)
        if winner is None:
            # 全军覆没(strength=0 之类): 有存货等下一巡
            self._wake_or_idle()
            return
        if winner.interrupt and winner.effort == 'none':
            # 胜利侧冷静期(原版 attended 后 suppress_seconds):
            # rank 赢家即"已经打断过一次". 只挂纯打断 —— barge-in
            # 起话包赢了不该把下一句的起话也堵上
            self._interrupt_quiet_until = (
                time.monotonic() + self.INTERRUPT_QUIET)
        if self._chain_alive and self._attention is not None:
            if self._preempted:
                # 链已判死等收尸: 赢家回缓冲头, 下一巡成链(本巡开头
                # drain 过 _pending, 不放回就是丢消息; 链终的
                # _wake_or_idle 会再点信号, 不抢跑)
                self._pending.insert(0, winner)
                return
            v = await self._challenge_incumbent(winner)
            if v == 'absorb':
                self._absorb(winner)
                return
            if v == 'defer':
                # 它还没开口(首字未到): 没有可停的东西 —— 新输入排队,
                # 等这一拍说完自己成下一届(链终的 _wake_or_idle 会点灯)
                self._pending.append(winner)
                return
            if v == 'lose':
                self._dispatch(winner, by=self._attention.impulse())
                return
            # v == 'win': 抢占(原版旧 attention.abort('interrupted'))
            await self._preempt(winner)
            return
        self._start_chain(winner, percepts)

    def _rank(self, candidates: List[Impulse]) -> Tuple[
            Optional[Impulse], Dict[str, str], List[Impulse]]:
        """原版 ``_rank_best_impulse_from_nuclei`` 同构塌缩: 群比选
        头号挑战者 + 输家交调用方处置. 产物 = (赢家, percepts, 输家).

        ① 同源聚合成候选(替原版"每核 peek 自己的最强"): 连打两行 =
        一拍(原版 input 核聚合), 规则见 ``_fold_source``.
        ② interrupt/command 各自成候选取最新(原版两核 last-impulse
        cache: several ≡ one), 不与正文聚合; 平手时特殊核优先
        ("指令立即执行", 保旧 fold 的独占语义).
        ③ 尾包快车道(原版 ``_AWAITED_IMPULSE_LANE``): attention 挂起
        在等的同 id 尾包无视优先级/强度直接胜出 —— 补完不受约束.
        ④ 字典序群比(先 priority 后 strength, 原版合成标量
        priority_strength 的字典序等价); 平手先到先得(原版 > 才替换).
        """
        cands: List[Tuple[Impulse, Dict[str, str], int]] = []
        specials: List[Impulse] = []
        groups: Dict[str, List[Impulse]] = {}
        for e in candidates:
            if e.strength == 0:
                continue   # 递毛巾的: 绝不竞争, 只占位
            if e.interrupt or e.command:
                specials.append(e)
            elif self._aggregates(e.source):
                groups.setdefault(e.source, []).append(e)
            else:
                # 核自报不聚合(内部引信等): 每包独立候选, 谁也不并谁
                cands.append((e, {e.source: e.text} if e.text else {}, 0))
        for evs in groups.values():
            cands.extend((imp, p, 0) for imp, p in self._fold_source(evs))
        for kind in ('interrupt', 'command'):
            picks = [e for e in specials if getattr(e, kind)]
            if picks:
                newest = picks[-1]
                percepts = ({newest.source: newest.text}
                            if newest.text and kind == 'interrupt' else {})
                cands.append((newest, percepts, 1))
        if not cands:
            return None, {}, []
        # 尾包快车道(attention 挂起等尾包)
        if (self._chain_alive and self._attention is not None
                and not self._attention.impulse().complete):
            for imp, p, _sp in cands:
                if self._attention.can_absorb(imp):
                    losers = [c[0] for c in cands if c[0] is not imp]
                    return imp, p, losers
        # 字典序群比(平手: 特殊核优先, 再先到先得)
        best = cands[0]
        for c in cands[1:]:
            if ((c[0].priority, c[0].strength, c[2])
                    > (best[0].priority, best[0].strength, best[2])):
                best = c
        losers = [c[0] for c in cands if c is not best]
        return best[0], best[1], losers

    def _fold_source(self, evs: List[Impulse]
                     ) -> List[Tuple[Impulse, Dict[str, str]]]:
        """同源聚合成候选(原版 input 核聚合塌缩替身), 单源至多数个
        候选(原版 nucleus 每巡只出一条: 被提走的不与邻居合并). 规则
        (与旧 _fold 行为兼容, 且免序依赖):
        - attention 在等的尾包先提走(原版 ``_AWAITED_IMPULSE_LANE``:
          快车道只取它 —— 尾包带全量, 并进邻居即污染), 独占候选;
        - 同 id 首尾在场 → 配对(首包消费, 尾包带全量), 其余完整邻居
          另立候选(输家回缓冲重赛, 下一届独立成链 —— 原版: 邻居
          留核缓冲, 不 drain 不合并);
        - 完整在场而首包无尾 → 完整聚合成候选, 散落首包丢弃(尾包
          自带全量, 稍后按完整消息正常仲裁 —— 不丢也不重复);
        - 只有首包 → 首包开链占位(挂起等尾包)."""
        cands: List[Tuple[Impulse, Dict[str, str]]] = []
        # attention 挂起在等的尾包 id(挂着时 impulse() 就是首包)
        if (self._chain_alive and self._attention is not None
                and not self._attention.impulse().complete):
            awaited = self._attention.impulse().id
            tails = [e for e in evs if e.complete and e.id == awaited]
            if tails:
                tail = tails[-1]
                cands.append(
                    (tail, {tail.source: tail.text} if tail.text else {}))
                evs = [e for e in evs if e is not tail]
        incompletes = [e for e in evs if not e.complete]
        completes = [e for e in evs if e.complete]
        if incompletes:
            first = incompletes[0]
            tail = next((e for e in completes if e.id == first.id), None)
            if tail is not None:
                cands.append(
                    (tail, {tail.source: tail.text} if tail.text else {}))
                others = [e for e in completes if e is not tail]
                if others:
                    cands.append(self._agg_source(others))
            elif completes:
                cands.append(self._agg_source(completes))
            else:
                cands.append(
                    (first, {first.source: first.text} if first.text else {}))
        elif completes:
            cands.append(self._agg_source(completes))
        return cands

    def _agg_source(self, evs: List[Impulse]) -> Tuple[Impulse, Dict[str, str]]:
        """完整消息聚合(原版 input 核聚合: 文本按序拼, 权重取 max,
        id 取最新 —— 连打两行 = 一拍).

        **在最新包上 model_copy, 不新建 Impulse**: 手写构造等于列白名单,
        没列到的字段一律掉回默认值 —— ``effort='none'`` 的完整包(原版
        "停而不答" primitive)会因此变成正常思考; 以后新增的字段同样会
        被静默吞掉. 聚合只该改"文本/优先级/强度"这三件它真正负责的事."""
        newest = evs[-1]
        imp = newest.model_copy(update={
            'text': '\n'.join(e.text for e in evs if e.text),
            'priority': max(e.priority for e in evs),
            'strength': max(e.strength for e in evs),
        })
        return imp, {imp.source: imp.text} if imp.text else {}

    def _aggregates(self, source: str) -> bool:
        """同源多包是否折成一拍(原版"各核自己聚合"的塌缩: 核自报
        ``aggregate``; 没注册核的源沿用聚合 —— 连打两行一拍)."""
        nuc = self._nuclei.get(source)
        return True if nuc is None else bool(nuc.aggregate)

    async def _challenge_incumbent(self, imp: Impulse) -> str:
        """链在场时 rank 赢家挑战现任(原版 rank 后 challenge_attention):
        同 id 尾包吸收 → FATAL/interrupt 必赢 → BACKGROUND 永不抢擂 →
        其余照抄 AbsAttention.challenge 分支序(吸收提前于保护期 ——
        挂起链等的正是尾包, 替身规则: 原版靠 nucleus 重试兜底, 这里
        不提前则挂起链永挂).

        顺序与 ``AbsAttention.challenge`` 一致: **同 id 吸收排在
        FATAL/interrupt 之前** —— 尾包即便带 interrupt(如 always 礼仪
        的交付包)也属于同一条输入, 必须先填坑, 不能被"FATAL 必赢"挤成
        抢占(否则起话包被抢走, 一句话断成两轮).

        最后的 ``defer`` 是本项目的判据(原版无此分支): **抢占的对象是
        "它正在说的话", 不是"它还没想完的脑子"** —— 在途拍一个字都
        还没投出去时, 让新输入排队而不是打断它. 依据见
        ``_beat_silent``; 显式 interrupt / FATAL 不受此限(那是"停"的
        语义承诺, 不是竞争).
        """
        # 同 id 尾包吸收优先(原版 challenge 第一判据)
        if self._attention.can_absorb(imp):
            return 'absorb'
        if imp.interrupt or imp.priority == Priority.FATAL:
            return 'win'
        if imp.priority == Priority.BACKGROUND:
            return 'lose'   # 原版: BACKGROUND 无权插队(next 不包含)
        v = self._attention.challenge(imp)   # win/lose/absorb
        if (v == 'win' and imp.mode != ChallengeMode.KNOCK
                and await self._beat_silent()):
            # knock 不排队: 它整个身份就是"忙时没听见就没听见"
            # (原版唯一可丢的入口), 排队等于把可丢变成必达
            return 'defer'
        return v

    async def _beat_silent(self) -> bool:
        """在途拍"还没开口"吗(灵魂自报 state: open 且未 spoke).

        为什么要有这一问: 首字延迟可以到八九秒(整段 prompt + 记忆),
        这段时间里用户看不到任何东西, 会以为消息没发出去而再补一句
        "?"。补话若按抢占处理, 原拍在第一个检查点(第一个字到了)就死,
        一个字都没说过 —— 两条消息一起白等, 用户只看到"刚才被打断
        了". 所以: 没开口的拍不抢, 补话排队成下一届.

        问不到灵魂(它整个没了)按"有东西可停"处置 —— 宁可抢占, 不留
        一个打不断的身体."""
        try:
            st = await self.req(self._ghost_id, 'state', {}, 3)
        except Exception:
            _log.debug('ghost state unavailable, assume speaking')
            return False
        return bool(st.get('open')) and not st.get('spoke')

    async def _ghost_probe(self) -> Optional[Dict[str, Any]]:
        """一拍失败后问一次: 灵魂还在不在; 不在的话, 现在活着的都有谁.

        返回 ``None`` = 查不到(老框架/查询失败) —— 调用方按"还在"处理,
        宁可走原来的中断留痕, 不要凭空多出一条"灵魂没了"。

        **为什么要把"全部实例"一起带回来**: 只报"14 不在"没法定位 ——
        "ghost 变成 20 了"(被重建成新实例) 与"整个树没了"(kernel 重来)
        是两种完全不同的病, 而模型和人都只能看着一个孤零零的 id 猜.
        一次查询同时回答这两件事, 文案里直接写出来。
        """
        try:
            rows = await self.get_running_routines()
        except Exception:
            return None
        return {'ghosts': [str(r.get('id')) for r in rows
                           if r.get('name') == 'ghost'],
                'rows': list(rows)}

    @staticmethod
    def _ghost_lost_reason(probe: Dict[str, Any]) -> str:
        """把"14 不在"翻译成人能直接动手的两句话.

        两种病长得一模一样, 修法完全不同 —— 一种是实例换了(重启装配件
        就会重建一个 ghost, 老的连同它的记忆一起进了历史), 一种是整棵树
        都退了(kernel/进程层面)。只看一个 id 分不出来, 所以这里把当时
        的实例表摊开写。
        """
        ghosts = probe.get('ghosts') or []
        if ghosts:
            return (f'现在跑的是 ghost {", ".join(ghosts)} —— 它被重建成新'
                    f'实例了(装配件热重载/重装), 这一拍还指着旧的; '
                    f'重启 moss 才能把两边接上')
        names = ', '.join(f'{r.get("name")}={r.get("id")}'
                          for r in (probe.get('rows') or []))
        return (f'当前一个 ghost 都没有(活着的: {names or "无"}) —— 灵魂被停'
                f'了, 重启 moss 才能回来')

    def _dispatch(self, imp: Impulse, by: Optional[Impulse] = None) -> str:
        """仲裁失败处置(照抄原版失败侧): next → 挂账+插队; notify /
        BACKGROUND → 挂账(送达下一拍, 永不丢); knock·command → 丢;
        default → suppress 回检录缓冲(原版 ``nucleus.suppress`` 缓冲
        重试同构: 不挂账不陪葬, 下届独立成链 —— 下个事件点灯的 rank
        顺带重比, 原版"新信号顺带扫冷却核"同构).
        未完输入不挂账 —— 尾包才载全量, 挂前半句只会与尾包重复.

        ``by`` = 赢家: 传给核的 ``on_suppress``(原版 ``suppress(
        suppress_by, suppressed)`` 同参), 核据此记冷却/降权."""
        if imp.mode == ChallengeMode.NEXT:
            if imp.text and imp.complete:
                self._inject(imp.source, imp.text)
            self._request_beat('next')
            return 'queued'
        if (imp.mode == ChallengeMode.NOTIFY
                or imp.priority == Priority.BACKGROUND):
            if imp.text and imp.complete:
                self._inject(imp.source, imp.text)
            return 'buffered'
        if imp.mode == ChallengeMode.KNOCK or imp.command:
            return 'dropped'
        nuc = self._nuclei.get(imp.source)
        if nuc is not None:
            nuc.on_suppress(imp, by)
        self._pending.append(imp)
        return 'suppressed'

    def _absorb(self, imp: Impulse) -> None:
        """同 id 尾包吸收: 折进当前链(更新强度/优先级/保护期/
        complete), 唤醒首包挂起."""
        pending = self._attention.absorb_impulse(imp)
        if pending is not None and pending.text:
            self._inject(pending.text)
        if self._impulse_event is not None:
            self._impulse_event.set()

    async def _preempt(self, winner: Impulse) -> None:
        """抢占(原版旧 attention.abort('interrupted') 同构): 在途拍在
        检查点抛错 → 留痕收链(拍链中断同一条通路) → 挑战者回检录
        缓冲头, 链死后下一巡成链; 首包挂起一并唤醒(残句回缓冲)."""
        self._preempted = True
        self._pending.insert(0, winner)
        self._has_signal.set()
        if self._impulse_event is not None:
            self._impulse_event.set()
        try:
            await self.req(self._ghost_id, 'abort',
                           {'reason': 'interrupted'})
        except Exception:
            _log.exception('abort request failed')

    def _start_chain(self, winner: Impulse, percepts: Dict[str, str]) -> None:
        winner = self._attend(winner)
        self._chain_task = asyncio.ensure_future(self._safe_chain(winner, percepts))

    def _attend(self, imp: Impulse) -> Impulse:
        """赢家上台(原版 ``nucleus.attended(impulse)``): 核可降级回运行档.

        原版 listener 抢 attention 时用的是提权档(first_strength=150 /
        WARNING), 上台后必须立刻降回运行档(INFO / 100) —— 否则它会
        以提权强度钉在台上, 同源下一个包再也挑战不动, 多轮对话退化成
        一串"抢到就独占".

        只在成链时问一次: 抢占路径的赢家会回缓冲, 下一巡照常成链;
        吸收是同一 attention 内的强度刷新, 不换人.
        """
        nuc = self._nuclei.get(imp.source)
        if nuc is None:
            return imp
        attended = nuc.on_attended(imp)
        return imp if attended is None else attended

    async def _safe_chain(self, winner: Impulse,
                          percepts: Dict[str, str]) -> None:
        try:
            await self._chain(winner, percepts)
        except Exception:
            _log.exception('chain crashed')
            self._attention = None
            self._chain_alive = False
            self._impulse_event = None
            self._wake_or_idle()

    # --- 拍链 ---

    async def _chain(self, winner: Impulse, percepts: Dict[str, str]) -> None:
        """一条拍链 = 一个 attention 的生命周期: 仲裁已定(rank 赢家,
        单源成链), 开链 → next 插队续拍(obs 只记账不驱动) → 收链."""
        impulse = winner
        self._attention = Attention(impulse)
        self._chain_alive = True
        self._impulse_event = asyncio.Event()
        self._preempted = False
        self._need_beat = False
        try:
            # 首包挂起(照抄原版 wait_ready): incomplete 开的链不想,
            # 等同 id 尾包(rank 快车道送来, 原版 _awaited_impulse_id
            # 同构); 挂起期间裁判照常巡场, 可被抢占
            while not self._attention.impulse().complete:
                if self._preempted:
                    return self._requeue(self._attention.impulse())
                await self._impulse_event.wait()
                self._impulse_event.clear()
                if self._preempted:
                    return self._requeue(self._attention.impulse())
            # 挂起解除(尾包吸收): 尾包自带全量(粘包归源)
            cur = self._attention.impulse()
            if not impulse.complete:
                percepts = {cur.source: cur.text} if cur.text else {}
            # 挂账折进 percepts(同源续接: 正文先到, 挂账后到)
            percepts = self._merge_inject(percepts, self._drain_inject())
            # 听侧读数跟着源走(低置信字/响度/是否收尾): percepts 是按源
            # keyed 的, 属性也只能挂在源上 —— 同源多包时以尾包为准
            attrs = self._percept_attrs(cur)
            # 瞬态槽清入本拍(原版 dynamic_context 帧前取最新同构)
            ts = self._drain_transients()
            # 空拍判据 = 思考强度, 不是 interrupt(原版两轴正交):
            # interrupt 只管"停身体"(抢占时 _preempt 已 abort 在途拍),
            # effort='none' 才是不思考不出声. **读当前包**(尾包) ——
            # 原版 ``_think.py:101-102`` 的 ``effort()`` 就是
            # ``attention.impulse().thinking_effort``, absorb 尾包后
            # 指向的是尾包; 尾包才带这次要回应的全量. 读首包会把
            # "起话占坑"的 none 一路带到回应拍(永远不答), 也会让
            # "忘了给 effort 的 partial 包"对着半句话开脑子.
            record_only = cur.effort == 'none'
            r = await self._think(
                percepts or None, ts or None,
                logos=cur.text if cur.command else '',
                record_only=record_only,
                percept_attrs=attrs)
            # 续拍循环(原版 need_observe while, _mindflow.py:1148):
            # **回执本身不点灯, 带 owed 戳的回执才点灯** —— 未声明的
            # 成功照旧只入史(下一届缝合缝被动读到), 错误与声明过的
            # 结果则有债. 点灯只有三个来源, 共用同一盏:
            # ① 手动 next 输家(_dispatch 的 queued 分支); ② 灵魂报
            # 本拍有 owed(_think 的 echo); ③ 灵魂敲 beat(债晚到, 拍
            # 已收时补点). 抢占即断. 反射拍/打断空拍除外(原版
            # effort='none' attention 全程不语: 回声入史, 评说留给
            # 下一届 attention). 续拍带挂账(期间的消息/瞬态刷新这一
            # 拍就该看见)
            if not cur.command and not record_only:
                while not self._preempted and self._need_beat:
                    self._need_beat = False
                    inject = self._drain_inject()
                    pending_ts = self._drain_transients()
                    r = await self._think(
                        self._merge_inject({}, inject) or None,
                        pending_ts or None)
        finally:
            self._attention = None
            self._chain_alive = False
            self._impulse_event = None
            # 挂账无人接盘(链已收尾): 合成消化拍(原版 need_observe 驱动
            # 下一帧同构, 消息不丢; 纯瞬态拍同构: 正文空, 只刷快照).
            # 只发生在链终 —— 静默态 aside 不建链(原版"送达不动脑"
            # 同构), 裁判的空搏动不触发. 灯还亮着(被抢占打断在续拍
            # 前, 灯是全局的: 原版 need_observe 挂在 observer 上也随
            # attention 死而存活)同样落成新一届首拍, 不丢.
            if (self._pending_inject or self._pending_transients
                    or self._need_beat):
                self._pending.append(Impulse(source='mindflow'))
            self._need_beat = False
            self._wake_or_idle()

    def _wake_or_idle(self) -> None:
        """链终/巡终的收尾判定: 检录缓冲有候选 → 重挂信号(下一巡
        开链); 无候选 → 挂 idle 屏障(挂账滞留不挂信号 —— 它们只由
        链终消化, 裸信号会造成 0.5s 自激循环)."""
        if self._pending:
            self._has_signal.set()
        else:
            self._maybe_idle()

    def _maybe_idle(self) -> None:
        if (not self._chain_alive and not self._pending
                and not self._pending_inject
                and not self._pending_transients):
            self._idle_event.set()

    async def _think(self, percepts: Optional[Dict[str, str]] = None,
                     transients: Optional[Dict[str, str]] = None,
                     logos: str = '', record_only: bool = False,
                     percept_attrs: Optional[Dict[str, Dict[str, Any]]] = None,
                     ) -> Dict[str, Any]:
        """一拍. 抛错 = 中断(原版 attention.abort(reason) 同构): 泵不
        崩, reason 留灵魂记忆(下一拍的底), 收链. 抢占走同一条路
        (ghost 在检查点抛错, reason='interrupted'). 留痕请求也失败
        (灵魂整个没了)只记日志 —— 泵不崩优先. percepts 按源 keyed
        (原版 Moment.percepts, 开拍落帧); transients 仅首拍带(回声拍
        快照已入槽, 原版 dynamic_context 帧前取最新同构); logos =
        反射拍的直投指令(原版 thinking_effort='none' 同构, 不调脑);
        record_only = 打断空拍(只开帧留痕, 不思考不出声不等观测).

        ``percept_attrs`` = 各源的听侧读数(``{源: {low_conf/db/paused}}``),
        与 percepts **同 key 同行**走: 它们是同一条消息的两个面(正文 +
        元信息), 分两条路走迟早对不上(帧里有正文没属性, 或反过来)."""
        payload: Dict[str, Any] = {}
        if percepts:
            payload['percepts'] = percepts
        if percept_attrs:
            payload['percept_attrs'] = percept_attrs
        if transients:
            payload['transients'] = transients
        if logos:
            payload['logos'] = logos
        if record_only:
            payload['record_only'] = True
        try:
            r = await self.req(self._ghost_id, 'think', payload)
        except Exception as e:
            probe = await self._ghost_probe()
            if probe is not None and str(self._ghost_id) not in probe['ghosts']:
                rows = probe.get('rows') or []
                if not any(r.get('name') == 'moss' for r in rows):
                    # 整棵树在收摊(Ctrl+C / 关窗 / 装配件整体重载): 关停是
                    # 先子后父, ghost 比这一拍先走是**正常顺序**. 这一刻
                    # 红字喊"请重启 moss"纯属吓人 —— 用户正好在重启.
                    _log.warning('灵魂随整棵树一起退了(这一拍作废, 不是故障): %s',
                                 e)
                    return {}
                # 灵魂实例没了(热重载把它重建成新实例/被停/连接收摊):
                # 这一拍作废, 而且**不许再给它发 interrupt** —— 那是给
                # 一个不存在的目标补一刀, 只会把同一件事打成两条
                # traceback(实测: beat interrupted + interrupt request
                # failed 成对出现, 看着像两个故障).
                why = self._ghost_lost_reason(probe)
                if not self._ghost_lost_warned:
                    self._ghost_lost_warned = True
                    _log.error('灵魂(Ghost %s)不在了, 这一拍作废: %s —— %s',
                               self._ghost_id, e, why)
                # 静默才是最难查的: 让 UI 上看得见"它没死, 是灵魂不在"
                await self.publish('interrupt',
                                   f'灵魂不在了(ghost {self._ghost_id} 已停), '
                                   '需要重启 moss', namespace='moss')
                return {}   # 无观测 → 收链
            self._ghost_lost_warned = False
            _log.exception('beat interrupted: %s', e)
            try:
                await self.req(self._ghost_id, 'interrupt',
                               {'reason': str(e)})
            except Exception:
                _log.exception('interrupt request failed')
            return {}   # 无观测 → 收链
        if r.get('echo') and not logos and not record_only:
            # 灵魂报"本拍声明过的观测已清空"(原版 need_observe 点灯
            # 那一侧): 与手动 next 同灯 —— 拍内清空即续一拍, 免擂台.
            # 反射拍/打断空拍不续(原版 effort='none' 全程不语: 回声
            # 入史, 评说留下一届)
            self._request_beat('observe')
        return r

    # --- 入口(受理登记, 原版 fire-and-forget 同构) ---

    @request('declare')
    async def declare(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """外界输入入口: 申报登记(declare ≈ 原版外界信号入口
        add_signal/on_signal —— signal.__state__ dispatched/ignored ↔
        verdict 同构; 原版 fire-and-forget 返回 None: 仲裁在裁判
        循环拉取时打, 调用方只见注册级回执). verdict:
        accepted(登记入检录缓冲) / dropped(空 knock / 核治理丢弃) /
        noop(打断冷静期, 或核的抑制冷却) / ignored(stale). aside =
        送达挂账不经竞争; transient = 刷瞬态槽不经竞争.

        ``source`` 同时是**核名**: 注册了核的源走"核缺省值 → 核治理
        → 检录"三步(原版 ``Nucleus.add_signal`` 的同步核); 没注册的
        源走裸参数(向后兼容)."""
        fields: Dict[str, Any] = dict(data or {})
        src = str(fields.get('source') or 'user')
        nuc = self._nuclei.get(src)
        if nuc is not None:
            # 核的缺省字段: 调用方没给(或给 null)的键才补 —— 显式值
            # 永远优先, 核不覆盖调用方的现场判断
            for key, value in nuc.defaults().items():
                if fields.get(key) is None:
                    fields[key] = value
            if not nuc.accepting():
                # 原版 BufferNucleus.suppress 后的静默期: 冷却内该源的
                # 新申报不再 fire(高频源降频); 不排队 —— 静默丢
                return {'verdict': 'noop'}

        def pick(key: str, default: Any) -> Any:
            value = fields.get(key)
            return default if value is None else value

        imp = Impulse(
            id=str(pick('id', '') or uuid4().hex),
            source=src,
            text=str(pick('text', '')),
            priority=Priority(int(pick('priority', Priority.NOTICE))),
            strength=int(pick('strength', 100)),
            complete=bool(pick('complete', True)),
            mode=ChallengeMode(str(pick('mode', '') or '')),
            transient=bool(pick('transient', False)),
            interrupt=bool(pick('interrupt', False)),
            # 原版两轴正交: interrupt 管"停身体", effort 管"答不答".
            # 裸写 interrupt=True = 原版 interrupt primitive 全组合
            # (FATAL + notify + effort='none' + interrupt=True), 所以
            # 调用方没给 effort 时按 primitive 补 'none'; barge-in 的
            # 起话包(停掉旧回复但正常回答)显式传 effort='normal'.
            effort=str(pick('effort', '') or
                       ('none' if pick('interrupt', False) else 'normal')),
            low_conf=str(pick('low_conf', '')),
            db=pick('db', None),
            paused=pick('paused', None),
            protection_time=float(pick('protection_time', 0)),
            strength_decay_seconds=float(
                pick('strength_decay_seconds', 20)),
            stale_timeout=float(pick('stale_timeout', 0)))
        if nuc is not None:
            # 治理(原版 add_signal 的同步核): 静态纠偏, 不耗时; 返回
            # None = 这条申报不出门(原版"没东西可宣告")
            governed = nuc.govern(imp)
            if governed is None:
                nuc.note('dropped')
                return {'verdict': 'dropped'}
            imp = governed
        if imp.interrupt:
            # 优先级强制 FATAL(原版 interrupt primitive: "语义承诺不可
            # 降级", 调用方给的 priority 一律无视)
            imp = imp.model_copy(update={'priority': Priority.FATAL})
        if imp.mode == ChallengeMode.KNOCK and not imp.text:
            # 原版 KnockNucleus: knock 必须带消息 —— 没东西可宣告的
            # 敲门当场丢弃
            return {'verdict': 'dropped'}
        if (imp.interrupt and imp.effort == 'none'
                and time.monotonic() < self._interrupt_quiet_until):
            # 冷静期只属于打断核(原版 InterruptNucleus.suppress_seconds):
            # barge-in 的起话包(effort='normal')是正常输入, 不降频
            return {'verdict': 'noop'}
        if imp.is_stale():
            return {'verdict': 'ignored'}
        self._idle_event.clear()
        if imp.transient:
            # 记分牌不竞争(原版 dynamic_context 不经仲裁): 同源覆盖
            # 刷槽, 点亮信号让消化拍带走(链在场则下拍带走)
            if imp.complete and imp.text:
                self._pending_transients[imp.source] = imp.text
                self._has_signal.set()
            if nuc is not None:
                nuc.note('accepted')
            self._maybe_idle()
            return {'verdict': 'accepted'}
        if imp.mode == ChallengeMode.ASIDE:
            # 原版 aside: 送达不接管 —— 永不经竞争, 直挂账(下一拍
            # 的 percepts 看见); 静默态只挂账**不建链**(原版"送达
            # 不动脑"), 下一条输入开链时折进首拍
            if imp.text and imp.complete:
                self._inject(imp.source, imp.text)
                self._has_signal.set()
            if nuc is not None:
                nuc.note('accepted')
            self._maybe_idle()
            return {'verdict': 'accepted'}
        # 其余: 登记入检录缓冲, 点亮信号线(原版 _nucleus_has_impulse
        # 只标记信号, 拉取权在裁判循环)
        self._pending.append(imp)
        self._has_signal.set()
        if nuc is not None:
            nuc.note('accepted')
        return {'verdict': 'accepted'}

    @request('command')
    async def command(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """反射弧入口(原版 CommandNucleus / ImpulsePrimitive.command_only):
        ``text`` = logos(CTML) 直投身体执行, 不经思考 —— 急停类反射
        用 ``priority=5``(原版 fatal_command). last-wins(rank 取最新,
        新指令覆盖未消费的旧指令); 仲裁失败即丢(指令无重试语义);
        空指令当场丢. 执行的 logos 落帧入史(反幻觉: 执行了就必须看得见)."""
        text = str(data.get('text') or '')
        if not text:
            return {'verdict': 'dropped'}   # 原版: 空 logos 不产 impulse
        imp = Impulse(
            id=str(data.get('id') or uuid4().hex),
            source=str(data.get('source', 'command')),
            text=text,
            priority=Priority(int(data.get('priority', Priority.NOTICE))),
            strength=int(data.get('strength', 100)),
            complete=True, command=True)
        self._idle_event.clear()
        self._pending.append(imp)
        self._has_signal.set()
        return {'verdict': 'accepted'}

    @request('beat')
    async def beat(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """灵魂点灯回调 —— 回声唯一的内部入口(与手动 /go 共用同一盏
        灯, 见 ``_request_beat``).

        为什么需要它: 债是"有人等这个结果", 而结果可能晚到 —— 拍已
        收、链已静时才落地. 灵魂是结果帧的唯一过路点, 由它回头敲裁
        判补一拍, 债就不必靠"下一届碰巧有人说话"兑付.

        链活 → 只置灯, 由续拍循环消费(同链续拍, 免擂台不重排名).
        链死 → 灯落成下一届首拍(原版 need_observe 挂在 observer 上,
        attention 死而旗活同构); 检录缓冲空则自己登记一个空 impulse
        把链开起来(空文本, 这一拍的料全在缝合缝里)."""
        self._request_beat(str((data or {}).get('reason') or 'observe'))
        if not self._chain_alive and not self._pending:
            self._pending.append(Impulse(source='mindflow'))
            self._has_signal.set()
        return {'ok': True}

    @request('nuclei')
    async def nuclei(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """感知源自解释面(原版 ``Nucleus.description/status`` 的汇聚;
        原版这个反身面走 Mindflow Channel, mossv2 用 req 面替身).

        模型与运维从这里看"身体有几种感官、各自什么策略": 缺省优先级/
        强度、是否聚合、抑制冷却、受理计数. 核**不是准入清单** ——
        没注册核的源照样能裸参数申报, 只是没有按源覆写的默认值.
        """
        name = str((data or {}).get('name') or '')
        if name:
            nuc = self._nuclei.get(name)
            return {'ok': nuc is not None,
                    'nucleus': nuc.snapshot() if nuc is not None else None}
        return {'ok': True,
                'nuclei': [nuc.snapshot()
                           for nuc in self._nuclei.values()]}

    @request('idle')
    async def idle(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """静默屏障(测试/收摊探针): 等裁判静默 —— 无在途链 + 检录
        缓冲空 + 无挂账. 受理登记后, "这轮说完做完没有"从这里观测
        (原版这个反身面走 Mindflow Channel, 未抄, 这是最小替身)."""
        await self._idle_event.wait()
        return {}

    # --- 杂项 ---

    def _request_beat(self, reason: str) -> None:
        """点"下一拍"灯(原版 ``need_observe`` 闩锁, 幂等置位: 一拍内
        点几次都只续一拍) —— **手动激活与回声共用这一盏**, 是二者
        "同一条路径"的落点:
        - 手动 next 挑战失败 → 原版 ``verdict='queued'`` 正是
          ``add_echoes([], need_observe=True)``(_mindflow.py:714-717);
        - 帧带 owed 戳(错误, 或声明过 ``observe="true"`` 的成功) →
          灵魂报 ``echo``(拍内), 或敲 ``beat``(债晚到).
        三个点灯人最终都由 attention 内同一个 while 循环消费
        (_mindflow.py:1148) —— 所以回声不是第二条驱动线, 只是灯的
        另一个来源; 也所以它不需要绕一趟 declare(那会多一圈 RPC,
        且链可能已收尾, 反而丢掉"免擂台续拍"的语义).

        判据的身份说明: 点灯的不是"结果"本身, 而是"有人在等这个
        结果"(错误自带债 / 成功靠声明认领, 见 shell ``_record_trace``)
        —— 与 Go 的 ``err`` 一致: 值不会自己跳转, 要不要看由调用方
        声明."""
        self._need_beat = True
        _log.debug('next beat requested by %s', reason)

    def _merge_inject(self, percepts: Dict[str, str],
                      inject: List[tuple[str, str]]) -> Dict[str, str]:
        """挂账折进 percepts(原版 inject_percepts → 下一帧 percepts
        同构): 同源续接(正文先到, 挂账后到), 异源新条目."""
        for src, text in inject:
            if src in percepts:
                percepts[src] = percepts[src] + '\n' + text
            else:
                percepts[src] = text
        return percepts

    @staticmethod
    def _percept_attrs(imp: Impulse) -> Dict[str, Dict[str, Any]]:
        """一条包的听侧读数 → ``{源: {属性}}``(空 = 这个源没有属性).

        **挂在源上, 不挂在包上**: percepts 本来就是按源 keyed 的(一拍里
        同源多包折成一段文字), 属性跟着同一个 key 走才不会错位; 同源多包
        时以最后一条(尾包/最新)为准 —— 起话包没有的读数, 尾包有.

        只放**真有的**属性: ``low_conf`` 空就不放(空字符串印出来是
        ``low_conf=""``, 让模型去猜"空是什么意思"), ``db``/``paused`` 为
        None 同理. 原版 ``_listen_message`` 也是这个规矩(没有的维度不占位).
        这里只负责"有没有", 印不印由渲染层定(现在只印 paused).
        """
        attrs: Dict[str, Any] = {}
        if imp.low_conf:
            attrs['low_conf'] = imp.low_conf
        if imp.db is not None:
            attrs['db'] = imp.db
        if imp.paused is not None:
            attrs['paused'] = imp.paused
        return {imp.source: attrs} if attrs else {}

    def _inject(self, source: str, text: str) -> None:
        """挂账(原版 inject_percepts 同构, 按源记) + 上限: 超限丢
        最旧 —— 挂账无上限是 DoS 面(原版同坑, 这里加闸: 流量异常时
        保最近的)."""
        self._pending_inject.append((source, text))
        if len(self._pending_inject) > self.MAX_INJECT:
            self._pending_inject = self._pending_inject[-self.MAX_INJECT:]

    def _requeue(self, impulse: Impulse) -> None:
        """链死时未入记忆的消息回检录缓冲(原版 nucleus buffer 保留
        同构); 保持 incomplete —— 尾包稍后到达时 rank 批内配对
        (原版核内部协议同构, 残句等它的尾包, 不强想半句)."""
        if not impulse.text:
            return
        self._pending.append(impulse)
        self._has_signal.set()

    def _drain_inject(self) -> List[tuple[str, str]]:
        inject, self._pending_inject = self._pending_inject, []
        return inject

    def _drain_transients(self) -> Dict[str, str]:
        ts, self._pending_transients = self._pending_transients, {}
        return ts

    async def stop(self) -> None:
        # 点退出灯 + 敲醒裁判循环(它可能在 wait_for 上等信号, 最长等 TICK).
        # 灯先亮再敲: 循环醒来第一件事就是查灯, 不会多跑一巡.
        self._stopped.set()
        self._has_signal.set()
        if self._chain_task is not None:
            self._chain_task.cancel()
