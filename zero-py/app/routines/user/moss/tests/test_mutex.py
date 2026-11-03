"""互斥层 e2e: 动作 modules 声明 + kernel 锥冲突.

三层分离的互斥层: 动作 routine 用 ``modules()`` 类方法声明占用模块,
kernel start 阶段 TryAcquire 原子占用(all-or-nothing); 跨轨冲突 →
``call`` 抛 StartError → worker 收成 error 帧回灌. 词表(闸门)与
lane(排序)都不管互斥 —— 组合矩阵: 同轨无条件排队 / 跨轨锥交 →
拒绝+回灌 / 跨轨不交 → 并行.

模块树(测试身体, 根固定 'root'):

    root ── chan.arms ── chan.arms.left
    │                 └─ chan.arms.right
    └─ chan.tail

验证三件事:
1. 祖先后代互斥: 占 chan.arms 在跑, 占 chan.arms.left 的动作被拒
   (锥 = 祖先+自己+后代, 双向堵);
2. 锥不交并行: left + tail 跨轨并行; 锥并集动作(左手+尾巴式)与
   不交兄弟 right 也并行;
3. 冲突帧 + 原子性: 并集动作撞上已占的 left → error 帧回灌, 且
   不半占(tail 当轮仍可用);
4. 能力面分组: capability 按身体分支渲染 modules/meta 声明, 带
   schema 的动作出参数签名(一份声明喂两张嘴).

用法: ``python -m app.routines.user.mossv2.tests.test_mutex``
"""
from __future__ import annotations

import asyncio
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))

from routine import Routines, kernel
from routine.hub import RoutineHub
from routine.routine import Modules, PassiveConfig, Routine

from ..channel import Channel
from ..ghost import Ghost
from ..mindflow import Mindflow
from ..moss import Moss
from ..prompt import Capability
from ..shell import Shell

# 模块树: 测试身体(kernel.create 建树, 只读律法)
_KERNEL_MODS = {
    'root': {'children': ['chan.arms', 'chan.tail']},
    'chan.arms': {'children': ['chan.arms.left', 'chan.arms.right']},
    'chan.arms.left': {},
    'chan.arms.right': {},
    'chan.tail': {},
}

_SLEEP = 0.3    # 动作时长: 盖过段间停顿, 保住重叠窗口
_PACE = 0.12    # 段间停顿: 先发的动作先占上模块(确定序)

_log: list[str] = []


class PacedBrain:
    """按帧吐字流, 帧内段间停顿 —— 让先发的动作先占上模块(确定序)."""

    frames: list[list[str]] = []   # 每拍一段列表(每段一个 CTML 标签)

    def __init__(self):
        self._n = 0

    async def stream(self, history, system):
        for seg in self.frames[self._n]:
            yield seg
            await asyncio.sleep(_PACE)
        self._n += 1


class _Act(Routine):
    """动作基类: 子类给 name + mods(锥声明走 modules() 类方法)."""

    mods: list[str] = []

    @classmethod
    def modules(cls, kwargs=None):
        return Modules(cls.mods)

    async def run(self, kwargs):
        _log.append(f'{self.name}.start')
        await asyncio.sleep(_SLEEP)
        _log.append(f'{self.name}.end')
        return {}

    async def stop(self) -> None:
        pass


def _act(name: str, mods: list[str], meta: dict | None = None) -> type:
    """声明表: 名字 → 占用模块(锥) + meta(参数 schema/说明)."""
    return type(name.upper(), (_Act,), {
        'name': name, 'mods': list(mods), 'meta': meta or {}})


LeftArm = _act('left_arm', ['chan.arms.left'])
RightArm = _act('right_arm', ['chan.arms.right'])
TailWag = _act('tail_wag', ['chan.tail'])
ArmsAll = _act('arms_all', ['chan.arms'])
Both = _act('both', ['chan.arms.left', 'chan.tail'])   # 跨域: 锥并集
Wave = _act('wave', ['chan.arms.right'], meta={
    'description': '挥手打招呼',
    'input_schema': {
        'type': 'object',
        'properties': {
            'hand': {'type': 'string', 'description': '哪只手'},
            'times': {'type': 'integer', 'default': 2},
        },
        'required': ['hand'],
    },
})

_VERBS = ['left_arm', 'right_arm', 'tail_wag', 'arms_all', 'both', 'wave']


class Sim(Routine):
    """测试探针: ('req', name, event, data, fut) 代发 req."""

    name = 'sim'
    is_passive = True

    async def run(self, kwargs):
        while True:
            _, name, event, data, fut = await _cmds.get()
            try:
                fut.set_result(await self.ctx.req(await self._rid(name), event, data))
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
    """一轮对话: 用户的话进桥梁(declare 即回), 等泵静默(idle 屏障)."""
    r = await _req('mindflow', 'declare', {'text': text})
    await _req('mindflow', 'idle')
    return r


async def _obs() -> list[dict]:
    """读灵魂记忆里最后一帧观测."""
    hist = (await _req('ghost', 'recall'))['history']
    for h in reversed(hist):
        if h['role'] == 'obs':
            return h['frames']
    return []


async def _wait_obs(pred, timeout: float = 5.0) -> list[dict]:
    """轮询读最后一帧观测直到谓词成立(回执不驱动回声: 拍收后回执
    迟到, 经 pending 渲染进 history 才可见)."""
    deadline = time.perf_counter() + timeout
    last: list[dict] = []
    while time.perf_counter() < deadline:
        last = await _obs()
        if last and pred(last):
            return last
        await asyncio.sleep(0.02)
    raise AssertionError(f'obs not settled: {last}')


async def _all_frames() -> list[dict]:
    """整条帧链的全部观测帧(跨拍): 回执不驱动回声, 迟到回执落更晚的帧."""
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


def _moss(prompts: list[str]) -> type:
    return type('M', (Moss,), {
        'name': 'moss',
        'is_passive': PassiveConfig(
            kwargs={'verbs': list(_VERBS), 'prompts': list(prompts)}),
    })


@asynccontextmanager
async def _env(prompts: list[str] | None = None,
              chans: list[str] | None = None):
    kref = await kernel.create(_KERNEL_MODS)
    rs = Routines()
    Ghost.brain_factory = PacedBrain   # 换脑注入(与 Say.engine_factory 同构)
    rs.register(_moss(prompts or []), Ghost, Shell, Channel, Sim,
                LeftArm, RightArm, TailWag, ArmsAll, Both, Wave, Capability,
                Mindflow)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        await asyncio.sleep(1.5)   # moss 带起 ghost + shell
        # 跨轨 case 的轨道控制面预建(轨表显式建后, 测试聚焦互斥语义,
        # chan 族动作在 test_dispatch 测)
        for c in chans or []:
            await _req('shell', 'chan', {'op': 'create', 'name': c})
        yield
    finally:
        await conn.close()
        Ghost.brain_factory = None   # 复位, 不跨用例泄漏


async def t_ancestor_descendant_mutex() -> None:
    """锥含祖先+后代: 占 chan.arms 在跑, 占 left 的被拒(跨轨也拒).

    帧归属与完成时刻无关(指令结果不触发回声: 错误随本拍 drain,
    arms_all 的回执照旧漂着、下一帧缝合缝读到), 所以断言跨帧取全量.
    """
    _log.clear()
    PacedBrain.frames = [['<a:arms_all/>', '<b:left_arm/>']]
    async with _env(chans=['a', 'b']):
        await _turn('干')
        frames: list = []
        for _ in range(120):
            frames = await _all_frames()
            if any(f.get('kind') == 'error' and f.get('verb') == 'left_arm'
                   for f in frames) and any(
                    f.get('kind') == 'action' and f.get('verb') == 'arms_all'
                    for f in frames):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(f'frames not settled: {frames}')
    errs = [f for f in frames if f.get('kind') == 'error']
    assert len(errs) == 1 and errs[0].get('verb') == 'left_arm', frames
    assert 'blocked' in errs[0]['text'], frames
    acts = [f['verb'] for f in frames if f.get('kind') == 'action']
    assert acts == ['arms_all'], frames
    print('t_ancestor_descendant_mutex ok: cone blocks both directions')


async def t_disjoint_parallel() -> None:
    """锥不交并行: left+tail 并行; 并集(both)与不交兄弟 right 也并行."""
    _log.clear()
    async with _env(chans=['a', 'b']):
        PacedBrain.frames = [['<a:left_arm/>', '<b:tail_wag/>'],
                              ['<a:both/>', '<b:right_arm/>']]
        await _turn('一')
        await _wait_log('left_arm.end', 'tail_wag.end')   # 漂着跑完
        assert _log.index('tail_wag.start') < _log.index('left_arm.end'), _log
        # 先等轮 1 回执入史(防止轮 2 开拍早于回执落地 → 跨轮混进同一条)
        await _wait_obs(lambda o: {f['verb'] for f in o
                                   if f['kind'] == 'action'}
                         == {'left_arm', 'tail_wag'})
        await _turn('二')
        # both 占 [left, tail] 并集; right 锥不交 → 都跑且并行;
        # 两轮回执陆续经 pending 入史, 轮 2 的帧随后落齐
        obs = await _wait_obs(lambda o: (
            {f['verb'] for f in o if f['kind'] == 'action'}
            == {'both', 'right_arm'}
            and not any(f['kind'] == 'error' for f in o)))
    assert _log.index('right_arm.start') < _log.index('both.end'), _log
    acts = {f['verb'] for f in obs if f['kind'] == 'action'}
    assert acts == {'both', 'right_arm'}, obs
    assert not any(f['kind'] == 'error' for f in obs), obs
    print('t_disjoint_parallel ok: disjoint cones parallel, union too')


async def t_union_atomic_frame() -> None:
    """并集动作撞已占 left → error 帧回灌, 且不半占(tail 当轮仍可用).

    错误自带注意债 → 当拍续一拍(第二帧: 模型看见 blocked 不再硬撞,
    什么都不说 = 空字流), 一链两拍收尾."""
    _log.clear()
    PacedBrain.frames = [['<a:left_arm/>', '<b:both/>', '<c:tail_wag/>'], []]
    async with _env(chans=['a', 'b', 'c']):
        await _turn('干')
        # left_arm 在跑, both 的锥并集含 left → StartError → error 帧;
        # 三帧都陆续入史(拍收不等在途, 跨帧取全量)
        obs = []
        for _ in range(120):
            obs = await _all_frames()
            if ({f['verb'] for f in obs if f['kind'] == 'action'}
                    == {'left_arm', 'tail_wag'}
                    and len([f for f in obs if f['kind'] == 'error']) == 1):
                break
            await asyncio.sleep(0.05)
    # 原子性: 被拒不半占 —— tail 当轮照常, 且与 left 并行(锥不交)
    errs = [f for f in obs if f['kind'] == 'error']
    assert len(errs) == 1 and errs[0].get('verb') == 'both', obs
    assert 'blocked' in errs[0]['text'], errs
    acts = {f['verb'] for f in obs if f['kind'] == 'action'}
    assert acts == {'left_arm', 'tail_wag'}, obs
    assert _log.index('tail_wag.start') < _log.index('left_arm.end'), _log
    print('t_union_atomic_frame ok: conflict framed, atomic reject')


async def t_capability_grouped() -> None:
    """能力面按锥分组: 一份 modules 声明喂两张嘴(互斥 + 词表教学)."""
    async with _env(['prompt/capability']):
        sys_text = (await _req('ghost', 'recall'))['system']
    # 分组 = 身体分支(树顶层), 声明随行列出
    assert 'chan.arms:' in sys_text and 'chan.tail:' in sys_text, sys_text
    assert '- arms_all (chan.arms)' in sys_text, sys_text
    assert '- left_arm (chan.arms.left)' in sys_text, sys_text
    # 跨分支动作单独段(左手+尾巴锥并集)
    assert '- both (chan.arms.left + chan.tail)' in sys_text, sys_text
    # 带 schema 的动作: 参数签名(必填裸名/可选带默认) + 说明随行
    assert '- wave(hand, times=2) — 挥手打招呼 (chan.arms.right)' in sys_text, sys_text
    # 分组序 = 身体序(树顶层分支序)
    assert sys_text.index('chan.arms:') < sys_text.index('chan.tail:'), sys_text
    print('t_capability_grouped ok: grouped by body branches, sig inline')


async def main() -> None:
    await t_ancestor_descendant_mutex()
    await t_disjoint_parallel()
    await t_union_atomic_frame()
    await t_capability_grouped()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
