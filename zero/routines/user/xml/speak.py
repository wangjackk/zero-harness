"""Speak -- TTS 文本转语音叶子 routine.

给 Act 当 on_body_text 派生的子: 收 text body(流式), 喂给 SeedTTS 合成音频,
player 直接推 binary PCM 给前端播放.

流式设计:
  on_message 收 {text,id} 按 id reorder 后 tts.feed(text);
  {_eof,id} 调 tts.finish() 让音频流自然结束.
  run 启动 player.play(tts.audio_chunks()), 阻塞到音频推送完毕.

TTS 资源(WS 连接 + player)全局单例复用(create_tts / get_player 懒加载),
Speak instance 只持独立 SeedTTS (每实例独立 feed queue).speak 只接收文字, 不关心是谁,
也不关心音频怎么送到前端 (那是 player 的事).
"""
from typing import Any, Dict

from routine import Routine, RoutineSource, Modules
from routine.logger import setup_logger

from .tts import create_tts, get_player

# 模块树常量 (kernel module tree 的 core/mouth): 占用后嘴巴互斥, 多 speak 串行.
_MODULE_MOUTH = 'mouth'

_log = setup_logger('speak')


class Speak(Routine):
    """TTS 文本转语音: 收 body -> feed SeedTTS -> player 推 binary PCM -> done.

    on_message 收 {text,id} 按 id reorder 后 feed 给 tts;
    {_eof,id} 是流终结信号, 调 tts.finish() 让音频流自然结束.
    run 启动 player 播放 tts 音频流, 阻塞到推送完毕.
    """

    meta = {'description': 'TTS 文本转语音(Seed WS 双向流)', 'hidden': True}

    def __init__(self) -> None:
        super().__init__()
        self._next_id: int = 0
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._tts = create_tts()
        self._player = get_player()

    async def on_created(self, rid: str, kwargs: Dict[str, Any]):
        """占用 mouth 模块: 嘴巴同时只能做一件事, 多个 speak 串行 (一个播完释放, 下一个才 start)."""
        return Modules([_MODULE_MOUTH])

    async def on_message(self, source: RoutineSource, data: Any) -> None:
        """收 body chunk(带 id): 按 id reorder 后 feed 给 tts.

        跟原逐字版同款 reorder 逻辑: 多条 chunk 可能乱序到达, 靠 data['id'] 排序.
        eof 也带 id, 顺序到了才调 finish(避免 eof 抢先丢掉未到的 chunk).
        """
        if not isinstance(data, dict):
            return
        seq = int(data.get('id', 0))
        self._pending[seq] = data
        while self._next_id in self._pending:
            m = self._pending.pop(self._next_id)
            self._next_id += 1
            if m.get('_eof'):
                await self._tts.finish()
                continue
            text = m.get('text', '')
            if text:
                await self._tts.feed(text)

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        """启动 player 播放 tts 音频流, 阻塞到推送完毕."""
        _log.info('speak %s: tts start', self.id)
        await self._player.play(self._tts.audio_chunks())
        _log.info('speak %s done', self.id)
        return {}

    async def stop(self) -> None:
        """打断时立即停 player (set abort → play 退出 → 关 stream 停音频)."""
        self._player.stop()
