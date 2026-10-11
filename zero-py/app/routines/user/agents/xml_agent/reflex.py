"""ReflexChain ---- 入站消息反射层(决策与执行分离).

act 期间的用户消息不再无条件打断, 先过 reflex 链出决策:

  PASS       直入主循环(落库 + 开新轮; idle 期消息)
  INTERRUPT  打断在途 react/act + 落库 + 开新轮(= 引入本层前的旧行为)
  DEFER      缓存不落库, react 收尾时 drain 落库并自动开新轮(用户插话
             被 hold, agent 说完自然接上; 落库时刻才定序, 尾序恒 user)
  DROP       纯噪声, 不落库不反应

链上环节只拿 (msg, state) 出决策, 不碰 agent 内部 ---- 决策执行集中在
agent 侧 executor(_run_reflex), 映射到 cancel/interrupt/store/start 既有
动作. Jev 失败/超时降级 INTERRUPT(宁打断不丢消息).

Jev 契约(demo_reflex 验证过): OpenRouter decisions API, Noul x2
(interrupt/cache), 双阈值 interrupt 0.5 / cache 0.2(缓冲优先,
不确定不丢). 协议层见 jev.py(JevClient), 本文件只组问题与出决策.
"""
from __future__ import annotations

import enum
import logging
from typing import Awaitable, Callable, Optional

from .jev import JevClient

logger = logging.getLogger(__name__)

# interrupt 高于此值立即打断(demo 实测概率分离度好, 0.5 余量足)
_THRESHOLD = 0.5

# cache 高于此值缓冲(DEFER). 更低的一档 _NOISE_FLOOR 之下才丢: 误缓冲
# 代价 = act 完后延迟响应一轮, 误丢弃 = 用户消息石沉大海 ---- 不确定
# 的短指令("展示"类, 分类器在 0.4~0.5 犹豫)默认缓冲, 只有大概率纯
# 噪声(自言自语/环境人声)才 DROP.
_NOISE_FLOOR = 0.2

# 单条文本截断(防长内容撑爆 Jev 32k 上下文)
_CLIP = 2000


def _clip(text: str) -> str:
    text = (text or '').strip()
    return text if len(text) <= _CLIP else text[:_CLIP] + '…(truncated)'


class Decision(enum.Enum):
    PASS = 'pass'
    INTERRUPT = 'interrupt'
    DEFER = 'defer'
    DROP = 'drop'


class ReflexState:
    """reflex 只读快照: agent 判定时刻的现场(不知道 agent 内部结构)."""

    def __init__(self, *, in_flight: bool, speaking: str,
                 recent_turns: list[tuple[str, str]]):
        self.in_flight = in_flight        # react 在途(= act 期)
        self.speaking = speaking           # agent 正在播报/生成的文本
        self.recent_turns = recent_turns   # 尾部轮次 [(user, assistant)]


# reflex 环节签名: 返回 None = 不决策, 交下一环; 默认(全 None)PASS
Reflex = Callable[[str, ReflexState], Awaitable[Optional[Decision]]]


async def _barge_in(jev: JevClient, speaking: str, asr_text: str,
                    recent_turns: list[tuple[str, str]]) -> dict:
    """act 期插话判断: 一次往返出双概率 -> {'interrupt': p, 'cache': p}."""
    state = {
        'speaking': _clip(speaking),
        'user_speech_while_speaking': _clip(asr_text),
        'recent_turns': [
            {'user': _clip(u), 'assistant': _clip(a)}
            for u, a in recent_turns[-1:]
        ],
    }
    questions = {
        'interrupt': {
            'type': 'noul',
            'instructions':
                '用户在 agent 说话期间插入了这句话。是否想让 agent 立即'
                '停下或纠正正在说的内容?',
            'criteria': {
                'true':
                    '明确叫停、纠正、反对当前内容, 或要求放下正在做的事'
                    '立刻改做别的。',
                'false':
                    '无关附和、闲聊、自言自语; 或用户明确表示"做完…/稍后/'
                    '等一下再说"的排队指令 ---- 说完再办, 不要打断。',
            },
        },
        'cache': {
            'type': 'noul',
            'instructions':
                '这句话是否包含与当前任务相关、值得 agent 记住稍后处理的'
                '新信息或新指令?',
            'criteria': {
                'true':
                    '新的待办、补充信息、修改要求, 或"做完…再说"式的排队'
                    '指令 ---- 需要后续处理, 但不必立即打断。',
                'false': '无关内容, 或无需后续处理。',
            },
        },
    }
    answers = await jev.decide(state, questions)
    return {
        'interrupt': float(answers['interrupt']['noul']),
        'cache': float(answers['cache']['noul']),
    }


async def state_gate(msg_text: str, state: ReflexState) -> Optional[Decision]:
    """代码层闸门: idle 直通(不需要语义判断, 零成本)."""
    return None if state.in_flight else Decision.PASS


def barge_in_reflex(jev: JevClient) -> Reflex:
    """act 期插话判断: Jev Noul x2 -> 打断/缓存/忽略(失败抛给上层降级)."""

    async def _reflex(msg_text: str, state: ReflexState) -> Optional[Decision]:
        r = await _barge_in(jev, state.speaking, msg_text, state.recent_turns)
        logger.info('reflex probabilities: interrupt=%.2f cache=%.2f',
                    r['interrupt'], r['cache'])
        if r['interrupt'] > _THRESHOLD:
            return Decision.INTERRUPT
        if r['cache'] > _NOISE_FLOOR:
            return Decision.DEFER
        return Decision.DROP

    return _reflex


class ReflexChain:
    """顺序过环节: 首个非 None 决策生效; 全放行 = PASS."""

    def __init__(self, *reflexes: Reflex):
        self._reflexes = reflexes

    async def run(self, msg_text: str, state: ReflexState) -> Decision:
        for reflex in self._reflexes:
            decision = await reflex(msg_text, state)
            if decision is not None:
                return decision
        return Decision.PASS
