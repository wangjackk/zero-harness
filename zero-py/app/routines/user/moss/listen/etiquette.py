"""聆听礼仪 —— 模型在运行期编程的交互风格（原版 ``host/listener/etiquette.py`` 移植）。

一份礼仪配置一个 listening session，由**三个彼此独立的层**组成，一种交互
风格是这三层笛卡尔积里的一个**坐标点，不是一条代码分支**：

- **onset**（起话）：开口的瞬间给什么信号。
- **stop**（判停）：这一轮"说完了"怎么判定。
- **deliver**（交付）：唯一的交付旋钮 —— 发信号，还是只在缓冲里留存。

下面 ``EtiquetteConfig`` 里那批开箱礼仪就是空间里的示例坐标。复制一个、改几个
字段，就是一种新风格，不用改代码。

与 mossv2 的对应关系：``Priority`` / ``ChallengeMode`` 的取值与原版逐值相同，
所以礼仪里的 ``priority`` / ``mode`` 可以原样喂给 ``mindflow.declare``。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..mindflow import Priority

__all__ = [
    'OnsetSpec', 'DeliverSpec', 'DeliverMode', 'ClassifierSpec', 'DetectSpec',
    'StopSpec', 'EtiquetteSpec', 'EtiquetteConfig',
    'once', 'always', 'aside', 'notify', 'scribe', 'observer',
    'keyword_end', 'scored', 'murmur', 'cue', 'knock',
]


#: 交付失败侧的处置 —— 与 ``mindflow.ChallengeMode`` 逐值对齐：
#: - ``''`` 默认：抢占失败即 suppress
#: - ``'aside'`` 只注入消息、绝不接管注意力
#: - ``'notify'`` 抢不到就 buffer（闲了再答）
#: - ``'next'`` buffer + 强制下一帧观察（插队）
DeliverMode = Literal['', 'aside', 'notify', 'next']


class OnsetSpec(BaseModel):
    """起话协议 —— 开口瞬间发生什么（barge-in）。"""

    emit: bool = Field(default=True, description='开口即发一个信号')
    interrupt: bool = Field(default=True, description='该信号停掉当前行为')
    priority: Priority = Field(default=Priority.WARNING,
                               description='起话信号的抢占档位')


class DeliverSpec(BaseModel):
    """交付协议 —— 唯一的旋钮是 ``emit``。

    ``emit=True``：判停后把这一轮推成 listener signal；
    ``emit=False``：一个信号都不发，强制留存，模型自己来拉（输入法语义）。
    没有"既不发送也不留存"的组合 —— ``emit=False`` 必然留存。

    ``interrupt`` / ``priority`` / ``mode`` 只在 ``emit=True`` 时有意义；
    ``history`` 只在 ``emit=False`` 时有意义（环形容量）。
    """

    emit: bool = Field(default=True, description='交付旋钮：True 推信号 / False 只留存')
    history: int = Field(default=8, ge=1, description='留存环形容量（emit=False 生效）')
    interrupt: bool = Field(default=False, description='交付信号停掉当前行为（emit=True）')
    priority: Priority = Field(default=Priority.INFO, description='交付信号抢占档位（emit=True）')
    mode: DeliverMode = Field(
        default='notify',
        description="抢占失败侧的去处：'notify' 排队 / 'aside' 只旁注 / "
                    "'next' 插队 / '' 直接 suppress（emit=True 生效）")
    low_confidence: float | None = Field(
        default=None,
        description='词级置信度阈值：低于它的字会列进交付信号的 low_conf 字段'
                    '（数据面；渲染层暂不印进 prompt）；None = 不列')


class ClassifierSpec(BaseModel):
    """挂在判停槽位上的可编程流式分类器。

    槽位只决定"这里要跑一个分类器"，``instruction`` 决定它判什么、怎么打分。
    ``context`` 携带易变提示（比如用户刚宣布的结束语、任务背景），由
    ``instruction`` 里的 ``<context>`` 块引用。
    """

    instruction: str = Field(default='', description='分类器自己的指令：判什么、怎么打分')
    context: str = Field(default='', description='易变提示（原样塞进 <context> 块）')
    threshold: int = Field(default=7, ge=0, le=9, description='分数 >= 阈值即提前判停')
    delay: float = Field(default=0.3, ge=0.0, description='打分防抖：这段时间内被下一句取代就不花调用')


class DetectSpec(BaseModel):
    """音频检测 —— 一个组合式槽位，检测到声音时**只发信号、不判停**。

    与 ``classifier`` 对称：一个是可编程的 LLM 判停，一个是可编程的音频检测器。
    """

    enabled: bool = Field(default=True, description='启用声音检测')
    threshold_db: float = Field(default=-50.0, description='能量阈值（rms_db），低于视为静音')
    handler: str | None = Field(default=None, description='可选的沙箱音频检测函数；None = 简单能量阈值')
    priority: Priority = Field(default=Priority.NOTICE, description='检出时 knock 信号的档位')
    cooldown: float = Field(default=10.0, ge=0.0, description='检出后抑制重复 knock 的秒数')


class StopSpec(BaseModel):
    """判停 —— 这一轮怎么算结束。四条可组合的 commit 路径：

    - ``silence``：最后一个分句之后再静音 N 秒（0 = 首个分句即端点）；
    - ``keywords``：显式结束词，命中立刻 commit；
    - ``classifier``：打分式提前判停，None = 不挂；
    - ``detect``：音频检测器，检出只发 knock，不 commit，None = 不挂。
    """

    silence: float = Field(default=1.5, ge=0.0,
                           description='静音 N 秒后判停；0 = 首个分句即端点')
    keywords: list[str] = Field(default_factory=list,
                                description='显式结束词，命中立刻 commit')
    classifier: ClassifierSpec | None = Field(default=None,
                                              description='打分式提前判停；None = 不挂')
    detect: DetectSpec | None = Field(default=None,
                                      description='只发 knock 的能量检测；None = 不挂')


class EtiquetteSpec(BaseModel):
    """一种礼仪 —— 名字 + 自述 + 三层配置。"""

    name: str = Field(description='礼仪名；被 EtiquetteConfig.default 引用')
    description: str = Field(default='', description='一句话自述，供模型感知')
    onset: OnsetSpec = Field(default_factory=OnsetSpec)
    stop: StopSpec = Field(default_factory=StopSpec)
    deliver: DeliverSpec = Field(default_factory=DeliverSpec)
    idle_timeout: float = Field(
        default=300.0, ge=0.0,
        description='多久没有任何识别事件就自行结束会话'
                    '（发一条 notify 告知，然后降级到 knock）；0 = 永不因空闲结束')


# ── 开箱礼仪：每个是一个坐标；复制一个改字段就是新礼仪 ──

# 一次：说一句立刻交付（silence=0 → 首个分句即端点）；不起话，不听中途。
once = EtiquetteSpec(
    name='once',
    description='只说一句 —— 首个分句即端点',
    onset=OnsetSpec(emit=False),
    stop=StopSpec(silence=0.0),
)

# 默认：打断式自由对话。起话即打断（barge-in），静音 1.5s 交付；抢不到注意力就排队，闲了再答。
always = EtiquetteSpec(
    name='always',
    description='可打断的轮流对话 —— 静音 1.5s 交付',
    onset=OnsetSpec(emit=True, interrupt=True, priority=Priority.WARNING),
    stop=StopSpec(silence=1.5),
    deliver=DeliverSpec(mode='notify'),
)

# 旁听：起话发信号但绝不打断；你干你的，我不接管。
aside = EtiquetteSpec(
    name='aside',
    description='只听不打断 —— 只旁注，绝不接管',
    onset=OnsetSpec(emit=True, interrupt=False, priority=Priority.NOTICE),
    stop=StopSpec(silence=1.5),
    deliver=DeliverSpec(interrupt=False, mode='aside'),
)

# 闲时回应：起话不打断，说的都收着；我忙就继续忙，闲了才回应。
notify = EtiquetteSpec(
    name='notify',
    description='先忙你的 —— 闲下来才回应',
    onset=OnsetSpec(emit=True, interrupt=False, priority=Priority.INFO),
    stop=StopSpec(silence=2.0),
    deliver=DeliverSpec(interrupt=False, priority=Priority.INFO, mode='notify'),
)

# 书记员：大段论述，一直听、一直做手头的事，但不打断；下轮一定轮到我。
scribe = EtiquetteSpec(
    name='scribe',
    description='长口述 —— 手头不停，但下轮轮到你',
    onset=OnsetSpec(emit=True, interrupt=False, priority=Priority.INFO),
    stop=StopSpec(silence=2.5),
    deliver=DeliverSpec(interrupt=False, mode='next'),
)

# 只录不答：不发任何信号，只把听到的留存，事后可拉读（转写/会议记录）。
observer = EtiquetteSpec(
    name='observer',
    description='只转写 —— 永不发信号，留存最近听到的文本',
    onset=OnsetSpec(emit=False),
    stop=StopSpec(silence=2.0),
    deliver=DeliverSpec(emit=False, history=16),
)

# 对讲机：显式端点，说完喊 over 才交付，不靠静音。
keyword_end = EtiquetteSpec(
    name='keyword_end',
    description='对讲机 —— 只有显式结束词才交付',
    stop=StopSpec(silence=30.0, keywords=['over', '完毕']),
)

# 低语旁听：持续听，每句一交付，绝不打断模型 —— 忙时被动挂账，闲了才唤醒。
# 已知陷阱（原版同注）：notify/next 只保证"忙时不抢占"；一旦某句随 attention
# 衰减最终抢到，preempt 仍带 interrupted 去 shell.clear() 停身体。"preempt 中性化"
# 是 mindflow 侧的独立待办。
murmur = EtiquetteSpec(
    name='murmur',
    description='每句都到你眼前但不打断；回不回应由你',
    onset=OnsetSpec(emit=False),
    stop=StopSpec(silence=0.0),
    deliver=DeliverSpec(interrupt=False, mode='notify'),
)

# 逐句接话：每句自成一段，每句强制下轮轮到你，但绝不打断身体。
cue = EtiquetteSpec(
    name='cue',
    description='连续输入 —— 每句各占一轮，但不停你的身体',
    onset=OnsetSpec(emit=False),
    stop=StopSpec(silence=0.0),
    deliver=DeliverSpec(interrupt=False, mode='next'),
)

# 长论述判停：挂一个可编程分类器判"这段说完没有"，打分到阈值提前交付，静默兜底。
scored = EtiquetteSpec(
    name='scored',
    description='分类器判停 + 静音兜底',
    stop=StopSpec(
        silence=3.0,
        classifier=ClassifierSpec(
            instruction=(
                '给一段实时语音转写里"说话人这个想法有多完整"打一个 0-9 的整数分。\n'
                '0-3 = 明显没说完 —— 半句被切断、以悬空的连词或未闭合的条件结尾'
                '（因为…、如果…、because…、if…），或者全是语气词。\n'
                '4-6 = 说不准 —— 在这儿停也说得过去，继续也行。\n'
                '7-9 = 明显说完了 —— 完整陈述、可回答的问题、招呼，或闭合的短答'
                '（是/不是/好）。\n'
                '按语义判断：ASR 会写出谐音和近音字，永远不要相信表面拼写。'
                '如果 <context> 里声明了显式结束语，听到它就是强完成证据。'
                '只输出那个数字，不要标点，不要解释。'
            ),
            threshold=7,
            delay=0.3,
        ),
    ),
)

# 门铃：空闲降级后的轻量能量检测 —— 不做 ASR，检测到声音发 knock，让模型决定要不要重新开口。
knock = EtiquetteSpec(
    name='knock',
    description='空闲门铃 —— 只测声音，敲一下，由模型决定接不接',
    stop=StopSpec(detect=DetectSpec()),
    idle_timeout=0.0,
)


class EtiquetteConfig(BaseModel):
    """已定义的礼仪 + 当前激活项（原版同时挂 ConfigStore 持久化，这里只做内存表）。"""

    etiquettes: list[EtiquetteSpec] = Field(
        default_factory=lambda: [
            once.model_copy(deep=True),
            always.model_copy(deep=True),
            aside.model_copy(deep=True),
            notify.model_copy(deep=True),
            scribe.model_copy(deep=True),
            observer.model_copy(deep=True),
            keyword_end.model_copy(deep=True),
            scored.model_copy(deep=True),
            murmur.model_copy(deep=True),
            cue.model_copy(deep=True),
            knock.model_copy(deep=True),
        ],
        description='已定义的礼仪表（名字 + 三层配置）')
    default: str = Field(default='always', description='当前激活的礼仪名')

    def get(self, name: str) -> EtiquetteSpec | None:
        for spec in self.etiquettes:
            if spec.name == name:
                return spec
        return None

    def upsert(self, spec: EtiquetteSpec) -> None:
        """按 name 增/改（同名覆盖，异名追加）。"""
        for i, existing in enumerate(self.etiquettes):
            if existing.name == spec.name:
                self.etiquettes[i] = spec
                return
        self.etiquettes.append(spec)

    def activate(self, name: str) -> None:
        """激活一种礼仪（设 default）。未定义则抛错。"""
        if self.get(name) is None:
            raise ValueError(f'etiquette {name!r} not defined')
        self.default = name

    def active(self) -> EtiquetteSpec | None:
        return self.get(self.default)
