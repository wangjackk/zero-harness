"""灵魂循环最小闭环: 用户的话 → Ghost(记忆+轮次+外接脑子) → Shell
(解析+全局词表+显式轨表) → Channel 队列 → 动作 routine → 观测回灌记忆.

验证九件事:
1. 串行/并行: 同 lane 动作按到达序逐个执行, 跨 lane 交错进行;
2. 记忆 = 感知关键帧(原版 Moment 同构): 一拍一帧, previous 缝合缝
   (上拍回执+停止原因)/percepts 按源/瞬态不入史/logos; 观测缝进
   下一帧, 下一轮脑子看得见; 滑窗按帧计(原版 max_moments_size);
   反幻觉: 中断拍已执行的部分 logos 也入史; epoch 压缩窗内历史
   转生 recap 置顶;
3. 中断: 一拍抛错不崩泵(原版 attention.abort 同构) —— idle 屏障放行,
   reason 留记忆, 下一轮照常;
4. 失败隔离 + 结果回灌: 动作抛错不杀 lane, 错误帧与动作结果帧
   一起随收尾 drain 回灌;
5. 违规面: 坏标签/未闭合/词表外动词收成观测帧, 通道不死;
6. 词表增删: 运行期 shell registry 挂动词, 挂上即调、摘掉即拒;
7. 轨道显式建: 默认只有 main, 未建的轨当场拒绝, chan_create
   动作开轨回执后下一轮可用;
8. 文字与出声分离: 裸文本 = 旁白(final answer, 不进轨不产帧);
   出声说话走 say(流式: 开标签入轨即 create, 体增量直投实例,
   created 态即可收, start 由轨调度, 闭标签 eof 收流);
9. 多源仲裁(照抄原版 rank+challenge 两场结构, 拉取时序): rank 群比
   (同源聚合一拍/特殊核 last-wins 独占/尾包快车道/字典序: 先
   priority 后 strength)+ 决赛(保护期/强度衰减/同源提权/抢占在途拍
   留痕/首包尾包吸收)+ 失败处置(next 插队/notify 挂账/knock·command
   丢/strength=0 绝不竞争/default 输家 suppress 回缓冲重赛 —— 下届
   独立成链) + 胶合层回归: declare 即回申报登记(原版 fire-and-forget,
   verdict 只有注册级回执)/idle 静默屏障/混队不挂死/保护期不挡吸收/
   静默 aside 无孤儿/未完输入不重复/输家各成链不合并;
10. 三核对齐(原版 interrupt/knock/command nucleus): 纯打断
   (FATAL+停而不答, 空拍留痕不回应, 胜利侧冷静期)/knock 可丢
   (忙时没听见就没听见)/反射弧(logos 直投身体不调脑, last-wins,
   失败即丢) + 打断收尾(原版 thinking 退出即 shell.clear 同构:
   未决流处死/队列丢弃/解析器重开/旧拍回声只入史不串台).
11. 指令结果不触发(obs 只记账不驱动): close 不等在途(指令漂着 ——
   原版拍尾 ``wait_tasks(to_be_observed=True)`` 只等观测类, 暂全
   不等), 回执迟到即时转发入史(pending 渲染可见); mindflow 不因
   obs 自续回声帧(原版默认命令完成不点 need_observe 同构), 下一届
   首帧缝合缝读到; 点灯只有两个来源且共用一盏(原版 need_observe
   闩锁): 手动 next 输家(queued)与灵魂报"声明观测已清空"(echo).

序的锁链(每跳都保序):
    脑字序 = mailbox 序 = 解析序 = shell 转发序 = lane FIFO = 执行序

用法: ``python -m app.routines.user.mossv2.tests.test_dispatch``
"""
from __future__ import annotations

import asyncio
import gzip
import json
import struct
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, ClassVar, Dict

_HERE = Path(__file__).resolve().parent
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))

from routine import Routines, kernel
from routine.hub import RoutineHub
from routine.routine import PassiveConfig, Routine, request

from ..asr import Asr, EarEvent
from ..asr.sauc import build_audio, build_init, parse_response
from ..channel import Channel
from ..code_kernel import CodeKernel
from ..commands.asr_mode import AsrMode
from ..commands.code import Code
from ..commands.control import Interrupt
from ..commands.chan import ChanClear, ChanCreate, ChanDelete, ChanStatus
from ..commands.noop import Noop
from ..commands.say import Say
from ..commands.sleep import Sleep
from ..container import All, AnyOf, Flow, Loop
from ..ghost import Ghost
from ..key_listen import KeyListen
from ..listen import EtiquetteConfig
from ..mindflow import Attention, Impulse, Mindflow, Priority
from ..moment import Moments
from ..moss import Moss
from ..modules import MODULES
from ..nucleus import NUCLEI, ListenerNucleus, Nucleus
from ..shell import Shell

_log: list[str] = []


class FakeBrain:
    """假脑: 按调用次序(主拍 + 回声帧)产出脚本字流, 记忆走真实循环."""

    script: list[str] = []   # 每拍一段 CTML 流(类级, 测试预置)

    def __init__(self):
        self._n = 0

    async def stream(self, history, system):
        text = self.script[self._n]
        self._n += 1
        for i in range(0, len(text), 5):   # 模拟增量: 小块吐
            yield text[i:i + 5]
            await asyncio.sleep(0.01)


class X(Routine):
    """一次性动作 routine: x 跑长."""

    name = 'x'

    async def run(self, kwargs):
        _log.append('x.start')
        await asyncio.sleep(0.15)
        _log.append('x.end')
        return {}

    async def stop(self) -> None:
        pass


class Y(Routine):
    """一次性动作 routine: y 跑短."""

    name = 'y'

    async def run(self, kwargs):
        _log.append('y.start')
        await asyncio.sleep(0.05)
        _log.append('y.end')
        return {}

    async def stop(self) -> None:
        pass


class Z(Routine):
    """一次性动作 routine: z 与 a 轨并行."""

    name = 'z'

    async def run(self, kwargs):
        _log.append('z.start')
        await asyncio.sleep(0.1)
        _log.append('z.end')
        return {}

    async def stop(self) -> None:
        pass


class Slow(Routine):
    """一次性动作 routine: slow 跑 1.2s —— 跨拍漂着的测试主角
    (close 不等在途: 拍收时它还在跑, 回执迟到入史)."""

    name = 'slow'

    async def run(self, kwargs):
        _log.append('slow.start')
        await asyncio.sleep(1.2)
        _log.append('slow.end')
        return {}   # 回执只说'成了'

    async def stop(self) -> None:
        pass


class SlowObs(Slow):
    """同 slow, 但出厂声明 ``observe``(原版 CommandMeta 同构):
    调用方不用写 observe, 回执自带注意债."""

    name = 'slowobs'
    meta: ClassVar[Dict[str, Any]] = {'observe': True}

    async def run(self, kwargs):
        _log.append('slowobs.start')
        await asyncio.sleep(1.2)
        _log.append('slowobs.end')
        return {}   # 注意债由 meta['observe'] 出厂声明


class Bump(Routine):
    """一次性动作: 记次数 —— loop 每轮重放应看到它被重新 create."""

    name = 'bump'
    rounds: ClassVar[list] = []

    async def run(self, kwargs):
        Bump.rounds.append(time.perf_counter())
        _log.append('bump')
        return {'details': {'n': len(Bump.rounds)}}

    async def stop(self) -> None:
        pass


class Hold(Routine):
    """可优雅停止的动作: stop hook 放开 run(清轨用例的主角).

    ``stop`` 不 cancel run, 而是让它自己返回 —— 与 say 截声同构,
    回执照常报账(打断不是销毁: 执行过就该留痕).
    """

    name = 'hold'

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._gate = asyncio.Event()

    async def run(self, kwargs):
        _log.append('hold.start')
        await self._gate.wait()
        _log.append('hold.end')
        return {'details': {'stopped': True}}

    async def stop(self) -> None:
        _log.append('hold.stop')
        self._gate.set()


class SleepProbe(Sleep):
    """带打点的 sleep: 验证"占道 = 本轨后面排队等它" + 真睡了多久."""

    name = 'sleep'   # 覆盖注册需同名

    spans: ClassVar[list] = []

    async def run(self, kwargs):
        t0 = time.perf_counter()
        r = await super().run(kwargs)
        SleepProbe.spans.append(time.perf_counter() - t0)
        _log.append('sleep.end')
        return r


class SayProbe(Say):
    """带打点的 say: run 起跑时刻 = 轨调度的 start 时刻."""

    name = 'say'   # 覆盖注册需同名(不写会按类名转成 say_probe)

    async def run(self, kwargs):
        _log.append('say.run')
        return await super().run(kwargs)


class _RecEngine:
    """测试引擎: 记 feed/finish 时刻(类级汇总), finish 后产一 chunk."""

    events: list[tuple[str, float]] = []   # ('feed' | 'finish', t)

    def __init__(self):
        self._fin = asyncio.Event()

    async def feed(self, text):
        _RecEngine.events.append(('feed', time.perf_counter()))

    async def finish(self):
        _RecEngine.events.append(('finish', time.perf_counter()))
        self._fin.set()

    async def audio_chunks(self, additions=''):
        await self._fin.wait()
        yield 'chunk'


class _RecPlayer:
    """测试播放器: 记 play 时段(类级汇总), 模拟出声时长."""

    plays: list[tuple[float, float]] = []

    def stop(self):
        pass

    async def play(self, chunks):
        t0 = time.perf_counter()
        async for _ in chunks:
            await asyncio.sleep(0.05)   # 模拟出声
        _RecPlayer.plays.append((t0, time.perf_counter()))


class SayTuned(Say):
    """注入测试引擎/播放器的 say(验证三点契约 + 一张嘴)."""

    name = 'say'   # 覆盖注册需同名
    engine_factory = staticmethod(_RecEngine)
    player_factory = staticmethod(_RecPlayer)


class HistBrain(FakeBrain):
    """记录每拍收到的 history(类级): 验证缝合缝可见性(迟到回执
    由下一届首帧读到)."""

    hists: list = []

    async def stream(self, history, system):
        HistBrain.hists.append([dict(h) for h in history])
        async for c in super().stream(history, system):
            yield c


class Pub(Routine):
    """测试探针: 往任意 pubsub 域发一帧(模拟别的器官广播, 如 say 的
    'output' 出声闸门 —— 耳朵据此半双工丢麦)."""

    name = 'pub'
    is_passive = True

    @request('pub')
    async def _pub(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(data or {})
        await self.publish(str(data.get('topic') or 'voice'),
                           data.get('frame') or {})
        return {'ok': True}

    async def run(self, kwargs):
        await asyncio.Event().wait()

    async def stop(self) -> None:
        pass


class FakeEar:
    """测试替身耳朵: 不碰麦克风/网络, 由用例投喂事件.

    与 ``Ear`` 同形状(``events`` 异步流 + ``mute`` + ``close``), 故
    ``Asr.ear_factory`` 一换即可测 routine 面(申报/半双工/诊断),
    协议与重连归 ``sauc.py`` / ``ear.py`` 自己的单测.
    """

    instances: list = []

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.muted: list = []
        self.commits = 0
        self.closed = False
        self.stats = {'sessions': 1, 'blocks': 0, 'utterances': 0,
                      'partials': 0, 'errors': 0}
        # 诊断面: 真耳朵记最近一块的 dBFS(status 里带出来), 替身不说话 -120
        self._last_db: float = -120.0
        #: 响度探针的读数(最近一块像人声的 dBFS); 用例用 ``hear()`` 设,
        #: ``None`` = 还没量到 —— 与真耳朵 ``level_db`` 同语义
        self._speech_db: float | None = None
        self._q: asyncio.Queue | None = None
        FakeEar.instances.append(self)

    async def events(self):
        self._q = asyncio.Queue()
        while not self.closed:
            ev = await self._q.get()
            if ev is None:
                return
            yield ev

    async def feed(self, kind: str, text: str = '', detail: str = '',
                   words: list | None = None) -> None:
        assert self._q is not None, 'ear not started'
        await self._q.put(EarEvent(kind, text, detail, words or []))

    def hear(self, db: float | None) -> None:
        """模拟"刚才那一块有多响"(听侧会在收到文本时来读这个数)."""
        self._speech_db = db

    def level_db(self) -> float | None:
        return self._speech_db

    def mute(self, on: bool, hangover: float = 0.35,
             watchdog: bool = True) -> None:
        self.muted.append(on)

    def uploading(self) -> bool:
        """诊断面: 替身不传音频, 按"最后一次 mute"算(与真耳朵同语义)。"""
        return not (self.muted and self.muted[-1])

    def commit(self) -> None:
        """判停收流: 替身没有流可收, 只记一笔(真耳朵发负序号尾包)。"""
        self.commits += 1

    async def close(self) -> None:
        self.closed = True
        if self._q is not None:
            self._q.put_nowait(None)


class Sim(Routine):
    """测试探针: ('req', name, event, data, fut) 代发 req(每个 req 独立
    task —— 多源并发仲裁的测试前提: 两个 declare 可以同时在途)."""

    name = 'sim'
    is_passive = True

    async def run(self, kwargs):
        loop = asyncio.get_running_loop()
        while True:
            _, name, event, data, fut = await _cmds.get()
            loop.create_task(self._do(name, event, data, fut))

    async def _do(self, name: str, event: str, data: dict, fut) -> None:
        try:
            fut.set_result(
                await self.ctx.req(await self._rid(name), event, data))
        except Exception as exc:
            fut.set_exception(exc)

    async def _rid(self, name: str) -> str:
        for r in await self.ctx.get_running_routines():
            if r.get('name') == name:
                return r['id']
        raise RuntimeError(f'routine not running: {name}')

    async def stop(self) -> None:
        pass


_cmds: asyncio.Queue = asyncio.Queue()

async def _req(name: str, event: str, data: dict | None = None):
    fut = asyncio.get_running_loop().create_future()
    _cmds.put_nowait(('req', name, event, data or {}, fut))
    return await asyncio.wait_for(fut, timeout=15)


async def _turn(text: str) -> dict:
    """一轮对话: 用户的话作为事件进桥梁(declare 即回), 等泵静默(idle
    屏障 = 回声帧也跑完, 返回即动作全部完成)."""
    r = await _req('mindflow', 'declare', {'text': text})
    await _req('mindflow', 'idle')
    return r


async def _obs() -> list[dict]:
    """读灵魂记忆里最后一帧观测(旁白/违规/动作结果)."""
    hist = (await _req('ghost', 'recall'))['history']
    for h in reversed(hist):
        if h['role'] == 'obs':
            return h['frames']
    return []


async def _all_frames() -> list[dict]:
    """读整条帧链里的全部观测帧(跨拍; 迟到回执会落在更晚的帧里)."""
    hist = (await _req('ghost', 'recall'))['history']
    out: list[dict] = []
    for h in hist:
        if h.get('role') == 'obs':
            out.extend(h.get('frames') or [])
    return out


async def _wait_log(*marks: str, timeout: float = 5.0) -> None:
    """轮询等动作端点落 _log(close 不等在途: 拍收后动作可能仍在跑)."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if all(m in _log for m in marks):
            return
        await asyncio.sleep(0.02)
    missing = [m for m in marks if m not in _log]
    raise AssertionError(f'actions did not settle: missing {missing}; log={_log}')


async def _wait_obs(pred=None, timeout: float = 5.0) -> list[dict]:
    """轮询读最后一帧观测直到谓词成立(回执不驱动回声: 拍收后回执
    迟到, 经 pending 渲染进 history 才可见)."""
    deadline = time.perf_counter() + timeout
    last: list[dict] = []
    while time.perf_counter() < deadline:
        last = await _obs()
        if last and (pred is None or pred(last)):
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(f'obs not settled: {last}')


async def _wait_all(pred, timeout: float = 6.0) -> list[dict]:
    """轮询**全部帧链**直到谓词成立(容器/loop 的回执常在拍收后才到,
    落在更晚的观测帧里; 与 ``_wait_obs``(只看最后一帧)互补)."""
    deadline = time.perf_counter() + timeout
    last: list[dict] = []
    while time.perf_counter() < deadline:
        last = await _all_frames()
        if pred(last):
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(f'frames not settled: {last}')


def _moss(verbs: list[str], max_moments: int = 100) -> type:
    """按词表装配 Moss(被动拉起时注入 Shell; max_moments 透传 Ghost
    记忆帧窗, 小窗测出窗语义)."""
    cls = type('M', (Moss,), {
        'name': 'moss',
        'is_passive': PassiveConfig(
            kwargs={'verbs': list(verbs), 'max_moments': max_moments}),
    })
    return cls


@asynccontextmanager
async def _env(verbs: list[str], *extra, brain: type = FakeBrain,
               chans: list[str] | None = None):
    kref = await kernel.create(MODULES)
    rs = Routines()
    Ghost.brain_factory = brain   # 换脑注入(与 Say.engine_factory 同构)
    rs.register(_moss(verbs), Ghost, Shell, Channel, Sim, X, Y, Z, Slow,
                Bump, Hold, ChanCreate, ChanStatus, ChanClear, ChanDelete, Say,
                Sleep,
                Noop, Interrupt,
                Code, CodeKernel, Mindflow, All, AnyOf, Flow, Loop, *extra)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        await asyncio.sleep(1.5)   # moss 带起 ghost + shell
        # 需要的轨道控制面预建(绕开 chan_create 走模型标签的链路,
        # 聚焦本套要测的语义)
        for c in chans or []:
            await _req('shell', 'chan', {'op': 'create', 'name': c})
        yield
    finally:
        await conn.close()
        Ghost.brain_factory = None   # 复位, 不跨用例泄漏


async def t_serial_parallel_aside() -> None:
    """同 lane 串行, 跨 lane 并行, 裸文本 = 旁白(final answer, 不进轨
    不产帧), 结果照常记账(回执不驱动回声: 拍收后漂着落账)."""
    _log.clear()
    FakeBrain.script = ['<a:x/><a:y/><b:z/>Hmm']
    async with _env(['x', 'y', 'z'], chans=['a', 'b']):
        await _turn('干')
        # close 不等在途: idle 放行时动作可能仍在跑; 等回执三帧齐
        # (动作 end 落 _log 与回执入史之间有报账窗口, 以 obs 为准)
        obs = await _wait_obs(lambda o: {f['verb'] for f in o
                                         if f['kind'] == 'action'}
                              == {'x', 'y', 'z'})

    # 串行: y 等 x 跑完才开(同 lane 队列)
    assert _log.index('y.start') > _log.index('x.end'), _log
    # 并行: z 在 x 还没跑完时就开了(跨 lane 各走各的轨)
    assert _log.index('z.start') < _log.index('x.end'), _log
    assert _log.index('z.end') < _log.index('y.end'), _log
    # 裸文本 = final answer: 身体不管 —— 不进轨不产帧(记忆的
    # assistant 原文即是), 出声说话才走 say
    assert all(f.get('verb') != 'say' for f in obs), obs
    # 动作结果: 记账齐全(pending 渲染可见), 同 lane FIFO 顺序保住
    acts = [f['verb'] for f in obs if f['kind'] == 'action']
    assert set(acts) == {'x', 'y', 'z'}, obs
    assert acts.index('x') < acts.index('y'), obs
    print('t_serial_parallel_aside ok: lane serial, lanes parallel, '
          'bare text is final answer')


async def t_container_all() -> None:
    """`<all>`: body 内全部完成才返回 + **占道**(scope 后同轨动作等它).

    两件事同时验证:
    - 不死锁: body 里的 `<main:z/>` 与容器同轨, 容器若在开标签就入队
      会挡住自己的子 —— 所以容器闭标签才入队;
    - 占道: `</all>` 之后的 `<main:y/>` 排在容器之后, 等 body 全部落定.
    """
    _log.clear()
    FakeBrain.script = ['<main:all><left:x/><main:z/></main:all><main:y/>']
    async with _env(['x', 'y', 'z'], chans=['left']):
        await _turn('干')
        await _wait_log('x.end', 'z.end', 'y.end')
    # 并行: z 没等 x(左轨慢动作)
    assert _log.index('z.start') < _log.index('x.end'), _log
    # 占道: y 等 all 关闭 = 等最慢的 x(0.15s) 落定
    assert _log.index('y.start') > _log.index('x.end'), _log
    print('t_container_all ok: waits all, occupies its lane, no deadlock')


async def t_container_flow() -> None:
    """`<_>`(flow): 只等**本轨**的子; 跨轨的慢动作不等.

    scope 锚 main: body 里 main:z(0.1s) 与 left:x(0.15s) 并行,
    scope 只等 z, 所以 scope 后的 y 在 x 结束前就开跑 —— 这正是原版
    `task.chan == enter_chan` 的平铺等价物.
    """
    _log.clear()
    FakeBrain.script = ['<main:_><left:x/><main:z/></main:_><main:y/>']
    async with _env(['x', 'y', 'z'], chans=['left']):
        await _turn('干')
        await _wait_log('x.end', 'z.end', 'y.end')
    assert _log.index('y.start') > _log.index('z.end'), _log   # 等本轨
    assert _log.index('y.start') < _log.index('x.end'), _log   # 不等跨轨
    print('t_container_flow ok: waits same-lane children only')


async def t_cross_lane_barrier() -> None:
    """跨轨栅栏: b 轨要等 a 轨做完再开始.

    写法 = 容器锚在**要等的轨**(b)上, 体内只放 a 的动作, b 自己的动作
    写在闭标签之后 —— 容器占住 b 轨直到体内落定, 后续同轨动作自然排队.

    反例是"把两边都写进体内": 体内指令一发出就投递, 与身体同在的
    a 动作并行(写在后面 ≠ 等在后面), 所以必须写在容器之外.
    """
    _log.clear()
    FakeBrain.script = ['<b:all><a:x/></b:all><b:y/>', '']
    async with _env(['x', 'y'], chans=['a', 'b']):
        await _turn('干')
        await _wait_log('x.end', 'y.end')
    assert _log == ['x.start', 'x.end', 'y.start', 'y.end'], _log
    print('t_cross_lane_barrier ok: b waits for a '
          '(container anchored on b, a\'s action in body)')


async def t_cross_lane_no_wait() -> None:
    """对照: 同样的标签, 两边都写进体内 → 并行, 不等.

    这条不是冗余 —— 它是"写在后面不等于等在后面"的活证据,
    提示词里那句提醒就是按它写的.
    """
    _log.clear()
    FakeBrain.script = ['<b:all><a:x/><b:y/></b:all>', '']
    async with _env(['x', 'y'], chans=['a', 'b']):
        await _turn('干')
        await _wait_log('x.end', 'y.end')
    assert _log.index('y.start') < _log.index('x.end'), _log
    print('t_cross_lane_no_wait ok: same-lane body still parallel '
          '(order of tags is not order of execution)')


async def t_container_any() -> None:
    """`<any>`: 任一完成即返回, 其余取消(原版 _clear_all_sub_tasks)."""
    _log.clear()
    FakeBrain.script = ['<main:any><left:slow/><main:y/></main:any>']
    async with _env(['y', 'slow'], chans=['left']):
        await _turn('干')
        obs = await _wait_obs(
            lambda o: any(f.get('verb') == 'any' for f in o), timeout=8)
    frame = next(f for f in obs if f.get('verb') == 'any')
    assert frame['kind'] == 'action', frame
    assert frame['result']['details']['children'] == 2, frame
    assert frame['result']['details']['cancelled'] >= 1, frame   # slow 被取消
    print('t_container_any ok: first done closes scope, others cancelled')


async def t_sleep_occupies_lane() -> None:
    """`<sleep>`: 普通动作 + 占道 —— 同轨后续排队等它, 且真的睡了."""
    _log.clear()
    SleepProbe.spans.clear()
    FakeBrain.script = ['<main:sleep duration="0.3"/><main:y/>']
    async with _env(['y'], SleepProbe):
        await _turn('等一下')
        await _wait_log('y.end')
    assert SleepProbe.spans and SleepProbe.spans[0] >= 0.28, SleepProbe.spans
    assert _log.index('sleep.end') < _log.index('y.start'), _log
    print('t_sleep_occupies_lane ok: sleep blocks its own lane for real')


async def t_noop_is_an_action() -> None:
    """`<noop/>`: 沉默变成可记账的动作, 但不带注意债(不叫醒自己)."""
    FakeBrain.script = ['<main:noop/>']
    async with _env([]):
        await _turn('嗯')
        frames = await _wait_obs(
            lambda o: any(f.get('verb') == 'noop' for f in o))
    frame = next(f for f in frames if f.get('verb') == 'noop')
    # noop 什么都不说: 回执只剩 content 的缺省值, details 空
    assert frame['kind'] == 'action', frame
    assert frame['result'] == {'content': 'ok', 'details': {}}, frame
    assert frame['owed'] is False, frame
    print('t_noop_is_an_action ok: deliberate silence is on the ledger, no debt')


async def t_loop_times() -> None:
    """`<loop times="3">`: body 重放三轮, 收尾回执自带注意债."""
    _log.clear()
    Bump.rounds.clear()
    # 第二拍: loop 收尾自带注意债 → 会买一次回声帧(原版 observe=True)
    FakeBrain.script = ['<main:loop times="3"><main:bump/></main:loop>', '']
    async with _env(['bump']):
        await _turn('三下')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'loop' for f in fs))
    assert _log.count('bump') == 3, _log
    frame = next(f for f in frames if f.get('verb') == 'loop')
    assert frame['result']['details']['loops'] == 3, frame
    assert frame['result']['content'] == '跑了 3 轮', frame
    assert frame['owed'] is True, frame
    print('t_loop_times ok: body replayed 3 rounds, done-frame carries debt')


async def t_loop_stops_on_error() -> None:
    """`<loop times="-1">`: 子投递失败即停(不无限重放), 后续同轨动作照跑."""
    _log.clear()
    FakeBrain.script = [
        '<main:loop times="-1"><main:nope/></main:loop><main:y/>', '']
    async with _env(['y']):
        await _turn('循环')
        await _wait_log('y.end')   # 没挂死
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'loop' for f in fs))
    frame = next(f for f in frames if f.get('verb') == 'loop')
    assert frame['result']['details']['loops'] == 0, frame   # 一轮都没跑完
    assert any(f.get('kind') == 'error' and 'unknown verb' in f.get('text', '')
               for f in frames), frames
    print('t_loop_stops_on_error ok: bad body stops the loop, lane moves on')


async def t_container_timeout() -> None:
    """`<all timeout>`: 到时不等, 取消剩余, 回执 timed_out + 自带债."""
    _log.clear()
    # 跨 lane: 同 lane 的子会在容器入队前就被串行跑完(timeout 无从生效)
    FakeBrain.script = ['<main:all timeout="0.2"><left:slow/></main:all>', '']
    async with _env(['slow'], chans=['left']):
        await _turn('等')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'all' for f in fs))
    frame = next(f for f in frames if f.get('verb') == 'all')
    assert frame['result']['details']['timed_out'] is True, frame
    assert frame['result']['content'].startswith('等超时'), frame
    assert frame['result']['details']['cancelled'] >= 1, frame
    assert frame['owed'] is True, frame
    print('t_container_timeout ok: timeout closes the scope and reports it')


async def t_clear_drops_queue() -> None:
    """`<chan_clear name="main"/>`: 即刻生效(不排在队列后) —— 排队的丢掉,
    在跑的优雅停下. 清轨是族内动作, 必须带 name(全停是 `<interrupt/>`)."""
    _log.clear()
    FakeBrain.script = ['<main:hold/><main:hold/>'
                        '<main:chan_clear name="main"/>']
    async with _env(['hold']):
        await _turn('停')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'chan_clear' for f in fs))
        await asyncio.sleep(0.2)
    assert _log.count('hold.start') == 1, _log            # 第二个被丢掉
    assert 'hold.stop' in _log and 'hold.end' in _log, _log  # 第一个优雅停
    assert any(f.get('verb') == 'chan_clear' for f in frames), frames
    print('t_clear_drops_queue ok: queued dropped, running stopped gracefully')


async def t_interrupt_all() -> None:
    """`<interrupt/>`: 清全部轨(跨 lane) —— 两条轨上的活都停."""
    _log.clear()
    FakeBrain.script = ['<left:hold/><main:hold/><main:interrupt/>']
    async with _env(['hold'], chans=['left']):
        await _turn('停')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'interrupt' for f in fs))
        await asyncio.sleep(0.2)
    assert _log.count('hold.start') == 2, _log
    assert _log.count('hold.stop') == 2, _log
    frame = next(f for f in frames if f.get('verb') == 'interrupt')
    assert sorted(frame['result']['details']['cleared']) == ['left', 'main'], frame
    print('t_interrupt_all ok: every lane cleared by one call_soon command')


async def t_code_cell() -> None:
    """`<code>`: 一个标签 = 一段源码 + 一条结果账 —— result 取末表达式,
    stdout 随回执回灌. 体内的 call 直接建子实例, 不进投递(根账里没有它)."""
    _log.clear()
    FakeBrain.script = [
        "<main:code>print('hi')\nawait call('y')\n1 + 1</main:code>", '']
    async with _env(['y']):
        await _turn('算')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    frame = next(f for f in frames if f.get('verb') == 'code')
    res = frame['result']['details']
    assert res['ok'] is True, res
    assert res['stdout'].strip() == 'hi', res
    assert res['result'] == 2, res
    # 模型读的是 content: stdout 与末表达式都在里面
    assert 'hi' in frame['result']['content'], frame
    assert frame['owed'] is True, frame                    # 回执是唯一可见面
    assert _log == ['y.start', 'y.end'], _log             # 子实例真跑了
    assert not any(f.get('verb') == 'y' for f in frames)  # 但不进根账
    print('t_code_cell ok: source in, {stdout, result} out')


async def t_code_occupies_lane() -> None:
    """code 是普通命令: 占本轨槽位, 同轨后续标签等它跑完.

    对照: 它体内的动作不进投递, 所以不会与自己排队 —— 占位与投递是两回事.
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>await asyncio.sleep(0.3)</main:code><main:y/>", '']
    async with _env(['y']):
        await _turn('等')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'y' for f in fs), timeout=9.0)
    verbs = [f.get('verb') for f in frames if f.get('verb') in ('code', 'y')]
    assert verbs == ['code', 'y'], (verbs, frames)
    print('t_code_occupies_lane ok: later same-lane tag waited for code')


async def t_code_error() -> None:
    """异常不冒到 runner: 收成 {ok: False, error} 并自带注意债."""
    _log.clear()
    FakeBrain.script = ['<main:code>x = 1 / 0</main:code>', '']
    async with _env([]):
        await _turn('炸')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    frame = next(f for f in frames if f.get('verb') == 'code')
    res = frame['result']['details']
    assert res['ok'] is False, res
    assert 'ZeroDivisionError' in res['error'], res
    assert '(line 1)' in res['error'], res
    assert '执行出错' in frame['result']['content'], frame
    assert frame['owed'] is True, frame
    print('t_code_error ok: a failed cell is a borrowable fact')


async def t_code_unknown_verb() -> None:
    """词表外的名字: 建实例时被 kernel 拒, 错误由 cell 收成结果."""
    _log.clear()
    FakeBrain.script = ["<main:code>await call('nosuch')</main:code>", '']
    async with _env([]):
        await _turn('调')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    frame = next(f for f in frames if f.get('verb') == 'code')
    assert frame['result']['details']['ok'] is False, frame
    assert 'not registered' in frame['result']['details']['error'], frame
    print('t_code_unknown_verb ok: unknown name fails inside code')


async def t_code_syntax() -> None:
    """语法错: 不执行, 但必须让模型看见(它自己写的代码).

    回执两段: CPython 的原话 + **出错那一行与指针**. 第二段是必须的 ——
    模型看不见自己发出去的体, 只给行号它就得凭记忆重写, "我是不是打了
    个全角逗号"只能靠猜(真踩过: `<code>` 里 `import time，asyncio`)。
    """
    _log.clear()
    FakeBrain.script = ['<main:code>x = = 1</main:code>', '']
    async with _env([]):
        await _turn('写错')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    frame = next(f for f in frames if f.get('verb') == 'code')
    assert frame['result']['details']['ok'] is False, frame
    assert 'SyntaxError' in frame['result']['content'], frame
    assert frame['owed'] is True, frame
    assert 'x = = 1' in frame['result']['content'], frame   # 原始行回显

    # 全角逗号: 点名的必须是 CPython(不是我们猜), 且指针落在那个字符上
    _log.clear()
    FakeBrain.script = [
        '<main:code>import time\uff0casyncio\nt0 = 1</main:code>', '']
    async with _env([]):
        await _turn('再写错')
        frames2 = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    err = next(f for f in frames2
               if f.get('verb') == 'code')['result']['content']
    assert 'U+FF0C' in err, err                         # CPython 原话
    lines = err.splitlines()
    assert len(lines) == 3, err                         # 头 / 源行 / 指针
    body, caret = lines[1][2:], lines[2][2:]
    assert body.startswith('import time\uff0c'), repr(body)
    assert caret.strip() == '^', repr(caret)
    assert body[len(caret) - 1] == '\uff0c', (body, caret)   # 指在逗号上
    print('t_code_syntax ok: compile error reported with the offending '
          'line echoed back, nothing executed')


async def t_code_say() -> None:
    """`await call('say', body=...)`: body 就是标签体, say 没有专用函数.

    两种 body 形状都走同一条路: 整段字符串 / 异步生成器(边产边递).
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>r1 = await call('say', body='你好')\n"
        "async def gen():\n"
        "    for s in ('再', '见'):\n"
        "        yield s\n"
        "r2 = await call('say', body=gen())\n"
        "print(r1['details']['words'], r2['details']['words'], "
        "r2['details'].get('stopped'))</main:code>", '']
    async with _env([]):
        await _turn('说')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    assert code['result']['details']['ok'] is True, code
    assert code['result']['details']['stdout'].strip() == '2 2 None', code
    assert not any(f.get('verb') == 'say' for f in frames)   # 不进根账
    print('t_code_say ok: body rides call (str + async gen, no root trace)')


async def t_code_handle() -> None:
    """句柄: create 拿 handle, 自己递 body, 自己决定何时 stop(说一半收口).

    判据是"截断回执": words 只算递进去的那几段, stopped 是已被说出的尾
    部 —— 也就是这一点文字真的走到了身上, 不是 create 完就停.
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>h = await create('say')\n"
        "await h.start()\n"
        "await h.feed('你好世界')\n"
        "await asyncio.sleep(0.1)     # 让它真收到这一段\n"
        "await h.stop()\n"
        "r = await h\n"
        "print(r['details']['words'], r['details']['stopped'])"
        "</main:code>", '']
    async with _env([]):
        await _turn('改口')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    res = code['result']['details']
    assert res['ok'] is True, res
    assert res['stdout'].strip() == '4 你好世界', res
    assert not any(f.get('verb') == 'say' for f in frames)   # 不进根账
    print('t_code_handle ok: create → feed → stop, handle is the instance')


async def t_code_container_refused() -> None:
    """容器在代码里没有意义: 子要投给 route 才有轨可锚, 当场报清楚."""
    _log.clear()
    FakeBrain.script = [
        "<main:code>await call('all', body='<main:y/>')</main:code>", '']
    async with _env(['y']):
        await _turn('套容器')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    assert code['result']['details']['ok'] is False, code
    assert 'needs a lane' in code['result']['content'], code
    assert not any(f.get('verb') == 'y' for f in frames)
    print('t_code_container_refused ok: containers stay a lane construct')


async def t_code_no_nest() -> None:
    """code 不能套 code: 同一个内核 = 同一把锁 + 同一份命名空间.

    让它在运行期等锁, 模型看到的是 600s 后一句"exec timeout" —— 看不出
    是自己等自己. 当场拒掉并说清写法(源码直接写在同一格里, 完全同义).
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>await call('code', body='1+1')</main:code>", '']
    async with _env(['y']):
        await _turn('套代码')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs), timeout=20.0)
    code = next(f for f in frames if f.get('verb') == 'code')
    assert code['result']['details']['ok'] is False, code
    assert 'cannot nest' in code['result']['content'], code
    assert 'inline' in code['result']['content'], code
    print('t_code_no_nest ok: nested code refused before it deadlocks')


async def t_code_body_refused() -> None:
    """体传错地方当场报错: 纯参数动作不吃体(call 与句柄两条路都拦).

    静默丢掉内容是最坏的失败 —— 所以两条入口都看同一份声明
    (``CtmlRoutine`` 自动盖的 ``meta['body']`` 旗), 不是 CtmlRoutine 就拒.
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>errs = 0\n"
        "try:\n"
        "    await call('y', body='内容')\n"
        "except Exception:\n"
        "    errs += 1\n"
        "h = await create('y')\n"
        "try:\n"
        "    await h.feed('内容')\n"
        "except Exception:\n"
        "    errs += 1\n"
        "await h.start()\n"
        "await h\n"
        "print(errs)</main:code>", '']
    async with _env(['y']):
        await _turn('塞错')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    assert code['result']['details']['ok'] is True, code
    assert code['result']['details']['stdout'].strip() == '2', code
    print('t_code_body_refused ok: body only goes to verbs that declare one')


async def t_code_spawn() -> None:
    """后台任务: 跨格存活, 停不停归模型, 内核只报账 + 收摊兜底.

    一次验五件事: ① 回执报"运行中谁 + 在等什么"(模型看得见才决定得了);
    ② 同名自动编号(循环里起一批同名任务很自然, 对不了号就报不了账);
    ③ 变量跨格保留, 下一格 `t.cancel()` 真停得掉;
    ④ 后台任务抛错不当 stderr 里的一句 warning 了事 —— 那是模型永远
    看不到的地方, 死讯必须走下一格的回执;
    ⑤ 后台任务里建的动作挂**内核**名下(格早收尾了) —— 挂格会拿到
    `parent routine N not found`, 这条判据就是那次故障的回归.
    """
    async def codes(n: int) -> list:
        fs = await _wait_all(
            lambda x: len([f for f in x if f.get('verb') == 'code']) >= n)
        return [f for f in fs if f.get('verb') == 'code']

    _log.clear()
    # 每拍两段: 主拍 + 点灯补拍(空脚本, 与 t_code_cell 同一配对)
    FakeBrain.script = [
        "<main:code>a = spawn(asyncio.sleep(30), name='watcher', "
        "desc='等配置改动')\n"
        "b = spawn(asyncio.sleep(30), name='watcher')</main:code>",
        '',
        "<main:code>a.cancel()\nb.cancel()</main:code>",
        '',
        "<main:code>async def poke():\n"
        "    await asyncio.sleep(0.03)\n"      # 等这一格收尾: 父必须换成内核
        "    return await call('sleep', duration=0.01)\n"
        "spawn(poke(), name='poke')</main:code>",
        '',
        "<main:code>async def bad():\n"
        "    await asyncio.sleep(0.01)\n"
        "    raise ValueError('boom')\n"
        "spawn(bad(), name='bad')</main:code>",
        '',
        "<main:code>1</main:code>",
        '']
    async with _env(['y']):
        await _turn('起个后台的')
        r1 = (await codes(1))[-1]['result']
        assert ('后台任务运行中 2 条: watcher(等配置改动), watcher#2'
                in r1['content']), r1
        assert r1['details']['bg'] == [
            {'name': 'watcher', 'desc': '等配置改动'},
            {'name': 'watcher#2'}], r1          # 同名自动编号

        await _turn('停掉它')
        r2 = (await codes(2))[-1]['result']
        assert '后台任务运行中' not in r2['content'], r2      # 停掉的不再报

        await _turn('后台里调个动作')
        await asyncio.sleep(0.25)            # 让 poke 在格收尾之后跑完
        await _turn('起个会挂的')
        r3 = (await codes(4))[-1]['result']
        # poke 成功 = 没有死讯; 挂格的话这里会是 `poke → CallError…parent …`
        assert 'bg_failed' not in r3['details'], r3
        assert r3['details']['bg'] == [{'name': 'bad'}], r3   # 还没死: 照常报
        await asyncio.sleep(0.15)                      # 等它抛
        await _turn('看看')
        r4 = (await codes(5))[-1]['result']
        assert '后台任务失败 1 条: bad → ValueError: boom' in r4['content'], r4
        assert r4['details']['bg_failed'] == [
            {'name': 'bad', 'error': 'ValueError: boom'}], r4
    print('t_code_spawn ok: bg tasks named+described, cancellable, '
          'death surfaced, kids under kernel')


async def t_code_bg_reap() -> None:
    """取消后台任务 → 它派出的子实例跟着收.

    父是内核意味着框架不会替它收(那是"停 code"的级联, 与脱格任务无关),
    所以内核要自己按"发起者"记账; 不记就留下一堆没人要却还在跑的实例.
    ``hold`` 的 stop hook 会打点, 收了没有看 ``_log`` 就知道.
    """
    async def codes(n: int) -> list:
        fs = await _wait_all(
            lambda x: len([f for f in x if f.get('verb') == 'code']) >= n)
        return [f for f in fs if f.get('verb') == 'code']

    _log.clear()
    FakeBrain.script = [
        "<main:code>async def pump():\n"
        "    await call('hold')\n"
        "t = spawn(pump(), name='pump')</main:code>",
        '',
        "<main:code>t.cancel()</main:code>",
        '',
        "<main:code>1</main:code>",
        '']
    async with _env(['y']):
        await _turn('起个卡住的后台任务')
        await asyncio.sleep(0.35)                    # 等 hold 起来
        assert 'hold.start' in _log, _log
        await _turn('停掉')
        for _ in range(40):               # 收尸要等 destroy 的 ack 往返, 别赌固定窗
            if 'hold.stop' in _log or 'hold.end' in _log:
                break
            await asyncio.sleep(0.05)
        assert 'hold.stop' in _log or 'hold.end' in _log, _log
        await _turn('看一眼')                         # 下一拍: 取消不该留死讯
        r = (await codes(3))[-1]['result']
        assert 'bg_failed' not in r['details'], r    # 主动取消不算失败
    print('t_code_bg_reap ok: cancelled bg task takes its children with it')


async def t_code_interrupt() -> None:
    """打断覆盖 code: 占槽位但在跑, clear 必须能找到并停它."""
    _log.clear()
    FakeBrain.script = [
        '<main:code>await asyncio.sleep(5)</main:code><main:interrupt/>', '']
    async with _env([]):
        t0 = time.perf_counter()
        await _turn('停')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs), timeout=8.0)
        span = time.perf_counter() - t0
    code = next(f for f in frames if f.get('verb') == 'code')
    assert code['result']['details'].get('stopped') is True, code
    assert '打断' in code['result']['content'], code
    assert span < 3.0, span
    print(f't_code_interrupt ok: cell cut in {span:.2f}s')


async def t_code_shares_ns() -> None:
    """跨格命名空间: 上一格定义的变量, 下一格还在(IPython 语义).

    实测判据: 每格新建 ns 的实现下第二格会 NameError; 共享内核则 42.
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>x = 41\nnames = ['a']</main:code>"
        "<main:code>print(x + 1, names)</main:code>", '']
    async with _env([]):
        await _turn('两格')
        frames = await _wait_all(
            lambda fs: len([f for f in fs if f.get('verb') == 'code']) >= 2)
    cells = [f for f in frames if f.get('verb') == 'code']
    assert len(cells) == 2, cells
    assert cells[0]['result']['details']['ok'] is True, cells[0]
    assert cells[1]['result']['details']['stdout'].strip() == "42 ['a']", cells[1]
    print('t_code_shares_ns ok: globals survive across cells')


async def t_code_one_cell_at_a_time() -> None:
    """跨 lane 两个 code 不并行: 第二个在 start 就被拒(模块冲突).

    code 声明占用 ``code`` 模块 —— 一台解释器一份命名空间, 一次一格.
    不声明的话两格会在 kernel 那把锁上**静默排队**: 模型以为在并行, 出了
    事只有一句 exec timeout, 看不出是谁等谁; 声明之后第二个当场收错误帧,
    这一拍就能改写法(并行要写在**同一格**里 gather).

    判据: a / b 一成一败, n 只被加过一次; 最后一格读到的还是 1.
    """
    _log.clear()
    body = ("n = globals().get('n', 0)\n"
            "globals()['n'] = n + 1\n"
            "await asyncio.sleep(0.2)\n")
    FakeBrain.script = [
        f"<a:code>{body}</a:code><b:code>{body}</b:code>"
        '<main:sleep duration="1"/><main:code>print(n)</main:code>', '']
    async with _env([], chans=['a', 'b']):
        await _turn('并发')
        frames = await _wait_all(
            lambda fs: len([f for f in fs if f.get('verb') == 'code']) >= 3,
            timeout=12.0)
    cells = [f for f in frames if f.get('verb') == 'code']
    assert len(cells) == 3, cells
    refused = [f for f in cells if f.get('kind') == 'error']
    ran = [f for f in cells if f.get('kind') == 'action']
    assert len(refused) == 1 and len(ran) == 2, cells
    assert "'code'" in str(refused[0].get('text')), refused[0]
    assert ran[-1]['result']['details']['stdout'].strip() == '1', cells
    print('t_code_one_cell_at_a_time ok: second cell refused, not queued')


async def t_code_kids_under_cell() -> None:
    """格子里 call 出去的动作挂在 code 名下(父=格), 停 code 能级联收它.

    执行宿主代建子, 但父子关系不跟着搬 —— 否则打断就漏掉正在跑的动作.
    判据是 hold 真的收到 stop: 父挂在内核名下时它不会被级联, cell 就一直
    挂在 ``await call('hold')`` 上.
    """
    _log.clear()
    FakeBrain.script = [
        "<main:code>await call('hold')</main:code><main:interrupt/>", '']
    async with _env(['hold']):
        t0 = time.perf_counter()
        await _turn('跑')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs), timeout=9.0)
        span = time.perf_counter() - t0
    code = next(f for f in frames if f.get('verb') == 'code')
    assert code['result']['details']['ok'] is True, code
    assert 'hold.start' in _log, _log
    assert 'hold.stop' in _log, _log        # 级联收到的
    assert span < 4.0, span
    print('t_code_kids_under_cell ok: cell actions cascade with the cell')


async def t_memory_across_turns() -> None:
    """记忆闭环: 观测进 history, 下一轮脑子看得见(回执不驱动回声 ——
    say 回执经 pending 缝进下一帧的缝合缝)."""
    seen: list[list] = []

    class RecallBrain(FakeBrain):
        script = ['<main:say>Hi</say>', '<main:say>Again</say>']

        async def stream(self, history, system):
            seen.append([dict(h) for h in history])
            async for c in super().stream(history, system):
                yield c

    async with _env2(RecallBrain):
        await _turn('一')
        await _wait_obs(lambda o: any(f.get('verb') == 'say' for f in o))
        await _turn('二')
    # 每轮单拍(say 回执不触发回声帧): 两轮两拍
    assert len(seen) == 2, seen
    assert [h['role'] for h in seen[0]] == ['user'], seen[0]
    # 第二轮脑子看得见: 第一轮的自己 + say 回执(pending 缝合缝)+ 新输入
    assert [h['role'] for h in seen[1]] == [
        'user', 'assistant', 'obs', 'user'], seen[1]
    print('t_memory_across_turns ok: obs lands in history, '
          'next turn sees it via the stitched seam')


async def t_interrupt_chain() -> None:
    """拍链中断(原版 attention.abort 同构): 一拍抛错不崩泵 —— declare
    屏障放行(不挂死), reason 留记忆(下一拍的底), 下一轮照常."""
    class BoomBrain(FakeBrain):
        """首拍脑挂(抛错), 后续照常吃脚本."""
        script = ['<main:say>缓过来了</say>']

        def __init__(self):
            super().__init__()
            self._dead = True

        async def stream(self, history, system):
            if self._dead:
                self._dead = False
                raise RuntimeError('brain dead')
            async for c in super().stream(history, system):
                yield c

    async with _env2(BoomBrain):
        # 轮 1: 主拍脑挂 → 收链交回(declare 放行, 不挂死) + 中断帧留记忆
        await asyncio.wait_for(_turn('一'), timeout=5)
        hist = (await _req('ghost', 'recall'))['history']
        assert [h['role'] for h in hist] == ['user', 'interrupted'], hist
        assert 'brain dead' in hist[1]['reason'], hist
        # 轮 2: 泵还活着 —— 照常想照常说, say 回执完整(迟到经 pending)
        await _turn('二')
        obs = await _wait_obs(
            lambda o: any(f['kind'] == 'action' and f.get('verb') == 'say'
                          for f in o))
    says = [f for f in obs if f['kind'] == 'action' and f.get('verb') == 'say']
    assert [s['result']['details']['words'] for s in says] == [4], obs
    print('t_interrupt_chain ok: pump alive, barrier released, reason kept')


async def t_verb_failure_isolated() -> None:
    """动作 routine 抛错不杀 lane —— 同 lane 后续动作照跑, 错误进帧."""
    _log.clear()

    class Boom(Routine):
        name = 'boom'

        async def run(self, kwargs):
            raise RuntimeError('boom')

        async def stop(self) -> None:
            pass

    class Ok(Routine):
        name = 'ok'

        async def run(self, kwargs):
            _log.append('ok')
            return {'content': '成了'}

        async def stop(self) -> None:
            pass

    FakeBrain.script = ['<main:boom/><main:ok/>', '这事黄了']
    async with _env(['boom', 'ok'], Boom, Ok):
        await _turn('干')
        obs = await _wait_obs(lambda o: any(
            f['kind'] == 'action' and f.get('verb') == 'ok' for f in o))
    # boom 失败 → error 帧带 owed 戳 → 点灯续一拍(错误也是回声:
    # "需要被注意的结果"), 模型当场知道失败; boom 挂不毒化同轨后续
    assert _log == ['ok'], _log
    # 失败帧(带 verb 指认) + 后续动作的结果帧都进史了
    assert any(f['kind'] == 'error' and f.get('verb') == 'boom' for f in obs), obs
    assert any(f['kind'] == 'action' and f.get('verb') == 'ok'
               and f.get('result') == {'content': '成了', 'details': {}}
               for f in obs), obs
    # 错误是结果的一种(Go 的 error 即值): 与成功帧同字段(verb/chan/rid),
    # 只换信封(kind)与载荷(text) —— 同一本账、同一缝合缝, 不是另一条
    # 控制流. rid 各自对号(两个动作两把), observe 声明集合落地时按它对账
    err = next(f for f in obs if f.get('kind') == 'error')
    act = next(f for f in obs if f.get('kind') == 'action'
               and f.get('verb') == 'ok')
    assert {'verb', 'chan', 'rid'} <= set(err), err
    assert {'verb', 'chan', 'rid'} <= set(act), act
    assert err['rid'] and act['rid'] and err['rid'] != act['rid'], obs
    # 错误自带注意债(owed), 成功没被认领则无债 —— 一帧一戳, 不另立账
    assert err['owed'] is True and not act.get('owed'), obs
    print('t_verb_failure_isolated ok: failed action framed, lane alive, '
          'error carries owed -> echo beat')


async def t_observe_claim() -> None:
    """observe="true" = 这次我要它的结果: 晚到(1.2s, 拍已收)也补一拍,
    第二拍从缝合缝看见回执; 未声明的成功不点灯(见 t_serial_parallel_aside).

    与原版的对齐关系: 那边 ``wait_tasks(to_be_observed=True)`` 是拍尾**真等**,
    我们撤了悬等, 所以这里不能叫 wait —— 它只是"回执到了点灯"(2026-10-20 改名).
    """
    _log.clear()
    HistBrain.hists.clear()
    HistBrain.script = ['<main:slow observe="true"/>', '收到, 等结果']
    async with _env(['slow'], brain=HistBrain):
        await _turn('跑个慢的')
        # 拍先收(close 不等在途), 1.2s 后回执迟到 → 灵魂敲裁判补灯
        deadline = time.perf_counter() + 10
        hist: list = []
        while time.perf_counter() < deadline:
            hist = (await _req('ghost', 'recall'))['history']
            if any(h.get('role') == 'assistant'
                   and '等结果' in str(h.get('text')) for h in hist):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(f'late debt did not light a beat: {hist}')
        # 债兑付而不是白开一拍: 回声拍看到的 history 里有 slow 的回执
        seen = HistBrain.hists[-1]
        assert any(h.get('role') == 'obs' and any(
            f.get('verb') == 'slow' for f in h.get('frames') or [])
            for h in seen), seen
    print('t_observe_claim ok: observe="true" late result lights a beat')


async def t_observe_value_guard() -> None:
    """``observe=`` 只认 ``"true"``: 别的值当场报错, 不许静默失效."""
    _log.clear()
    FakeBrain.script = ['<main:sleep duration="0.01" observe="always"/>', '收到']
    async with _env(['sleep']):
        await _turn('试')
        obs = await _wait_obs(lambda o: any(f['kind'] == 'error' for f in o))
    err = next(f for f in obs if f['kind'] == 'error')
    assert 'observe takes "true"' in str(err.get('text')), err
    assert err['owed'] is True, err
    print('t_observe_value_guard ok: bad value raises')


async def t_meta_observe() -> None:
    """动作 meta 声明 ``observe``: 调用方**不写 observe** 也有债 ——
    出厂声明与调用期声明同名(原版 :1667 ``observe or meta.always_observe``
    的 or 合并同构). 结果晚到同样补一拍, 回执回到模型眼前."""
    _log.clear()
    HistBrain.hists.clear()
    HistBrain.script = ['<main:slowobs/>', '天气收到了']
    async with _env(['slowobs'], SlowObs, brain=HistBrain):
        await _turn('查一下天气')
        deadline = time.perf_counter() + 10
        hist: list = []
        while time.perf_counter() < deadline:
            hist = (await _req('ghost', 'recall'))['history']
            if any(h.get('role') == 'assistant'
                   and '天气收到' in str(h.get('text')) for h in hist):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(
                f'meta observe did not light a beat: {hist}')
        # 债真兑付: 回声拍的 history 里有 slowobs 的回执帧
        seen = HistBrain.hists[-1]
        assert any(h.get('role') == 'obs' and any(
            f.get('verb') == 'slowobs' for f in h.get('frames') or [])
            for h in seen), seen
    print('t_meta_observe ok: meta declaration alone carries the debt')


async def t_bad_tag_and_unterminated() -> None:
    """坏标签/未闭合/词表外动词收成观测帧, 通道不死."""
    # 每轮后面跟一段空流: 协议违规帧带 owed 戳, 会点灯续一拍 ——
    # 空流那拍无动作, 收链(回声不放大)
    FakeBrain.script = ['<a:b:c/>', '', '<a:x', '']
    async with _env(['x', 'y', 'z']):
        await _turn('一')
        f1 = await _wait_obs(lambda o: any('malformed tag' in f['text']
                                           for f in o))
        await _turn('二')   # 未闭合在流收尾报
        f2 = await _wait_obs(lambda o: any('unterminated' in f['text']
                                           for f in o))
    assert any('malformed tag' in f['text'] for f in f1), f1
    assert any('unterminated' in f['text'] for f in f2), f2
    print('t_bad_tag_and_unterminated ok: bad input -> obs frames, channel alive')


async def t_bad_param_refused() -> None:
    """参数名写错当场拒, 文案给出可用参数名.

    原本的下场是一句静音: pydantic 丢掉多余字段, 投递"成功"、效果为零 ——
    模型以为设了 dur, 下一拍还在等一个不会发生的事实, 随后自己编一个结果.
    投递口按签名白名单判, 不给这中间留任何一格.
    """
    _log.clear()
    FakeBrain.script = ['<main:x bogus="1"/>', '']
    async with _env(['x', 'sleep']):
        await _turn('写错参数')
        frames = await _wait_all(
            lambda fs: any(f.get('kind') == 'error' for f in fs))
    err = next(f for f in frames if f.get('kind') == 'error')
    assert 'bogus' in err['text'], err
    assert '没有参数' in err['text'], err
    assert 'x.start' not in _log, _log      # 没被建起来跑(拒在投递口)

    # 签名里的参数照常放行(白名单不许紧成"什么都不收")
    _log.clear()
    FakeBrain.script = ['<main:sleep duration="0.1"/>', '']
    async with _env(['x', 'sleep']):
        await _turn('写对参数')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'sleep' for f in fs))
    assert not [f for f in frames if f.get('kind') == 'error'], frames
    print('t_bad_param_refused ok: unknown arg refused at the door')


async def t_stray_body_refused() -> None:
    """不收 body 的 routine 收到内容: 说清"没人读", 字节不投递.

    ``<x/>`` 与 ``<x></x>`` 是同一条路(见 ``body.py``), 空 body 不算错 ——
    只有真的来了字节, 才谈得上"内容放错了地方". 实例在开标签时已经建了,
    所以这里不是撤回, 而是**说清**: 这段没人读.
    """
    _log.clear()
    FakeBrain.script = ['<main:x></main:x>', '']
    async with _env(['x']):
        await _turn('空 body')
        await _wait_log('x.end')
        frames = await _all_frames()
    assert not [f for f in frames if f.get('kind') == 'error'], frames

    _log.clear()
    FakeBrain.script = ['<main:x>这段没人读</main:x>', '']
    async with _env(['x']):
        await _turn('有 body')
        frames = await _wait_all(
            lambda fs: any(f.get('kind') == 'error' for f in fs))
    err = next(f for f in frames if f.get('kind') == 'error')
    assert '不收 body' in err['text'], err
    assert 'x.end' in _log, _log            # 实例照常跑完(建在开标签时)
    print('t_stray_body_refused ok: body to a param-only verb is reported')


async def t_code_bad_kwarg_refused() -> None:
    """代码面同一道闸: 关键字写错当场抛给这一格(不让 Python 面比 CTML 面松)."""
    FakeBrain.script = [
        "<main:code>try:\n"
        "    await call('sleep', dur=0.1)\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__, 'dur' in str(exc))</main:code>", '']
    async with _env(['sleep']):
        await _turn('写错关键字')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    out = code['result']['details']['stdout'].strip()
    assert out.startswith('CallError'), code
    assert out.endswith('True'), code
    print('t_code_bad_kwarg_refused ok: unknown kwarg raises in the cell')


async def t_leaf_body_decoded() -> None:
    """叶子 body 的实体还原成真字符; 容器 body 不还原(那是下一层的源码).

    模型在标记语言里写 ``&lt;`` 是本能 —— code 的 body 是**源码**, 整条链
    若按字节原样透传, 进 Python 的就是 ``&lt;``, 当场 SyntaxError(实测:
    ``if abs(x-0.5) &lt;= 0.48`` -> invalid syntax)。所以按"body 到此为止"
    分两路: 叶子(say 的话 / code 的源码 / 黑板要写的字)还原, 容器不还原
    —— 父若还原, 一个转义就变成执行(注入)。
    """
    # ① 根上的 code: body 是源码
    FakeBrain.script = [
        "<main:code>print(1 &lt;= 2 and 3 &gt; 2)</main:code>", '']
    async with _env(['code']):
        await _turn('转义的小于号')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    out = (code['result'].get('details') or {}).get('stdout', '').strip()
    assert out == 'True', code

    # ② 容器里的 code: 叶子身份不因嵌套改变(容器转发时也还原)
    FakeBrain.script = [
        "<main:all><main:code>print('&lt;tag&gt;')</main:code></main:all>", '']
    async with _env(['code']):
        await _turn('容器里的转义')
        frames = await _wait_all(
            lambda fs: any(f.get('verb') == 'code' for f in fs))
    code = next(f for f in frames if f.get('verb') == 'code')
    out = (code['result'].get('details') or {}).get('stdout', '').strip()
    assert out == '<tag>', code

    # ③ 容器自己的 body 不解码: 转义过的标签仍是字面, 不会执行
    _log.clear()
    FakeBrain.script = ["<main:all>&lt;x/&gt;</main:all>", '']
    async with _env(['x']):
        await _turn('容器 body 里的转义标签')
        frames = await _all_frames()
    assert 'x.end' not in _log, _log          # 解了码就会跑起来 = 注入
    assert not [f for f in frames if f.get('kind') == 'error'], frames
    print('t_leaf_body_decoded ok: leaf bodies unescape, container bodies do not')


async def t_hot_registration() -> None:
    """词表增删: 运行期 shell registry 挂动词, 挂上即调、摘掉即拒."""
    _log.clear()

    class Ping(Routine):
        """一次性动作 routine: 热挂的对象."""

        name = 'ping'

        async def run(self, kwargs):
            _log.append('ping')
            return {}

        async def stop(self) -> None:
            pass

    # 轮 1 与轮 3 的协议违规帧带 owed(会续一拍), 各补一段空流
    FakeBrain.script = ['<main:ping/>', '', '<main:ping/>',
                        '<main:ping/>', '']
    async with _env(['x', 'y', 'z'], Ping):
        # 词表外: 协议违规当场暴露(观测帧, 不入轨)
        await _turn('一')
        assert any(f['kind'] == 'error' and 'unknown verb: ping' in f['text']
                   for f in await _wait_obs())
        # 增挂: 挂上即调, 返回快照可见
        snap = await _req('shell', 'registry', {'op': 'reg', 'routine': 'ping'})
        assert 'ping' in snap['verbs'], snap
        await _turn('二')
        obs = await _wait_obs(lambda o: any(
            f['kind'] == 'action' and f.get('verb') == 'ping' for f in o))
        assert _log == ['ping'], _log
        assert any(f['kind'] == 'action' and f.get('verb') == 'ping' for f in obs), obs
        # 摘挂: 摘掉即拒
        await _req('shell', 'registry', {'op': 'unreg', 'routine': 'ping'})
        await _turn('三')
        assert any(f['kind'] == 'error' and 'unknown verb: ping' in f['text']
                   for f in await _wait_obs())
    print('t_hot_registration ok: reg -> callable, unreg -> rejected')


async def t_hot_meta_rescan() -> None:
    """热挂也重扫 meta: 出厂声明是装配期快照, 不跟词表走就静默失效.

    名字进了词表、能被叫起, 但 ``observe`` 还停在快照上 —— 回执永远
    没债, 模型以为结果会自己回来, 结果等到下一届. 没人报错, 最难查.
    """
    _log.clear()

    class Quiet(Routine):
        """只声明"总要被看", 调用处一个字都不写."""

        name = 'quiet'
        meta = {'observe': True}

        async def run(self, kwargs):
            return {'content': '静默成功'}

        async def stop(self) -> None:
            pass

    FakeBrain.script = ['<main:quiet/>', '', '<main:quiet/>', '']
    async with _env([], Quiet):
        # 词表外: 叫不动(声明在不在都轮不到它)
        await _turn('一')
        assert any(f['kind'] == 'error' and 'unknown verb: quiet' in f['text']
                   for f in await _wait_obs())
        # 挂进词表 → 重扫, 出厂声明当场生效(不用写 observe="true")
        await _req('shell', 'registry', {'op': 'reg', 'routine': 'quiet'})
        await _turn('二')
        obs = await _wait_obs(lambda o: any(
            f.get('verb') == 'quiet' for f in o))
    frame = next(f for f in obs if f.get('verb') == 'quiet')
    assert frame['owed'] is True, frame
    print('t_hot_meta_rescan ok: reg rescans meta, observe applies')


async def t_chan_lifecycle() -> None:
    """轨道显式建: 默认 main 可用, 未建的轨当场拒, chan_create 开轨
    回执确认后(下一轮)新轨可用并行, 重复开轨幂等. 开轨回执自带注意债
    (出厂声明 observe: 回执是"新轨可用"的唯一凭据), 故开轨那拍
    之后必定多一帧回声 —— 模型当场看见开轨结果, 不用等用户再开口."""
    _log.clear()
    HistBrain.hists.clear()
    HistBrain.script = [
        '<main:x/><b:x/>',                       # 轮 1: b 未建
        '',                                       # 轮 1 回声: unknown lane 带 owed
        '<main:chan_create name="b"/>'
        '<main:chan_create name="b"/>',          # 轮 2: 开轨 ×2(幂等)
        '',                                       # 轮 2 回声: 开轨回执自带债
        '<b:x/><main:y/>',                       # 轮 3: b 轨并行
    ]
    async with _env(['x', 'y', 'z'], brain=HistBrain):   # chan_create 出厂注入, 名单不用带
        # 轮 1: main 预建直接可用; b 未建 → error 帧照常记账(obs 不驱动:
        # 无回声帧), 动作不执行
        await _turn('一')
        await _wait_log('x.end')
        # error 帧带 owed 会续一拍: 迟到回执被缝进更晚的帧, 扫全链找它
        deadline = time.perf_counter() + 5
        obs1: list = []
        while time.perf_counter() < deadline:
            obs1 = await _all_frames()
            if any(f['kind'] == 'error' and 'unknown lane: b' in f['text']
                   for f in obs1):
                break
            await asyncio.sleep(0.02)
        assert _log == ['x.start', 'x.end'], _log
        assert any(f['kind'] == 'error' and 'unknown lane: b' in f['text']
                   for f in obs1), obs1
        # 轮 2: chan_create 走 main 轨, 两次都成功(幂等), 回执带全量轨名
        await _turn('二')
        obs2 = await _wait_obs(lambda o: len([f for f in o
                                              if f['kind'] == 'action'
                                              and f.get('verb') == 'chan_create']) == 2)
        creates = [f for f in obs2 if f['kind'] == 'action'
                   and f.get('verb') == 'chan_create']
        assert len(creates) == 2, obs2
        # 回执一行就带全量轨名: 模型不必再问一次"现在有哪些轨"
        assert all('轨 b 就绪' in f['result']['content']
                   and 'b' in f['result']['content'] for f in creates), creates
        # 开轨回执点灯: 轮 2 共两拍(主拍 + 回声), 且回声那拍真读到了回执
        # —— 模型不写 observe 也知道新轨开好了(否则失败可见/成功不可见)
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline and len(HistBrain.hists) < 4:
            await asyncio.sleep(0.02)
        assert len(HistBrain.hists) == 4, len(HistBrain.hists)
        seen = HistBrain.hists[3]
        assert any(h.get('role') == 'obs' and any(
            f.get('verb') == 'chan_create' for f in h.get('frames') or [])
            for h in seen), seen
        # 轮 3: b 轨已开 —— y(main) 在 x(b) 还没跑完时开跑 = 并行
        _log.clear()
        await _turn('三')
        await _wait_log('x.end', 'y.end')
        assert _log.index('y.start') < _log.index('x.end'), _log
        assert not any(f['kind'] == 'error' for f in await _obs())
    print('t_chan_lifecycle ok: main default, unknown lane rejected, '
          'chan_create opens (idempotent) and its receipt lights a beat')


async def t_chan_admin() -> None:
    """chan 族三扇门: status 带运行态 / delete 只收空轨 / main 不许删.

    delete 的"只收空轨"不是洁癖: 队列非空时删轨会卡在 ``channel.stop()``
    的 ``_jobs.join()`` 上等活跑完 —— 那是挂住调用方, 不是回收. 所以有活
    当场拒绝, 并把下一步(先 clear)写进错误里.
    """
    _log.clear()
    FakeBrain.script = [
        '<main:chan_status/>',                              # 1 看轨(带运行态)
        '',                                                 # 1 回声
        '<main:chan_clear/>',                               # 2 缺 name → 拒
        '<main:chan_delete name="c"/>',                     # 2 空轨可删
        '',
        '<b:sleep duration="0.3"/><b:sleep duration="0.3"/>'
        '<main:chan_delete name="b"/>',                     # 3 b 有活 → 拒
        '',
        '<main:chan_delete name="main"/>',                  # 4 main 不许删
        '',
        '<c:x/>',                                           # 5 已删的轨投递
        '',
    ]
    async with _env(['x', 'y'], chans=['b', 'c']):
        for beat in ('一', '二', '三', '四', '五'):
            await _turn(beat)
        frames = await _all_frames()

    stats = [f for f in frames if f.get('verb') == 'chan_status']
    assert len(stats) == 1, stats
    lanes = {r['chan']: r for r in stats[0]['result']['details']['lanes']}
    assert set(lanes) >= {'main', 'b', 'c'}, lanes
    assert 'queued' in lanes['main'] and 'running' in lanes['main'], lanes
    dels = [f for f in frames if f.get('verb') == 'chan_delete']
    assert len(dels) == 3, dels
    ok = [f for f in dels if f['kind'] == 'action']
    assert len(ok) == 1 and ok[0]['result']['details']['deleted'] == 'c', ok
    assert 'c' not in ok[0]['result']['details']['lanes'], ok
    errs = [str(f.get('text') or '') for f in frames if f['kind'] == 'error']
    assert any('lane still busy' in t for t in errs), errs
    assert any('chan_clear needs name' in t for t in errs), errs
    assert any('main lane cannot be deleted' in t for t in errs), errs
    assert any('unknown lane: c' in t for t in errs), errs
    print('t_chan_admin ok: status carries runtime state, delete takes only '
          'idle lanes, main protected')


async def t_asr_intake() -> None:
    """耳朵(asr): 按礼仪交付 —— once 坐标下"定稿即申报"(源名 asr, 灵魂
    历史里渲染成 <listen source="asr">); 流式中间态(partial)不进脑; 自己出声期半双工
    丢麦克风(不然 TTS 会被自己听见, 回声变成新的一拍).

    耳朵本体(麦克风/WS/协议)不在这里测 —— 用 FakeEar 换掉工厂, 只
    测 routine 面(礼仪切换 / 申报 / 半双工 / 诊断)."""
    HistBrain.hists.clear()
    HistBrain.script = ['听到了呀', '不该有的拍']
    real_factory = Asr.ear_factory
    Asr.ear_factory = staticmethod(FakeEar)
    FakeEar.instances.clear()
    try:
        async with _env([], Pub, Asr, brain=HistBrain):
            ear = FakeEar.instances[-1]
            for _ in range(60):            # 等 run 起来(events() 已挂)
                if ear._q is not None:
                    break
                await asyncio.sleep(0.05)
            assert ear._q is not None, 'asr run 没起来'
            # 装配默认 ptt(按住才听): 本用例测的是 always 侧的入关策略
            assert (await _req('asr', 'mode',
                               {'mode': 'always'}))['listening'] is True
            # 切到 once: 首个分句即端点(否则 always 要等 1.5s 静音)
            etq = await _req('asr', 'etiquette', {'name': 'once'})
            assert etq['ok'] and etq['etiquette'] == 'once', etq
            # 中间态: 只记状态, 不开拍
            await ear.feed('partial', '今天天气')
            await asyncio.sleep(0.3)
            assert HistBrain.hists == [], HistBrain.hists
            assert (await _req('asr', 'status'))['partial'] == '今天天气'
            # 定稿分句: 一句话 → 交付 → 一拍
            await ear.feed('utterance', '今天天气怎么样')
            await _req('mindflow', 'idle')
            assert len(HistBrain.hists) == 1, HistBrain.hists
            seen = HistBrain.hists[0]
            assert any(h.get('role') == 'user'
                       and h.get('text') == '今天天气怎么样'
                       and h.get('source') == 'asr' for h in seen), seen
            # 半双工: say 出声 → 丢麦; 收声 → 恢复
            await _req('pub', 'pub', {'topic': 'output',
                                      'frame': {'kind': 'speech',
                                                'state': 'start'}})
            await _req('pub', 'pub', {'topic': 'output',
                                      'frame': {'kind': 'speech',
                                                'state': 'stop'}})
            for _ in range(40):
                if len(ear.muted) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert ear.muted[-2:] == [True, False], ear.muted
            st = await _req('asr', 'status')
            assert st['declares'] == 1 and st['dropped'] == 0, st
            assert st['listener']['etiquette'] == 'once', st
            # 判停即收流: 定稿当场让耳朵发尾包, 不等 3s 静音超时
            assert ear.commits >= 1, '礼仪判停没传到耳朵(commit)'
    finally:
        Asr.ear_factory = real_factory
    print('t_asr_intake ok: etiquette once -> utterance declares a beat, '
          'partial ignored, self-speech mutes the mic')


async def t_asr_mode() -> None:
    """`<asr_mode>`: 切耳朵听音模式; 状态留在耳朵里(指令退出不带走).

    always = 麦常开(现状); ptt = 默认闭嘴, 等按钮."""
    FakeBrain.script = ['<main:asr_mode mode="ptt" button="f9"/>', '']
    real_factory = Asr.ear_factory
    Asr.ear_factory = staticmethod(FakeEar)
    FakeEar.instances.clear()
    try:
        async with _env([], Pub, Asr, AsrMode):
            ear = FakeEar.instances[-1]
            for _ in range(60):
                if ear._q is not None:
                    break
                await asyncio.sleep(0.05)
            assert ear._q is not None, 'asr run 没起来'
            await _turn('切成按键说话')
            frames = await _wait_all(
                lambda fs: any(f.get('verb') == 'asr_mode' for f in fs))
            act = next(f for f in frames if f.get('verb') == 'asr_mode')
            res = act['result']
            # 载荷一句话: 切好了 + 怎么用; 原始字段在 details
            assert res['content'] == 'mode=ptt, 按住 f9 才听', res
            assert res['details'] == {'ok': True, 'mode': 'ptt',
                                      'button': 'f9',
                                      'listening': False}, res
            # 状态在耳朵里: 指令已退出, req status 仍看到 ptt
            st = await _req('asr', 'status')
            assert st['mode'] == 'ptt' and st['button'] == 'f9', st
            assert st['listening'] is False, st
            assert ear.muted and ear.muted[-1] is True, ear.muted
            # 切回 always: 麦重新常开(留一段没提交的先交掉是另一条用例)
            r = await _req('asr', 'mode', {'mode': 'always'})
            assert r == {'ok': True, 'mode': 'always', 'button': 'f9',
                         'listening': True}, r
            assert ear.muted[-1] is False, ear.muted
    finally:
        Asr.ear_factory = real_factory
        FakeEar.instances.clear()
        FakeBrain.script = []
    print('t_asr_mode ok: <asr_mode> sets ptt/always; state lives in the ear')


async def t_asr_ptt() -> None:
    """ptt: 按住才听, 松开即交付, 松手后的识别尾巴不再开新段.

    松开不走静音判停(那是 always 的语义) —— 用户松手就是"说完了"."""
    HistBrain.hists.clear()
    HistBrain.script = ['听到了']
    real_factory = Asr.ear_factory
    Asr.ear_factory = staticmethod(FakeEar)
    FakeEar.instances.clear()
    try:
        async with _env([], Pub, Asr, brain=HistBrain):
            ear = FakeEar.instances[-1]
            for _ in range(60):
                if ear._q is not None:
                    break
                await asyncio.sleep(0.05)
            assert ear._q is not None, 'asr run 没起来'
            # 静音判停拉长到 5s: 若交付发生了, 只可能是 release 干的
            spec = EtiquetteConfig().get('always').model_copy(deep=True)
            spec.name = 'slow'
            spec.stop.silence = 5.0
            await _req('asr', 'upsert_etiquette',
                       {'json': spec.model_dump_json()})
            await _req('asr', 'etiquette', {'name': 'slow'})
            r = await _req('asr', 'mode', {'mode': 'ptt', 'button': 'f9'})
            assert r['listening'] is False, r
            # 没按住: 识别事件不进策略层
            await ear.feed('partial', '不该听到')
            await asyncio.sleep(0.3)
            assert HistBrain.hists == [], HistBrain.hists
            # 按钮名对不上: 忽略(不是错误)
            bad = await _req('asr', 'press', {'button': 'other'})
            assert bad.get('ignored'), bad
            # 按下 → 说 → 松开: 立刻交付
            down = await _req('asr', 'press', {'button': 'f9'})
            assert down['ok'] and down['down'] is True, down
            await ear.feed('partial', '你好')
            await ear.feed('utterance', '你好呀')
            up = await _req('asr', 'release', {'button': 'f9'})
            assert up['down'] is False and up['result'] == 'committed', up
            await _req('mindflow', 'idle')
            assert len(HistBrain.hists) == 1, HistBrain.hists
            seen = HistBrain.hists[0]
            assert any(h.get('role') == 'user'
                       and h.get('text') == '你好呀' for h in seen), seen
            # 松手后服务端迟到的尾巴: 不再开新段/重复交付
            await ear.feed('utterance', '你好呀。')
            await asyncio.sleep(0.3)
            assert len(HistBrain.hists) == 1, HistBrain.hists
    finally:
        Asr.ear_factory = real_factory
        FakeEar.instances.clear()
        HistBrain.script = []
    print('t_asr_ptt ok: press listens, release commits, tail dropped')


class FakeWatcher:
    """假监听后端: 由用例手动给"按下/松开"边沿(真身是 pynput)."""

    instances: list = []

    def __init__(self, button: str, on_edge) -> None:
        self.button = button
        self.on_edge = on_edge
        self.started = False
        self.stopped = False
        FakeWatcher.instances.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    @classmethod
    def last(cls) -> 'FakeWatcher':
        assert cls.instances, 'watcher 没建'
        return cls.instances[-1]


class FastKeys(KeyListen):
    """测试键盘: 100ms 刷耳朵模式(真身 1s), 后端换成可手动触发的假监听."""

    name = 'key_listen'
    is_passive = PassiveConfig(kwargs={'refresh': 0.1})
    watcher_factory = staticmethod(FakeWatcher)
    backend_check = staticmethod(lambda: None)   # 替身后端, 不要求装 pynput


async def t_key_ptt() -> None:
    """键盘按钮源: 按下/松开两个**边沿**变成 asr 的 press/release.

    键名不在键盘 routine 里配 —— 它从耳朵读当前 button, 所以这里只切
    耳朵的模式, 键盘自己就跟上了(切回 always 还会把监听停掉)."""
    real_factory = Asr.ear_factory
    Asr.ear_factory = staticmethod(FakeEar)
    FakeEar.instances.clear()
    FakeWatcher.instances.clear()
    try:
        async with _env([], Pub, Asr, AsrMode, FastKeys):
            ear = FakeEar.instances[-1]
            for _ in range(60):
                if ear._q is not None:
                    break
                await asyncio.sleep(0.05)
            assert ear._q is not None, 'asr run 没起来'
            await _req('asr', 'mode', {'mode': 'ptt', 'button': 'f9'})
            for _ in range(80):            # 等键盘刷到 ptt 并建好监听
                if FakeWatcher.instances:
                    break
                await asyncio.sleep(0.05)
            w = FakeWatcher.last()
            assert w.button == 'f9' and w.started, w.button
            w.on_edge(True)                # 按下
            for _ in range(60):
                if (await _req('asr', 'status'))['listening']:
                    break
                await asyncio.sleep(0.05)
            assert (await _req('asr', 'status'))['listening'] is True
            assert ear.muted and ear.muted[-1] is False, ear.muted
            w.on_edge(False)               # 松开
            for _ in range(80):
                if not (await _req('asr', 'status'))['listening']:
                    break
                await asyncio.sleep(0.05)
            assert (await _req('asr', 'status'))['listening'] is False
            assert ear.muted[-1] is True, ear.muted
            # 切回 always: 键盘把监听停掉(不白占钩子)
            await _req('asr', 'mode', {'mode': 'always'})
            for _ in range(40):
                if w.stopped:
                    break
                await asyncio.sleep(0.05)
            assert w.stopped, 'always 模式还在监听键盘'
    finally:
        Asr.ear_factory = real_factory
        FakeEar.instances.clear()
        FakeWatcher.instances.clear()
    print('t_key_ptt ok: key edges drive asr press/release')


async def t_onset_absorb() -> None:
    """起话包(complete=False)与交付包(complete=True)同 id → mindflow 同 id
    吸收: 开口先占坑挂起(不调脑), 说完填坑才开拍 —— 同一句话只算一轮,
    历史里只出现一次(不会"起话叫一拍 + 交付再叫一拍"听两遍)."""
    HistBrain.hists.clear()
    HistBrain.script = ['在']
    real_factory = Asr.ear_factory
    Asr.ear_factory = staticmethod(FakeEar)
    FakeEar.instances.clear()
    try:
        async with _env([], Pub, Asr, brain=HistBrain):
            ear = FakeEar.instances[-1]
            for _ in range(60):
                if ear._q is not None:
                    break
                await asyncio.sleep(0.05)
            assert ear._q is not None, 'asr run 没起来'
            # 装配默认 ptt(按住才听): 本用例测的是 always 侧的"占坑→填坑"
            assert (await _req('asr', 'mode',
                               {'mode': 'always'}))['listening'] is True
            # 注册一个静音更短的 always 副本(测试提速), 并切过去。
            # 保留 onset.interrupt=True(barge-in): 原版两轴正交 ——
            # interrupt 只停身体, effort='normal' 照常回答, 所以
            # "占坑等尾包"与 interrupt 不冲突(controller 显式传
            # effort='normal'; 之前靠关掉 interrupt 绕过的分歧已修)
            spec = EtiquetteConfig().get('always').model_copy(deep=True)
            spec.name = 'fast'
            spec.stop.silence = 0.2
            r = await _req('asr', 'upsert_etiquette',
                           {'json': spec.model_dump_json()})
            assert r['ok'] and r['etiquette'] == 'fast', r
            assert (await _req('asr', 'etiquette',
                               {'name': 'fast'}))['etiquette'] == 'fast'
            # 起话: FATAL + interrupt 开链占坑, 挂起不想 → 不调脑
            await ear.feed('partial', '你好')
            await asyncio.sleep(0.15)
            assert HistBrain.hists == [], '起话包只占坑, 不该开拍'
            # 交付: 同 id 吸收填坑 → 这时才开一拍, 且只有这一拍
            await ear.feed('utterance', '你好呀')
            await _req('mindflow', 'idle')
            assert len(HistBrain.hists) == 1, HistBrain.hists
            seen = HistBrain.hists[0]
            users = [h for h in seen if h.get('role') == 'user']
            assert len(users) == 1, f'同 id 吸收后只有一条用户消息: {seen}'
            assert users[0].get('text') == '你好呀', users
            st = await _req('asr', 'status')
            assert st['listener']['onsets'] == 1, st
            assert st['listener']['delivers'] == 1, st
            assert st['declares'] == 2, st     # 起话 + 交付各一条
    finally:
        Asr.ear_factory = real_factory
    print('t_onset_absorb ok: onset occupies, deliver fills same id, one turn')


class ProbeNucleus(Nucleus):
    """测试核: 全字段缺省 + 同步治理(裁剪 / 空文本丢弃)."""

    name = 'probe'
    description = '测试核: 模拟一个外部传感器源'
    aggregate = False
    seen: list = []

    def defaults(self):
        return {'priority': int(Priority.CRITICAL), 'strength': 42,
                'mode': 'notify'}

    def govern(self, imp):
        type(self).seen.append(
            (int(imp.priority), imp.strength, imp.mode.value, imp.text))
        text = imp.text.strip()
        if not text:
            return None            # 原版"没东西可宣告"
        return imp.model_copy(update={'text': text})


class CoolNucleus(Nucleus):
    """测试核: 输了之后自报抑制冷却(原版 suppress_seconds)."""

    name = 'cool'
    description = '测试核: 抑制冷却'
    suppress_seconds = 1.0


async def t_nucleus() -> None:
    """感知源核: 一源一核提供缺省参数 + 同步治理 + 抑制冷却 + 自解释
    (原版 Nucleus 的声明面与治理面); 核没有自己的循环(群比仍在裁判
    循环单点打), 没注册核的源走裸参数(向后兼容)."""
    ProbeNucleus.seen.clear()
    NUCLEI.register(ProbeNucleus)
    NUCLEI.register(CoolNucleus)
    try:
        async with _env([], brain=StreamBrain):
            # 自解释面: 默认四核 + 新注册两核, 且核各自策略可见
            snap = await _req('mindflow', 'nuclei')
            names = {n['name'] for n in snap['nuclei']}
            assert {'user', 'asr', 'command', 'mindflow',
                    'probe', 'cool'} <= names, names
            mf = await _req('mindflow', 'nuclei', {'name': 'mindflow'})
            assert mf['ok'] and mf['nucleus']['aggregate'] is False, mf
            # 缺省值: 没给 priority/strength/mode 时用核的
            r = await _req('mindflow', 'declare',
                           {'source': 'probe', 'text': '  闹钟  '})
            assert r['verdict'] == 'accepted', r
            prio, strength, mode, text = ProbeNucleus.seen[-1]
            assert (prio, strength, mode) == (4, 42, 'notify'), \
                ProbeNucleus.seen[-1]
            assert text == '  闹钟  ', ProbeNucleus.seen[-1]  # 治理前原文
            # 显式值压过核缺省
            await _req('mindflow', 'declare',
                       {'source': 'probe', 'text': 'x', 'priority': 0,
                        'strength': 7})
            prio, strength, _, _ = ProbeNucleus.seen[-1]
            assert (prio, strength) == (0, 7), ProbeNucleus.seen[-1]
            # 治理丢弃(空文本) → dropped
            r = await _req('mindflow', 'declare',
                           {'source': 'probe', 'text': '   '})
            assert r['verdict'] == 'dropped', r
            # 未注册源: 裸参数照旧(核不是准入门槛)
            r = await _req('mindflow', 'declare',
                           {'source': 'ghost_src', 'text': 'hi',
                            'mode': 'notify'})
            assert r['verdict'] == 'accepted', r
            # 抑制冷却: 输一次后该源静默一段(原版 suppress_seconds)
            await _req('mindflow', 'idle')   # 前几条 notify 链先收干净
            asyncio.create_task(
                _req('mindflow', 'declare', {'text': '占住'}))
            await asyncio.sleep(0.15)      # 让这条开链(慢流 0.4s)
            await _req('mindflow', 'declare',
                       {'source': 'cool', 'text': '打岔', 'priority': 0})
            await asyncio.sleep(0.3)       # 等裁判判输 → on_suppress
            r = await _req('mindflow', 'declare',
                           {'source': 'cool', 'text': '再打岔',
                            'priority': 0})
            assert r['verdict'] == 'noop', r
            st = await _req('mindflow', 'nuclei', {'name': 'cool'})
            assert 'cooling' in st['nucleus']['status'], st
            await asyncio.sleep(1.1)       # 冷却过期
            r = await _req('mindflow', 'declare',
                           {'source': 'cool', 'text': '第三声',
                            'priority': 0})
            assert r['verdict'] == 'accepted', r
            await _req('mindflow', 'idle')
    finally:
        NUCLEI.unregister('probe')
        NUCLEI.unregister('cool')
    print('t_nucleus ok: per-source defaults, sync govern, suppress '
          'cooldown, self-describing nuclei')


class AttendProbe(Nucleus):
    """测试核: 记录上台调用, 并降级成 strength=0 —— 便于在链上观察到
    ``on_attended`` 的返回值确实被采用."""

    name = 'attend_probe'
    aggregate = False
    got: ClassVar[list] = []

    def defaults(self):
        return {'priority': int(Priority.INFO), 'strength': 100,
                'complete': True}

    def on_attended(self, imp):
        type(self).got.append((int(imp.priority), int(imp.strength)))
        return imp.model_copy(update={'strength': 0})


async def t_onset_nucleus() -> None:
    """起话专属规矩(原版 ``ListenerNucleus``): 首包提权 150 / 上台降回
    INFO+100 / 两层冷却只拦起话、交付包免疫 / 强度 TTL 120s; 并验证
    mindflow 在成链时真的问过 ``on_attended`` 且采用其返回值."""
    nuc = ListenerNucleus()
    onset = Impulse(source='asr', strength=150,
                    priority=Priority.WARNING, complete=False)
    deliver = Impulse(source='asr', strength=100,
                      priority=Priority.INFO, complete=True)

    # ① 首包提权由核钉死: 调用方给 100 也提回 first_strength
    pinned = nuc.govern(onset.model_copy(update={'strength': 100}))
    assert pinned is not None and pinned.strength == 150, pinned
    # ② 强度 TTL: 起话包 120s(原版 strength_decay_seconds), 不是通用 20s
    assert nuc.defaults()['strength_decay_seconds'] == 120.0, nuc.defaults()
    # ③ 起话专用的提权值不漏进交付包
    tail = nuc.govern(onset.model_copy(update={'complete': True,
                                               'strength': 150}))
    assert tail is not None and tail.strength == 100, tail

    # ④ 输了 → 大冷却(0.5s): 起话包被丢, 交付包照发
    nuc.on_suppress(onset, None)
    assert nuc.govern(onset.model_copy()) is None, '冷却期起话包该被丢'
    assert nuc.govern(deliver.model_copy()) is not None, '交付包不该受冷却'
    assert 'onset cooling' in nuc.status(), nuc.status()
    # ⑤ 冷却过期即恢复
    nuc._onset_suppress_until = 0.0
    assert nuc.govern(onset.model_copy()) is not None, '冷却过期后该放行'

    # ⑥ 赢了 → 降回运行档 + 小冷却(0.1s, 防同源下一个包立刻抢回)
    attended = nuc.on_attended(Impulse(
        source='asr', strength=150, priority=Priority.WARNING,
        complete=False))
    assert attended is not None, '提权档上台该被降级'
    assert (int(attended.priority), attended.strength) == (
        int(Priority.INFO), 100), attended
    assert nuc.govern(onset.model_copy()) is None, 'attended 冷却该拦住起话'
    assert nuc.govern(deliver.model_copy()) is not None, '交付仍不受限'
    # 已在运行档(INFO/100)的赢家不再改写
    assert nuc.on_attended(deliver.model_copy()) is None, '运行档不该被改写'

    # ⑦ 自解释面带上起话参数
    snap = nuc.snapshot()['onset']
    assert snap['first_strength'] == 150 and snap['normal_strength'] == 100, snap
    assert snap['suppress_seconds'] == 0.5 and snap['attended_seconds'] == 0.1, snap
    assert snap['strength_decay_seconds'] == 120.0, snap

    # ⑧ 成链时 mindflow 真的问了 on_attended, 且用的是它的返回值
    AttendProbe.got.clear()
    NUCLEI.register(AttendProbe)
    try:
        async with _env([], brain=StreamBrain):
            r = await _req('mindflow', 'declare',
                           {'source': 'attend_probe', 'text': 'A'})
            assert r['verdict'] == 'accepted', r
            await asyncio.sleep(0.15)      # 让这条成链(慢流 0.4s)
            assert AttendProbe.got == [(int(Priority.INFO), 100)], \
                f'成链没问 on_attended: {AttendProbe.got}'
            await _req('mindflow', 'idle')
    finally:
        NUCLEI.unregister('attend_probe')
    print('t_onset_nucleus ok: onset 150 pinned, attended downgrades to '
          'INFO/100, two-tier cooldown only blocks onset, 120s TTL')


async def t_sauc_protocol() -> None:
    """豆包协议帧(纯语法): init 带音频格式与行为参数; 音频帧 seq 递增,
    尾帧负序号 + 不压缩; 回包按 flags 位解出 seq/尾包/文本/定稿分句;
    错误包(int32 code)另解."""
    init = build_init('u1', sample_rate=16000)
    assert init[0] >> 4 == 1 and init[0] & 0x0F == 1, init[:4]
    assert init[1] >> 4 == 0x01, init[:4]          # full client request
    size = struct.unpack('>I', init[8:12])[0]
    loaded = json.loads(gzip.decompress(init[12:12 + size]))
    assert loaded['audio'] == {'format': 'pcm', 'codec': 'raw',
                               'rate': 16000, 'bits': 16,
                               'channel': 1}, loaded['audio']
    assert loaded['request']['enable_nonstream'] is False, loaded
    assert loaded['request']['enable_ddc'] is True, loaded
    assert loaded['request']['end_window_size'] == 800, loaded
    assert loaded['user']['uid'] == 'u1', loaded
    # 音频帧: seq 紧随 header; 尾帧负序号 + flags=3; 空 payload 不压缩
    a2 = build_audio(b'\x01\x02', 2)
    assert struct.unpack('>i', a2[4:8])[0] == 2 and a2[1] & 0x0F == 1, a2[:12]
    last = build_audio(b'', 9, is_last=True)
    assert struct.unpack('>i', last[4:8])[0] == -9 and last[1] & 0x0F == 3
    assert struct.unpack('>I', last[8:12])[0] == 0 and last[2] & 0x0F == 0
    # 服务端回包: header(flags=seq|last) + seq + size + gzip(json)
    body = {'result': {'text': '你好', 'utterances': [
        {'text': '你好', 'definite': True, 'start_time': 0, 'end_time': 800},
        {'text': '在吗', 'definite': False}]}}
    msg = gzip.compress(json.dumps(body, ensure_ascii=False).encode('utf-8'))
    frame = (bytes([0x11, (0x09 << 4) | 0x03, 0x11, 0x00])
             + struct.pack('>i', 5) + struct.pack('>I', len(msg)) + msg)
    r = parse_response(frame)
    assert r.code == 0 and r.is_last and r.seq == 5, r
    assert r.text == '你好' and len(r.utterances) == 2, r
    assert r.utterances[0]['definite'] is True, r
    # 错误包: 0x0F + code(int32) + size + 原文(明文 JSON, 不压缩)
    err = b'{"error":"bad"}'
    eframe = (bytes([0x11, (0x0F << 4), 0x10, 0x00])
              + struct.pack('>i', 45000001) + struct.pack('>I', len(err))
              + err)
    e = parse_response(eframe)
    assert e.code == 45000001 and 'bad' in e.error, e
    print('t_sauc_protocol ok: init/audio frames build, responses parse')


async def t_ear_keepalive() -> None:
    """段内不断流: 发送泵在队列空时补 200ms 静音包(开口之后才补).

    服务端按包推进时间轴, 断口会被当成句尾; 段内补静音把它和"连接空闲"
    解耦。**待命期(没开口/已判停/松手)一包都不发** —— 那时该收流, 见
    ``t_ear_upload_on_demand``。有块时必须是块, 不许拿静音顶替真实音频
    (否则用户说话被吞).
    """
    import numpy as np

    from ..asr.ear import Ear

    def pcm_of(frame: bytes) -> bytes:
        assert frame[1] >> 4 == 0x02, frame[:4]   # AUDIO_ONLY_REQUEST
        size = struct.unpack('>I', frame[8:12])[0]
        payload = frame[12:12 + size]
        return gzip.decompress(payload) if payload else b''

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list = []

        async def send(self, data: bytes) -> None:
            self.sent.append(data)

    # ① 段内(刚说过话)队列空: 每 200ms 补一包 6400B(3200 采样@16k)全零, seq 连号
    ear = Ear(api_key='x')
    ear._q = asyncio.Queue(maxsize=4)
    ear._last_sound = time.monotonic()   # 段内: 不是待命(待命期一包都不发)
    ear._streaming = True
    ws = FakeWS()
    pump = asyncio.ensure_future(ear._send(ws))
    await asyncio.sleep(0.55)
    pump.cancel()
    try:
        await pump
    except asyncio.CancelledError:
        pass
    assert len(ws.sent) >= 2, len(ws.sent)
    assert ear.stats['silence'] == len(ws.sent), ear.stats
    for f in ws.sent:
        pcm = pcm_of(f)
        assert len(pcm) == 6400 and not any(pcm), len(pcm)   # 200ms 静音
    seqs = [struct.unpack('>i', f[4:8])[0] for f in ws.sent]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), seqs

    # ② 有块时发块(不插静音): 首包就是采集块本身
    ear2 = Ear(api_key='x')
    ear2._q = asyncio.Queue(maxsize=4)
    ear2._q.put_nowait(np.ones(1600, dtype=np.int16))
    ws2 = FakeWS()
    pump2 = asyncio.ensure_future(ear2._send(ws2))
    await asyncio.sleep(0.1)
    pump2.cancel()
    try:
        await pump2
    except asyncio.CancelledError:
        pass
    first = pcm_of(ws2.sent[0])
    assert len(first) == 3200 and any(first), first[:8]   # 1600 采样块
    assert ear2.stats['silence'] == 0, ear2.stats
    print('t_ear_keepalive ok: silence packets keep the stream alive, '
          'real blocks never replaced')


async def t_say_and_bare_text() -> None:
    """文字与出声分离: 裸文本 = 旁白(final answer, 无帧); 出声走 say
    (跨 chunk 增量); 同轨连续 say 按 FIFO 串行. 快 say 回执也不触发
    回声帧("听见自己说话"病根拔除 —— obs 只记账不驱动)."""
    HistBrain.hists.clear()
    HistBrain.script = ['<main:say>你好呀朋友</say>\n嗯呐\n<main:say>嗯</say>']
    async with _env([], brain=HistBrain):   # say 出厂在册, 名单为空也能说
        await _turn('干')
        obs = await _wait_obs(lambda o: any(
            f['kind'] == 'action' and f.get('verb') == 'say'
            and f['result']['details'].get('words') == 1 for f in o))
    # 出声的两段完整收流(同轨 FIFO 序, 回执只报字数不复读内容);
    # '嗯呐' 是旁白 —— 不产帧; 单拍收链(say 回执不驱动回声帧)
    assert len(HistBrain.hists) == 1, HistBrain.hists
    says = [f for f in obs if f['kind'] == 'action' and f.get('verb') == 'say']
    assert [s['result']['details']['words'] for s in says] == [5, 1], obs
    assert all('said' not in s['result']['details'] for s in says), obs
    assert not [f for f in obs if f['kind'] == 'error'], obs
    print('t_say_and_bare_text ok: aside silent, say speaks, '
          'no echo beat from receipts')


async def t_say_two_streams() -> None:
    """分轨双 say 流: 分轨同时, 两路都完整回执(一张嘴的物理播放
    约束留 L1 real 引擎的共享播放队列)."""
    FakeBrain.script = ['<a:say>左路说</say><b:say>右路说</say>']
    async with _env([], chans=['a', 'b']):
        await _turn('干')
        obs = await _wait_obs(lambda o: len([f for f in o
                                             if f['kind'] == 'action'
                                             and f.get('verb') == 'say']) == 2)
    says = [f for f in obs if f['kind'] == 'action' and f.get('verb') == 'say']
    assert [s['result']['details']['words'] for s in says] == [3, 3], obs
    print('t_say_two_streams ok: lanes parallel, both streams receipted')


async def t_stream_start_scheduled() -> None:
    """开标签只 create, start 由轨调度: 同轨前序动作没跑完, say 不
    开口 —— FIFO 对流式与原子一视同仁(实例在轨手里, 不越权插队)."""
    _log.clear()
    FakeBrain.script = ['<main:x/><main:say>看我的手</say>']
    async with _env(['x', 'y', 'z'], SayProbe):
        await _turn('干')
        obs = await _wait_obs(lambda o: any(
            f['kind'] == 'action' and f.get('verb') == 'say'
            and f['result']['details'].get('words') == 4 for f in o))
    # x(0.15s) 没跑完, say 不 start —— 排队期间的增量/eof 已被实例
    # (created 态)收好, 轨轮到它时一把收割
    assert _log.index('say.run') > _log.index('x.end'), _log
    says = [f for f in obs if f['kind'] == 'action' and f.get('verb') == 'say']
    assert [s['result']['details']['words'] for s in says] == [4], obs
    print('t_stream_start_scheduled ok: stream start scheduled by lane FIFO')


@asynccontextmanager
async def _env2(brain: type, max_moments: int = 100):
    kref = await kernel.create({})
    rs = Routines()
    Ghost.brain_factory = brain   # 换脑注入(与 Say.engine_factory 同构)
    rs.register(_moss([], max_moments), Ghost, Shell, Channel, Sim, Say,
                Mindflow)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        await asyncio.sleep(1.5)
        yield
    finally:
        await conn.close()
        Ghost.brain_factory = None   # 复位, 不跨用例泄漏


class StreamBrain:
    """慢流假脑: 每拍先慢吐空块制造仲裁窗口, 记录每拍看到的最新
    user text(= 桥梁投喂的输入)."""

    inputs: list = []
    chunks: int = 20   # 慢流块数(~0.4s 仲裁窗口)

    async def stream(self, history, system):
        StreamBrain.inputs.append(
            next((h['text'] for h in reversed(history)
                  if h.get('role') == 'user'), None))
        for _ in range(self.chunks):
            yield ''
            await asyncio.sleep(0.02)
        yield '好'


class XYBrain:
    """标签流脑: 立刻吐两条原子标签(x 在跑 0.15s, y 在队), 之后
    频繁空块撑住流不收尾 —— 打断收尾测试用: 中途 interrupt 时
    流仍开着, 收尾走 abort 路径(未 start 的 y 应被丢弃)."""

    async def stream(self, history, system):
        yield '<x/><y/>'
        for _ in range(100):
            yield ''
            await asyncio.sleep(0.02)   # 密集空块: abort 检查点频繁


class MultiLaneBrain:
    """多轨标签流脑: 一拍内在三条轨各起动作(main: slow 在跑 + y 排队,
    a: x, b: z), 之后频繁空块撑住流不收尾 —— 跨轨并行打断用: 收尾时
    三条轨同时在册, 并行的 abort 请求都要打到各自轨上."""

    async def stream(self, history, system):
        yield '<main:slow/><main:y/><a:x/><b:z/>'
        for _ in range(100):
            yield ''
            await asyncio.sleep(0.02)


async def _declare(text: str, **kw) -> dict:
    """带仲裁参数的 declare(priority/strength/mode/id/complete/...)."""
    return await _req('mindflow', 'declare', {'text': text, **kw})


async def t_attention_unit() -> None:
    """Attention 仲裁单元(照抄原版 AbsAttention/BaseAttention): 分支序
    + 强度线性衰减 + 同源提权 + 保护期 + 首包吸收."""
    a = Attention(Impulse(id='u1', source='ear', strength=100,
                          strength_decay_seconds=0.5, complete=False))
    # FATAL 永远赢; 更低优先级输
    assert a.challenge(
        Impulse(id='x', source='eye', priority=Priority.FATAL)) == 'win'
    assert a.challenge(
        Impulse(id='x', source='eye', priority=Priority.INFO)) == 'lose'
    # stale 最先短路(任何仲裁之前)
    assert a.challenge(
        Impulse(id='x', source='eye', stale_timeout=0.01)) == 'lose'
    await asyncio.sleep(0.05)
    # 同 id 未 complete → absorb; 折进后 complete 更新, 再来不吸收
    tail = Impulse(id='u1', source='ear', text='全量', complete=True)
    assert a.challenge(tail) == 'absorb'
    assert a.absorb_impulse(tail) is None
    assert a.impulse().complete is True
    assert a.challenge(Impulse(id='u1', source='ear')) == 'lose'

    # 保护期: 同级更强也输(与强度无关, 防抖)
    b = Attention(Impulse(id='v1', source='ear', strength=100,
                          protection_time=5))
    assert b.is_protected()
    assert b.challenge(
        Impulse(id='v2', source='eye', strength=300)) == 'lose'

    # 强度衰减: decay 0.5s(保护期 0.1s), 等待后已衰减未归零
    c = Attention(Impulse(id='w1', source='ear', strength=100,
                          strength_decay_seconds=0.5))
    await asyncio.sleep(0.2)
    s = c.current_strength()
    assert 40 < s < 100, s
    assert c.challenge(
        Impulse(id='w2', source='eye', strength=s + 30)) == 'win'
    assert c.challenge(
        Impulse(id='w3', source='eye', strength=max(s - 30, 1))) == 'lose'
    # 同源提权 ×1.1: 强度落在 (s/1.1, s) 开区间 —— 同源赢, 异源输
    t = int((s + s / Attention.SOURCE_ESCALATION) / 2)
    assert c.challenge(
        Impulse(id='w4', source='ear', strength=t)) == 'win'
    assert c.challenge(
        Impulse(id='w5', source='eye', strength=t)) == 'lose'
    print('t_attention_unit ok: verdict order, decay curve, '
          'source escalation, protection')


async def t_preempt() -> None:
    """抢占(照抄原版 preempted → abort('interrupted')): FATAL 挑战进行
    中的链 → 在途拍在检查点断(reason 留痕), 挑战者开新链; declare 即回
    并返回判定."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('慢慢说'))
        await asyncio.sleep(0.15)   # 进流中(检查点窗口)
        r = await asyncio.wait_for(_declare('急停', priority=5), timeout=5)
        assert r['verdict'] == 'accepted', r   # 受理: 仲裁在拉取时打
        await asyncio.wait_for(t1, timeout=5)   # idle 等两条链都收完
        hist = (await _req('ghost', 'recall'))['history']
    assert StreamBrain.inputs == ['慢慢说', '急停'], StreamBrain.inputs
    assert [h['role'] for h in hist] == [
        'user', 'interrupted', 'user', 'assistant'], hist
    assert hist[1]['reason'] == 'interrupted', hist
    print('t_preempt ok: fatal preempts, in-flight beat aborted with '
          'trace, challenger opens new chain')


async def t_defer_silent() -> None:
    """还没开口的拍不被普通输入抢走: 抢占的对象是"它正在说的话", 不是
    "它还没想完的脑子".

    首字延迟可以有八九秒(整段 prompt + 记忆), 用户看不到东西就会再补
    一句"?"。补话若按抢占处理, 原拍在第一个检查点(首字到了)就死, 一个
    字都没说过 —— 两条消息一起白等(2026-10-21 01:49 实测: 首字 8.3s,
    "?"+"666" 把整拍打断, 只回了一句"刚才被打断了").

    StreamBrain 头 20 块是空块(0.4s, 等于"还在想"), 此时普通 declare 必须
    **排队不抢**: 两拍都调过脑, 记忆里没有 interrupted 留痕. 对照用例:
    t_preempt(FATAL)与 t_barge_in(interrupt=True)照旧抢占 —— 那是"停"的
    语义承诺, 不是竞争."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('第一句'))
        await asyncio.sleep(0.15)      # 空块期: 字流还没投过一个字
        r = await asyncio.wait_for(_declare('补一句'), timeout=5)
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)
        hist = (await _req('ghost', 'recall'))['history']
    # 原拍没被打断: 两句各自成拍(同源聚合只能折进同一巡, 这里隔了 0.15s)
    assert StreamBrain.inputs == ['第一句', '补一句'], StreamBrain.inputs
    assert 'interrupted' not in [h['role'] for h in hist], hist
    print('t_defer_silent ok: a beat that has not spoken yet is not '
          'preempted; the extra line queues as the next beat')


async def t_notify_buffer() -> None:
    """仲裁失败不丢消息(原版 inject_percepts 同构): BACKGROUND+notify
    挂账, 当前链收尾后无人接盘 → 合成链消化(need_observe 驱动下一帧
    同构). idle 等的就是这个消化."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)
        r = await asyncio.wait_for(
            _declare('低优', priority=-1, mode='notify'), timeout=5)
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)
    assert StreamBrain.inputs == ['主话', '低优'], StreamBrain.inputs
    print('t_notify_buffer ok: buffered message digested by follow-up '
          'chain, never lost')


async def t_next_mode() -> None:
    """next 插队(照抄原版 queued): 挑战失败但当前链强制多看一拍,
    插队消息进该拍."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)
        r = await asyncio.wait_for(_declare('插队', mode='next'), timeout=5)
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)
    assert StreamBrain.inputs == ['主话', '插队'], StreamBrain.inputs
    print('t_next_mode ok: next forces an extra beat for the message')


async def t_absorb_partial() -> None:
    """首包-尾包(照抄原版 absorb + wait_ready): complete=False 开链
    挂起不想, 同 id 尾包吸收才开拍(粘包归源: 尾包带全量); strength=0
    绝不竞争(yielded)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        r = await _declare('前半句', id='u1', complete=False)
        assert r['verdict'] == 'accepted', r
        await asyncio.sleep(0.1)
        assert StreamBrain.inputs == []   # 挂起: 占链不想
        # strength=0: 受理但绝不竞争 —— 无痕即回
        r = await asyncio.wait_for(_declare('静默', strength=0), timeout=3)
        assert r['verdict'] == 'accepted', r
        assert StreamBrain.inputs == []
        r = await _declare('前半句后半句全量', id='u1')
        assert r['verdict'] == 'accepted', r   # 尾包: rank 快车道吸收
        await _req('mindflow', 'idle')
        hist = (await _req('ghost', 'recall'))['history']
    assert StreamBrain.inputs == ['前半句后半句全量'], StreamBrain.inputs
    assert [h['role'] for h in hist] == ['user', 'assistant'], hist
    print('t_absorb_partial ok: partial hangs, tail absorbs, '
          'zero-strength yields')


async def t_effort_from_tail() -> None:
    """空拍判据读**尾包**(原版 ``_think.effort()`` =
    ``attention.impulse().thinking_effort``): 起话包自带
    effort='none'(interrupt primitive 的产物) 不许传染给回应拍 ——
    尾包才带这次要回应的全量; 反过来尾包自己声明 none 才是"停而不答".

    旧实现读首包: 裸写 interrupt=True 的起话包(declare 按 primitive
    补 effort='none')会把整段对话打成空拍 —— 一句都不答."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        # 起话包照 primitive 写了 none, 尾包(交付)才有正文
        r = await _declare('前半句', id='u1', complete=False,
                           interrupt=True, effort='none')
        assert r['verdict'] == 'accepted', r
        await asyncio.sleep(0.1)
        assert StreamBrain.inputs == []      # 挂起: 半句话不开脑子
        r = await _declare('前半句后半句', id='u1')
        assert r['verdict'] == 'accepted', r
        await _req('mindflow', 'idle')
    assert StreamBrain.inputs == ['前半句后半句'], StreamBrain.inputs

    # 尾包自己声明 effort='none' → 只开帧留痕, 不回答
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        r = await _declare('只留痕', effort='none')
        assert r['verdict'] == 'accepted', r
        await _req('mindflow', 'idle')
    assert StreamBrain.inputs == [], StreamBrain.inputs
    print('t_effort_from_tail ok: 起话包的 none 不传染, 尾包 none 才不答')


async def t_suppress_into_hanging() -> None:
    """default 输家 suppress 回检录缓冲(原版 ``nucleus.suppress`` 缓冲
    重试同构): 挂起链的 INFO 挑战者输 → 不挂账不陪葬, 回缓冲重赛;
    尾包开拍的本届结束后, 它独立成链 —— 消息不丢."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        await _declare('首句', id='u1', complete=False)
        await asyncio.sleep(0.1)
        r = await _declare('旁白', priority=0)   # INFO < NOTICE: 必输
        assert r['verdict'] == 'accepted', r
        r = await _declare('首句全量', id='u1')
        assert r['verdict'] == 'accepted', r
        await _req('mindflow', 'idle')
    assert StreamBrain.inputs == ['首句全量', '旁白'], StreamBrain.inputs
    print('t_suppress_into_hanging ok: loser waits in buffer, gets its '
          'own chain after the suspended term')


async def t_quiet_aside_buffered() -> None:
    """静默态 aside(bug 回归): 无链时 aside 只挂账不建链 —— 旧版把
    调用方 future 挂进无人释放的列表, 下一条链直接覆盖 = 调用方
    永挂; declare 即回后此路自愈. 消息不丢: 下一条输入开链时折进首拍."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        r = await asyncio.wait_for(_declare('旁资', mode='aside'), timeout=3)
        assert r['verdict'] == 'accepted', r
        await asyncio.sleep(0.1)
        assert StreamBrain.inputs == []   # 不建链: 系统仍静默
        await _turn('正事')               # 下一条输入开链, 折进旁资
    assert StreamBrain.inputs == ['正事\n旁资'], StreamBrain.inputs
    print('t_quiet_aside_buffered ok: quiet aside returns at once, '
          'folded into the next chain')


async def t_partial_mixed_queue() -> None:
    """混队聚合(bug 回归): 完整消息与首包同窗 —— 各走各路, 消息不丢
        不重复, idle 不挂死. 原版 nucleus 每巡只出一条: 首尾同窗在
        fold 批内配对(尾包带全量), 双双输给现任 → suppress 回缓冲,
        本届结束后独立成链(裁判在哪个节拍拉取都同一结局, 免序依赖)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        # 完整话先成链; 首包+尾包随后在途 —— 同窗配对, 尾包全量被思考
        await _declare('完整话')
        await asyncio.sleep(0.1)   # 完整话已在途(占住注意力)
        await asyncio.gather(
            _declare('语音首包', id='v1', complete=False),
            _declare('语音首包语音尾包全量', id='v1'))
        await _req('mindflow', 'idle')   # 旧版在此永挂(idle 超时)
        assert StreamBrain.inputs == [
            '完整话', '语音首包语音尾包全量'], StreamBrain.inputs
        # 再来一遍: 另一条先成链, 首包+尾包在途同结论
        await _declare('另一条')
        await asyncio.sleep(0.1)
        await asyncio.gather(
            _declare('语音首包', id='v2', complete=False),
            _declare('全量二', id='v2'))
        await _req('mindflow', 'idle')
    assert StreamBrain.inputs == [
        '完整话', '语音首包语音尾包全量', '另一条', '全量二'], \
        StreamBrain.inputs
    print('t_partial_mixed_queue ok: no fold across partials, '
          'no hang, no duplication')


async def t_absorb_beats_protection() -> None:
    """保护期不挡吸收(bug 回归): 挂起链带保护期, 同 id 尾包在保护
    期内到达 —— 旧版走保护期分支 suppressed, 挂起链等的尾包进了
    挂账, 无物可唤醒 = 永挂; 吸收提到保护期之前(原版靠 nucleus
    缓冲重试兜底, 塌缩版无 nucleus, 这是替身)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        await _declare('首包', id='u1', complete=False, protection_time=5)
        await asyncio.sleep(0.05)   # 保护期内
        r = await _declare('首包全量', id='u1')
        assert r['verdict'] == 'accepted', r   # 保护期内照样吸收(快车道)
        await _req('mindflow', 'idle')         # 旧版在此永挂
    assert StreamBrain.inputs == ['首包全量'], StreamBrain.inputs
    print('t_absorb_beats_protection ok: tail absorbs even inside '
          'protection window')


class RecScriptBrain(FakeBrain):
    """假脑 + 输入记录: 每拍记录桥梁投喂的最新 user text."""

    inputs: list = []
    script = ['<a:x/>', '']   # 主话拍 + 配对链拍

    async def stream(self, history, system):
        RecScriptBrain.inputs.append(
            next((h['text'] for h in reversed(history)
                  if h.get('role') == 'user'), None))
        async for c in super().stream(history, system):
            yield c


async def t_suppressed_partial_no_dup() -> None:
    """未完输入不重复(替身规则回归): 在途链让首包/尾包双双输给现任
    → suppress 回检录缓冲; 本届(主话拍 —— x 回执不驱动, 无回声拍)
    结束后同 id 首尾在 rank 批内配对, 独立成链 —— 前半句被尾包全量
    吃掉, 模型看到的输入只有一份全量('半截' 永不单独出现)."""
    RecScriptBrain.inputs.clear()
    async with _env(['x'], brain=RecScriptBrain, chans=['a']):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.1)   # beat1 在途(x 跑 0.15s)
        r = await _declare('半截', id='v1', complete=False)
        assert r['verdict'] == 'accepted', r
        r = await _declare('半截全量', id='v1')
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)   # 本届+配对链都收完
    assert RecScriptBrain.inputs == ['主话', '半截全量'], \
        RecScriptBrain.inputs   # 关键: '半截' 不在(不与全量重复),
    # 且无回声拍(x 回执只入史, inputs[1] 直接是配对链)
    print('t_suppressed_partial_no_dup ok: losing partial dropped, '
          'tail injected once')


async def t_say_engine() -> None:
    """引擎三点契约: delta 即喂(边到边, 首喂早于 finish), eof 收流,
    播完才回执; 一张嘴: 分轨双 say 并行调度但播放不重叠(先到先说)."""
    _RecEngine.events.clear()
    _RecPlayer.plays.clear()
    FakeBrain.script = ['<a:say>左路</say><b:say>右路</say>']
    async with _env([], SayTuned, chans=['a', 'b']):
        await _turn('干')
        obs = await _wait_obs(lambda o: len([f for f in o
                                             if f['kind'] == 'action'
                                             and f.get('verb') == 'say']) == 2)
    says = [f for f in obs if f['kind'] == 'action' and f.get('verb') == 'say']
    assert [s['result']['details']['words'] for s in says] == [2, 2], obs
    # 边到边: 首 feed 早于 finish(delta 到达即喂, 不攒到 eof)
    feeds = [t for k, t in _RecEngine.events if k == 'feed']
    fins = [t for k, t in _RecEngine.events if k == 'finish']
    assert feeds and fins and feeds[0] < fins[0], _RecEngine.events
    # 一张嘴: 两段播放串行(后段开始 >= 前段结束) —— close 不等播放,
    # 拍收早于播完是"指令结果不触发"的自然结果
    assert len(_RecPlayer.plays) == 2, _RecPlayer.plays
    (a0, a1), (b0, b1) = _RecPlayer.plays
    assert b0 >= a1, _RecPlayer.plays
    print('t_say_engine ok: feed streaming, plays serialized, receipted')


class SeenBrain:
    """记史假脑: 每拍记录所见的整份 history(滑窗观测). 裸文本 =
    旁白无 obs → 每轮单拍(user + assistant 两条入记忆)."""

    seen: list = []

    async def stream(self, history, system):
        SeenBrain.seen.append([dict(h) for h in history])
        yield '好'


class RecapBrain(SeenBrain):
    """记史 + 摘要假脑: epoch 调用(system 是摘要指令)回一段 recap;
    正常思考回旁白. 以 system 关键词区分两路."""

    async def stream(self, history, system):
        if '压缩' in (system or ''):
            yield '前情: 用户打了招呼并问了天气.'
            return
        async for chunk in super().stream(history, system):
            yield chunk


async def t_memory_window() -> None:
    """记忆滑窗(照抄原版 BaseMomentsObserver deque max_moments_size):
    **按帧计窗** —— 一拍一帧, 超窗丢最旧帧, 出窗即忘; 脑子 feed 与
    recall 读到的是同一份回合制视图(to_history_turns); 不修齐回合
    边界(原版 to_history_turns 同样可能让首回合残头, 残头是已知
    代价不是缺陷)."""
    # 单元: 帧窗语义(丢最旧帧 + 出窗计数 + 帧视图)
    m = Moments(3)
    for i in range(5):
        f = m.new_moment({'user': str(i)})
        m.commit_logos(f, f'答{i}')
    turns = m.history_turns()
    assert [e['text'] for e in turns if e['role'] == 'user'] == [
        '2', '3', '4'], turns
    assert m.dropped == 2 and len(m) == 3, (m.dropped, len(m))
    fv = m.frames_view()
    assert len(fv) == 3 and fv[-1]['percepts'] == {'user': '4'}, fv
    assert fv[-1]['previous'] == {'frames': [], 'stop_reason': ''}, fv
    assert 'dynamic_context' not in fv[-1], fv   # 瞬态不入持久视图

    # 集成: 小窗(3 帧)跑 4 轮(裸文本无 obs → 每轮单拍单帧) → 4 帧入
    # 窗 3, '一' 出窗即忘; 末拍 feed 与窗口同一份(6 条 = 3 帧 × 2)
    SeenBrain.seen.clear()
    async with _env2(SeenBrain, max_moments=3):
        for t in ('一', '二', '三', '四'):
            await _turn(t)
        r = await _req('ghost', 'recall')
    hist = r['history']
    assert r['dropped'] == 1 and r['epochs'] == 0, r
    assert [h['role'] for h in hist] == [
        'user', 'assistant'] * 3, hist
    texts = [h['text'] for h in hist if h['role'] == 'user']
    assert texts == ['二', '三', '四'], texts   # '一' 已出窗即忘
    # 脑子看到的就是 recall 读到的同一份: 末拍 feed = 3 帧渲染(在途帧
    # 的 logos 未落 = 只有 user, 5 条); 收拍后 recall = 6 条(在途帧
    # logos 已落) —— 同一份帧链, 差的只是拍还没结束)
    assert len(SeenBrain.seen[-1]) == 5, len(SeenBrain.seen[-1])
    assert [h['role'] for h in SeenBrain.seen[-1]] == [
        'user', 'assistant'] * 2 + ['user'], SeenBrain.seen[-1]
    assert not any(h.get('role') == 'user' and h.get('text') == '一'
                   for h in SeenBrain.seen[-1]), SeenBrain.seen[-1]
    print('t_memory_window ok: bounded by frames, oldest dropped, '
          'brain and recall share the same turns')


async def t_transient_state() -> None:
    """瞬态快照(原版 dynamic_context + percepts 同源覆盖合一): 同源
    新包覆盖旧包, 永不进记忆正文(历史零膨胀), 脑子每拍尾拼看到各源
    最新"现在"; 仲裁输了也最终入槽(瞬态挂账, 原版 percepts buffer
    下一帧仍进 moment 同构)."""
    # 单元: 覆盖 + 不入史 + feed 尾拼
    m = Moments(5)
    f = m.new_moment({'user': '你好'})
    m.commit_logos(f, '好')
    m.set_dynamic({'power': '电量70%'})
    m.set_dynamic({'power': '电量80%'})   # 同源覆盖
    m.set_dynamic({'pos': '客厅'})
    assert len(m) == 1 and m.dropped == 0, (len(m), m.dropped)
    assert m.transients() == {'power': '电量80%', 'pos': '客厅'}, \
        m.transients()
    feed = m.feed()
    # 帧渲染 [user, assistant] + 瞬态尾拼(同源只剩最新; 结构化条目,
    # 前缀与块形态由 brain 入口渲染, 数据层不拼)
    assert feed[0]['text'] == '你好' and len(feed) == 4, feed
    assert feed[1] == {'role': 'assistant', 'text': '好'}, feed
    assert feed[-2] == {'role': 'transient', 'text': '电量80%',
                        'source': 'power'}, feed
    assert feed[-1] == {'role': 'transient', 'text': '客厅',
                        'source': 'pos'}, feed
    # 瞬态不入帧: 持久视图无 dynamic_context, 回合制条目无快照
    assert 'dynamic_context' not in m.frames_view()[0], m.frames_view()
    assert not any('电量' in str(e) for e in m.history_turns()), \
        m.history_turns()

    # 集成A: 同源两条瞬态排队 → fold 同源取最新, 一拍入槽
    SeenBrain.seen.clear()
    async with _env2(SeenBrain):
        await _turn('看下状态')
        r1 = _req('mindflow', 'declare', {'text': '电量70%', 'source': 'power',
                                      'transient': True})
        r2 = _req('mindflow', 'declare', {'text': '电量80%', 'source': 'power',
                                      'transient': True})
        await asyncio.gather(r1, r2)
        await _req('mindflow', 'idle')
        rec = await _req('ghost', 'recall')
    assert rec['transients'] == {'power': '电量80%'}, rec['transients']
    # 瞬态不进记忆正文: history 里没有电量条目
    assert not any('电量' in (h.get('text') or '')
                   for h in rec['history']), rec['history']
    # 脑子 feed 尾拼看到最新快照(末次 feed 含 role='transient' 的电量)
    assert any(h.get('role') == 'transient' and h.get('text') == '电量80%'
               and h.get('source') == 'power'
               for h in SeenBrain.seen[-1]), SeenBrain.seen[-1]
    # 纯瞬态拍: 折叠产物正文空 → 不产 user 空条目
    assert not any(h.get('role') == 'user' and not h.get('text')
                   for h in SeenBrain.seen[-1]), SeenBrain.seen[-1]

    # 集成B: 在途拍期间快照输仲裁(BACKGROUND) → 瞬态挂账 → 本链
    # 下一拍/合成拍入槽(不丢, 不进正文)
    SeenBrain.seen.clear()
    async with _env2(SeenBrain):
        t1 = asyncio.create_task(_turn('忙话'))
        await asyncio.sleep(0.15)   # 进流中
        await _req('mindflow', 'declare', {'text': '电量9%', 'source': 'power',
                                      'transient': True,
                                      'priority': -1})   # BACKGROUND 必输
        await asyncio.wait_for(t1, timeout=8)
        await _req('mindflow', 'idle')
        rec = await _req('ghost', 'recall')
    assert rec['transients'] == {'power': '电量9%'}, rec['transients']
    assert not any('电量' in (h.get('text') or '')
                   for h in rec['history']), rec['history']
    print('t_transient_state ok: same-source overwrite, never in '
          'history, brain sees latest, losers land via pending')


async def t_epoch() -> None:
    """Epoch 压缩(原版 new_epoch: compact + recap): 窗内历史摘要置顶,
    窗清空, epochs 计数, dropped 不涨(压缩不是忘); 后续拍脑子以
    <recap> 块开头. 原版供料未接线(with_epoch_recap 无注册方), 这里
    通路接通 —— ghost epoch 入口用脑子生成摘要."""
    # 单元: 空 recap / 空窗不动作; 正常压缩清窗 + 计数
    m = Moments(5)
    m.new_epoch('')   # 空 recap: 不动作
    assert m.epochs == 0 and len(m) == 0
    f = m.new_moment({'user': '一'})
    m.commit_logos(f, '答')
    m.new_epoch('')   # 有帧但空 recap: 不动作
    assert m.epochs == 0 and len(m) == 1
    m.new_epoch('前情')   # 正常: 清窗转生 recap(置顶渲染)
    assert m.epochs == 1 and len(m) == 0, (m.epochs, len(m))
    assert m.history_turns() == [{'role': 'recap', 'text': '前情'}], \
        m.history_turns()
    assert m.dropped == 0   # 压缩转生, 不是出窗忘却

    # 集成: 两轮记忆 → epoch → 窗=单条 recap; 再一轮 brain feed
    # 以 <recap> 块开头(brain.input_items 渲染成块)
    SeenBrain.seen.clear()
    async with _env2(RecapBrain):
        await _turn('你好')
        await _turn('天气怎样')
        r = await _req('ghost', 'epoch')
        assert '前情' in r['recap'], r
        rec = await _req('ghost', 'recall')
        assert rec['epochs'] == 1, rec
        assert [h['role'] for h in rec['history']] == ['recap'], rec['history']
        assert rec['dropped'] == 0, rec
        await _turn('谢谢')
        rec2 = await _req('ghost', 'recall')
    assert [h['role'] for h in rec2['history']] == [
        'recap', 'user', 'assistant'], rec2['history']
    # 末拍 feed 首条是 recap(brain 入口渲染成 <recap> 块;
    # SeenBrain 记的是 feed 原始条目 —— role='recap' 直接可见)
    assert SeenBrain.seen[-1][0]['role'] == 'recap', SeenBrain.seen[-1]
    print('t_epoch ok: window compacted into recap, epochs counted, '
          'dropped untouched, next beats see <recap> first')


async def t_pure_interrupt() -> None:
    """纯打断(原版 ImpulsePrimitive.interrupt: FATAL + notify +
    effort='none'): 停在途拍留痕, 开一帧空拍 —— 不思考不出声, 携带的
    text 由空拍 percepts 入史(原版"可携带 messages 解释中断原因, 由
    下一帧 percepts drain"同构)但不被回应; 胜利侧冷静期内再敲静默丢
    (原版 suppress_seconds=0.5 同参)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)
        r = await asyncio.wait_for(_declare('停', interrupt=True), timeout=5)
        assert r['verdict'] == 'accepted', r   # 受理: rank 在裁判循环打
        # 等打断落地(空拍入史) —— 冷静期从 rank 胜出时起计
        for _ in range(100):
            if any(h.get('text') == '停'
                   for h in (await _req('ghost', 'recall'))['history']):
                break
            await asyncio.sleep(0.02)
        # 冷静期内再敲: 静默丢(打断无累积语义, 多次等价一次)
        r2 = await asyncio.wait_for(_declare('停', interrupt=True), timeout=5)
        assert r2['verdict'] == 'noop', r2
        await asyncio.wait_for(t1, timeout=5)
        hist = (await _req('ghost', 'recall'))['history']
    # 只思考过一拍: 空拍不调脑
    assert StreamBrain.inputs == ['主话'], StreamBrain.inputs
    # 停而不答: interrupted 留痕 + "停"入史(user), 无 assistant 回应
    assert [h['role'] for h in hist] == ['user', 'interrupted', 'user'], hist
    assert hist[1]['reason'] == 'interrupted', hist
    assert hist[2]['text'] == '停', hist
    print('t_pure_interrupt ok: stop without answer, empty beat records '
          'the reason, cooldown drops repeats')


async def t_barge_in() -> None:
    """barge-in(原版 listener 的 spec.interrupt): 在途拍被抢占, 但新
    话题**照常回答** —— interrupt 只管停身体, effort='normal' 决定开
    拍思考. 与 t_pure_interrupt 对照: 同为 interrupt=True, 只差
    effort, 一个停而不答一个停而回答(原版两轴正交)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)      # 让主拍先开流
        r = await asyncio.wait_for(
            _declare('换个话题', interrupt=True, effort='normal'),
            timeout=5)
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)
        await _req('mindflow', 'idle')
        hist = (await _req('ghost', 'recall'))['history']
    # 两拍都调过脑: 被抢的拍 + barge-in 新拍(空拍的话不会出现在 inputs)
    assert StreamBrain.inputs == ['主话', '换个话题'], StreamBrain.inputs
    # 旧拍留痕 + 新话题作为 user 入史, 且真有 assistant 回应
    roles = [h['role'] for h in hist]
    assert 'interrupted' in roles, hist
    assert any(h.get('role') == 'user' and h.get('text') == '换个话题'
               for h in hist), hist
    assert roles[-1] == 'assistant', hist
    print('t_barge_in ok: interrupt stops the body but the new topic is '
          'answered (effort=normal)')


async def t_interrupt_idle() -> None:
    """闲时打断(原版 trivially attended): 空拍照样开(留痕帧), 不调脑;
    空文本不产 user 条目(没东西可宣告)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        r = await asyncio.wait_for(_declare('', interrupt=True), timeout=5)
        assert r['verdict'] == 'accepted', r
        await _req('mindflow', 'idle')
        hist = (await _req('ghost', 'recall'))['history']
    assert StreamBrain.inputs == [], StreamBrain.inputs   # 空拍不调脑
    assert hist == [], hist   # 空文本空史: 没留下任何条目
    print('t_interrupt_idle ok: idle interrupt opens an empty beat, '
          'no brain, no entries')


async def t_knock() -> None:
    """knock(原版 KnockNucleus, 八核唯一可丢): 空敲门当场丢; 忙时
    丢了不挂账(下届看不见 —— 端侧自己持有内容, 门没敲开就再敲一次);
    闲时敲 = 普通挑战, 赢了走真实思考."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        r = await asyncio.wait_for(_declare('', mode='knock'), timeout=5)
        assert r['verdict'] == 'dropped', r   # 注册级: 没东西可宣告, 丢
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)
        r = await asyncio.wait_for(
            _declare('门外来信', mode='knock'), timeout=5)
        # 受理登记; 仲裁丢不可见(原版 fire-and-forget) —— 行为从
        # 下面的历史断言观测: 忙时没听见就没听见
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)
        # 仲裁输了当场丢: 消息没进史, 也没人思考它
        hist = (await _req('ghost', 'recall'))['history']
        assert all(h.get('text') != '门外来信' for h in hist), hist
        # 闲时敲 = 正常入场(原版 default mode impulse, 赢了真实思考)
        r = await asyncio.wait_for(
            _declare('有事找你', mode='knock'), timeout=5)
        assert r['verdict'] == 'accepted', r
        await _req('mindflow', 'idle')
    assert StreamBrain.inputs == ['主话', '有事找你'], StreamBrain.inputs
    print('t_knock ok: losable by contract — empty dropped, busy dropped, '
          'free answered by real thinking')


async def t_rank_unit() -> None:
    """rank 单元(原版 ``_rank_best_impulse_from_nuclei`` 群比塌缩):
    command/interrupt 独占拍取最新(last-wins, 平手特殊核优先, 被遮蔽
    正文 suppress 回缓冲重赛)/同源聚合连打两行一拍/尾包快车道(挂起
    链在等的同 id 尾包无视优先级直接胜出)/字典序群比(先 priority 后
    strength)/strength=0 绝不竞争."""
    m = Mindflow()
    await m.on_created('test-mindflow', {'ghost_id': 'g'})
    # command last-wins + 独占: 未消费的旧指令被静默覆写, 正文成输家
    winner, percepts, losers = m._rank([
        Impulse(command=True, text='<x/>', source='command'),
        Impulse(command=True, text='<y/>', source='command'),
        Impulse(text='正文', source='user'),
    ])
    assert winner is not None and winner.command and winner.text == '<y/>'
    assert percepts == {}, percepts   # 指令是 logos 不是输入
    assert [l.text for l in losers] == ['正文'], losers
    for l in losers:
        m._dispatch(l)
    assert [p.text for p in m._pending] == ['正文']   # suppress 回缓冲
    # interrupt 独占 + 平手特殊核优先(同级同强度压正文)
    winner, percepts, losers = m._rank([
        Impulse(interrupt=True, text='停', source='user'),
        Impulse(text='正文2', source='user'),
    ])
    assert winner is not None and winner.interrupt and winner.text == '停'
    assert percepts == {'user': '停'}, percepts   # 空拍只落打断的原因
    assert [l.text for l in losers] == ['正文2'], losers
    # 同源聚合: 连打两行 = 一拍(文本按序拼, 权重取 max)
    winner, percepts, losers = m._rank([
        Impulse(text='第一行', source='user'),
        Impulse(text='第二行', source='user'),
    ])
    assert winner is not None and winner.text == '第一行\n第二行', winner
    assert percepts == {'user': '第一行\n第二行'}, percepts
    assert losers == [], losers
    # 字典序: 先 priority 后 strength(低级大力输给高级小力)
    winner, _, _ = m._rank([
        Impulse(text='低级大力', source='a',
                priority=Priority.INFO, strength=900),
        Impulse(text='高级小力', source='b',
                priority=Priority.NOTICE, strength=10),
    ])
    assert winner is not None and winner.text == '高级小力', winner
    # strength=0 绝不竞争: 全员递毛巾 → 本巡无赢家(占位即逝)
    winner, percepts, losers = m._rank(
        [Impulse(text='观众', source='a', strength=0)])
    assert winner is None and percepts == {} and losers == []
    # 尾包快车道: 挂起链在等的同 id 尾包无视优先级直接胜出(FATAL 让道)
    m._chain_alive = True
    m._attention = Attention(
        Impulse(id='u1', source='user', text='半截', complete=False))
    winner, percepts, losers = m._rank([
        Impulse(id='u1', source='user', text='半截全量', complete=True),
        Impulse(text='更要紧', source='other', priority=Priority.FATAL),
    ])
    assert winner is not None and winner.text == '半截全量', winner
    assert percepts == {'user': '半截全量'}, percepts
    assert [l.text for l in losers] == ['更要紧'], losers
    print('t_rank_unit ok: specials claim the beat, same-source folds, '
          'awaited tail lanes past FATAL, lexicographic rank')


async def t_suppress_own_term() -> None:
    """default 输家独立成链(原版 nucleus.suppress 回缓冲重赛同构):
    在途链的同级挑战者(保护窗口内必输)→ suppress 回检录缓冲, 不挂账
    不陪葬; 本届结束后下一巡自成一条链 —— 各归各答, 与挂账(并入下
    一拍 percepts)不同路; 重赛时钟 = 裁判 0.5s 空搏动(原版同参)."""
    StreamBrain.inputs.clear()
    async with _env2(StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)   # 进流中(现任保护窗口内)
        r = await asyncio.wait_for(_declare('后来话'), timeout=5)
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)   # 两届都收完
    assert StreamBrain.inputs == ['主话', '后来话'], StreamBrain.inputs
    print('t_suppress_own_term ok: loser waits in buffer, gets its own '
          'chain after the term')


async def t_long_action_async() -> None:
    """长动作跨拍漂着(指令结果不触发): close 不等在途 —— slow(1.2s)
    拉不长本拍, 拍收链收; 回执迟到入史(pending 渲染), 不驱动回声帧
    (原版默认命令完成不点 need_observe 同构); 下一届首帧缝合缝读到."""
    _log.clear()
    HistBrain.hists.clear()
    HistBrain.script = ['<main:slow/>', '收到']
    async with _env(['slow'], brain=HistBrain):
        t0 = time.perf_counter()
        await _turn('去跑长活')
        dt = time.perf_counter() - t0
        # close 没等 slow(指令漂着): 拍收时 slow 还在跑, idle 先放行
        assert 'slow.start' in _log and 'slow.end' not in _log, (_log, dt)
        # 无回声帧: 单拍收链
        assert len(HistBrain.hists) == 1, HistBrain.hists
        # slow 跨拍存活跑完, 回执迟到 → 门禁(拍已收)只入史
        await _wait_log('slow.end')
        # 下一届首帧缝合缝读到 slow 回执(被动可读, 不点灯)
        await _turn('接着说')
        assert len(HistBrain.hists) == 2, HistBrain.hists
        assert any('slow' in str(h) for h in HistBrain.hists[1]), \
            HistBrain.hists[1]
        # 且下一届也单拍收链(slow 回执照旧不驱动)
        await asyncio.sleep(0.3)
        assert len(HistBrain.hists) == 2, HistBrain.hists
    print('t_long_action_async ok: close does not wait, receipt lands '
          'as pending, next chain reads it without echo beat')


async def t_multilane_same_beat() -> None:
    """跨轨回执都记账(指令结果不触发): main 轨 slow(1.2s) 与 a 轨 y
    快动作同时在途, close 不等 —— 各轨漂着跑完, 回执陆续迟到入史;
    下一届首帧一次读到两轨结果; 无回声帧."""
    _log.clear()
    HistBrain.hists.clear()
    HistBrain.script = ['<main:slow/><a:y/>', '收到']
    async with _env(['slow', 'y'], brain=HistBrain, chans=['a']):
        await _turn('并行干活')
        assert 'y.end' not in _log or 'slow.end' not in _log, \
            _log   # close 不等: 总有一轨没收尾(idle 先放行)
        await _wait_log('slow.end', 'y.end')
        # 两轨回执都入史(pending 渲染, 迟到路径不丢)
        await _wait_obs(lambda o: 'slow' in str(o) and 'y' in str(o))
        # 下一届首帧缝合缝一次读到两轨结果
        await _turn('接着说')
        assert len(HistBrain.hists) == 2, HistBrain.hists
        echo = str(HistBrain.hists[1])
        assert "'verb': 'slow'" in echo, HistBrain.hists[1]
        assert "'verb': 'y'" in echo, HistBrain.hists[1]
        # 无回声帧: 下一届也单拍收链
        await asyncio.sleep(0.3)
        assert len(HistBrain.hists) == 2, HistBrain.hists
    print('t_multilane_same_beat ok: cross-lane receipts all land, '
          'next chain reads both without echo beat')


async def t_command_reflex() -> None:
    """反射弧(原版 ImpulsePrimitive.command_only): CTML 直投身体执行
    不调脑(无脑也能跑); logos 落帧入史(反幻觉: 执行了就必须看得见);
    回声不评说(原版 effort='none' attention 全程不语 —— 链收, 结果
    留给下一届 attention 的缝合缝)."""
    StreamBrain.inputs.clear()
    _log.clear()
    async with _env(['x', 'y'], brain=StreamBrain):
        r = await asyncio.wait_for(
            _req('mindflow', 'command', {'text': '<x/>'}), timeout=5)
        assert r['verdict'] == 'accepted', r   # 受理: logos 待 rank
        # 空指令当场丢(原版: 空 logos 不产 impulse)
        r = await asyncio.wait_for(
            _req('mindflow', 'command', {'text': ''}), timeout=5)
        assert r['verdict'] == 'dropped', r
        await _req('mindflow', 'idle')
        # x 回执迟到经 pending 入史(反射拍早收, 链外漂着)
        for _ in range(100):
            hist = (await _req('ghost', 'recall'))['history']
            if any(h['role'] == 'obs' for h in hist):
                break
            await asyncio.sleep(0.05)
        hist = (await _req('ghost', 'recall'))['history']
    assert StreamBrain.inputs == [], StreamBrain.inputs   # 反射不调脑
    assert 'x.start' in _log and 'x.end' in _log, _log      # 指令真执行了
    roles = [h['role'] for h in hist]
    assert 'assistant' in roles, roles   # 执行的 logos 入史
    assert any(h.get('text') == '<x/>' for h in hist), hist
    assert 'obs' in roles and roles.index('obs') > roles.index('assistant'), (
        roles)   # 回声(x 结果)在 logos 之后, 且无第二拍评说
    print('t_command_reflex ok: reflex executes without the brain, '
          'logos recorded, obs left for the next attention')


async def t_command_arbitration() -> None:
    """反射弧仲裁: FATAL 必抢占(原版 fatal_command 同构, 在途拍留痕);
    NOTICE 输给保护期内的在途链 → 失败即丢(指令无重试语义)."""
    StreamBrain.inputs.clear()
    _log.clear()
    async with _env(['x'], brain=StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)
        r = await asyncio.wait_for(
            _req('mindflow', 'command',
                 {'text': '<x/>', 'priority': 5}), timeout=5)
        assert r['verdict'] == 'accepted', r   # 受理: FATAL 抢占看行为
        await asyncio.wait_for(t1, timeout=5)
        hist = (await _req('ghost', 'recall'))['history']
        assert 'x.start' in _log, _log          # 抢占后指令立即执行
        roles = [h['role'] for h in hist]
        assert 'interrupted' in roles, roles     # 在途拍留痕
        assert 'assistant' in roles, roles        # 反射 logos 入史
    # NOTICE 指令输给在途链: 仲裁丢不可见, 不重试不执行(行为断言)
    _log.clear()
    async with _env(['x'], brain=StreamBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.15)
        r = await asyncio.wait_for(
            _req('mindflow', 'command', {'text': '<x/>'}), timeout=5)
        assert r['verdict'] == 'accepted', r
        await asyncio.wait_for(t1, timeout=5)
    assert 'x.start' not in _log, _log
    print('t_command_arbitration ok: fatal preempts, notice loses and '
          'drops without retry')


async def t_abort_teardown() -> None:
    """打断收尾(原版 thinking 因 abort 退出即 shell.clear 同构): 未决
    流处死 / 队列中未 start 的动作 destroy(原版 clear 取消 pending tasks) /
    在跑动作 stop(优雅停止: 快动作收底下跑完照常报账, 原版在跑任务
    fail(INTERRUPTED) 的宽工程化) / 解析器重开; 旧拍回声
    带旧拍号, 只入史不串台(下一拍的字不落旧流的嘴)."""
    _log.clear()
    async with _env(['x', 'y'], brain=XYBrain):
        t1 = asyncio.create_task(_turn('主话'))
        await asyncio.sleep(0.08)   # x 在跑(0.15s), y 在队
        r = await asyncio.wait_for(_declare('', interrupt=True), timeout=5)
        assert r['verdict'] == 'accepted', r   # 受理: 打断看行为断言
        await asyncio.wait_for(t1, timeout=5)
        # x 跑完报账(stop 收底: 快动作 main task 正常退出): 迟到 trace
        # 带旧拍号转发, 轮询等入史(打断的空拍不消化回声)
        hist: list = []
        for _ in range(100):
            hist = (await _req('ghost', 'recall'))['history']
            if any(h['role'] == 'obs' for h in hist):
                break
            await asyncio.sleep(0.05)
        assert any(h['role'] == 'obs' for h in hist), hist
    assert 'x.start' in _log, _log           # 在跑的: stop 收底, 跑完
    assert 'x.end' in _log, _log
    assert 'y.start' not in _log, _log       # 队列里的: 没轮到就 destroy 了
    print('t_abort_teardown ok: stream killed, queued destroyed, in-flight '
          'stopped gracefully, parser reset, stale echoes only recorded')


async def t_abort_multilane() -> None:
    """跨轨并行打断(shell ``_finish`` abort 分支 gather): 一拍内三条
    轨各在跑动作(main: slow 在跑 + y 排队 / a: x / b: z) —— 打断请求
    并行发出, 逐轨各自收底: 排队的 destroy(无账, 没执行过不留痕),
    在跑的 stop(跑完照常报账), 账本照样 drain 回灌. 跨轨本无顺序
    语义(谁完成谁入账), 账一条不少."""
    _log.clear()
    async with _env(['slow', 'y', 'x', 'z'], brain=MultiLaneBrain,
                    chans=['a', 'b']):
        t1 = asyncio.create_task(_turn('并行干活'))
        await asyncio.sleep(0.08)   # slow / x / z 在跑, main:y 在队
        r = await asyncio.wait_for(_declare('', interrupt=True), timeout=5)
        assert r['verdict'] == 'accepted', r   # 受理: 打断看行为断言
        await asyncio.wait_for(t1, timeout=5)
        await _wait_log('x.end', 'z.end', 'slow.end')
        # 三轨回执都入账(在跑的 stop 收底后照常报账, 迟到也进 pending;
        # 帧视图 + pending 一起找 —— 中途可能有空拍把 pending 缝走)
        snap: dict = {}
        for _ in range(100):
            snap = await _req('ghost', 'recall')
            landed = list(snap['pending']['frames'])
            for fr in snap['frames']:
                landed.extend(fr['previous']['frames'])
            verbs = {f.get('verb') for f in landed
                     if f.get('kind') == 'action'}
            if {'slow', 'x', 'z'} <= verbs:
                break
            await asyncio.sleep(0.05)
        assert {'slow', 'x', 'z'} <= verbs, (verbs, snap['pending'])
    assert 'y.start' not in _log, _log                  # 排队的: destroy
    assert 'x.start' in _log and 'x.end' in _log, _log  # 在跑的: stop 收底
    assert 'z.start' in _log and 'z.end' in _log, _log
    assert 'slow.start' in _log and 'slow.end' in _log, _log
    print('t_abort_multilane ok: parallel abort across three lanes, '
          'queued destroyed, in-flight stopped, receipts all land')


async def t_go_revive() -> None:
    """手动续拍(/go = declare mode='next' 无文本, UI '激活'按钮):
    链死后不带新输入独立开回声拍 —— 开帧缝合缝缝着漂到的动作
    回执(pending), 模型被动读到(指令结果不触发的显式激活路径;
    链活时同 mode 即插队, t_next_mode 已覆盖)."""
    HistBrain.hists.clear()
    HistBrain.script = ['<main:slow/>', '拿到结果了']
    async with _env(['slow'], brain=HistBrain):
        await _turn('查天气')   # 主拍: slow(1.2s) 漂着, 拍收链死
        # 回执漂到入史(close 不等在途, 报账有窗口 → 轮询以 obs 为准)
        await _wait_obs(lambda o: any(f.get('verb') == 'slow' for f in o))
        # recall 观测面: pending 正缝着漂到的 slow 回执(火种旗亮),
        # recap 键在位(空 = 无 epoch 压缩) —— UI 侧栏读同一份
        snap = await _req('ghost', 'recall')
        assert any(f.get('verb') == 'slow'
                   for f in snap['pending']['frames']), snap['pending']
        assert snap['pending']['need_observe'] is True, snap['pending']
        assert snap['recap'] == '', snap['recap']
        r = await asyncio.wait_for(
            _req('mindflow', 'declare', {'mode': 'next'}), timeout=3)
        assert r['verdict'] == 'accepted', r
        await _req('mindflow', 'idle')   # 等回声拍跑完
    # 两拍: 主拍 + 手动续拍; 续拍不带新输入, 但缝合缝里有 slow 回执
    assert len(HistBrain.hists) == 2, [str(h) for h in HistBrain.hists]
    echo = str(HistBrain.hists[-1])
    assert 'slow' in echo, HistBrain.hists[-1]   # 缝合缝读到漂到的回执
    print('t_go_revive ok: /go revives a beat without new input, '
          'stitch seam reads the drifting receipt')


async def t_echo_lamp() -> None:
    """回声与手动 next 共用一盏灯(原版 need_observe 闩锁): 灵魂报
    ``echo=True``(本拍声明过的观测已清空) → 同一条拍链内续一帧,
    不重开链、不重排名 —— 原版 next 输家 ``verdict='queued'`` 也是
    ``add_echoes([], need_observe=True)``(同一盏灯、同一个 while 循环).
    灯幂等: 点一次只续一拍(不是计数器)."""
    HistBrain.hists.clear()
    HistBrain.script = ['主拍', '回声拍']
    orig_req = Mindflow.req
    thinks: list[int] = []

    async def fake_req(self, target, event, data=None, timeout=30.0):
        r = await orig_req(self, target, event, data, timeout)
        if event == 'think':
            thinks.append(1)
            if len(thinks) == 1:
                # 冒充灵魂: 本拍声明过的观测集合已清空 → 点灯
                return {**r, 'echo': True}
        return r

    Mindflow.req = fake_req
    try:
        async with _env2(HistBrain):
            await _turn('开始')
    finally:
        Mindflow.req = orig_req
    assert len(thinks) == 2, thinks            # 首拍 + 一轮回声, 只多一拍
    assert len(HistBrain.hists) == 2, [str(h) for h in HistBrain.hists]
    # 回声拍带缝合缝(上一拍的话), 不是空转
    assert any('主拍' in str(h) for h in HistBrain.hists[-1]), \
        HistBrain.hists[-1]
    print('t_echo_lamp ok: echo lights the same lamp as /go, one extra '
          'beat inside the same chain, idempotent')


async def main() -> None:
    # 用例名过滤(并行分片用): python -m ...test_dispatch t_knock t_preempt
    # —— 按定义序跑指定用例, 供 run_parallel 多进程分片(全局 _log/
    # FakeBrain.script 类属性使同进程 gather 不安全, 分片走进程隔离)
    only = [a for a in sys.argv[1:] if not a.startswith('-')]
    if only:
        import inspect
        table = {n: f for n, f in globals().items()
                 if n.startswith('t_') and inspect.iscoroutinefunction(f)}
        unknown = [a for a in only if a not in table]
        assert not unknown, f'unknown cases: {unknown}'
        ran = 0
        for n, f in table.items():   # globals 保定义序
            if n in only:
                await f()
                ran += 1
        assert ran == len(only), f'ran {ran}, expected {len(only)}'
        print(f'ALL PASS ({ran} cases)')
        return
    await t_serial_parallel_aside()
    await t_container_all()
    await t_container_flow()
    await t_container_any()
    await t_cross_lane_barrier()
    await t_cross_lane_no_wait()
    await t_sleep_occupies_lane()
    await t_noop_is_an_action()
    await t_loop_times()
    await t_loop_stops_on_error()
    await t_container_timeout()
    await t_clear_drops_queue()
    await t_interrupt_all()
    await t_code_cell()
    await t_code_occupies_lane()
    await t_code_error()
    await t_code_unknown_verb()
    await t_code_syntax()
    await t_code_say()
    await t_code_handle()
    await t_code_container_refused()
    await t_code_no_nest()
    await t_code_body_refused()
    await t_code_spawn()
    await t_code_bg_reap()
    await t_code_interrupt()
    await t_code_shares_ns()
    await t_code_one_cell_at_a_time()
    await t_code_kids_under_cell()
    await t_memory_across_turns()
    await t_interrupt_chain()
    await t_verb_failure_isolated()
    await t_observe_claim()
    await t_observe_value_guard()
    await t_meta_observe()
    await t_bad_tag_and_unterminated()
    await t_bad_param_refused()
    await t_stray_body_refused()
    await t_code_bad_kwarg_refused()
    await t_leaf_body_decoded()
    await t_hot_registration()
    await t_hot_meta_rescan()
    await t_chan_lifecycle()
    await t_chan_admin()
    await t_asr_intake()
    await t_asr_mode()
    await t_asr_ptt()
    await t_key_ptt()
    await t_onset_absorb()
    await t_nucleus()
    await t_onset_nucleus()
    await t_sauc_protocol()
    await t_ear_keepalive()
    await t_say_and_bare_text()
    await t_say_two_streams()
    await t_stream_start_scheduled()
    await t_say_engine()
    await t_attention_unit()
    await t_preempt()
    await t_defer_silent()
    await t_notify_buffer()
    await t_next_mode()
    await t_absorb_partial()
    await t_effort_from_tail()
    await t_suppress_into_hanging()
    await t_quiet_aside_buffered()
    await t_partial_mixed_queue()
    await t_absorb_beats_protection()
    await t_suppressed_partial_no_dup()
    await t_suppress_own_term()
    await t_long_action_async()
    await t_multilane_same_beat()
    await t_memory_window()
    await t_transient_state()
    await t_epoch()
    await t_pure_interrupt()
    await t_barge_in()
    await t_interrupt_idle()
    await t_knock()
    await t_rank_unit()
    await t_command_reflex()
    await t_command_arbitration()
    await t_abort_teardown()
    await t_abort_multilane()
    await t_go_revive()
    await t_echo_lamp()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
