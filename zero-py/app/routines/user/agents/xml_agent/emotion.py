"""Emotion ---- 轮级情绪系统: Jev 评估出增量, 代码积分持状态, TTS 指令表达.

仿生分工(四块):
  评估  Jev Scorex2 每轮出 (Dvalence, Darousal) ---- appraisal, 模型无状态
  心境  integrate() 漏桶积分回归基线 ---- homeostasis, 纯代码
  表达  tts_instruction() 两轴独立出短语再拼合 ---- 不自主, 系统强制
  反哺  mood_state() 把当前 mood 喂回 Jev state ---- 心境一致性

粒度: 轮级基调(每轮 speak 一条 context_texts 语音指令); 两轴离基线均
<0.2 的中性带不干预(不下发 additions, TTS 用默认语气).

边界: mood 不持久化(重启回落基线); 衰减按轮(decay), 墙钟衰减二期.
"""
from __future__ import annotations

from dataclasses import dataclass

# 单条 turn 文本进 Jev state 的截断(控量)
_CLIP = 300


@dataclass(frozen=True)
class Temperament:
    """气质: persona 级常量(先代码默认, 后续可提进 persona XML)."""

    base_v: float = 0.05   # 基线: 略阳光
    base_a: float = 0.0    # 基线: 平静
    gain: float = 0.6      # 敏感度: 事件增量折入比
    decay: float = 0.65    # 每轮向基线回归比(约 3 轮忘一半)


def integrate(v: float, a: float, dv: float, da: float,
              t: Temperament) -> tuple[float, float]:
    """漏桶积分: 每轮先向基线回归再吃增量, clamp [-1, 1]."""

    def _axis(x: float, base: float, d: float) -> float:
        return max(-1.0, min(1.0, base + (x - base) * t.decay + d * t.gain))

    return _axis(v, t.base_v, dv), _axis(a, t.base_a, da)


def fold_delta(pos: float) -> float:
    """Score 档位位置 -> 轴增量: (pos - 中点) * 步长. 5 档中点 2 步长 0.25."""
    return (float(pos) - 2.0) * 0.25


def emotion_questions() -> dict:
    """Score x 2: valence(愉悦度) / arousal(活跃度), 5 档低 -> 高.

    指令聚焦 user 本轮语气(实测验证: 原措辞会被 agent 的礼貌回应稀释,
    Jev 一律答"基本不变"; 以 user 语气为准后信号幅度/置信度都正常).
    """

    return {
        'valence_shift': {
            'type': 'score',
            'instructions': (
                '聚焦 user 本轮输入的语气: 用户是在批评、指责、表达不满'
                '(变差), 还是在夸奖、感谢、表达满意(变好)? '
                'agent 回应只是参考, 以 user 语气为准'),
            'criteria': ['明显变差', '略微变差', '基本不变',
                         '略微变好', '明显变好'],
        },
        'arousal_shift': {
            'type': 'score',
            'instructions': (
                '聚焦 user 本轮输入的语气: 用户情绪激动、愤怒、急切(活跃), '
                '还是平静、冷淡、疲惫(沉静)? 以 user 语气为准'),
            'criteria': ['明显变沉静', '略微沉静', '基本不变',
                         '略微活跃', '明显活跃'],
        },
    }


def mood_state(current: str, v: float, a: float) -> dict:
    """Jev state: 仅本轮输入 + 当前 mood(反哺).

    不带历史轮次 ---- 实测(decider-2b 对照实验)历史会锚定判断: 连续骂后
    的夸奖被判负(把旧骂的平均进当前输入的判读), 单轮则夸骂分明
    (+0.44/-0.42); 代价是反讽等依赖上下文的输入按字面判(轮级粗染色的
    可接受边界). current_mood 反哺保留: 轻微向当前情绪回归(语义即
    shift 的参考点), 不翻转判读.
    """

    return {
        'recent_turns': [{'user': current[:_CLIP], 'agent': ''}],
        'current_mood': (
            f'valence={v:+.2f}({"好" if v > 0.05 else "坏" if v < -0.05 else "中性"}), '
            f'arousal={a:+.2f}({"活跃" if a > 0.05 else "沉静" if a < -0.05 else "平静"})'
        ),
    }


def _v_phrase(v: float) -> str:
    """valence -> 愉悦度短语. 不夹带唤醒语义(语速/能量归 arousal 轴),
    两轴组合才自洽: 低落+语速快=烦躁紧张, 低落+语速慢=沮丧."""
    if v >= 0.5:
        return '明显开心'
    if v >= 0.2:
        return '轻快愉悦'
    if v <= -0.5:
        return '明显低落、难过'
    if v <= -0.2:
        return '有点低落'
    return ''


def _a_phrase(a: float) -> str:
    if a >= 0.5:
        return '语速快、充满能量'
    if a >= 0.2:
        return '语速稍快'
    if a <= -0.5:
        return '语速缓慢低沉'
    if a <= -0.2:
        return '语速平缓'
    return ''


def tts_instruction(v: float, a: float) -> str:
    """mood -> TTS 语音指令(context_texts). 中性带返回空 = 不干预.

    直接给情绪描述(轻快愉悦/低落/语速快), 不加"用xx的语气说"包装 ----
    指令遵循通路自己渲染语气.
    """
    parts = [p for p in (_v_phrase(v), _a_phrase(a)) if p]
    return '、'.join(parts)
