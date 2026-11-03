"""Say —— 说话: 流式接收 body, 占轨(嘴也是身体的一部分), 引擎三点契约.

说话也是一个 routine, 同样走 lane 队列: 开标签时轨即 create 本实例(body 增量
直投 —— created 态即可收消息, 边到边收), start 由轨按到达序调度,
闭标签 = eof 收流. 同轨连续 say 天然串行(FIFO); 一张嘴的物理约束
(跨轨并行也不重叠出声)由模块级播放锁保证.

引擎接入(对齐公共 tts 包三件套): ``feed``(delta 即喂, 边到边合成) /
``finish``(eof 收流) / ``audio_chunks``(音频流, 播放器消费) ——
engine_factory/player_factory 类属性注入, **缺省按部署事实选**: 有
``SEED_TTS_API_KEY`` 就真声(SeedTTS 流式合成 + 本地扬声器), 否则
silent(零依赖, 回执确定性). 一个名字一个 routine ---- "有没有 key"是
部署差异, 不是第二种身份, 别拿两个同名类互相覆盖. 播放与打断
竞速: eof → 播完才算说完; abort → 截声丢缓冲.

body 的两条消息(``{'text'}`` 增量 / ``{'eof'}`` 收尾)由 ``CtmlRoutine`` 统一
收流, 本类只回答"每段怎么办": ``on_body_chunk`` 边到边喂引擎,
``on_body_done`` 收流(播完才算说完, 等播放留到 run 里). say 不需要知道
"delta"这种专用名词 —— body 字节流原样到达, 它自己把它当文本.

打断不走消息面 —— 走生命周期: channel 对在跑实例 ``stop``(优雅
停止), 本类 stop hook 截声收流, run 返回截断回执照常报账(原版
speech.clear 截声 + stopped_message 同构); 未 start 的被 destroy,
无账(没说过就没痕).

run 返回执行回执: 说完就只有 details(played/words, content 缺省 'ok'),
被打断才有 content("已说几个字 + 末尾几个字"), details 里多一个 stopped
—— 说的全文 assistant 记忆已有一份(logos), echoes 只报身体干得怎么样,
不复读内容(原版 played_message/stopped_message 同构); 结果经轨的报账
统一回灌(与原子动作同一回收路径).
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine.logger import setup_logger

from ..ctml import CtmlRoutine

__all__ = ['Say']

_log = setup_logger('moss.say')

# 一张嘴: 模块级播放锁 —— 跨轨 say 并行调度, 但出声不重叠
_mouth: asyncio.Lock = asyncio.Lock()


class SayInput(BaseModel):
    """say 入参(create 即标签属性透传; body 走消息流, 不在入参)."""

    voice: str = Field(default='', description='音色(留引擎消费)')


class _SilentEngine:
    """silent 引擎: 零音频; audio_chunks 挂到 finish 才结束
    (say 存活期 = 文本段全长, 对齐真实引擎"边喂边播"的时序)."""

    def __init__(self) -> None:
        self._fin = asyncio.Event()

    async def feed(self, text: str) -> None:
        pass

    async def finish(self) -> None:
        self._fin.set()

    async def audio_chunks(self, additions: str = ''):
        await self._fin.wait()
        return
        yield   # 使其成为空 async generator(挂起后即流尽)


class _SilentPlayer:
    """silent 播放器: 消费即弃; 截声由 run 侧 cancel 兑现."""

    def stop(self) -> None:
        pass

    async def play(self, chunks) -> None:
        async for _ in chunks:
            pass


def _real_engine_factory():
    """真实引擎: 公共 tts 包 SeedTTS(懒 import, 需 SEED_TTS_API_KEY).
    注意三级相对( ``...tts``): tts 是 ``user/`` 下的兄弟包, 两个点只到
    mossv2(挪进 commands/ 子包后层级要跟对, 实跑踩过)."""
    from ...tts import create_tts
    return create_tts()


def _real_player_factory():
    """真实播放器: 公共 tts 包 LocalSpeaker(懒 import, sounddevice)."""
    from ...tts import get_player
    return get_player()


def _default_engine():
    """缺省引擎: 有 key 用真声, 没有就 silent(不装声卡的机器/单测都能跑)."""
    return _real_engine_factory() if os.environ.get('SEED_TTS_API_KEY') \
        else _SilentEngine()


def _default_player():
    """缺省播放器, 与引擎同一条判据(出声的两端只能成对选)."""
    return _real_player_factory() if os.environ.get('SEED_TTS_API_KEY') \
        else _SilentPlayer()


class Say(CtmlRoutine):
    """说话: 流式接收 body, 开标签即收流, 发声等轨调度."""

    name = 'say'

    # 引擎/播放器工厂(测试注入替身; 缺省按 SEED_TTS_API_KEY 选真声或 silent)
    engine_factory = staticmethod(_default_engine)
    player_factory = staticmethod(_default_player)

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '说话: body = 要说的话(发出即开始收流, 发声等轨调度).'
        ),
        # 收 body(默认是不收, 见 ctml.CtmlRoutine): 要说的话写在标签中间,
        # 不是参数.
        'body': True,
        # 备注(跟着能力走, 见 prompt/capability._item): 只在 say 进词表时
        # 出现. 讲的都是 say 自己的事 —— body 的收流时机、闭标签那个高频错误.
        'example': (
            'body 边到边收: 发出就开始说, 不必等你把这句写完.\n'
            '闭标签只写裸名字, 不带轨道前缀(带前缀是最常见的解析错误).'
        ),
        'input_schema': SayInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        await super().on_created(rid, kwargs)
        SayInput.model_validate(kwargs or {})
        self._engine = type(self).engine_factory()
        self._player = type(self).player_factory()

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        async with _mouth:   # 一张嘴: 播放互斥, 先到先说
            t0 = time.monotonic()
            # 出声闸门广播: 耳朵(半双工)据此丢麦克风, 防自听回灌
            await self._tell_output(True)
            play = asyncio.create_task(
                self._player.play(self._engine.audio_chunks()))
            try:
                await self._body_ready.wait()   # body 终结(eof 或 打断)
                if not self._aborted:
                    # eof: 播完才算说完(计时含播放全程). 回执只报执行
                    # 事实(时长/字数) —— 说的全文 assistant 记忆已有
                    # 一份(logos), echoes 不复读内容(原版 played_message
                    # 同构)
                    await play
                    return {'details': {
                        'played': f'{time.monotonic() - t0:.1f}s',
                        'words': len(self.body_text)}}
                # abort: 截声, 丢缓冲. 进度回执(秒数+已说字数+尾部) ——
                # 模型据此决定续说/改说(原版 stopped_message 同构)
                self._player.stop()
                text = self.body_text
                return {'content': (f'被截断: 已说 {len(text)} 字, '
                                    f'末尾「{text[-8:]}」'),
                        'details': {'played': f'{time.monotonic() - t0:.1f}s',
                                    'words': len(text),
                                    'stopped': text[-8:]}}
            finally:
                # 关闸门**必须**落到线上: 本任务被 cancel(force stop)时,
                # 直接 await 会立刻抛 CancelledError 把 publish 跳过 ——
                # 耳朵就永久静音(闸门只有 say 能放下). shield 让内层
                # 任务在取消后继续跑完.
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await asyncio.shield(self._tell_output(False))
                if not play.done():
                    play.cancel()
                    await asyncio.gather(play, return_exceptions=True)

    async def _tell_output(self, speaking: bool) -> None:
        """出声闸门广播(``pubsub 'output'``): 半双工耳朵据此丢麦克风.

        发不出去只记日志 —— 观测面故障不该拦住说话本身."""
        try:
            await self.publish('output', {
                'kind': 'speech',
                'state': 'start' if speaking else 'stop'})
        except Exception:
            _log.debug('output publish failed', exc_info=True)

    async def on_body_chunk(self, text: str) -> None:
        """body 到一段就喂引擎(边收边合成) —— 与标签中间那段同一条消息面."""
        await self._engine.feed(text)

    async def on_body_done(self) -> None:
        """eof: 引擎收流; 播完才算说完(run 里等播放)."""
        await self._engine.finish()

    async def stop(self) -> None:
        # 优雅打断(channel stop, 原版 speech.clear 截声): 截声 +
        # 收流 —— run 返回截断回执(stopped 尾部), 照常报账
        self.abort()
        self._player.stop()
