"""TTS 包: Seed WS 双向流协议 + Speaker + SeedTTS + LocalSpeaker.

协议层无业务依赖.
LocalSpeaker 用 sounddevice 本地播放 PCM, 三级流水线首帧即播.

create_tts() 工厂懒加载单例 WS 连接 (SeedWSConnection + Speaker), 每次
返回独立 SeedTTS 实例 (独立 feed queue), 供 speak routine 使用.
"""
import os
from typing import Optional

from .interface import AudioChunk, TTS
from .audio_speaker import AudioSpeaker, LocalSpeaker
from .seed import SeedWSConnection, Speaker, SeedTTS, Event

__all__ = [
    "AudioChunk", "TTS",
    "AudioSpeaker", "LocalSpeaker",
    "SeedWSConnection", "Speaker", "SeedTTS", "Event",
    "create_tts", "get_player",
]

# ── 单例 (跨 speak routine 复用, 避免每次重建 TLS+协议握手) ────────────────────
_conn: Optional[SeedWSConnection] = None
_speaker: Optional[Speaker] = None
_player: Optional[LocalSpeaker] = None


def create_tts() -> SeedTTS:
    """返回一个 SeedTTS 实例, 共享全局 WS 连接 + Speaker.

    环境变量:
      SEED_TTS_API_KEY     — 火山引擎 API Key (必填)
      SEED_TTS_SPEAKER_ID  — 音色 ID (可选, 默认 zh_female_xiaohe_uranus_bigtts)
      SEED_TTS_RESOURCE_ID — 模型版本 (可选, 默认 seed-tts-2.0)
    """
    global _conn, _speaker
    if _conn is None:
        api_key     = os.environ.get("SEED_TTS_API_KEY", "")
        speaker_id  = os.environ.get(
            "SEED_TTS_SPEAKER_ID", "zh_female_xiaohe_uranus_bigtts",
        )
        resource_id = os.environ.get("SEED_TTS_RESOURCE_ID", "seed-tts-2.0")
        _conn    = SeedWSConnection(api_key=api_key, resource_id=resource_id)
        _speaker = Speaker(_conn, speaker_id=speaker_id)
    return SeedTTS(_speaker)


def get_player() -> LocalSpeaker:
    """返回全局 LocalSpeaker 单例 (sounddevice 本地播放)."""
    global _player
    if _player is None:
        _player = LocalSpeaker()
    return _player
