"""play_music -- 播放本地音乐文件, body 派生子边放边跑.

继承 XmlRoutine: ``<play_music><dance duration="3"/></play_music>`` 边放音乐边跳舞.

占 ``audio`` 模块: 多个 play_music 串行; body 子(如 dance)占 ``body``, 跟 audio
不冲突, 父子并发. 短提示音(sound_effect)不占模块, 可叠在音乐上.
"""
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from routine import Modules
from app.modules import MODULE_AUDIO
from ..xml.xml_routine import XmlRoutine
from .wav_player import WavPlayer


_ASSETS_DIR = Path(__file__).parent / 'assets'
_DEFAULT_NAME = 'Found-My-Way'


def _resolve(name: str) -> Path:
    """按 name 在 assets/ 下查找任意扩展名的音频文件."""
    matches = [p for p in _ASSETS_DIR.glob(f'{name}.*') if p.suffix.lower() != '.json']
    if not matches:
        raise FileNotFoundError(f'no asset matching "{name}.*" under {_ASSETS_DIR}')
    return matches[0]


class PlayMusicInput(BaseModel):
    """play_music input."""

    name: str = Field(_DEFAULT_NAME, description='音乐名(assets/ 下的文件名, 不含扩展名)')
    duration: Optional[float] = Field(
        10, description='播放秒数; >0 播指定秒数后自动退出, 0 或 null 播整首')


class PlayMusic(XmlRoutine):
    """按 name 播放音乐, body 派生子(如 dance)边放边跑."""

    meta = {
        'description': '按 name 播放本地音乐(assets/ 曲单, 见 list_music)',
        'input_schema': PlayMusicInput.model_json_schema(),
    }

    def __init__(self):
        super().__init__()
        self._player: Optional[WavPlayer] = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        await super().on_created(rid, kwargs)
        return Modules([MODULE_AUDIO])

    async def run(self, kwargs: Dict[str, Any]):
        inputs = PlayMusicInput(**kwargs)
        name = inputs.name
        duration = inputs.duration
        if not name:
            raise ValueError('name parameter is required')
        if duration is not None and duration < 0:
            raise ValueError(f'duration must be >= 0 or None, got {duration!r}')
        if duration == 0:
            duration = None

        # 单 instance + 跨 shell 调度有漏: 第二个 run() 进来时, self._player
        # 可能仍对应上一首未完的播放.不清掉直接覆盖, 旧播放就成孤儿继续响
        # -> 听起来像"同时放两首".先把老的干掉.
        await self._stop_previous()

        file_path = _resolve(name)
        self._logger.info(
            'music: playing %s (duration=%s)',
            file_path, 'full' if duration is None else f'{duration}s',
        )

        self._player = WavPlayer(self._logger)
        await self._player.play(file_path, duration=duration)

    async def _stop_previous(self):
        player = self._player
        if player is None:
            return
        self._logger.warning(
            'music: previous player still alive, stopping before restart'
        )
        await player.stop()

    async def stop(self):
        self._logger.info('music: stopping')
        player = self._player
        if player is None:
            return
        self._player = None
        await player.stop()
