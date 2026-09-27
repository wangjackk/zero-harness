"""SoundEffect -- 短提示音(纯合成 chirp, 无音频文件依赖).

start: 660→990Hz 上行(动作开始), stop: 990→660Hz 下行(动作结束).
numpy 正弦扫频 + 5ms 淡入淡出防爆音.

不占 audio 模块 ---- 独立 OutputStream 与正在播放的音乐自动混音
(提示音必须随时可响, 不能被占着 audio 的音乐阻塞). car/move 在
起步/停住时调用.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal

import numpy as np
from pydantic import BaseModel, Field

from routine import Routine

from .wav_player import WavPlayer

_SR = 44100
_DUR = 0.16                       # 单音时长
_FADE = int(_SR * 0.005)          # 淡入淡出样本数

# effect → (f0, f1) Hz
_EFFECTS: Dict[str, tuple] = {
    'start': (660.0, 990.0),
    'stop': (990.0, 660.0),
}


class SoundEffectInput(BaseModel):
    """sound_effect input."""

    effect: Literal['start', 'stop'] = Field(
        description='提示音: start 上行(动作开始), stop 下行(动作结束)')
    volume: float = Field(0.6, ge=0.0, le=1.0, description='音量 0~1; 默认 0.6')


class SoundEffectOutput(BaseModel):
    """sound_effect output."""

    effect: str = Field(description='实际播放的提示音')
    duration: float = Field(description='播放时长(秒)')


def _chirp(f0: float, f1: float, dur: float, volume: float) -> np.ndarray:
    t_n = int(_SR * dur)
    phase = 2.0 * np.pi * np.cumsum(np.linspace(f0, f1, t_n)) / _SR
    wave = np.sin(phase) * volume
    wave[:_FADE] *= np.linspace(0.0, 1.0, _FADE)
    wave[-_FADE:] *= np.linspace(1.0, 0.0, _FADE)
    return wave.astype(np.float32)


class SoundEffect(Routine):
    """播放短合成提示音(起步上行/停住下行, ~0.16s)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '播放短合成提示音(start 上行 / stop 下行, ~0.16s), '
            '用于动作开始/结束的听觉提示; 与音乐混音不互斥'),
        'input_schema': SoundEffectInput.model_json_schema(),
        'output_schema': SoundEffectOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = SoundEffectInput.model_validate(kwargs)
        f0, f1 = _EFFECTS[inp.effect]
        await WavPlayer(self._logger).play(_chirp(f0, f1, _DUR, inp.volume), _SR)
        return SoundEffectOutput(effect=inp.effect, duration=_DUR).model_dump()
