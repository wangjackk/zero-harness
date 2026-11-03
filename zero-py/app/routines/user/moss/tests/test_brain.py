"""真脑接线: Brain 的 ``stream`` 接 zero-py 的 LLMClient(brain_factory
注入 Ghost). 假脑注入(覆盖 ``_make_client``), 不碰网络. 验证四件事:
1. 记忆 → input items 转译: user/assistant 原样, obs 帧文本化;
2. 全链路一轮: declare → LLM 字流 → shell/channel → 动作结果回灌记忆;
3. 多轮: 第二轮脑子看得见自己的话(assistant)与上轮观测(obs 文本),
   instructions 两轮相同(装配前缀稳定, 前缀缓存友好);
4. 回声驱动语义(注意债 owed): 回执默认只记账, **带债的回执才点灯** ——
   违规 error 自带债(运行时强制), 所以当拍即续一拍矫正; 未声明的成功
   回执不驱动, 经 pending 入史, 下一轮主拍缝合缝看见.

用法: ``python -m app.routines.user.mossv2.tests.test_brain``
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))

from routine import Routines, kernel
from routine.hub import RoutineHub
from routine.routine import PassiveConfig, Routine, subscribe

from ...agents._core.llm import Completed, ReasoningDelta, TextDelta
from ..brain import Brain, input_items
from ..channel import Channel
from ..commands.say import Say
from ..ghost import Ghost
from ..mindflow import Mindflow
from ..moss import Moss
from ..modules import MODULES
from ..prompt import Persona
from ..shell import Shell


class X(Routine):
    """一次性动作 routine: x."""

    name = 'x'

    async def run(self, kwargs):
        await asyncio.sleep(0.05)
        return {'content': 'ok'}

    async def stop(self) -> None:
        pass


class FakeClient:
    """假脑: 按轮吐脚本字流, 记录每次收到的 input/instructions.
    ``think`` 每轮先吐一遍思考(reasoning delta) —— 正文之前/之间都可能,
    实测模型边说边想."""

    def __init__(self, script: list[str], think: list[str] | None = None):
        self.script = script
        self.think = list(think or ())
        self.calls: list[dict] = []

    async def stream(self, input, *, instructions=None, **kw):
        self.calls.append({
            'input': [dict(i) for i in input],
            'instructions': instructions,
        })
        text = self.script[len(self.calls) - 1]
        for t in self.think:
            yield ReasoningDelta(text=t)
        for i in range(0, len(text), 7):
            yield TextDelta(text[i:i + 7])
            await asyncio.sleep(0.01)
        yield Completed('rid-fake', text)


def _ghost(script: list[str],
           think: list[str] | None = None,
           images: bool = False) -> tuple[type, FakeClient]:
    """Brain 子类 + 假脑(覆盖 _make_client, 不碰网络).

    ``images``: 假脑自报吃不吃图(真脑从 models.yaml 的 ``image`` 读) ——
    组装侧据此决定附不附图, 假的也必须能表这个态, 否则测不到图那条路.
    """
    client = FakeClient(script, think)
    client.supports_images = images

    class B(Brain):
        def _make_client(self):
            return client

    return B, client


_cmds: asyncio.Queue = asyncio.Queue()
_think_seen: list[str] = []   # 订阅 moss/think(思考流)的收货清单


class Sim(Routine):
    """测试探针: ('req', name, event, data, fut) 代发 req."""

    name = 'sim'
    is_passive = True

    @subscribe('think', namespace='moss')
    async def _grab_think(self, source, data) -> None:
        """思考流只在广播里出现: 记下来证明它确实出去了(且只走这条)."""
        _think_seen.append(str(data))

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


async def _req(name: str, event: str, data: dict | None = None):
    fut = asyncio.get_running_loop().create_future()
    _cmds.put_nowait(('req', name, event, data or {}, fut))
    return await asyncio.wait_for(fut, timeout=15)


async def _turn(text: str) -> list[dict]:
    """一轮对话: 用户的话进桥梁(declare 即回), 等泵静默(idle 屏障, 含
    回声帧); 返回本轮全部观测帧(拍链各段 obs 汇总)."""
    await _req('mindflow', 'declare', {'text': text})
    await _req('mindflow', 'idle')
    return await _round_obs()


async def _round_obs() -> list[dict]:
    """读最后一轮 user 之后的观测帧(回执不驱动回声: 拍收后陆续迟到
    入史, 需要时轮询此读数)."""
    hist = (await _req('ghost', 'recall'))['history']
    users = [i for i, h in enumerate(hist) if h['role'] == 'user']
    u = users[-1] if users else -1
    return [f for h in hist[u + 1:] if h['role'] == 'obs' for f in h['frames']]


async def _wait_obs(n: int) -> list[dict]:
    """轮询等本轮观测攒齐 n 帧(回执迟到: close 不等在途, 动作帧可能
    在 idle 之后才落账)."""
    out: list[dict] = []
    for _ in range(100):
        out = await _round_obs()
        if len(out) >= n:
            break
        await asyncio.sleep(0.05)
    return out


@asynccontextmanager
async def _env(brain: type, verbs: tuple[str, ...] = ('x',),
               extra: tuple = (), prompts: tuple[str, ...] = ('prompt/persona',)):
    kref = await kernel.create(MODULES)
    rs = Routines()
    moss = type('M', (Moss,), {
        'name': 'moss',
        'is_passive': PassiveConfig(
            kwargs={'verbs': list(verbs), 'prompts': list(prompts)}),
    })
    hub = RoutineHub(rs)
    Ghost.brain_factory = brain   # 换脑注入(与 Say.engine_factory 同构)
    rs.register(moss, Ghost, Shell, Channel, Sim, X, Persona, Say,
                Mindflow, *extra)
    conn = await hub.connect(kref)
    try:
        await asyncio.sleep(1.5)   # moss 带起 ghost + shell
        yield
    finally:
        await conn.close()
        Ghost.brain_factory = None   # 复位, 不跨用例泄漏


async def t_input_items() -> None:
    """转译: 输入按原版块形态渲染 —— 一拍一条 ``<percepts>``,
    听来的话包 ``<listen source created>``, 文本裸行, 观测/中断/瞬态
    各成一块(不再用 ``[源]`` 前缀)."""
    ts = 1_600_000_000.0   # 固定墙钟: 只断言格式, 不绑运行时刻
    items = input_items([
        {'role': 'user', 'text': 'hi'},
        {'role': 'user', 'text': '听到的', 'source': 'asr', 'created': ts},
        {'role': 'assistant', 'text': '<a:x/>done'},
        {'role': 'obs', 'frames': [
            {'kind': 'error', 'verb': 'boom', 'text': 'bang'},
            {'kind': 'action', 'verb': 'x', 'result': {'ok': 1}},
        ]},
        {'role': 'interrupted', 'reason': 'turn timeout'},
        {'role': 'transient', 'text': '电量80%', 'source': 'power'},
    ])
    # 连续输入(打字 + 听来) 合并成一拍: 文本裸行, 听来的带 listen 属性
    p = items[0]
    assert p['role'] == 'user' and p['content'].startswith('<percepts>\n'), p
    assert p['content'].endswith('\n</percepts>'), p
    assert '\nhi\n' in p['content'], p
    assert '<listen source="asr" created="D' in p['content'], p
    assert items[1] == {'role': 'assistant', 'content': '<a:x/>done'}, items[1]
    # 观测 = 一条回执一行, 仍是 frame_line 的唯一写法
    ob = items[2]['content']
    assert ob.startswith('<observe>\n') and ob.endswith('\n</observe>'), ob
    assert 'error(boom): bang' in ob, ob
    assert 'x: ok' in ob, ob
    # 中断帧 = 环境事实(下一拍可见上一拍怎么断的)
    assert items[3] == {'role': 'user',
                        'content': '<stop_reason>turn timeout</stop_reason>'}, items[3]
    dc = items[4]['content']
    assert dc.startswith('<dynamic_context>\n') and 'power: 电量80%' in dc, dc
    # 回读同源: 灵魂 recall 问脑子"你读到什么", 拿到的就是喂进去的这份
    # (render_history 只是把同一份渲染换个入口, 不是第二套逻辑)
    brain = Brain.__new__(Brain)      # 不走 __init__: 不造 client、不碰网络
    hist = [{'role': 'user', 'text': 'hi'}]
    assert brain.render_history(hist) == input_items(hist, supports_images=False), \
        '回读必须复用 stream 的渲染'
    print('t_input_items ok: percepts/listen/observe/stop_reason/dynamic_context')


#: 16x16 纯红 PNG(80 字节, 手搓的合法图 —— 真模型会真的解它: 一个字节不对
#: 就是 400 "unsupported image", 所以测试既不能用坏图, 也不能只造个假文件)
_PNG_RED = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAIAAACQkWg2AAAAF0lEQVR4nGP4z8BA'
    'EiJN9aiGUQ1DSgMAkPn/Afnh+ngAAAAASUVORK5CYII=')


def t_image_blocks() -> None:
    """read_image 交回的图真进了 prompt: 多模态 blocks, 且**只挂最近一次**.

    三件事，每一件错了都不报错：

    1. 图得是 block（``input_text`` + ``input_image``）而不是塞进 ``content``
       那句文本 —— 一张截图 base64 几十万字符，写进一行等于把上下文烧光；
    2. 只挂**最后一条**带图的观测：历史全量重放，每拍都发一遍图，三拍就爆
       窗口。更早的观测只剩那行元数据（路径/尺寸/字节数都在，要看再读一次）；
    3. 路径读不出来（文件被删/不是图片）就退回纯文本，不发坏 URL ——
       否则整次请求 400，模型连那句"我读到了一张图"都看不见。
    """
    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, 'a.png')
        with open(img, 'wb') as f:
            f.write(_PNG_RED)
        history = [
            {'role': 'obs', 'frames': [
                {'kind': 'action', 'verb': 'read_image',
                 'result': {'content': 'Read image: a.png', 'images': [img]}}]},
            {'role': 'assistant', 'text': '看过了'},
            {'role': 'obs', 'frames': [
                {'kind': 'action', 'verb': 'read_image',
                 'result': {'content': 'Read image: a.png', 'images': [img]}}]},
        ]
        items = input_items(history, supports_images=True)
        first, last = items[0]['content'], items[-1]['content']
        # 早的那条只有文本(图已经过景了)
        assert isinstance(first, str) and first.startswith('<observe>\n'), first
        # 最近那条: 文本在前图在后
        assert isinstance(last, list), last
        assert last[0] == {'type': 'input_text',
                           'text': '<observe>\nread_image: Read image: a.png\n'
                                   '</observe>'}, last[0]
        assert last[1]['type'] == 'input_image', last
        assert last[1]['image_url'].startswith('data:image/png;base64,'), last[1]

        # 模型不吃图: 一个 block 都不给(发了就是 400), 但文本照旧
        plain = input_items(history, supports_images=False)
        assert all(isinstance(i['content'], str) for i in plain), plain

        # 路径读不出来: 退回纯文本, 不崩也不发坏 URL
        gone = input_items([{'role': 'obs', 'frames': [
            {'kind': 'action', 'verb': 'read_image',
             'result': {'content': 'x', 'images': [os.path.join(tmp, 'no.png')]}}]}],
            supports_images=True)
        assert isinstance(gone[0]['content'], str), gone[0]

        # 回读(界面): 块的位置留着, base64 不给 —— 也不读磁盘(文件不存在
        # 照样有图块). 心跳每秒拉一次 recall, 真编码就是每秒重读一遍图.
        fake = Brain.__new__(Brain)
        fake._llm = type('L', (), {'supports_images': True})()
        read = fake.render_history([{'role': 'obs', 'frames': [
            {'kind': 'action', 'verb': 'read_image',
             'result': {'content': 'x',
                        'images': [os.path.join(tmp, 'gone.png')]}}]}])
        blocks = read[0]['content']
        assert isinstance(blocks, list), blocks
        assert blocks[1] == {'type': 'input_image', 'image_url': ''}, blocks

    print('t_image_blocks ok: 图 → blocks, 只挂最近一拍, 读不出就退回文本')


async def t_read_image_flow() -> None:
    """端到端：模型写 ``<read_image/>`` → 下一拍真的带着图。

    中间过五层（标签解析 → channel 投递 → routine 读文件 → 回执载荷校验 →
    帧回灌记忆 → 脑子组装 blocks）。任何一层把 ``images`` 抄丢，模型得到的
    都是"Read image: … "一句配一张看不见的图 —— **而且不报错**。单测只证明
    两头会处理，这一条证明中间没断。
    """
    from ...xml.read_image import ReadImage

    with tempfile.TemporaryDirectory() as tmp:
        img = os.path.join(tmp, 'shot.png')
        with open(img, 'wb') as f:
            f.write(_PNG_RED)
        # 读数类出厂 observe → 回执带债、可能自驱一拍, 脚本多备几条
        g, client = _ghost([f'<read_image file_path="{img}"/>', '看到了',
                            '图里是个方块', '嗯'], images=True)
        async with _env(g, verbs=('read_image',), extra=(ReadImage,)):
            await _turn('看看这张图')
            frames = await _wait_obs(1)
            shots = [f for f in frames
                     if (f.get('result') or {}).get('images')]
            assert shots, frames          # 回执里带着图路径
            assert shots[0]['kind'] == 'action', shots[0]
            await _turn('图里是什么')
    feeds = [i for c in client.calls for i in c['input']
             if isinstance(i.get('content'), list)]
    assert feeds, '脑子从来没收到过图块'
    blocks = feeds[0]['content']
    assert any(b.get('type') == 'input_image' for b in blocks), blocks
    print('t_read_image_flow ok: <read_image/> → 回执带图 → 下一拍 input 有图块')


def t_listen_attrs() -> None:
    """听来的话只有 ``paused`` 印成属性；``low_conf`` / ``db`` 一律不印。

    三个读数都由**听侧**算好随包送来（核没有麦克风，脑子没有逐字置信度），
    但这一层只印一个：

    - ``paused`` 只在 ``false`` 时印：现在进脑子的都是收尾包，恒真的属性
      写进每一行只是每拍付一遍字数；但机制留着 —— 哪天中间态也进脑子，
      模型得能分辨"他还在说，别抢答"；
    - ``low_conf`` / ``db`` **暂不印**（2026-10-25）：attrs 里带着也不许漏到
      标签上。漏出去不会报错，只会让模型看见两个它没被告知含义的属性名；
    - 没有的属性不占位（``paused=""`` 只会让模型去猜空串什么意思）。
    """
    ts = 1_600_000_000.0
    items = input_items([
        {'role': 'user', 'text': '天汽不错', 'source': 'asr', 'created': ts,
         'attrs': {'low_conf': '汽', 'db': 54.234, 'paused': True}},
        {'role': 'user', 'text': '还在说', 'source': 'asr', 'created': ts,
         'attrs': {'low_conf': '还', 'db': -35.0, 'paused': False}},
        {'role': 'user', 'text': '干净的一句', 'source': 'asr', 'created': ts},
    ])
    lines = [ln for ln in items[0]['content'].splitlines()
             if ln.startswith('<listen')]
    assert len(lines) == 3, lines
    for ln in lines:                      # 一个都不许漏
        assert 'low_conf' not in ln and 'db=' not in ln, ln
    assert 'paused' not in lines[0], lines[0]      # 收尾包不印（恒真）
    assert 'paused="false"' in lines[1], lines[1]  # 中间包要印
    assert lines[0].count('"') == 4, lines[0]      # 只剩 source/created 两对

    # 转义: 属性值里的尖括号不许撑破块结构（坏 XML 会让模型猜边界）
    esc = input_items([{'role': 'user', 'text': 'x', 'source': 'a<b'}])
    assert 'source="a&lt;b"' in esc[0]['content'], esc[0]['content']
    print('t_listen_attrs ok: 只印 paused, low_conf/db 不上标签')


async def t_listen_attrs_flow() -> None:
    """端到端：declare 带的听侧读数，走到渲染层后**只留下 paused**。

    渲染单测只证明"给了 attrs 会怎么印"；这一条证明 **attrs 能到那儿** ——
    中间过四层（mindflow 仲裁按源归属性 → ghost 开帧 → moment 渲染 →
    brain 拼标签）。两件事一起断：paused 穿到了；low_conf/db 没有漏出去
    （漏出去同样不报错，只是模型看见两个没解释过的属性名）。
    """
    g, client = _ghost(['好'])
    async with _env(g):
        await _req('mindflow', 'declare', {
            'text': '天汽不错', 'source': 'asr',
            'low_conf': '汽', 'db': 54.234, 'paused': False,
        })
        await _req('mindflow', 'idle')
    blob = '\n'.join(str(i.get('content') or '')
                     for i in client.calls[0]['input'])
    assert 'paused="false"' in blob, blob  # 中间态: 读数穿到属性上
    assert 'low_conf' not in blob and 'db=' not in blob, blob
    print('t_listen_attrs_flow ok: 读数到渲染层后只留 paused')


async def t_turn_and_memory() -> None:
    """全链路一轮 + 多轮记忆: 每轮单拍(回执不驱动回声), 第二轮脑子
    看得见自己的话与上轮观测(pending 缝合缝)."""
    g, client = _ghost([
        '<main:x/><main:say>你好</say>',   # 第一轮主拍: x + say(有观测)
        '<main:say>好的</main:say>',       # 第二轮主拍
    ])
    async with _env(g):
        await _turn('一')
        # 回执迟到(close 不等在途): 轮询等 x + say 两帧入史
        r1: list[dict] = []
        for _ in range(100):
            r1 = await _round_obs()
            if len(r1) == 2:
                break
            await asyncio.sleep(0.05)
        assert len(r1) == 2, r1   # x + say 的 action 帧(说话也是动作)
        await _turn('二')
        snap = await _req('ghost', 'recall')
        sys_text = snap['system']
    # 第一轮主拍: input 只有 user(块形态), instructions = 装配前缀(persona)
    c1 = client.calls[0]
    assert c1['input'] == [{'role': 'user',
                            'content': '<percepts>\n一\n</percepts>'}], c1
    assert c1['instructions'] == sys_text, c1
    # 第一轮无回声帧(回执只记账不驱动): 第二次调用即第二轮主拍
    assert len(client.calls) == 2, client.calls
    # 第二轮主拍: 自己的话(assistant) + 上轮观测(<observe>) + 新 user 话
    c3 = client.calls[1]
    roles = [i['role'] for i in c3['input']]
    assert roles == ['user', 'assistant', 'user', 'user'], c3
    assert c3['input'][1]['content'] == '<main:x/><main:say>你好</say>', c3
    obs_text = c3['input'][2]['content']
    # say 也是动作: 回执只报执行事实, 原文整段一份在 assistant 侧 ——
    # echoes 不复读内容(原版 played_message 同构); details(played/words)
    # 没有通往 prompt 的路, 所以观测行只有 content
    assert 'x: ok' in obs_text, obs_text
    assert 'say: ok' in obs_text, obs_text
    assert 'played' not in obs_text, obs_text
    assert c3['input'][2]['content'].startswith('<observe>\n'), obs_text
    assert c3['instructions'] == c1['instructions'], c3
    # 回读同源: recall 报的 model_input 就是脑子吃的那份(拍一只有 user 块,
    # 后拍的帧只往后追加) —— 界面"历史"页显示的是原物, 不是照规则抄的副本
    assert snap['model_input'][0] == c1['input'][0], snap['model_input']
    assert snap['system'] == c1['instructions'], snap['system']
    print('t_turn_and_memory ok: full loop, single beat per turn, '
          'next turn sees self + obs')


async def t_error_retry() -> None:
    """错误自带注意债(与远端动作失败同一条路): 违规当拍即续一拍 ——
    灵魂看见自己的原文与 error 观测, 换对写法补上那声没说出的话.
    用户不必为身体口误再喊一次."""
    g, client = _ghost([
        '<main:say>你好</speak>',      # 第一拍: 闭合名错 → unterminated
        '<main:say>你好</say>',        # 回声拍: 看了 error, 换对写法
    ])
    async with _env(g):
        await _turn('说')
        # 违规带债 → 本拍多一拍回声, 一链两拍
        assert len(client.calls) == 2, client.calls
        # 回声拍的 input 里, 自己的违规原文与 error 观测都在(obs 入史)
        c2 = client.calls[1]
        text = ' '.join((i.get('content') or '') for i in c2['input'])
        assert '<main:say>你好</speak>' in text, c2['input']
        assert 'unterminated' in text, c2['input']
        r1 = await _wait_obs(2)
    # 本拍既有 error 帧, 又有矫正后 say 的 action 帧
    assert any(f['kind'] == 'error' and 'unterminated' in f['text']
               for f in r1), r1
    assert any(f['kind'] == 'action' and f.get('verb') == 'say'
               for f in r1), r1
    print('t_error_retry ok: violation owes a beat, corrected in turn')


async def t_error_echo_once() -> None:
    """违规只买一次回声: 每轮恰好"违规一拍 + 矫正一拍"两拍收链 —— 错误
    不级联、不跨轮残留(矫正即还清, 链自收); 三轮的错误全留记忆."""
    g, client = _ghost([
        '<main:say>甲</speak>', '<main:say>甲</say>',
        '<main:say>乙</speak>', '<main:say>乙</say>',
        '<main:say>丙</speak>', '<main:say>丙</say>',
    ])
    async with _env(g):
        r1 = await _turn('一')
        r2 = await _turn('二')
        r3 = await _turn('三')
    assert len(client.calls) == 6, client.calls   # 每轮两拍, 一拍不多
    for r in (r1, r2, r3):
        assert any(f['kind'] == 'error' for f in r), r
    c6 = client.calls[5]
    # 三轮的错误全留记忆: 末拍看得见前两轮的违规原文与 error
    errs = [i for i in c6['input'] if i['role'] == 'user'
            and 'unterminated' in (i.get('content') or '')]
    assert len(errs) >= 2, c6['input']
    print('t_error_echo_once ok: one echo per violation, '
          'chain closes when corrected')


async def t_reasoning_bypass() -> None:
    """思考走旁路: ``on_reasoning`` 收全, ``stream`` 只 yield 正文 ——
    reasoning delta 永远不进对话流(边说边想的交错顺序也照实分派)."""
    got: list[str] = []

    class ThinkClient:
        async def stream(self, input, *, instructions=None, **kw):
            yield ReasoningDelta(text='想一半, ')
            yield TextDelta(text='你好')
            yield ReasoningDelta(text='边说边想')
            yield TextDelta(text='世界')

    class B(Brain):
        def _make_client(self):
            return ThinkClient()

    brain = B()

    async def sink(t: str) -> None:
        got.append(t)

    brain.on_reasoning = sink
    body = ''.join([c async for c in brain.stream([], '')])
    assert body == '你好世界', body
    assert got == ['想一半, ', '边说边想'], got
    print('t_reasoning_bypass ok: reasoning to sink, text to stream')


async def t_reasoning_not_in_memory() -> None:
    """思考只广播(UI 看见), 不进 logos(记忆)/身体: 帧里记的是它说的话,
    草稿不是承诺."""
    g, _ = _ghost(['<main:say>甲</main:say>'],
                  think=['先想: 要不要说甲', '算了, 就说甲'])
    async with _env(g):
        _think_seen.clear()
        await _turn('说')
        await _wait_obs(1)
        rec = await _req('ghost', 'recall')
    assert ''.join(_think_seen) == '先想: 要不要说甲算了, 就说甲', _think_seen
    logos = [f.get('logos') or '' for f in rec['frames']]
    assert any('<main:say>甲</main:say>' in l for l in logos), logos
    assert not any('先想' in l for l in logos), logos
    print('t_reasoning_not_in_memory ok: broadcast only, '
          'never logos/body')


async def main() -> None:
    await t_input_items()
    t_image_blocks()
    await t_read_image_flow()
    t_listen_attrs()
    await t_reasoning_bypass()
    await t_reasoning_not_in_memory()
    await t_turn_and_memory()
    await t_listen_attrs_flow()
    await t_error_retry()
    await t_error_echo_once()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
