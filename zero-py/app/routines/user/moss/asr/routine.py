"""asr —— 耳朵: 麦克风 → 豆包流式识别 → 礼仪判停 → mindflow 申报入口.

自己实现, 不订阅外部总线: 本 routine 是**完整的听觉器官**, 不是别人
识别结果的下游。分层照旧 —— **感知源是 routine, 入关策略不是**: 器官
(``ear.py``)只做"听"(采集 + 识别 + 定稿分句); 策略(``../listen``)做
"怎么交"(起话/判停/交付); 竞不竞争 / 谁接管 / 怎么处置全在 mindflow.

- 起话(FIRST, 文本首次非空) → ``declare(complete=False)``: 一条占坑包,
  高优打断 + 把注意力钉住(原版 ListenerNucleus onset 同构);
- 判停(礼仪的 silence/keywords/classifier) → commit → 切段 **+ 收识别流**
  (旁观针里调 ``ear.commit()`` 发尾包; 判停之后这条流一个字都不再传);
- 听音模式默认 ``ptt``: 按住按钮才听、松手即交付 —— 边界由手给, 判停层
  让位(``<asr_mode mode="always"/>`` 才回到"麦常开 + 自己猜边界");
- 交付(TAIL) → ``declare(complete=True)``: **与起话同 id**, 走 mindflow
  同 id 吸收 —— "开口 → 交付"读作一轮, 不会为同一句话被叫两次;
- ``observer`` 类礼仪(``deliver.emit=False``)不申报, 只进留存槽,
  模型用 ``req transcript`` 拉读;
- ``partial`` 不单独进脑(只在起话/诊断里用).

断线语义: 识别流断了耳朵自己退避重连(``ear.py``), routine 不退出;
``SEED_API_KEY`` 缺失时耳朵静默待命(打一条日志), 不影响整机起动.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from routine import Routine, request, subscribe
from routine.logger import setup_logger

from ..listen import EtiquetteConfig, ListenerController
from ..mindflow import Priority
from .ear import Ear, EarEvent
from .sauc import DEFAULT_RESOURCE_ID, DEFAULT_URL

__all__ = ['Asr', 'AsrInput']

_log = setup_logger('moss.asr')

# ptt 松手后的交付窗口(秒): 见 Asr._flush。
#: 至少等这么久 —— 在途音频要上传(采集粒度)、服务端要吐最后一包 partial;
#: 固定等到点会切掉最后几个字, 所以还叠了下面两条"还在吐字就继续等"。
_PTT_SETTLE_S = 0.2
#: "最近还在吐字"的判据: 距最后一条 partial 不足它, 就再等一拍(上限见下)。
_PTT_QUIET_S = 0.2
#: 上限: 服务端一直不吐字也不能无限等(交付延迟是用户直接感受)。
#: 必须小于耳朵收流前的空窗 ``ear._TAIL_S=0.6`` —— 松手已关麦, 那条流
#: 0.6s 后自己收掉, 等得比它久就等不到任何东西了。
_PTT_SETTLE_MAX_S = 0.5


def _default_ear(cfg: 'AsrInput') -> Ear:
    """真耳朵: 凭据走环境(SEED_API_KEY, 与 seed-tts 同账号体系)."""
    api_key = (os.environ.get('SEED_ASR_API_KEY')
               or os.environ.get('SEED_API_KEY') or '')
    if not api_key and not (os.environ.get('SEED_ASR_APP_KEY')
                            and os.environ.get('SEED_ASR_ACCESS_KEY')):
        _log.error('SEED_API_KEY 未设置: 耳朵静默(装配不失败)')
    return Ear(api_key=api_key, device=cfg.device,
               # 旧版控制台鉴权(可选): appid + access token
               app_key=os.environ.get('SEED_ASR_APP_KEY') or '',
               access_key=os.environ.get('SEED_ASR_ACCESS_KEY') or '',
               resource_id=os.environ.get('SEED_ASR_RESOURCE_ID')
               or DEFAULT_RESOURCE_ID,
               url=os.environ.get('SEED_ASR_URL') or DEFAULT_URL)


class AsrInput(BaseModel):
    """耳朵入参(装配声明)."""

    source: str = Field(
        default='asr',
        description='申报源名(percepts 按源 keyed; 非 user 源在灵魂历史里'
                    '带 [源] 前缀)')

    priority: Optional[int] = Field(
        default=None,
        description='交付包的仲裁优先级覆盖; 空 = 听礼仪的(always 是 INFO)')

    strength: int = Field(
        default=100, ge=0, le=1000,
        description='交付包初始强度(同级比较用; 0 = 绝不竞争)')

    etiquette: str = Field(
        default='always',
        description='启动礼仪(见 listen/etiquette.py: once/always/aside/'
                    'notify/scribe/observer/keyword_end/scored/murmur/cue/knock)')

    device: Optional[str] = Field(
        default=None,
        description='麦克风(设备索引或名字; 空 = 系统默认输入设备)')

    mute_on_say: bool = Field(
        default=True,
        description='半双工: 自己说话时丢麦克风(本地喇叭+本地麦没有 AEC,'
                    ' 不丢会自听回灌)')

    mode: str = Field(
        default='ptt',
        description='听音模式: ptt = 按住按钮才听、松开即交付(默认; '
                    '按键事件由外部 press/release 送达); always = 麦常开')

    button: str = Field(
        default='f9',
        description='ptt 的按钮名(press/release 的 button 必须对上; '
                    '键盘 routine 按这个名字监听)')


class Asr(Routine):
    """耳朵: 被动常驻, 麦克风 → 豆包流式识别 → mindflow.declare."""

    name = 'asr'
    is_passive = True

    # 耳朵工厂(测试注入替身; 真身走 SEED_API_KEY + 本机麦克风)
    ear_factory = staticmethod(_default_ear)

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '耳朵: 被动常驻, 麦克风采集 + 流式识别, 按礼仪'
            '(onset/stop/deliver 三轴)交付给 mindflow: 开口发占坑包、'
            '说完按判停交付、observer 类只留存可拉读; 中间态不进脑'),
        'input_schema': AsrInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        self._cfg = AsrInput()
        self._ear: Optional[Ear] = None
        self._ctl: Optional[ListenerController] = None
        self._halt: Optional[asyncio.Event] = None
        self._mindflow_id: Optional[str] = None
        self._partial = ''    # 最近一条流式中间态(诊断/UI 面)
        self._partial_at = 0.0  # 最后一条 partial 的时刻(交付窗口靠它判"还在吐字")
        # 听音模式(状态活在耳朵里, 不在控制指令里 —— 指令退出不带走;
        # 装配默认 ptt: 按住说话、松手交付, 边界由手给)
        self._mode: str = self._cfg.mode    # ptt | always
        self._button: str = self._cfg.button   # ptt 按钮名(与 press/release 配对)
        self._ptt_down: bool = False  # 按钮当前是否按住
        self._flushing: bool = False  # 松手后正在交付(门短暂仍开)
        self._declares = 0    # 成功申报次数(诊断)
        self._dropped = 0     # mindflow 未就绪丢弃数(诊断)
        self._bg: set[asyncio.Task] = set()   # 围观广播在途(防 GC 回收)

    async def run(self, kwargs: Dict[str, Any]) -> None:
        self._cfg = AsrInput.model_validate(kwargs or {})
        self._ear = type(self).ear_factory(self._cfg)
        self._ctl = ListenerController(
            self._emit,
            config=EtiquetteConfig(),
            source=self._cfg.source,
            strength=self._cfg.strength,
            priority=self._cfg.priority,
            # 旁观针: 本段全文/定稿 → pubsub moss/heard(交互面流式渲染);
            # 与申报是两条路, 渲染面缺席不影响耳朵干活
            witness=self._witness,
            # 响度探针: 听侧在收到文本的那一刻采一次(那时人一定在说话),
            # 段内求均值 = 这一段多响. 器官只管量, 不认识礼仪也不认识脑子
            level=self._ear.level_db,
        )
        if self._cfg.etiquette and self._cfg.etiquette != 'always':
            self._ctl.set_etiquette(self._cfg.etiquette)
        self._mode = self._cfg.mode
        self._button = self._cfg.button
        self._ptt_down = False
        # ptt 的段边界是"松手": 判停层不许抢这一刀(见 controller.set_manual_boundary)
        self._ctl.set_manual_boundary(self._mode == 'ptt')
        if self._mode == 'ptt':
            # ptt 默认闭嘴: 建麦前也能设, 耳朵自己记期望态.
            # watchdog=False: 没按就是没按, 不按多久都不许自己开麦.
            self._ear.mute(True, 0.0, watchdog=False)
        self._halt = asyncio.Event()
        _log.info('asr up: source=%s etiquette=%s device=%r mute_on_say=%s '
                  'mode=%s button=%r',
                  self._cfg.source, self._ctl.active().name, self._cfg.device,
                  self._cfg.mute_on_say, self._mode, self._button)
        try:
            async for ev in self._ear.events():
                await self._on_event(ev)
            if self._halt is not None and self._halt.is_set():
                # 关停路径: stop() 关了耳朵, 事件流随之结束 —— 正常收摊.
                # 这里必须分开判: 混在下面那条里, 每次 Ctrl+C 都会刷一条指着
                # "上一条 state 日志"(根本不存在)的假 WARNING, 真故障会被淹.
                _log.info('asr 收摊: 耳朵随 stop 关闭')
            else:
                # 耳朵自己收摊(凭据被拒/无 key): routine 不退出, 保持可查
                # (req status 仍能看原因), 等 stop 再走
                _log.warning('asr ear stopped on its own; 待命'
                             '(原因见上一条 state 日志)')
            await self._halt.wait()
        finally:
            if self._ctl is not None:
                await self._ctl.aclose()
            await self._ear.close()

    async def stop(self) -> None:
        if self._halt is not None:
            self._halt.set()
        for t in list(self._bg):      # 围观广播在途的, 别留给 loop 收摊
            t.cancel()
        self._bg.clear()
        if self._ctl is not None:
            await self._ctl.aclose()
        if self._ear is not None:
            await self._ear.close()

    @request('status')
    async def _req_status(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """诊断: 采集/识别计数 + 礼仪快照 + 最近中间态 + 申报目标."""
        stats = dict(self._ear.stats) if self._ear is not None else {}
        listener = self._ctl.snapshot() if self._ctl is not None else {}
        return {'ok': True, 'ear': stats, 'listener': listener,
                'partial': self._partial,
                'mode': self._mode, 'button': self._button,
                'listening': self._listening(),
                'uploading': bool(self._ear and self._ear.uploading()),
                # 最近一块的 dbFS: "为什么老在建流"直接看这个数(-45 上下
                # 是底噪, -20 上下才是人声)
                'db': round(self._ear._last_db, 1) if self._ear else None,
                'declares': self._declares, 'dropped': self._dropped,
                'mindflow': self._mindflow_id}

    # --- 听音模式(always / ptt) ---

    def _listening(self) -> bool:
        """现在麦克风是否该收: always 永远收, ptt 只在按住时收.

        ``_flushing`` = 松手后正在交付的路上: 这段时间采集队列里还有已采到
        的块要过策略层, 门必须开着 —— 否则最后几个字会被自己丢掉.
        """
        return (self._mode == 'always' or self._ptt_down
                or self._flushing)

    @request('mode')
    async def _req_mode(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """查/切听音模式: ``always`` 麦常开 / ``ptt`` 按住按钮才听.

        切到 ptt 立刻闭嘴(等按键); 切回 always 前把没提交的那段交掉 ——
        否则它永远卡在判停循环里, 且切走后下一个 partial 会接在旧段后面.
        """
        d = data or {}
        mode = str(d.get('mode') or '').strip().lower()
        button = str(d.get('button') or '').strip()
        if not mode and not button:
            # 纯查询: 一个字都不改 —— 键盘 routine 每隔一会儿就来问一次,
            # 带副作用会把"按住"状态冲掉、把麦关掉.
            return {'ok': True, 'mode': self._mode, 'button': self._button,
                    'listening': self._listening()}
        if mode and mode not in ('always', 'ptt'):
            return {'ok': False, 'observe': True,
                    'error': f'unknown mode {mode!r} (always|ptt)'}
        if mode == 'always' and self._mode == 'ptt':
            self._flushing = True          # 交付路上门别关(同 release)
            try:
                await self._flush()
            finally:
                self._flushing = False
        if mode:
            self._mode = mode
        if button:
            self._button = button
        self._ptt_down = False
        if self._ctl is not None:
            # 切到 ptt = 边界交给手; 切回 always = 判停层重新自己猜
            self._ctl.set_manual_boundary(self._mode == 'ptt')
        if self._ear is not None:
            # ptt 的闭嘴是"没按"(故意, 不设期限); always 是放开(不静音)
            self._ear.mute(self._mode == 'ptt', 0.0,
                           watchdog=self._mode != 'ptt')
        _log.info('asr mode -> %s (button=%r, listening=%s)',
                  self._mode, self._button, self._listening())
        return {'ok': True, 'mode': self._mode, 'button': self._button,
                'listening': self._listening()}

    @request('press')
    async def _req_press(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """按钮按下: 开麦. 模式/按钮名不对 → ``ignored``(不是错误)."""
        return await self._ptt(data, down=True)

    @request('release')
    async def _req_release(self, source,
                           data: Dict[str, Any]) -> Dict[str, Any]:
        """按钮松开: 关麦 + 立刻交付(不等静音判停)."""
        return await self._ptt(data, down=False)

    async def _ptt(self, data: Dict[str, Any], down: bool) -> Dict[str, Any]:
        button = str((data or {}).get('button') or '').strip()
        if self._mode != 'ptt':
            return {'ok': True, 'ignored': 'mode is always'}
        if button and button != self._button:
            return {'ok': True, 'ignored':
                    f'button {button!r} != {self._button!r}'}
        if down == self._ptt_down:
            return {'ok': True, 'down': down, 'already': True}
        self._ptt_down = down
        result = None
        if down:
            if self._ear is not None:
                self._ear.mute(False, 0.0)     # 按下立刻开
        else:
            if self._ear is not None:
                self._ear.mute(True, 0.0, watchdog=False)   # 松开立刻关麦
            self._flushing = True              # 但门再开一会儿: 让尾巴交完
            try:
                result = await self._flush()
            finally:
                self._flushing = False
        _log.info('asr ptt %s (button=%r) %s', 'down' if down else 'up',
                  button or self._button, result or '')
        return {'ok': True, 'down': down, 'result': result}

    async def _flush(self) -> str:
        """把当前段立刻交出去(ptt 松开 / 切模式前): 走正常交付路径.

        松手那一刻管道里还有两段在路上: ①采集侧还没传上去的音频(采集粒度
        ``_ACCUM_MS``, 松手那一块仍在采); ②服务端还没吐出来的那包 partial.
        **固定等一个常数**必然切掉其中一段 —— 少字是硬伤, 所以等到"最近
        ``_PTT_QUIET_S`` 没有新 partial"再交, 但不超过 ``_PTT_SETTLE_MAX_S``
        (服务端不吐字时不能一直等, 交付延迟是用户直接感受).
        """
        if self._ctl is None:
            return 'listener not ready'
        t0 = time.monotonic()
        while True:
            await asyncio.sleep(0.05)
            elapsed = time.monotonic() - t0
            quiet = time.monotonic() - self._partial_at
            if elapsed >= _PTT_SETTLE_S and quiet >= _PTT_QUIET_S:
                break
            if elapsed >= _PTT_SETTLE_MAX_S:
                break
        return await self._ctl.send_now()

    @request('etiquette')
    async def _req_etiquette(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """查/切礼仪: 不带 name 查当前 + 全部坐标; 带 name 切换."""
        if self._ctl is None:
            return {'ok': False, 'error': 'listener not ready'}
        name = (data or {}).get('name')
        if not name:
            return {'ok': True, **self._ctl.snapshot(),
                    'all': [e.name for e in self._ctl.config().etiquettes]}
        return {'ok': True, 'etiquette': self._ctl.set_etiquette(str(name))}

    @request('upsert_etiquette')
    async def _req_upsert(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """增/改一种礼仪(整份 EtiquetteSpec JSON)."""
        if self._ctl is None:
            return {'ok': False, 'error': 'listener not ready'}
        raw = (data or {}).get('json') or ''
        try:
            return {'ok': True, 'etiquette': self._ctl.upsert_etiquette(raw)}
        except Exception as exc:
            return {'ok': False, 'error': f'{exc}'}

    @request('transcript')
    async def _req_transcript(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """拉读留存槽(observer 类礼仪用): current + 最近 n 段 + 溢出计数."""
        if self._ctl is None:
            return {'ok': False, 'error': 'listener not ready'}
        n = int((data or {}).get('n', 0) or 0)
        return {'ok': True, **self._ctl.transcript(n)}

    @request('send_now')
    async def _req_send_now(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """人类强发送: 自动交付礼仪 force commit; 留存礼仪 drain 后发一条."""
        if self._ctl is None:
            return {'ok': False, 'error': 'listener not ready'}
        return {'ok': True, 'result': await self._ctl.send_now()}

    # --- 听 ---

    async def _on_event(self, ev: EarEvent) -> None:
        """耳朵事件 → 策略层; 中间态另外留一份给 status, 连接态打日志.

        ptt 没按住时**识别事件一律不进策略层**: 松手那一下已经
        ``send_now`` 交了整段, 服务端随后吐的 final 是同一句的尾巴 ——
        再喂进去会开一个新段、重复交付一次. 连接态不丢(诊断要看).
        """
        if not self._listening():
            # ptt 没按住: 识别事件一律不进策略层(连接态照旧打日志) ——
            # 否则没按住时的噪声会发起话包, 打断身体.
            if ev.kind == 'state':
                _log.info('asr state: %s', ev.detail)
            return
        if ev.kind == 'partial':
            self._partial = ev.text
            self._partial_at = time.monotonic()   # 交付窗口靠它判"还在吐字"
        elif ev.kind == 'state':
            _log.info('asr state: %s', ev.detail)
        if self._ctl is not None:
            await self._ctl.feed(ev)

    @subscribe('output')
    async def _on_output(self, source, data: Any) -> None:
        """半双工闸门: 自己出声期丢麦克风(免得把自己的话听成用户的话)."""
        if not self._cfg.mute_on_say or self._ear is None:
            return
        frame = data if isinstance(data, dict) else {}
        if frame.get('kind') != 'speech':
            return
        if self._mode == 'ptt' and not self._ptt_down:
            # ptt 没按: 麦本来就关着(而且关着才不上传) —— 闸门这时既不必
            # 关, 更**不许开**: 它一 mute(False) 就把"没按"的静音解掉,
            # 松手之后整间屋子会被当成用户的话传上去(还全是识别时长).
            return
        self._ear.mute(frame.get('state') == 'start')

    # --- 围观: 交互面流式渲染 ---

    def _witness(self, text: str, final: bool) -> None:
        """策略层的旁观针(同步): 投一条 ``heard`` 广播, 不等它.

        ``final=True`` 除了定稿 UI, 还**顺手把识别流收掉**(``ear.commit()``):
        判停是段边界的权威, 它一落定这段文本就固定了, 不必再等静音超时(
        3s)或尾巴空窗(0.6s) —— 发个负序号让服务端回尾包, 一条流到此为止
        (原版 ``Recognizer.commit()`` 同构)。两条路都在这里挂: ptt 松手的
        ``send_now`` 和 always 的静音判停, 走的都是 ``ListenerController``
        的 ``_commit`` → 本针。

        ``_commit`` 是判停回调, 卡在这里=判停变慢; 广播走 task 丢出去,
        丢了也不算事故(UI 掉线/无人订阅时本来就没人听)。
        """
        if final and self._ear is not None:
            self._ear.commit()
        task = asyncio.ensure_future(self._publish_heard(text, final))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _publish_heard(self, text: str, final: bool) -> None:
        """广播到 ``moss/heard``: ``partial`` 是**本段全文**(不是增量),
        订阅方按整行替换渲染; ``final`` = 判停定稿."""
        try:
            await self.publish('heard', {
                'kind': 'final' if final else 'partial',
                'text': text,
                'source': self._cfg.source,
            }, namespace='moss')
        except Exception:
            _log.debug('heard publish failed', exc_info=True)

    async def _emit(self, **fields: Any) -> Dict[str, Any]:
        """申报出口(策略层的 emit 针): 字段直通 mindflow.declare.

        仲裁结论调用方不可见 —— declare 是 fire-and-forget 的登记, 原版同语义.
        """
        rid = await self._mindflow_rid()
        text = str(fields.get('text', ''))
        if rid is None:
            self._dropped += 1
            _log.warning('asr dropped (mindflow not running): %r', text[:40])
            return {'verdict': 'dropped'}
        body: Dict[str, Any] = {
            'id': fields.get('id') or uuid4().hex,
            'source': fields.get('source') or self._cfg.source,
            'text': text,
            'priority': int(fields.get('priority', Priority.NOTICE)),
            'strength': int(fields.get('strength', self._cfg.strength)),
            'complete': bool(fields.get('complete', True)),
            'mode': str(fields.get('mode', '') or ''),
            'interrupt': bool(fields.get('interrupt', False)),
            # 原版 thinking_effort 直通: 起话/交付包都是 normal(停了也要
            # 回答), 漏掉这个键会让 interrupt=True 被 declare 按 primitive
            # 补成 'none', 整句话变成"停而不答"的空拍
            'effort': str(fields.get('effort') or 'normal'),
            # 听侧专有的读数: 低置信字拼成一串 / 这一段的平均响度 /
            # 这一段是否收尾. 起话包没有前两样(段还没结账) —— 空值就发空值.
            # 三个都照发(数据面), 印不印由渲染层定: 现在只印 paused.
            'low_conf': str(fields.get('low_conf') or ''),
            'db': fields.get('db'),
            'paused': fields.get('paused'),
        }
        try:
            r = await self.req(rid, 'declare', body)
        except Exception:
            _log.exception('declare asr failed')
            return {'verdict': 'error'}
        self._declares += 1
        _log.info('asr -> mindflow: %r (complete=%s verdict=%s)',
                  text[:60], body['complete'], (r or {}).get('verdict'))
        return r or {}

    async def _mindflow_rid(self) -> Optional[str]:
        """裁判地址: 按名查运行实例, 查到即缓存(mindflow 常驻且唯一).

        装配序: passive 拉起次序无保证 —— 耳朵可能先于 mindflow 就绪,
        所以第一次真正要用时才查, 查不到就丢帧打日志, 不阻塞首帧."""
        if self._mindflow_id:
            return self._mindflow_id
        try:
            for r in await self.get_running_routines():
                if r.get('name') == 'mindflow':
                    self._mindflow_id = str(r['id'])
                    return self._mindflow_id
        except Exception:
            _log.exception('mindflow lookup failed')
        return None
