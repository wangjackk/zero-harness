"""耳朵的器官: 麦克风采集 + 一条常驻识别流(豆包 SAUC 大模型流式识别).

数据流::

    sounddevice InputStream(16k / 16bit / 单声道)
        → 有界队列(满了丢最旧 —— 延迟优先于完整, ASR 不等积压)
        → 发送泵(seq 递增, gzip 组包)
        服务端 VAD 分句 → 接收泵 → EarEvent

事件面(``events()`` 是无限异步流, 断线自己退避重连):

- ``utterance``: 一个**定稿分句**(服务端 VAD 判停 + 二遍识别确认
  ``definite``) —— 这是"用户说了一句话", 由 routine 申报成拍;
- ``partial``: 流式中间态(还在说), 只做状态/UI, 不进脑;
- ``state``: 连接状态变化(重连/错误), 只打日志。

半双工: ``mute()`` 期间麦克风块**直接丢弃** —— 本地扬声器和本地麦克风
之间没有 AEC, 不丢就是自听回灌(TTS 被自己听见, 回声变成新的一拍)。

静音有两个主人, 期限不同 —— ``watchdog``: 闸门(TTS 出声期)是**临时**静音,
stop 帧丢了会永久失聪, 故 120s 强制放麦兜底; ptt 的"没按"是**故意**长静音,
不设期限(给它兜底就成了"两分钟没人按, 麦自己开了").

**建连不等于上传, 麦克风常开也不等于一直上传**(原版 ``silence_gate_factory``
同构): 只要没有音频可传, 这条流就该收 —— 四条判据走同一条路:

- ptt 没按(``_muted`` + ``watchdog=False``);
- 闸门静音(TTS 出声期, 麦克风在丢帧; 原版此时这一段早已 commit 收流);
- always 里安静到 ``_SILENCE_CLOSE_S``(3s, 长于服务端 VAD 800ms 与礼仪判停 2s);
- **判停**(``commit()``: 礼仪的 silence/keywords/classifier 或 ptt 松手的
  ``send_now``) —— 这条最省: 段边界一旦落定, 文本就固定了, 不必等静音超时,
  发个负序号尾包让服务端收尾即可(原版 ``Recognizer.commit()`` 同构)。

静音块**不入队**(``_GATE_DB``): 常开麦的空闲期只有一次 rms 计算, 线上零
字节、也不建连接; 而**一段之内**(开口后到收流前)静音照传 —— 服务端要靠那
段尾巴判句尾(``end_window_size``), 剪了就把最后一句留在半截。

**开门比入队更严**(``_OPEN_DB`` + 连续 ``_OPEN_BLOCKS`` 块): 入队门只回答"这
块要不要传", 拿它当开门判据会让环境底噪(风扇/空调/键鼠, 常在 -50 上下)反复
建流又收流 —— 每次赔一次握手和一段静音上传, 日志上就是 endless 的
``stream up`` / ``stream down``。开门只认"像人声且连着两下", 底噪永远开不了门;
那 400ms 候选音频攒在 ``_pending`` 里, 开门时一起补上, 第一个字不会丢。

判停之后这条流吐的东西**一律不进策略层**: 那段文本已经交付过, 再喂一次
会开新段、把同一句念两遍(``_session`` 里 ``_commit_sent_at`` 那道闸)。

代价: 每段一句一条流。按下/开口那一刻的握手 200~400ms, 音频在队列里等着
一个块都不丢; 跨句上下文随流复位(原版每 turn 一条流, 本来就不靠它)。

已知边界:
- **采集粒度 40ms, 上传粒度 200ms**(``_ACCUM_MS`` / ``_BLOCK_MS``): 松手
  那一刻正在采的音频必须送出, 否则每句话结尾固定丢一段 —— 这就是"松开前
  几个字识别不到"的产地。横跨静音起点的那一块**立刻整体交出**;
- 换流只在句界发生(服务端按流累计上下文, ``max_utterances`` /
  ``max_session_s`` 到了就换一条, 复位上下文, 不切断句中);
- 设备不支持 16k 直采时退到原生率, 回调里 numpy 降采样(整数比用盒式
  均值兼作抗混叠, 非整数比线性插值) —— 语音够用, 不引 scipy。
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional
from uuid import uuid4

import numpy as np
import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from routine.logger import setup_logger

from .sauc import (
    DEFAULT_RESOURCE_ID,
    DEFAULT_URL,
    build_audio,
    build_init,
    parse_response,
)

__all__ = ['EarEvent', 'Ear']

_log = setup_logger('moss.asr')

_TARGET_RATE = 16000
_BLOCK_MS = 200          # 大模型流式 200ms 一包性能最优(官方 + volc.rs 同参)
#: 采集粒度(ms): 比上传粒度小得多。松手时"正在采"的那一块会被静音挡住,
#: 粒度就是这句话结尾的损失上限 —— 200ms 一丢就是"最后几个字听不到"。
_ACCUM_MS = 40
_QUEUE_MAX = 64          # 6.4s 积压上限, 超了丢最旧
_RECV_POLL_S = 1.0       # 收包轮询: 周期性检查关闭/轮换, 不无限阻塞
_KEEPALIVE_S = _BLOCK_MS / 1000.0   # 段内补静音的节奏(= 一包时长)
#: 收流前的空窗: 不发字节, 但流先留着 —— 服务端对最后 200ms 音频的回包
#: 还在路上, 要赶在 ``Asr._flush`` 的交付窗口里到; 等不到再收流.
_TAIL_S = 0.6
#: 判停发尾包后等回尾包的耐心(> 服务端出尾包的正常耗时; 到点直接收流).
_COMMIT_GRACE_S = 2.0
#: 能量门(dbFS): 低于它算"没人说话", 不入队 —— 原版
#: ``silence_gate_factory(threshold_db=-50.0)`` 同一个判据同一个值。
_GATE_DB = -50.0
#: **开门门**(dbFS): 只有像人声的块才值得为它建一条会话并给它续命.
#:
#: ``_GATE_DB`` 只回答"要不要入队", 拿它当开门判据就会抖: 环境底噪(风扇/
#: 空调/键鼠)常常在 -50 上下, 于是"过一块→建流→安静 3s→收流→再过一块"
#: 无限循环, 每次都赔一次握手 + 一段静音上传。开门必须比入队门高一截,
#: 让"线上有没有音频"只由"人声"决定。
_OPEN_DB = -42.0
#: 连续几块过开门门才认(2 块 = 400ms): 单块的"咔哒"(键鼠/碰麦/关门)不算
#: 有人在说. 这 400ms 不会丢 —— 先攒在 ``_pending``, 开门时一起补进队列。
_OPEN_BLOCKS = 2
#: 一段之内安静这么久就收流. 必须长于服务端 VAD(800ms)与礼仪判停(silence
#: 最宽 2.0s): 短了会把还在判停的那一段剪断, 后半句另起一段、前缀全丢。
_SILENCE_CLOSE_S = 3.0
#: 闸门 mute 的兜底: 连续静音超此长度强制放麦(防 stop 帧丢了导致永久失聪)。
#: 只管**闸门**(TTS 半双工) —— ptt 的不按是**故意长静音**, 不设期限.
_MUTE_MAX_S = 120.0
_SILENCE = np.zeros(int(_TARGET_RATE * _BLOCK_MS / 1000), dtype=np.int16)
#: 队列里的**判停哨兵**: 不是一个音频块, 而是"立刻发尾包"这条指令本身。
#:
#: 发送泵睡在 ``q.get()`` 里, 只有队列有东西才会醒 —— 事件(``_commit``)单独
#: 是叫不醒它的, 那就只能靠超时轮询回头看事件(尾包最迟晚一个轮询周期才发)。
#: 把"要收尾了"也做成队列里的一个元素, 唤醒和数据走同一条路: 零轮询、零新
#: 事件、也不碰 ``Queue.get()`` 被取消时的元素归属(``asyncio.wait`` 那套要)。
_FINAL = object()


def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    """麦克风原生率 → 16k(纯 numpy: 整数比盒式均值, 否则线性插值)。"""
    if src == dst or x.size == 0:
        return x
    ratio = src / dst
    n = int(round(ratio))
    if n >= 2 and abs(ratio - n) < 1e-6:
        usable = (x.size // n) * n
        if not usable:
            return np.zeros(0, dtype=np.int16)
        # 盒式均值 = 低通 + 抽取, 兼作抗混叠
        return x[:usable].reshape(-1, n).astype(np.float32).mean(
            axis=1).astype(np.int16)
    if x.size < 2:
        return np.zeros(0, dtype=np.int16)
    idx = np.arange(0.0, x.size - 1, ratio)
    lo = np.floor(idx).astype(np.int64)
    frac = (idx - lo).astype(np.float32)
    return (x[lo] * (1 - frac) + x[lo + 1] * frac).astype(np.int16)


def _rms_db(x: np.ndarray) -> float:
    """一块音频的能量(dbFS): 20*log10(rms / 32768). 全零/空 → -120."""
    if x.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float32))))
    if rms <= 0.0:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


def _status_of(exc: BaseException) -> int:
    """握手被拒时的 HTTP 状态码(非握手错误返回 0)."""
    if isinstance(exc, InvalidStatus):
        return int(getattr(exc.response, 'status_code', 0) or 0)
    return 0


def _norm_device(device) -> Optional[int | str]:
    """设备入参: 空 → 系统默认; 纯数字串 → 索引; 其余按名字。"""
    if device is None or device == '':
        return None
    if isinstance(device, int):
        return device
    text = str(device).strip()
    return int(text) if text.isdigit() else text


@dataclass
class EarEvent:
    """耳朵吐出的一件事(按 kind 分派, text 承载内容)。

    ``words`` = 这句的词级明细(``[{'text': ..., 'conf': ...}]``), 只有
    ``utterance`` 有: 引擎开了 ``show_utterances`` 就给, 没给就是空表.
    **原样带着走, 不在这里筛** —— 阈值("多低才算可疑")是礼仪的旋钮
    (``deliver.low_confidence``), 器官不认识礼仪.
    """

    kind: str            # 'utterance' | 'partial' | 'state'
    text: str = ''
    detail: str = ''     # state: 人读的原因
    words: list = field(default_factory=list)   # 词级明细(utterance)

    def __str__(self) -> str:
        return f'{self.kind}:{self.text or self.detail}'


def _words_of(utterance: dict) -> list:
    """引擎的分句 → 词级明细 ``[{'text', 'conf'}]``.

    ``conf`` 两种键名都认(火山流式是 ``conf``, 有些网关回 ``confidence``);
    缺失/坏值 → ``None``, 表示"引擎没报" —— 与"报了低分"是两件事, 不能
    混(``None`` 不许当低置信参与筛选).
    """
    out: list = []
    for w in utterance.get('words') or []:
        if not isinstance(w, dict):
            continue
        conf = w.get('conf', w.get('confidence'))
        try:
            conf = None if conf is None else float(conf)
        except (TypeError, ValueError):
            conf = None
        out.append({'text': str(w.get('text') or ''), 'conf': conf})
    return out


class _Mic:
    """sounddevice 采集: 回调线程只做拷贝/降采样, 入队交给事件循环。"""

    def __init__(self, device, on_block) -> None:
        self._device = _norm_device(device)
        self._on_block = on_block
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stream = None
        self._src_rate = _TARGET_RATE
        self._mute_until = 0.0
        self._mute_from = 0.0
        self._watchdog = True      # 这次静音要不要 120s 兜底(见 set_mute)
        # 攒块: 采集粒度和上传粒度分开 —— ``_cb`` 每 ``_ACCUM_MS`` 收一次,
        # 凑满 ``_BLOCK_MS`` 才交给事件循环。粒度粗是"松手前几个字听不到"
        # 的产地: 整块 200ms 一丢, 松手时正在采的那一块全没了。
        self._pending: list = []
        self._block_n = int(_TARGET_RATE * _BLOCK_MS / 1000)

    def open(self) -> None:
        import sounddevice as sd
        self._loop = asyncio.get_running_loop()
        try:
            extra = sd.WasapiSettings(auto_convert=True)  # 让系统替我们转率
        except Exception:
            extra = None
        try:
            self._start(sd, _TARGET_RATE, extra)
            _log.info('mic up: %d Hz (device=%r)', _TARGET_RATE, self._device)
        except Exception as exc:
            native = int(sd.query_devices(self._device, 'input')
                         .get('default_samplerate') or 48000)
            _log.info('mic %d Hz refused (%r), native %d Hz + 降采样',
                      _TARGET_RATE, exc, native)
            self._start(sd, native, None)
            _log.info('mic up: %d Hz (device=%r)', native, self._device)

    def _start(self, sd, rate: int, extra) -> None:
        self._src_rate = rate
        step = max(1, int(rate * _ACCUM_MS / 1000))
        kwargs = dict(samplerate=rate, channels=1, dtype='int16',
                      blocksize=step, device=self._device,
                      callback=self._cb, extra_settings=extra)
        try:
            # 低延迟: 少一层驱动缓冲, "松手时还在路上"的音频更短
            self._stream = sd.InputStream(latency='low', **kwargs)
        except Exception:
            self._stream = sd.InputStream(**kwargs)
        self._stream.start()

    def _cb(self, indata, frames, time_info, status) -> None:
        # 音频线程: 只允许拷贝与 O(n) 运算, 不许 await/加锁
        if status:
            _log.debug('mic status: %s', status)
        x = np.asarray(indata).reshape(-1)
        if self._src_rate != _TARGET_RATE:
            x = _resample(x, self._src_rate, _TARGET_RATE)
        self._feed(x, time.monotonic())

    def _feed(self, x: np.ndarray, now: float) -> None:
        """收一小块(采集粒度)并按上传粒度交出: 静音**起点所在的那一块**不许丢.

        旧实现拿 200ms 当采集粒度: 松手一刻正在采的那一整块被 ``return``
        掉 —— 每句话结尾固定丢最多 200ms, 就是"松开前几个字识别不到"。
        现在 40ms 一块, 且横跨静音起点的那一块**立刻整体送出**(不等凑满),
        边界损失只剩一个采集粒度。
        """
        if not x.size:
            return
        if now < self._mute_until:
            started = now - x.size / _TARGET_RATE
            edge = self._mute_from + _ACCUM_MS / 1000.0
            if not (self._mute_from and started <= edge):
                # 兜底: 闸门"start" 丢了(说话方被取消/异常收尾)会永久静音,
                # 耳朵就瞎了 —— 超长静音强制放麦, 宁可漏一点自听.
                # 只对闸门静音生效: ptt 的不按是故意长静音, 不按就是不按.
                if (self._watchdog and self._mute_from
                        and now - self._mute_from > _MUTE_MAX_S):
                    _log.warning(
                        'mic muted %.0fs, 强制放麦(疑似丢失 stop 帧)',
                        now - self._mute_from)
                    self._mute_until = 0.0
                    self._mute_from = 0.0
                else:
                    return               # 半双工: 说话期丢弃
        self._pending.append(x.copy())
        if (sum(b.size for b in self._pending) < self._block_n
                and now >= self._mute_until):
            return                       # 没凑满一整包: 等下一小块(静音边上不等)
        block = np.concatenate(self._pending)
        self._pending = []
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._on_block, block)

    def set_mute(self, on: bool, hangover: float,
                 watchdog: bool = True) -> None:
        """关/开麦克风。``watchdog`` = 这次静音要不要 120s 兜底。

        兜底是给**闸门**的(丢失 stop 帧会永久失聪); ptt 的"没按"是故意
        长静音 —— 给它兜底就成了"两分钟没人按, 自己把麦打开", 既是冤案
        也违背按住才听。
        """
        self._mute_until = (float('inf') if on
                            else time.monotonic() + max(0.0, hangover))
        self._mute_from = time.monotonic() if on else 0.0
        self._watchdog = bool(watchdog)

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()


class Ear:
    """一只耳朵: 一条 WS 识别流(断了重连), 麦克风常开。"""

    def __init__(self, *, api_key: str, resource_id: str = DEFAULT_RESOURCE_ID,
                 url: str = DEFAULT_URL, device=None,
                 app_key: str = '', access_key: str = '',
                 params: Optional[dict] = None,
                 max_utterances: int = 50, max_session_s: float = 600.0,
                 backoff_initial: float = 0.5,
                 backoff_cap: float = 8.0) -> None:
        self._api_key = api_key
        self._resource_id = resource_id
        self._url = url
        self._device = device
        # 旧版控制台鉴权(可选): appid + access token, 二者齐才用它
        self._app_key = app_key
        self._access_key = access_key
        self._params = params
        self._max_utterances = max_utterances
        self._max_session_s = max_session_s
        self._backoff_initial = backoff_initial
        self._backoff_cap = backoff_cap

        self._q: Optional[asyncio.Queue] = None
        self._mic: Optional[_Mic] = None
        self._ws = None
        self._seq = 1
        self._closed = False
        # ptt 松开 → 收流待命; 按下 → 叫醒(建流). 见 _wait_awake
        self._awake: Optional[asyncio.Event] = None
        # 判停信号: set 之后发送泵发尾包(负序号), 这条流到此为止
        self._commit: Optional[asyncio.Event] = None
        # 尾包已发(>0 的时刻): 之后这条流吐的文本不再进策略层
        self._commit_sent_at: float = 0.0
        # 闸门期望态: mute 可能早于麦克风建立(装配期就切 ptt), 建好后补上
        self._muted: bool = False
        self._mute_hangover: float = 0.0
        self._mute_watchdog: bool = True
        # 能量门状态: 最近一次"有人说话"的时刻 / 本段是否已开口(段内不再拦)
        self._last_sound: float = 0.0
        self._streaming: bool = False
        # 开门判据(见 _OPEN_DB): 连续过门块数 / 开门时那一块的 dbFS / 最近一块
        self._open_run: int = 0
        self._open_db: float = 0.0
        self._last_db: float = -120.0
        #: 最近一块**像人声**的 dBFS(静音块不更新). 听侧要的是"这段说话多响",
        #: 不是"此刻多响" —— 判停之后 ``_last_db`` 已经掉到静音, 拿它当读数
        #: 会把每一段都报成 -60 上下.
        self._speech_db: Optional[float] = None
        # 开门前攒的候选开头(<= _OPEN_BLOCKS 块): 攒够一起入队, 不丢第一个字
        self._pending: list = []
        self.stats = {'sessions': 0, 'blocks': 0, 'gated': 0, 'silence': 0,
                      'commits': 0, 'utterances': 0, 'partials': 0,
                      'duds': 0, 'errors': 0}

    # ── 对外 ──────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[EarEvent]:
        """无限事件流: 采集 → 识别 → 定稿分句; 断线退避重连, 直到 close。"""
        if not self._api_key and not (self._app_key and self._access_key):
            yield EarEvent('state', detail='no credentials (SEED_API_KEY)')
            return
        self._q = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._awake = asyncio.Event()
        self._commit = asyncio.Event()
        self._mic = _Mic(self._device, self._push)
        self._mic.open()
        # 装配期设过的闸门态(如 ptt 默认闭嘴)在麦克风建好后生效
        if self._muted:
            self._mic.set_mute(True, 0.0, self._mute_watchdog)
        if self._dormant:
            _log.info('asr 待命: 无音频可传, 不建识别流(建连≠上传)')
        backoff = self._backoff_initial
        auth_fails = 0
        try:
            while not self._closed:
                # ptt 没按: 不建流(线上零字节), 在这里等按下
                await self._wait_awake()
                if self._closed:
                    break
                try:
                    async for ev in self._session():
                        yield ev
                    backoff = self._backoff_initial
                    auth_fails = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._closed:
                        break
                    self.stats['errors'] += 1
                    status = _status_of(exc)
                    if status in (401, 403):
                        # 鉴权失败重试无意义(退避重连只会刷日志): 三次即停
                        auth_fails += 1
                        detail = (f'auth rejected ({status}): 检查 API Key '
                                  '与豆包流式识别资源是否开通')
                        _log.error('asr %s', detail)
                        yield EarEvent('state', detail=detail)
                        if auth_fails >= 3:
                            _log.error('asr 认证连续失败 %d 次, 耳朵停机'
                                       '(改好凭据后重启进程)', auth_fails)
                            return
                    else:
                        _log.warning('asr session died: %r', exc)
                        yield EarEvent('state', detail=f'reconnect: {exc}')
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self._backoff_cap)
        finally:
            await self.close()

    @property
    def _dormant(self) -> bool:
        """线上此刻不该有音频 —— 该收流待命了.

        两条判据:
        - 麦克风在丢帧(ptt 没按 / 闸门静音): 根本不会有块;
        - 麦克风开着(always)但已经安静 ``_SILENCE_CLOSE_S``: 段已判停, 再传
          就只是把静音按识别时长交上去。这里的"安静"按 ``_OPEN_DB`` 算 ——
          底噪再响也不算有人说话, 它既开不了门, 也别想给已有的流续命。

        **麦克风常开不等于一直上传** —— 常开的代价只是每 200ms 一次 rms。
        """
        if self._muted:
            return True
        return time.monotonic() - self._last_sound > _SILENCE_CLOSE_S

    def uploading(self) -> bool:
        """线上现在是否该有音频(空闲/未按时 False; 诊断面用)。"""
        return not self._dormant

    def level_db(self) -> Optional[float]:
        """最近一块像人声的 dBFS —— **听侧折响度用的探针**(原版
        ``_on_audio_level`` 的原料)。

        听侧(controller)在收到文本事件时采一次样: 那一刻人一定在说话,
        所以采到的就是"这句话多响"。段内的采样求均值 = 这一段的平均响度。
        ``None`` = 还没听到过声音 —— 与"很安静"是两件事, 不许当 0 用。
        """
        return self._speech_db

    def commit(self) -> None:
        """判停: 让发送泵发尾包(负序号), 这条流到此结束.

        段边界是**策略层**的权威(礼仪的 silence/keywords/classifier, 或 ptt
        松手时 ``send_now``); 它一落定, 这一段的文本就固定了, 再传也没有
        字会进交付。所以不必等静音超时(3s)也不必等尾巴空窗(0.6s) —— 发个
        负序号让服务端把尾包吐出来, 流随即收掉。原版 ``Recognizer.commit()``
        就是这一手: "让 send loop 发 NEG, 拿尾包后切段开下一 turn"。

        同步、可重入、无返回值: 事件记状态, **哨兵负责叫醒** —— ``_commit``
        是判停回调(witness)里调的, 那里不能 await, 所以真正的发包在发送泵里。
        光置事件叫不醒睡在 ``q.get()`` 里的发送泵(它只看队列), 于是顺手往
        队列塞一个 ``_FINAL``: 发送泵当拍就醒, 不必等超时轮询回来看。
        """
        if self._commit is not None:
            self._commit.set()
        q = self._q
        if q is None:
            return
        try:
            q.put_nowait(_FINAL)
        except asyncio.QueueFull:
            # 判停后队列里剩的块本来就不再传: 腾一格给哨兵
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(_FINAL)

    def _end_segment(self) -> None:
        """段结束(判停 / 静音收流 / 流断): 回到"等下一次开口"的起点.

        清 ``_last_sound`` 是必需的: 判停时距上次出声可能只有 1.5s(礼仪判停
        最短那一档), 不清的话 ``_dormant`` 还是 False —— ``events()`` 会立刻
        重连一条新流、接着把静谧的 1.5s 当静音包传上去, 白建一条流白传一段
        静音。清掉之后判据只剩一条: **有新声音才建流**。
        """
        self._streaming = False
        self._last_sound = 0.0
        self._open_run = 0
        self._pending.clear()

    def mute(self, on: bool, hangover: float = 0.35,
             watchdog: bool = True) -> None:
        """闸门: 丢麦克风块(TTS 半双工 / ptt 松开都走这里).

        期望态记在耳朵上: 麦克风还没建立时也能"先关后开", 建好即生效.
        ``watchdog=False`` = 这次静音是"故意的"(ptt 没按), 不设期限 —— 兜底
        会让"两分钟没人按, 麦自己开了".

        两种静音对占线的要求一样: 麦克风在丢帧就没有音频可传, 发送泵排空
        即收流(TTS 出声期也不再补静音保活, 原版那时这一段早就 commit 了).
        """
        self._muted = bool(on)
        self._mute_hangover = max(0.0, float(hangover))
        self._mute_watchdog = bool(watchdog)
        if on:
            # 关麦就是"这段不听了": 候选开头作废, 别在放开时把它们
            # 当新一段的脑袋传上去(那是关麦前的音频)。
            self._pending.clear()
            self._open_run = 0
        if self._mic is not None:
            self._mic.set_mute(self._muted, self._mute_hangover,
                               self._mute_watchdog)
        if self._awake is not None and not self._dormant:
            self._awake.set()      # 放开且还有新鲜音频: 叫醒 events() 去建流

    async def close(self) -> None:
        self._closed = True
        if self._awake is not None:
            self._awake.set()      # 别让 events() 卡在待命里等不到关停
        if self._mic is not None:
            self._mic.close()
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    # ── 内部 ──────────────────────────────────────────────────────

    async def _wait_awake(self) -> None:
        """ptt 待命: 等到"该上传了"才返回(always / 已按下 = 立刻返回)。

        先 clear 再复查: ``mute(False)`` 可能正好落在 clear 与 wait 之间,
        那个 set 会被自己抹掉 —— 复查 ``_dormant`` 把它捡回来。
        """
        awake = self._awake
        while not self._closed and self._dormant and awake is not None:
            awake.clear()
            if not self._dormant:
                return
            await awake.wait()

    def _push(self, block: np.ndarray) -> None:
        """采集块入队(事件循环内): 队列满就丢最旧, 不阻塞采集.

        两个门, 各管一件事:

        - ``_GATE_DB``(入队门): 没开口时的静音一个字节都不入队(原版
          ``silence_gate_factory`` 同构) —— 否则常开麦的空闲期会把队列灌满
          静音, 一开口先上传 6.4s 垃圾。开口之后(``_streaming``)静音照传:
          服务端要靠那段尾巴判句尾。
        - ``_OPEN_DB``(开门门): 只有像人声的块才**建流并续命**。用它当门是
          为了不做"过一块就建一条会话"的抖动 —— 环境底噪在 ``_GATE_DB`` 上下
          浮动时, 那一块传上去既没有字, 又要赔一次握手(见常量注释)。

        开门要连续 ``_OPEN_BLOCKS`` 块: 单块咔哒声不开流, 但那段音频先攒在
        ``_pending`` 里, 开门时一起补进队列 —— 第一块不会被丢掉。
        """
        q = self._q
        if q is None or self._closed:
            return
        self.stats['blocks'] += 1
        db = _rms_db(block)
        self._last_db = db
        if db >= _GATE_DB and not self._muted:
            self._speech_db = db
        if db >= _OPEN_DB:
            self._open_run += 1
        else:
            self._open_run = 0

        if self._dormant or not self._streaming:
            # 待命: 攒候选开头, 攒够才开门
            if db >= _GATE_DB and not self._muted:
                self._pending.append(block)
                if len(self._pending) > _OPEN_BLOCKS:
                    self._pending.pop(0)
            else:
                self._pending.clear()
            if self._open_run < _OPEN_BLOCKS:
                self.stats['gated'] += 1
                return
            self._streaming = True
            self._last_sound = time.monotonic()
            self._open_db = db
            pending, self._pending = self._pending, []
            for item in pending:
                self._enqueue(item)
            if self._awake is not None:
                self._awake.set()   # 有人开口: 叫醒 events() 去建流
            return

        # 段内: 尾巴照传; 但只有"像人声"的块才给它续命(底噪不许把流吊着)
        if db >= _OPEN_DB:
            self._last_sound = time.monotonic()
        self._enqueue(block)

    def _enqueue(self, block: np.ndarray) -> None:
        """入队(满则丢最旧): 采集侧不许阻塞。"""
        q = self._q
        if q is None:
            return
        try:
            q.put_nowait(block)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                q.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(block)

    async def _session(self) -> AsyncIterator[EarEvent]:
        """一条 WS 会话: init → 发送泵(后台) + 接收泵(本函数)。"""
        headers = {
            'X-Api-Resource-Id': self._resource_id,
            'X-Api-Request-Id': uuid4().hex,
            'X-Api-Connect-Id': uuid4().hex,
            'X-Api-Sequence': '-1',
        }
        # 新版控制台: 一把 X-Api-Key; 旧版: appid + access token(二者齐才用)
        if self._app_key and self._access_key:
            headers['X-Api-App-Key'] = self._app_key
            headers['X-Api-Access-Key'] = self._access_key
        else:
            headers['X-Api-Key'] = self._api_key
        async with websockets.connect(
                self._url, additional_headers=headers, max_size=None,
                ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            self.stats['sessions'] += 1
            await ws.send(build_init(uuid4().hex, params=self._params))
            self._seq = 2          # init 占了 seq=1
            # 上一条流的判停不许传染给这一条(发送泵靠这个事件决定发不发尾包)
            if self._commit is not None:
                self._commit.clear()
            self._commit_sent_at = 0.0
            # 开门那块的 dBFS 一起打出来: "一直在刷 up/down"时, 这行是判据 ——
            # -45 左右 = 环境底噪还在过门; -20 左右 = 真有人在说话(那是正常轮换)。
            _log.info('asr stream up (%.0f dBFS, %s)', self._open_db, self._url)
            sender = asyncio.create_task(self._send(ws))
            emitted = 0
            absorbed = ''          # 流内已定稿分句的拼接(partial 的全量前缀)
            last_partial = ''
            opened = self.stats['partials'] + self.stats['utterances']
            started = time.monotonic()
            try:
                while not self._closed:
                    try:
                        data = await asyncio.wait_for(ws.recv(), _RECV_POLL_S)
                    except asyncio.TimeoutError:
                        if (self._commit_sent_at
                                and time.monotonic() - self._commit_sent_at
                                > _COMMIT_GRACE_S):
                            _log.info('asr commit: 尾包未到, 直接收流')
                            return
                        continue
                    except ConnectionClosed:
                        if self._closed:
                            raise
                        if self._dormant or self._commit_sent_at:
                            # 收流是自己干的(松手/判停/静音): 正常退场, 不是故障
                            # (判 _dormant 而不是 sender.done(): close 与
                            #  recv 报错有先后, 读 done 会撞上竞态)
                            return
                        raise
                    resp = parse_response(data)
                    if resp.code != 0:
                        raise RuntimeError(
                            f'sauc {resp.code}: {resp.error[:200]}')
                    if self._commit_sent_at:
                        # 判停已落定: 这段交付过了, 流里剩下的话不许再进策略层
                        # —— 尾包里的定稿若被当成新段, 同一句会被念两遍。
                        if resp.is_last:
                            return
                        continue
                    definite = [u for u in resp.utterances
                                if u.get('definite')]
                    new = definite[emitted:]
                    emitted = len(definite)
                    for u in new:
                        text = str(u.get('text') or '').strip()
                        if not text:
                            continue
                        absorbed += text
                        self.stats['utterances'] += 1
                        yield EarEvent('utterance', text,
                                       words=_words_of(u))
                    # 定稿帧不再补 partial(已定稿的话不必再"正在说")
                    # 服务端 ``result.text`` 是**整条流**的全量累积(含已
                    # 定稿的历史分句), 而 partial 的语义是"这一段正在说的
                    # 全文". 不裁掉已定稿前缀的话, 上一句会随 partial 混进
                    # 下一段的全文(controller 的 ``_text = text`` 是全替换),
                    # 交付时变成"上一句 + 这一句"的拼接.
                    if not new and resp.text:
                        partial = resp.text
                        if absorbed and partial.startswith(absorbed):
                            partial = partial[len(absorbed):].lstrip()
                        if partial and partial != last_partial:
                            last_partial = partial
                            self.stats['partials'] += 1
                            yield EarEvent('partial', partial)
                    if resp.is_last:
                        return
                    # 轮换只在句界: 服务端上下文随分句累计, 换流即复位
                    if new and (emitted >= self._max_utterances
                                or time.monotonic() - started
                                > self._max_session_s):
                        _log.info('asr stream rotate (%d 分句)', emitted)
                        return
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
                if self._ws is ws:
                    self._ws = None
                # 空转: 这条流一个字符都没吐出来 —— 开门多半是噪声(键鼠/底噪),
                # 不是话. 计数是为了让"到底在建什么流"变成可查的数, 不靠猜。
                if self.stats['partials'] + self.stats['utterances'] == opened:
                    self.stats['duds'] += 1
                    _log.debug('asr 空转: 这条流 %.1fs 没有识别到任何字',
                               time.monotonic() - started)
                # 流没了: 段作废 —— 静音重新被门挡住, 别把尾巴灌进下一条流
                self._end_segment()

    async def _send(self, ws) -> None:
        """发送泵: 段内补静音保时间轴连续; 判停发尾包; 静音/松手收流.

        三条出口, 按优先级:

        1. **判停**(``commit()``): 发一个**负序号**空包(``is_last``) —— 服务端
           回尾包, 这条流就算完了(原版 ``Recognizer.commit()`` 同一手)。队列里
           剩下的块属于已判停的那一段, 不再传;
        2. **待命**(``_dormant``: 松手/闸门静音/安静够久): 一个字节都不发, 空等
           ``_TAIL_S`` 让最后 200ms 音频的回包落地, 再 close。一松手就 close 会
           把最后几个字剪掉;
        3. **段内**(有人说话): 有块发块, 没块补静音 —— 服务端按包推进时间轴,
           断口会被它当成句尾。

        待命期的空窗是**一次等到点**, 不是 200ms 轮询: 判停会往队列里塞哨兵、
        新声音会入队, 两个唤醒源都走 ``q.get()``。
        """
        q = self._q
        commit = self._commit
        assert q is not None
        idle_at = 0.0            # 进待命的时刻(0 = 不在待命)
        while not self._closed:
            if commit is not None and commit.is_set():
                self.stats['commits'] += 1
                self._commit_sent_at = time.monotonic()
                # 哨兵**之前**的块属于已判停的那一段: 不再传, 也别留给下一
                # 条流(跨流共享队列, 留着会被新流当成新段开头再识别一遍)。
                # 哨兵之后的保留: 判停落定那几毫秒里入队的可能已是下一句。
                while True:
                    try:
                        item = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is _FINAL:
                        break
                _log.info('asr commit: 发尾包收流 (seq=%d)', self._seq)
                with contextlib.suppress(Exception):
                    await ws.send(build_audio(b'', self._seq, is_last=True))
                return
            if self._dormant:
                if not idle_at:
                    idle_at = time.monotonic()
                left = _TAIL_S - (time.monotonic() - idle_at)
                try:
                    # 下界留 1ms: timeout<=0 时 wait_for 不看队列直接判超时,
                    # 会把已经躺在队列里的哨兵/新块当成"空窗已到"
                    block = await asyncio.wait_for(q.get(), max(1e-3, left))
                except asyncio.TimeoutError:
                    if not self._muted:
                        why = f'安静 {_SILENCE_CLOSE_S:.0f}s'
                    else:
                        why = '闸门静音' if self._mute_watchdog else 'ptt 松开'
                    _log.info('asr stream down (%s, 不再上传)', why)
                    self._end_segment()   # 段结束: 静音重新被门挡住
                    with contextlib.suppress(Exception):
                        await ws.close()   # 顺带叫醒 recv 退场
                    return
                idle_at = 0.0
            else:
                idle_at = 0.0
                try:
                    block = await asyncio.wait_for(q.get(), _KEEPALIVE_S)
                except asyncio.TimeoutError:
                    if self._dormant:
                        continue    # 这一拍里刚松手: 一个静音包都不补
                    block = _SILENCE
                    self.stats['silence'] += 1
            if block is _FINAL:
                continue            # 判停哨兵: 回循环头发尾包(不是音频)
            try:
                await ws.send(build_audio(block.tobytes(), self._seq))
            except Exception as exc:
                # 发送侧故障不能静默: 不报的话接收侧只会等到超时才复活
                _log.warning('asr send failed: %r', exc)
                raise
            self._seq += 1
