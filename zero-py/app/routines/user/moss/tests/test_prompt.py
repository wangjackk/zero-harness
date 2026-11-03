"""prompt 片段组装: Moss 按名单取片段拼**一次**, 注入 Ghost 系统前缀.

验证三件事:
1. 组装: persona + capability 按序拼进 system, 脑子每轮收同一个
   前缀(不重拼 —— 前缀缓存友好);
2. capability 渲染装配时词表快照: 装配后热挂的词不进前缀(快照语义,
   要新前缀就显式重组, 不自动刷新);
3. registry 只读 op: ``list`` 返回快照, 不增不删.

用法: ``python -m app.routines.user.moss.tests.test_prompt``
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import ClassVar
from contextlib import asynccontextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))

from routine import Routines, kernel
from routine.hub import RoutineHub
from routine.routine import PassiveConfig, Routine

from app.routines.user.routine.doc import RoutineDoc

from ..channel import Channel
from ..ghost import Ghost
from ..mindflow import Mindflow
from ..moss import BUILTIN_VERBS, Moss
from ..modules import MODULES
from ..prompt import Capability, Creator, Persona
from ..shell import Shell


class FakeBrain:
    """假脑: 按调用次序(每轮一拍, 回执不驱动回声)产固定字流, 记录
    脑子每次收到的 system."""

    script: list[str] = ['Hi']
    seen_systems: list[str] = []   # 类级, 测试预置/回读
    seen_inputs: list[str] = []    # 每拍的 history(JSON), 看回执有没有灌进下一拍

    def __init__(self):
        self._n = 0

    async def stream(self, history, system):
        self.seen_systems.append(system)
        self.seen_inputs.append(
            json.dumps(history, ensure_ascii=False, default=str))
        text = self.script[self._n]
        self._n += 1
        yield text


class X(Routine):
    """一次性动作 routine: 词表占位. 带 example —— 验证"提示词跟能力走"
    (只在这个名字进词表时, 这段示例才出现在前缀里)."""

    name = 'x'

    meta: ClassVar[dict] = {
        'description': '测试动作',
        'example': 'X 的用法: <main:x/> 这一行只在 x 进词表时出现.',
    }

    async def run(self, kwargs):
        return {}

    async def stop(self) -> None:
        pass


class Obs(Routine):
    """出厂声明 observe 的 routine: 清单那行**不许**因此多长一个括注.

    机制本身还在(``shell._apply_meta`` 据此每次都要回执), 但它不是提示词该
    通告的事: 要不要这一次的结果, 由标签上的 ``observe="true"`` 现决定; 清单
    只要让模型知道这个名字存在(用法走 ``routine/doc``).
    """

    name = 'obs'

    meta: ClassVar[dict] = {
        'description': '查询类: 结果自动回到眼前',
        'observe': True,
    }

    async def run(self, kwargs):
        return {'content': 'ok'}

    async def stop(self) -> None:
        pass


class Sig(Routine):
    """带参数说明的 routine: 签名下面必须跟着参数子行.

    ``input_schema`` 里每个参数的 ``description`` 是写给"要填这个参数的人"
    的 —— protocol 说"参数看清单里它那一行", 落点就是这些子行. 没写说明的
    参数不占行(空行比没有说明更坏: 读的人以为漏了东西).
    """

    name = 'sig'

    meta: ClassVar[dict] = {
        'description': '带参数说明',
        'example': '用法: sig(loud=True, times=3) 这段只在 sig 进词表时出现.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'loud': {'type': 'boolean', 'default': False,
                         'description': '喊出来'},
                'times': {'type': 'integer', 'default': 2,
                          'description': '重复几次'},
                'quiet': {'type': 'integer', 'default': 0},
            },
            'required': [],
        },
    }

    async def run(self, kwargs):
        return {'content': 'ok'}

    async def stop(self) -> None:
        pass


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


def _moss(verbs: list[str], prompts: list[str],
          detail: list[str] | None = None,
          observe: list[str] | None = None) -> type:
    """按词表 + prompt 名单装配 Moss(被动拉起时注入)."""
    kwargs = {'verbs': list(verbs), 'prompts': list(prompts)}
    if detail is not None:      # None = 不声明 → 能力面全详列(向后一致的默认)
        kwargs['detail'] = list(detail)
    if observe is not None:     # 出厂回执的装配声明(通用 routine 的 meta 不带)
        kwargs['observe'] = list(observe)
    return type('M', (Moss,), {
        'name': 'moss',
        'is_passive': PassiveConfig(kwargs=kwargs),
    })


@asynccontextmanager
async def _env(verbs: list[str], prompts: list[str], extra: tuple = (),
               detail: list[str] | None = None,
               observe: list[str] | None = None):
    kref = await kernel.create(MODULES)
    rs = Routines()
    Ghost.brain_factory = FakeBrain   # 换脑注入(与 Say.engine_factory 同构)
    rs.register(_moss(verbs, prompts, detail, observe), Ghost, Shell, Channel,
                Sim, X, Persona, Capability, RoutineDoc, Mindflow, *extra)
    hub = RoutineHub(rs)
    conn = await hub.connect(kref)
    try:
        await asyncio.sleep(1.5)   # moss 带起 ghost + shell(含 prompt 组装)
        yield
    finally:
        await conn.close()
        Ghost.brain_factory = None   # 复位, 不跨用例泄漏


async def t_param_desc() -> None:
    """参数说明: 印成签名下的子行, 且只印有说明的那些.

    签名只装名字与默认值 —— 单位 / 取值 / 跨字段约束装不进去, 也不该把
    一行撑成一段(那行还要被扫读). 空说明不留行: 空白行读起来像"这里本该
    有东西", 比没有更坏.
    """
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env(['sig', 'x'], ['prompt/capability'], extra=(Sig,)):
        await _turn('一')
    lines = FakeBrain.seen_systems[0].splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith('- sig'))
    assert lines[i + 1] == '  loud: 喊出来', lines[i:i + 4]
    assert lines[i + 2] == '  times: 重复几次', lines[i:i + 4]
    assert not any(ln.startswith('  quiet:') for ln in lines), lines[i:i + 5]
    # 参数子行与示例块同缩进: 中间必须空一行, 否则示例首行会被读成
    # 上一个参数的续写(bb_write 的 "at 是基线…" 正是这种形状)
    assert lines[i + 3] == '', lines[i:i + 6]
    assert lines[i + 4].startswith('  用法:'), lines[i:i + 6]
    # 子行之后回到下一项(子行不是"贴到下一项头上"的东西)
    assert lines[i + 5].startswith('- '), lines[i:i + 6]
    # 签名本身不乱: 参数名与默认值照旧
    assert "sig(loud=False, times=2, quiet=0)" in lines[i], lines[i]
    print('t_param_desc ok: 参数说明按子行印, 没说明的不占行')


async def t_detail_split() -> None:
    """两级披露: detail 点名的详列, 名单外只印名字(名字就是名字).

    前缀是每拍都在的常驻成本, 而车身那种能力一次对话摸不到两次 —— 名字
    留在眼前, 用法留在 ``routine/doc`` 里按需取. 三件事:
    1. 名单内 = 签名 + 参数子行 + 示例; 名单外 = 一个名字, 不占多行;
    2. 名字行**只有名字** —— 不带 ``⟨body⟩``/``⟨observe⟩`` 括注(机制不从
       索引行通告: body 怎么写看它自己 description, observe 看标签属性);
    3. 只在真有名字项、且 routine/doc 在词表时才印那句提示(指向不存在的能力更坏).
    """
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env(['sig', 'x', 'obs'], ['prompt/capability'],
                    extra=(Sig, Obs), detail=['sig']):
        await _turn('一')
    lines = FakeBrain.seen_systems[0].splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith('- sig'))
    assert lines[i + 1] == '  loud: 喊出来', lines[i:i + 3]   # 1) 名单内详列
    assert any(ln.startswith('  用法:') for ln in lines), lines
    rest = next(ln for ln in lines if ln.startswith('  另有:'))
    assert 'x' in rest.split() and 'obs' in rest.split(), rest
    assert '⟨' not in rest, rest        # 2) 索引行不带括注
    assert '- x' not in lines and '- obs' not in lines, lines   # 不再各占一块
    assert 'X 的用法' not in '\n'.join(lines), '裸名项不该带示例'
    assert any(ln.startswith('(只列名字的那些') for ln in lines), lines

    # 点名 x → 它从"另有"行回到各占一块(词表里剩下的内置没注册, 仍裸名)
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env(['sig', 'x'], ['prompt/capability'],
                    extra=(Sig,), detail=['sig', 'x']):
        await _turn('二')
    lines2 = FakeBrain.seen_systems[0].splitlines()
    assert any(ln.startswith('- x — 测试动作') for ln in lines2), lines2
    assert any(ln.startswith('  X 的用法:') for ln in lines2), lines2
    rest2 = next(ln for ln in lines2 if ln.startswith('  另有:'))
    assert 'x' not in rest2.split()[1:], rest2
    print('t_detail_split ok: 点名详列, 其余只留名字')


async def t_detail_unknown() -> None:
    """detail 写错名字: 忽略那一项(退回裸名) + 日志点名, 但不拖垮装配.

    一个笔误不该让整具身体起不来 —— kernel 拉起失败 = 灵魂不在, 那比多印
    几行字难查得多. 也不该当没发生: 错误进日志(装配期就看得见), 而清单里
    那一项退回名字(用法还能用 routine/doc 取).
    """
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env(['sig'], ['prompt/capability'], extra=(Sig,),
                    detail=['sig', 'nope']):
        await _turn('一')
    lines = FakeBrain.seen_systems[0].splitlines()
    assert any(ln.startswith('- sig') for ln in lines), lines   # 对的照旧详列
    assert not any('nope' in ln for ln in lines), lines         # 错的不会凭空出现
    print('t_detail_unknown ok: 写错的名字被忽略(日志点名), 装配照常')


async def t_doc_returns_entry() -> None:
    """``routine/doc`` 取回的就是清单里那一项: 签名 + 参数说明 + 示例.

    裸名项的用法没有丢, 只是搬到了这一步之后 —— 所以两者的渲染必须是同一
    个函数(``capability.entry``), 否则清单和 doc 会各自漂移. 用 x(只有
    example, 无参数) 验证: 它裸名时清单里没有示例, 查过之后下一拍的输入
    里出现示例. (``routine/doc`` 是公共包里那个老能力, 不是新造的第二套.)

    ``observe`` 由装配声明(``_env(observe=[...])``)给, 不写在 routine 的
    meta 里 —— 它是跨身体共用的通用件, 要不要回执归用它那具身体判断.
    """
    FakeBrain.seen_systems.clear()
    FakeBrain.seen_inputs.clear()
    FakeBrain.script = ['<main:routine/doc name="x"/>', *(['ok'] * 8)]
    async with _env(['sig', 'x'], ['prompt/capability'],
                    extra=(Sig,), detail=['sig'], observe=['routine/doc']):
        await _turn('一')
        for _ in range(10):
            if any('X 的用法' in s for s in FakeBrain.seen_inputs):
                break
            await asyncio.sleep(0.3)
    first = FakeBrain.seen_inputs[0]
    assert 'X 的用法' not in first, first          # 裸名项: 清单里没有示例
    assert any('X 的用法' in s for s in FakeBrain.seen_inputs), \
        FakeBrain.seen_inputs
    got = next(s for s in FakeBrain.seen_inputs if 'X 的用法' in s)
    assert '- x — 测试动作' in got, got            # 与清单同一段渲染
    print('t_doc_returns_entry ok: routine/doc 回的就是清单那一项')


async def t_system_assembled() -> None:
    """Moss 按名单拼 persona+capability 注入 Ghost; 脑子每拍收同
    一个前缀(x 回执不驱动回声: 每轮单拍)."""
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['<main:x/>', '<main:x/>']
    async with _env(['x'], ['prompt/persona', 'prompt/capability']):
        await _turn('一')
        await _turn('二')
    assert len(FakeBrain.seen_systems) == 2, FakeBrain.seen_systems
    sys1 = FakeBrain.seen_systems[0]
    # 不重拼: 每拍字节级同一个前缀(前缀缓存命中的前提)
    assert all(s == sys1 for s in FakeBrain.seen_systems), FakeBrain.seen_systems
    # persona 段(稳定在前) + capability 段(易变在后)都进了
    assert 'Moss' in sys1, sys1
    assert '- x' in sys1, sys1
    # 排序纪律: persona 在 capability 前(稳定在前, 易变在后)
    assert sys1.index('Moss') < sys1.index('- x'), sys1
    print('t_system_assembled ok: assembled once, stable prefix both turns')


async def t_no_markers() -> None:
    """能力清单里一个 ``⟨…⟩`` 都不许有: 名字就是名字, 加说明就是 description.

    括注原来挂在两处: 签名后的 ``⟨body: …⟩``/``⟨observe⟩`` 与裸名后的
    ``⟨body⟩``/``⟨observe⟩``. 它们想通告的机制都不在清单上 —— body 怎么写
    看它自己的 description("body = 要说的话"), 这一次要不要结果看标签属性.
    同一条事实说两遍的代价是每拍都要付, 而收益是零.
    """
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env(['x', 'obs'], ['prompt/capability'], extra=(Obs,)):
        await _turn('一')
    s = FakeBrain.seen_systems[0]
    # 无参数时签名不长空括号: 行首是 `- 名字`, 不是 `- 名字()`
    obs_line = next(ln for ln in s.splitlines() if ln.startswith('- obs'))
    x_line = next(ln for ln in s.splitlines() if ln.startswith('- x'))
    assert obs_line == '- obs — 查询类: 结果自动回到眼前', obs_line
    assert x_line == '- x — 测试动作', x_line
    assert '⟨' not in s and '⟩' not in s, s
    print('t_no_markers ok: 清单只有名字与说明, 没有 ⟨body⟩/⟨observe⟩ 括注')


async def t_body_declared() -> None:
    """``meta['body']`` **默认是不收**, 收 body 的类自己写 ``True``.

    meta 是机制的输入: 机制读的每个键都该在 meta 里看得见. 靠继承推导出来的
    True, 读 catalog/看日志的人说不清它是哪来的. 而漏写的代价是**响的**
    (喂 body 当场拒 + 一句"内容只能写进参数"), 不是静默 —— 所以默认该落在
    "不收"这一侧.
    """
    from ..container import All, AnyOf, Container, Flow, Loop
    from ..ctml import CtmlRoutine
    from ..commands.board.pixel import BbPixel
    from ..commands.board.write import BbWrite
    from ..commands.code.code import Code
    from ..commands.say import Say

    # 全族都要显式声明(容器族靠 **Container.meta 展开, 也是声明)
    for cls in (Container, All, AnyOf, Flow, Loop,
                Say, Code, BbWrite, BbPixel):
        assert issubclass(cls, CtmlRoutine), cls.__name__
        assert cls.meta.get('body') is True, (cls.__name__, cls.meta)
    for cls in (X, Obs, Sig):
        assert not issubclass(cls, CtmlRoutine), cls.__name__
        assert 'body' not in cls.meta, (cls.__name__, cls.meta)

    # 在 CtmlRoutine 下面但没写: 就是**不收** —— 机制照"不收"办, 不会静默
    class _Default(CtmlRoutine):
        meta = {'description': '内容写参数'}

    assert 'body' not in _Default.meta, _Default.meta
    assert _Default.meta is not Say.meta     # 各写各的, 不串
    # 键之外一个字没动(说明/示例原样, 没有第二份文档)
    assert '说一句话' in Say.meta['description'] or 'body' in Say.meta['description']
    print('t_body_declared ok: 默认不收, 收 body 的类显式写 True')


async def t_query_observed() -> None:
    """只读能力 = 查询能力 → 必须**出厂**声明 ``observe``; 动作类不许.

    查询类调它就是为了那一句读数。不声明就得靠模型每次在标签上手写
    ``observe="true"``，而清单**已经不印** ⟨observe⟩ 标记了（2026-10-25
    撤销）—— 模型连线索都没有，于是"调了一下，什么都没看见"，拍结束就
    过去了，它还以为自己查过了。car 系列漏的正是这一条。

    动作类反过来：发出去就算做完（"好了，走了"），结果下一拍自然出现，
    声明了反而每动一下多开一拍去等一个"跑完，已停"。
    """
    from app.routines.user.car.map import CarMap
    from app.routines.user.car.move import CarMove
    from app.routines.user.car.snap import CarSnap
    from app.routines.user.car.status import CarStatus
    from app.routines.user.car.stop import CarStop
    from app.routines.user.car.track import CarTrack
    from app.routines.user.car.where import CarWhere
    from app.routines.user.xml.read_image import ReadImage
    from ..commands.query_weather import QueryWeather

    for cls in (CarStatus, CarWhere, CarMap, CarStop, CarSnap, QueryWeather):
        assert cls.meta.get('observe') is True, (cls.name, cls.meta)
    for cls in (CarMove, CarTrack):
        assert 'observe' not in cls.meta, (cls.name, cls.meta)
    # 跨身体共用的通用件不许在自己的 meta 里带这具身体的调用语法:
    # 回执归装配声明(app/moss.yaml 的 observe:), 行为链见 t_doc_returns_entry.
    assert 'observe' not in ReadImage.meta, ReadImage.meta
    import yaml
    cfg = yaml.safe_load((_HERE.parents[3] / 'moss.yaml').read_text('utf-8'))
    moss_entry = next(e for e in cfg['routines']
                      if isinstance(e, dict)
                      and 'moss/moss.py' in str(e.get('path')))
    declared = set(moss_entry['kwargs'].get('observe') or [])
    assert {'routine/doc', 'read_image'} <= declared, declared
    print('t_query_observed ok: 自家查询类出厂 observe, 通用件的回执归装配声明')


async def t_capability_snapshot() -> None:
    """capability 渲染装配时快照: 装配后热挂的词不进前缀(不自动刷新)."""
    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['<main:x/>']
    async with _env(['x'], ['prompt/capability']):
        await _req('shell', 'registry', {'op': 'reg', 'routine': 'y'})
        await _turn('一')
    sys1 = FakeBrain.seen_systems[0]
    assert '- x' in sys1, sys1
    assert '- y' not in sys1, sys1   # 热注册不刷新前缀(要新前缀就显式重组)
    print('t_capability_snapshot ok: assembly-time snapshot, no auto refresh')


async def t_capability_follows_verbs() -> None:
    """提示词跟能力走: 能力没进词表, 它那几行就不会出现在前缀里.

    三件事:
    1. 动作进了词表 → 它的 ``meta['example']`` 随行出现;
    2. 同一个动作不在词表 → 行与示例都不在(不是"文档里还讲着");
    3. 容器进词表 → 共享结构说明只印一次(四个容器同一份文本).
    """
    from ..container import All, AnyOf, Loop

    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['<main:x/>']
    async with _env(['x'], ['prompt/capability']):
        await _turn('一')
    s1 = FakeBrain.seen_systems[0]
    assert 'X 的用法' in s1, s1        # 1) 进词表 → 示例在
    assert '跨轨栅栏' not in s1, s1    # 容器没在词表 → 容器备注不在

    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env([], ['prompt/capability']):
        await _turn('二')
    s2 = FakeBrain.seen_systems[0]
    assert '- x' not in s2, s2
    assert 'X 的用法' not in s2, s2    # 2) 未注入 → 行与示例都不在

    FakeBrain.seen_systems.clear()
    FakeBrain.script = ['ok']
    async with _env([], ['prompt/capability'], (All, AnyOf, Loop)):
        await _turn('三')
    s3 = FakeBrain.seen_systems[0]
    assert '跨轨栅栏' in s3, s3
    assert s3.count('跨轨栅栏') == 1, s3   # 3) 共享段只印一次
    print('t_capability_follows_verbs ok: example follows the word list')


async def t_one_noun() -> None:
    """教学面只有一个名词: **routine**(万物皆 routine).

    系统里不存在"命令/动作/指令"这第二种单位, `commands/` 只是分类目录.
    出现第二个名词不是文风问题, 是**语义事故**: 模型会以为存在两种调用
    方式、两套生命周期, 于是去猜该用哪个.

    顺带守住另一半: meta 是**能力名片**, 里面不许出现**单边**的调用示范.
    description / body 一句话谁都读, 必须前端中立; example 可以示范标签
    形态, 但同一行必须给出对应的代码写法(⇔ / call(...)) —— 只示范一种,
    就是把某个前端当成了唯一说法(而这个 routine 未必长在它上面).

    还要守住**术语原词**: 标签中间那段叫 **body**(代码面 `meta['body']` /
    `call(name, body=...)` 同名), 不许退回到"体/标签体"这种自造中译 ——
    那会让模型多做一次中英映射, 而映射错的那次就是"把内容塞进参数".
    """
    tag = re.compile(r'(^|\s)</?[A-Za-z_]')
    nouns = ('动作', '命令', '指令')
    # 窄组合, 只抓"体"当术语用的样子: 单字 "体" 太常见(身体/具体/整体),
    # 先去掉这些固有词, 再看剩下的地方有没有术语用法.
    wrong_body = re.compile(
        r'⟨体|标签体|带体|吃体|收体|喂体|写体|投体|段体|等体|体是|体里|'
        r'体内|体边|体的|体增量|体不进|体不再|体终结')
    noise = re.compile(r'身体|具体|整体|实体|体系|体检|一体|对话体|物体|主体')

    def _no_body_word(text: str, where: str) -> None:
        hit = wrong_body.search(noise.sub('', text))
        assert hit is None, \
            f'{where} 把 body 写成了 {hit.group()!r}(术语只有一个: body)'

    from ..commands import asr_mode as _asr
    from ..commands import code as _code
    from ..commands import say as _say
    from ..commands.chan import (ChanClear, ChanCreate, ChanDelete,
                                 ChanStatus)
    from ..commands.control import Interrupt
    from ..commands.noop import Noop
    from ..commands.query_weather import QueryWeather
    from ..commands.sleep import Sleep
    from ..container import All, AnyOf, Flow, Loop

    classes = [ChanCreate, ChanStatus, ChanClear, ChanDelete, _say.Say,
               _code.Code, _asr.AsrMode, Sleep, Noop, Interrupt,
               QueryWeather, All, AnyOf, Flow, Loop]
    for c in classes:
        # body 不再是"一句话的字段": 它被 CtmlRoutine 盖成机制旗(True), 所以
        # 教学文本的检查只认作者写的两处(description / example).
        for k in ('description', 'example'):
            text = str(c.meta.get(k) or '')
            for w in nouns:
                assert w not in text, \
                    f'{c.name}.{k} 出现第二种名词 {w!r}: {text}'
            if k == 'example':
                # 示例可以示范两种前端, 但**必须成对**给出对应关系
                # (⇔ / call(...)): 只示范标签形态 = 把某个前端当成了唯一
                # 说法, 而这个 routine 未必长在那个前端上.
                for ln in text.splitlines():
                    if tag.search(ln):
                        assert '⇔' in ln or 'call(' in ln, \
                            f'{c.name}.example 只给了一种前端的写法 ' \
                            f'(缺对应关系): {ln}'
            else:
                # 名片与 body 那句话必须前端中立: 谁都能读, 谁都不被绑死.
                assert not tag.search(text), \
                    f'{c.name}.{k} 里写了标签语法(该由 protocol 讲): {text}'
            _no_body_word(text, f'{c.name}.{k}')

    # 教学文本同理(协议文本里当然有标签语法, 那是它的职责)
    base = _HERE.parent / 'prompt'
    for name in ('persona.md', 'protocol.md', 'creator.md'):
        text = (base / name).read_text(encoding='utf-8')
        for w in nouns:
            assert w not in text, f'{name} 出现第二种名词 {w!r}'
        _no_body_word(text, name)
    print('t_one_noun ok: routine is the only noun; body keeps its name; '
          'meta carries no syntax')


async def t_registry_read() -> None:
    """registry list: 只读快照(词表 + 详列名单); reg 后下次读可见."""
    async with _env(['x'], [], detail=['x']):
        # 出厂词表自动在册(单一出处 = moss.BUILTIN_VERBS)
        expect = set(BUILTIN_VERBS) | {'x'}
        snap = await _req('shell', 'registry', {'op': 'list'})
        # detail 是装配参数, 跟词表走同一份快照(能力面渲染要它)
        assert snap == {'verbs': sorted(expect), 'detail': ['x']}, snap
        await _req('shell', 'registry', {'op': 'reg', 'routine': 'y'})
        snap = await _req('shell', 'registry', {'op': 'list'})
        # 热注册只加词表项: 新名字默认裸名(要详列就重组前缀)
        assert snap == {'verbs': sorted(expect | {'y'}), 'detail': ['x']}, snap
    print('t_registry_read ok: list is read-only snapshot')


async def t_creator_surface() -> None:
    """creator/dynamic 装配面: 提示词可产、实验场有锚点、运维桥入清单."""
    import yaml
    from ..dynamic import DynamicProbe

    text = await Creator().run({})
    assert 'routine/check_name' in text and 'shell/registry' in text, text
    assert '真实自验' in text and 'dynamic' in text, text
    # creator 不只教写作/挂载, 也教新 routine 的结果如何驱动下一拍.
    assert 'observe="true"' in text and "'observe': True" in text, text
    assert '不要把它写进 `Input` schema' in text, text
    assert '只用 `code` 的 `await call` 自验，验证不到这条回灌链路' in text, text
    # 原始回执可被 code 读取, 但 stdout 会进 content: 示例不得整包打印.
    assert '**不要 `print()` 整个返回字典**' in text, text
    assert 'print(mounted)' not in text and 'print(result)' not in text, text
    assert "print('mount:', mounted.get('content'))" in text, text
    probe = await DynamicProbe().run({})
    assert probe['details']['ok'] is True, probe

    cfg = yaml.safe_load((_HERE.parents[3] / 'moss.yaml').read_text('utf-8'))
    paths = [str(e.get('path')) if isinstance(e, dict) else str(e)
             for e in cfg['routines']]
    assert 'routines/user/moss/dynamic' in paths, paths
    assert 'routines/user/moss/registry.py' in paths, paths
    assert 'routines/user/routine' in paths, paths
    moss_entry = next(e for e in cfg['routines'] if isinstance(e, dict)
                      and e.get('path') == 'routines/user/moss/moss.py')
    prompts = moss_entry['kwargs']['prompts']
    assert 'prompt/creator' in prompts, prompts
    assert prompts.index('prompt/creator') < prompts.index('prompt/capability')
    print('t_creator_surface ok: creator + dynamic + registry assembled')


async def main() -> None:
    await t_creator_surface()
    await t_system_assembled()
    await t_param_desc()
    await t_detail_split()
    await t_detail_unknown()
    await t_doc_returns_entry()
    await t_capability_snapshot()
    await t_capability_follows_verbs()
    await t_no_markers()
    await t_body_declared()
    await t_query_observed()
    await t_one_noun()
    await t_registry_read()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
