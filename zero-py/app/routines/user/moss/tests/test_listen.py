"""听侧策略层单测：礼仪空间 / 判停三路 / 留存槽。

纯逻辑，不起 kernel —— 这三件在原版里本来就是"可独立测试的出口位点"，
移植后保持同一性质（``stop_judge.py`` 的模块注释原话：independently testable）。

跑法::

    PYTHONPATH="D:/kshell/zero-harness/zero-py/routine-py" \\
    ./.venv/Scripts/python.exe -m app.routines.user.mossv2.tests.test_listen
    ... test_listen t_stop_judge_silence          # 单跑一个
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import time

from ..listen import (
    Clause,
    EtiquetteConfig,
    Event,
    ListenerController,
    Phase,
    Segment,
    SegmentBuffer,
    StopJudge,
    parse_stop_score,
)
from ..listen import etiquette as etq
from ..mindflow import Priority


def _ev(seg: str, phase: Phase, text: str, clause: str | None = None) -> Event:
    return Event(stream_id='s1', segment_id=seg, phase=phase, text=text,
                 clause=Clause(text=clause) if clause else None)


def _ear(kind: str, text: str):
    """假耳朵事件（``EarEvent`` 的最小替身：controller 只读 kind/text/detail）。"""
    from types import SimpleNamespace
    return SimpleNamespace(kind=kind, text=text, detail='')


async def t_etiquette_space() -> None:
    """三轴积 + 11 个开箱坐标：字段默认值与原版逐字段一致，坐标差异可查。"""
    cfg = EtiquetteConfig()
    names = [e.name for e in cfg.etiquettes]
    assert names == ['once', 'always', 'aside', 'notify', 'scribe', 'observer',
                     'keyword_end', 'scored', 'murmur', 'cue', 'knock'], names
    assert cfg.default == 'always' and cfg.active().name == 'always'

    # 三个旋钮的出厂默认（原版 OnsetSpec / DeliverSpec / StopSpec）
    on = etq.OnsetSpec()
    assert (on.emit, on.interrupt, on.priority) == (True, True, Priority.WARNING)
    dv = etq.DeliverSpec()
    assert (dv.emit, dv.history, dv.interrupt, dv.priority, dv.mode) == \
        (True, 8, False, Priority.INFO, 'notify')
    st = etq.StopSpec()
    assert (st.silence, st.keywords, st.classifier, st.detect) == (1.5, [], None, None)

    # 坐标之间的关键差异
    assert cfg.get('once').onset.emit is False
    assert cfg.get('once').stop.silence == 0.0
    assert cfg.get('observer').deliver.emit is False
    assert cfg.get('observer').deliver.history == 16
    assert cfg.get('knock').stop.detect is not None
    assert cfg.get('knock').idle_timeout == 0
    assert cfg.get('scribe').deliver.mode == 'next'
    assert cfg.get('aside').deliver.mode == 'aside'
    assert cfg.get('aside').onset.interrupt is False
    assert cfg.get('keyword_end').stop.keywords == ['over', '完毕']
    assert cfg.get('scored').stop.classifier.threshold == 7
    assert sum(1 for e in cfg.etiquettes if e.stop.classifier) == 1, '只有 scored 挂分类器'

    # 运行期增改（模型 set_etiquette_spec 的同构面）
    mine = cfg.get('always').model_copy(deep=True)
    mine.name = 'mine'
    mine.stop.silence = 0.4
    cfg.upsert(mine)
    cfg.activate('mine')
    assert cfg.active().stop.silence == 0.4
    try:
        cfg.activate('nope')
    except ValueError:
        pass
    else:
        raise AssertionError('激活未定义的礼仪应抛错')
    print('t_etiquette_space ok: 3 knobs x 11 coords, upsert/activate')


async def t_stop_judge_silence() -> None:
    """静音路径：每个新分句展期，到期只 commit 一次，新段重置。"""
    commits: list[float] = []
    j = StopJudge(commit=lambda: commits.append(time.monotonic()),
                  segment_vad=0.3)
    try:
        await j.feed(_ev('seg1', Phase.FIRST, '你'))
        await j.feed(_ev('seg1', Phase.PARTIAL, '你好'))
        assert not commits, 'FIRST/PARTIAL 不 commit'
        await j.feed(_ev('seg1', Phase.CLAUSE, '你好', '你好'))
        await asyncio.sleep(0.18)
        assert not commits, '静音未到期(0.18 < 0.3)'
        # 新分句展期：上一个 deadline 作废
        await j.feed(_ev('seg1', Phase.CLAUSE, '你好呀', '呀'))
        await asyncio.sleep(0.18)
        assert not commits, '展期生效（距新分句 0.18 < 0.3）'
        await asyncio.sleep(0.2)
        assert len(commits) == 1, f'静音到期只 commit 一次: {commits}'
        # 已 commit 的段继续喂不重复提交
        await j.feed(_ev('seg1', Phase.CLAUSE, '你好呀啊', '啊'))
        await asyncio.sleep(0.1)
        assert len(commits) == 1, '同段不重复 commit'
        # 新段重置状态机
        await j.feed(_ev('seg2', Phase.FIRST, '再'))
        await j.feed(_ev('seg2', Phase.CLAUSE, '再来', '再来'))
        await asyncio.sleep(0.4)
        assert len(commits) == 2, '新段重新计时'
    finally:
        j.close()
    print('t_stop_judge_silence ok: 展期 / 单次 commit / 新段重置')


async def t_stop_partial_extends_silence() -> None:
    """``partial`` 也展期静音计时：人一直在说，就不该被"没有新分句"切段.

    真事（2026-10-20 实测日志）：服务端迟迟不吐新的 ``utterance``（它自己的
    VAD 还没判停），而我们这边的 ``silence`` 只认 CLAUSE —— 3 秒一到就把
    "去看看我电脑"交了出去，紧接着下半句"里面应该…"另起一段，一句话变成
    两条交付，模型看到的是重复的两截。
    """
    commits: list[float] = []
    j = StopJudge(commit=lambda: commits.append(time.monotonic()),
                  segment_vad=0.3)
    try:
        await j.feed(_ev('seg1', Phase.CLAUSE, '去看看我电脑', '去看看我电脑'))
        # 连续 partial（每 0.1s 一条，累计 0.6s > 0.3s）：一次都不该交
        phrase = '去看看我电脑里面应该'
        for i in range(6):
            await asyncio.sleep(0.1)
            await j.feed(_ev('seg1', Phase.PARTIAL, phrase[:4 + i]))
            assert not commits, f'还在说，不该 commit（第 {i + 1} 次 partial）'
        # 真的静下来了：到期交一次
        await asyncio.sleep(0.45)
        assert len(commits) == 1, f'静下来才 commit: {commits}'
    finally:
        j.close()
    print('t_stop_partial_extends_silence ok: partial 展期, 静下来才交')


async def t_stop_judge_routes() -> None:
    """关键词立刻交、分类器高分提前交、低分不交、防抖窗口内不花调用。"""
    # 1) 关键词：不等静音
    kw: list[str] = []
    j = StopJudge(commit=lambda: kw.append('kw'), segment_vad=30.0,
                  keywords=['完毕'])
    try:
        await j.feed(_ev('s', Phase.CLAUSE, '好的完毕', '好的完毕'))
        assert kw == ['kw'], '关键词命中立刻 commit'
    finally:
        j.close()

    # 2) 分类器高分：提前 commit
    calls: list[str] = []
    hits: list[int] = []

    async def high(prompt: str) -> str:
        calls.append(prompt)
        return '8'

    j = StopJudge(commit=lambda: hits.append(1), segment_vad=30.0,
                  caller=high, threshold=7, judge_delay=0.02)
    try:
        await j.feed(_ev('s', Phase.CLAUSE, '今天天气不错', '今天天气不错'))
        await asyncio.sleep(0.25)
        assert hits == [1], f'8 >= 7 应提前 commit: {hits}'
        assert len(calls) == 1 and '今天天气不错' in calls[0]
    finally:
        j.close()

    # 3) 分类器低分：不 commit（等静音兜底）
    hits2: list[int] = []

    async def low(prompt: str) -> str:
        return '3'

    j = StopJudge(commit=lambda: hits2.append(1), segment_vad=30.0,
                  caller=low, threshold=7, judge_delay=0.02)
    try:
        await j.feed(_ev('s', Phase.CLAUSE, '因为', '因为'))
        await asyncio.sleep(0.2)
        assert not hits2, f'3 < 7 不应 commit: {hits2}'
    finally:
        j.close()

    # 4) 防抖：安全期内被下一句取代 → 一次调用都不花
    calls2: list[str] = []

    async def slow(prompt: str) -> str:
        calls2.append(prompt)
        return '9'

    j = StopJudge(commit=lambda: None, segment_vad=30.0,
                  caller=slow, judge_delay=0.15)
    try:
        await j.feed(_ev('s', Phase.CLAUSE, '一', '一'))
        await asyncio.sleep(0.05)
        await j.feed(_ev('s', Phase.CLAUSE, '一二', '二'))
        await asyncio.sleep(0.05)
        await j.feed(_ev('s', Phase.CLAUSE, '一二三', '三'))
        await asyncio.sleep(0.4)
        assert len(calls2) == 1, f'防抖窗口内被取代不花调用: {len(calls2)}'
        assert calls2[0].count('\n') == 2, f'最后一次应带全部三句: {calls2[0]!r}'
    finally:
        j.close()

    # 5) 打分解析
    assert parse_stop_score(' 7') == 7
    assert parse_stop_score('score: 9.') == 9
    assert parse_stop_score('无法判断') is None
    print('t_stop_judge_routes ok: keywords / classifier high-low / debounce')


async def t_segment_buffer() -> None:
    """留存槽：增长全文、定稿入历史、迟到事件不回填、环形溢出、drain 取走。"""
    buf = SegmentBuffer(history=2)
    buf.on_event(_ev('seg1', Phase.PARTIAL, '你'))
    buf.on_event(_ev('seg1', Phase.PARTIAL, '你好'))
    assert buf.peek_current() is not None and buf.peek_current().text == '你好'
    # TAIL 是切段标记，不更新 current（定稿交给 on_segment）
    buf.on_event(_ev('seg1', Phase.TAIL, '你好'))
    assert buf.peek_current().text == '你好'
    buf.on_segment(Segment(id='seg1', stream_id='s1', text='你好',
                           clauses=[Clause(text='你好')]))
    assert buf.peek_current() is None, '签发后 current 清空'
    assert [h.text for h in buf.peek_recent()] == ['你好']
    assert buf.peek_recent()[-1].to_dict()['clauses'] == ['你好']
    # 已定稿段的迟到文本不得回填 current
    buf.on_event(_ev('seg1', Phase.PARTIAL, '迟到'))
    assert buf.peek_current() is None, '迟到事件被 _last_finalized_id 拦掉'
    # 环形溢出（history=2）
    for i in (2, 3, 4):
        buf.on_segment(Segment(id=f'seg{i}', stream_id='s1', text=f'第{i}句'))
    assert buf.forgotten() >= 2, buf.forgotten()
    assert [h.segment_id for h in buf.peek_recent()] == ['seg3', 'seg4']
    # drain = 人类强发送：current 在前，历史随后，取完清空
    buf.on_event(_ev('seg5', Phase.PARTIAL, '还没说完'))
    out = buf.drain()
    assert [h.text for h in out] == ['还没说完', '第3句', '第4句'], [h.text for h in out]
    assert buf.peek_current() is None and buf.peek_recent() == []
    # 缩容计入 forgotten
    buf2 = SegmentBuffer(history=4)
    for i in range(4):
        buf2.on_segment(Segment(id=f'x{i}', stream_id='s1', text=f'x{i}'))
    buf2.resize(2)
    assert buf2.forgotten() == 2, buf2.forgotten()
    print('t_segment_buffer ok: current / finalize / overflow / drain')


async def t_listener_controller() -> None:
    """控制器：起话占坑（complete=False）→ 判停 → 交付填坑（同 id）。"""
    sent: list[dict] = []

    async def emit(**fields):
        sent.append(fields)
        return {'verdict': 'accepted'}

    cfg = EtiquetteConfig()
    fast = cfg.get('always').model_copy(deep=True)
    fast.name = 'fast'
    fast.stop.silence = 0.1          # 测试缩短静音
    cfg.upsert(fast)
    cfg.activate('fast')
    ctl = ListenerController(emit, config=cfg)
    try:
        # 说话中：首次非空 → 一条起话包
        await ctl.feed(_ear('partial', '今天'))
        assert len(sent) == 1, sent
        onset = sent[0]
        assert onset['complete'] is False
        assert onset['interrupt'] is True
        assert onset['priority'] == int(Priority.WARNING)
        assert onset['strength'] == 150
        assert onset['mode'] == ''
        # 定稿一句 → 判停到期 → 交付包，与起话包同 id（吸收填坑）
        await ctl.feed(_ear('utterance', '今天天气怎么样'))
        await asyncio.sleep(0.25)
        assert len(sent) == 2, sent
        deliver = sent[1]
        assert deliver['complete'] is True
        assert deliver['id'] == onset['id'], '同段同 id 才能吸收'
        assert deliver['text'] == '今天天气怎么样'
        assert deliver['mode'] == 'notify'
        assert deliver['priority'] == int(Priority.INFO)
        snap = ctl.snapshot()
        assert snap['commits'] == 1 and snap['delivers'] == 1 and snap['onsets'] == 1
    finally:
        await ctl.aclose()

    # 留存礼仪（observer）：一个信号都不发，模型自己拉
    sent.clear()
    cfg2 = EtiquetteConfig()
    ctl = ListenerController(emit, config=cfg2)
    try:
        assert ctl.set_etiquette('observer') == 'observer'
        await ctl.feed(_ear('partial', '会议'))
        await ctl.feed(_ear('utterance', '会议开始'))
        assert sent == [], f'observer 不推信号: {sent}'
        tr = ctl.transcript()
        assert tr['enabled'] is True
        assert tr['current'] is not None and tr['current']['text'] == '会议开始'
        # 人类强发送：drain 留存，作为一条交付包发出
        r = await ctl.send_now()
        assert 'sent' in r, r
        assert len(sent) == 1 and sent[0]['complete'] is True
        assert sent[0]['text'] == '会议开始'
        assert ctl.transcript()['current'] is None, 'drain 后清空'
    finally:
        await ctl.aclose()

    # 运行期改礼仪（JSON）与未定义名的拒绝
    ctl = ListenerController(emit, config=EtiquetteConfig())
    try:
        assert ctl.set_etiquette('nope').startswith('etiquette'), '未定义名应拒绝'
        spec = ctl.config().get('once').model_copy(deep=True)
        spec.name = 'mine'
        spec.stop.silence = 0.0
        assert ctl.upsert_etiquette(spec.model_dump_json()) == 'mine'
        assert ctl.set_etiquette('mine') == 'mine'
        assert ctl.active().name == 'mine'
    finally:
        await ctl.aclose()
    print('t_listener_controller ok: onset→deliver 同 id / observer 留存 / 切礼仪')


async def t_hearing_facts() -> None:
    """段尾结账：低置信字 + 这一段多响（两个数据面读数，渲染层暂不印）。

    三件事，都是**听侧才知道**的（核没有麦克风，脑子没有逐字置信度）：

    1. 低置信字只列**字**、不列分数，且 ``conf is None``（引擎没报，不是
       低分）不算 —— 把"没报"当"可疑"会让模型对着正常的话发问；
    2. ``db`` 是**这一段**的均值：采样发生在收到文本的那一刻（人一定在
       说话），段尾结算后清零 —— 所以第二段不会带着第一段的读数；
    3. 起话包没有这两样（段还没结账）；阈值没配（``None``）就不列字。
    """
    sent: list[dict] = []
    levels = [-30.0, -40.0]

    async def emit(**fields):
        sent.append(fields)
        return {'verdict': 'accepted'}

    def level():
        return levels.pop(0) if levels else -60.0

    cfg = EtiquetteConfig()
    fast = cfg.get('always').model_copy(deep=True)
    fast.name = 'fast'
    fast.stop.silence = 0.1          # 测试缩短静音
    fast.deliver.low_confidence = 0.9
    cfg.upsert(fast)
    cfg.activate('fast')
    ctl = ListenerController(emit, config=cfg, level=level)
    try:
        await ctl.feed(_ear('partial', '今天'))
        ev = _ear('utterance', '今天天气')
        ev.words = [{'text': '今', 'conf': 0.99}, {'text': '天', 'conf': 0.42},
                    {'text': '气', 'conf': None}]
        await ctl.feed(ev)
        await asyncio.sleep(0.25)
        deliver = sent[-1]
        assert deliver['complete'] is True, deliver
        assert deliver['low_conf'] == '天', deliver      # 只列低分那个字
        assert deliver['db'] == -35.0, deliver           # (-30 + -40) / 2
        assert deliver['paused'] is True, deliver
        assert ctl.snapshot()['db'] == -35.0, ctl.snapshot()
        # 起话包没有结账读数（段尾才有）
        assert 'low_conf' not in sent[0] and 'db' not in sent[0], sent[0]
        # 第二段从零攒：没有新采样就没有 db，不会沿用上一段的 -35
        await ctl.feed(_ear('utterance', '再来一句'))
        await asyncio.sleep(0.25)
        assert sent[-1]['db'] == -60.0, sent[-1]   # 只剩探针的兜底读数
    finally:
        await ctl.aclose()

    # 阈值没配 = 不列字；探针没挂 = 没有 db（不是 0）
    sent.clear()
    cfg3 = EtiquetteConfig()
    fast3 = cfg3.get('always').model_copy(deep=True)
    fast3.name = 'fast3'
    fast3.stop.silence = 0.1
    cfg3.upsert(fast3)
    cfg3.activate('fast3')
    ctl = ListenerController(emit, config=cfg3)
    try:
        ev = _ear('utterance', '随便说一句')
        ev.words = [{'text': '随', 'conf': 0.01}]
        await ctl.feed(ev)
        await asyncio.sleep(0.25)
        d = sent[-1]
        assert d['low_conf'] == '', d          # 旋钮 off → 空（不是全部字）
        assert d['db'] is None, d              # 没挂探针 → 没这个读数
    finally:
        await ctl.aclose()
    print('t_hearing_facts ok: low_conf 只列字 / db 段内均值 / 起话包无结账')


async def t_listen_witness() -> None:
    """围观针（UI 流式渲染）：partial 推**本段全文**（不是增量），判停推
    一次 final，且 final 先于交付包。

    它和 emit 是两条路：observer 礼仪不申报，围观照旧（用户仍要看见自己
    说的话）；没有针时一切照旧（既有用例覆盖）。
    """
    order: list[str] = []
    said: list[tuple[str, bool]] = []

    async def emit(**fields):
        order.append('emit:final' if fields.get('complete') else 'emit:onset')
        return {'verdict': 'accepted'}

    def witness(text, final):
        said.append((text, final))
        order.append('final' if final else 'partial')

    cfg = EtiquetteConfig()
    fast = cfg.get('always').model_copy(deep=True)
    fast.name = 'fast'
    fast.stop.silence = 0.1
    cfg.upsert(fast)
    cfg.activate('fast')
    ctl = ListenerController(emit, config=cfg, witness=witness)
    try:
        await ctl.feed(_ear('partial', '今天'))
        await ctl.feed(_ear('partial', '今天天气'))
        await ctl.feed(_ear('utterance', '今天天气怎么样'))
        await asyncio.sleep(0.25)
        # 每次都是整段全文（订阅方整行替换即可流式长出来）
        assert said[:3] == [('今天', False), ('今天天气', False),
                            ('今天天气怎么样', False)], said
        finals = [txt for txt, f in said if f]
        assert finals == ['今天天气怎么样'], f'定稿只叫一次: {said}'
        # final 先于交付：交付要等 declare 回执，UI 不该陪着等
        assert order.index('final') < order.index('emit:final'), order
    finally:
        await ctl.aclose()

    # 留存礼仪：不申报，但强发送时补一次定稿（否则行停在 partial）
    said.clear()
    ctl = ListenerController(emit, config=EtiquetteConfig(), witness=witness)
    try:
        ctl.set_etiquette('observer')
        await ctl.feed(_ear('partial', '会议'))
        await ctl.feed(_ear('utterance', '会议开始'))
        await ctl.send_now()
        assert said[-1] == ('会议开始', True), said
    finally:
        await ctl.aclose()
    print('t_listen_witness ok: partial 全文 / final 一次且先于交付 / '
          'observer 强发送补定稿')


async def t_ptt_manual_boundary() -> None:
    """ptt: 段边界只认"松手" —— 静音 / 分类器 / 关键词三把刀全部让位.

    真事（2026-10-20 实测日志）: 按住说话, 中途停了 1.5s, 判停把那半句交
    了出去, 后半句另起一段 —— 一句变两条, 后一条还带着前一条的前缀。用户
    的心智很直: 我按住期间这句没完, 松手才是句号.
    """
    sent: list[dict] = []

    async def emit(**fields):
        sent.append(fields)
        return {'verdict': 'accepted'}

    async def hot(prompt: str) -> str:      # 什么话都判 9 分(最容易误交)
        return '9'

    cfg = EtiquetteConfig()
    fast = cfg.get('scored').model_copy(deep=True)
    fast.name = 'fast'
    fast.stop.silence = 0.1                 # 自动判停会立刻交
    fast.stop.keywords = ['完毕']
    cfg.upsert(fast)
    cfg.activate('fast')
    ctl = ListenerController(emit, config=cfg, caller=hot,
                             manual_boundary=True)
    try:
        await ctl.feed(_ear('partial', '今天'))
        await ctl.feed(_ear('utterance', '今天天气完了完毕'))
        await asyncio.sleep(0.3)             # 远超 silence(0.1), 也超打分防抖
        assert len(sent) == 1, f'ptt 下不该自己判停: {sent}'
        r = await ctl.send_now()             # 松手
        assert r == 'committed', r
        await asyncio.sleep(0.05)            # 交付包是 task 掷出去的
        assert len(sent) == 2, sent
        assert sent[1]['text'] == '今天天气完了完毕'
        assert sent[1]['complete'] is True and sent[1]['id'] == sent[0]['id']
        # 切回自动判停: 同一套配置立刻恢复"自己猜"
        ctl.set_manual_boundary(False)
        await ctl.feed(_ear('partial', '明天呢'))
        await ctl.feed(_ear('utterance', '明天呢'))
        await asyncio.sleep(0.4)
        assert len(sent) == 4, f'always 侧应恢复自动判停: {sent}'
    finally:
        await ctl.aclose()
    print('t_ptt_manual_boundary ok: 松手前不切段 / 松手即交 / 切回恢复判停')


async def t_clause_overlap_merge() -> None:
    """分句以 partial 的尾巴开头时不许念两遍.

    实况（2026-10-20 21:48 日志）：partial 已经把"…黑板的那个。前端页面"
    累计进来了，服务端随后把它补全成一句"前端页面，是吧？"再吐一遍 ——
    两边都带"前端页面"。旧规则"既不结尾也不开头就往后接"于是念成
    "…前端页面前端页面，是吧？"。
    """
    sent: list[dict] = []

    async def emit(**fields):
        sent.append(fields)
        return {'verdict': 'accepted'}

    cfg = EtiquetteConfig()
    fast = cfg.get('always').model_copy(deep=True)
    fast.name = 'fast'
    fast.stop.silence = 0.1
    cfg.upsert(fast)
    cfg.activate('fast')
    ctl = ListenerController(emit, config=cfg)
    try:
        await ctl.feed(_ear('partial', '如果我有一个黑板的那个。前端页面'))
        await ctl.feed(_ear('utterance', '前端页面，是吧？'))
        await asyncio.sleep(0.25)
        assert sent[-1]['complete'] is True, sent
        assert sent[-1]['text'] == '如果我有一个黑板的那个。前端页面，是吧？', \
            sent[-1]['text']
        # 真·下一句（毫无重叠）仍要接上，不能被合并规则吃掉
        await ctl.feed(_ear('partial', '那这个呢'))
        await ctl.feed(_ear('utterance', '那这个呢'))
        await asyncio.sleep(0.1)
        await ctl.feed(_ear('utterance', '再看看别的'))
        await asyncio.sleep(0.25)
        assert sent[-1]['text'] == '那这个呢再看看别的', sent[-1]['text']
    finally:
        await ctl.aclose()
    print('t_clause_overlap_merge ok: 与 partial 重叠的部分只念一遍')


async def t_partial_reset_keeps_prefix() -> None:
    """识别会话重启后的 partial 只覆盖后半句, 不许把前半句抹掉.

    实况（2026-10-20 22:11）：起话包是 ``'你可以同时'``，松手交付的却是
    ``'说话，然后。'`` —— 中间服务端换了一次识别流（半双工静音/重连），
    新 partial 从断点重新算，旧的 full-replace 规则就把前半句丢了。
    """
    sent: list[dict] = []

    async def emit(**fields):
        sent.append(fields)
        return {'verdict': 'accepted'}

    cfg = EtiquetteConfig()
    fast = cfg.get('always').model_copy(deep=True)
    fast.name = 'fast'
    fast.stop.silence = 0.1
    cfg.upsert(fast)
    cfg.activate('fast')
    ctl = ListenerController(emit, config=cfg)
    try:
        # 断点续接：不重叠 → 接上
        await ctl.feed(_ear('partial', '你可以同时'))
        await ctl.feed(_ear('partial', '说话，然后。'))
        assert ctl._text == '你可以同时说话，然后。', ctl._text
        assert await ctl.send_now() == 'committed'
        await asyncio.sleep(0.05)
        assert sent[-1]['text'] == '你可以同时说话，然后。', sent[-1]['text']

        # 改字：共同前缀够长 → 以新的为准，不重复
        await ctl.feed(_ear('partial', '今天天汽'))
        await ctl.feed(_ear('partial', '今天天气怎么样'))
        assert ctl._text == '今天天气怎么样', ctl._text
        await ctl.send_now()

        # 回退：新的比旧的短 → 保住长的
        await ctl.feed(_ear('partial', '明天去哪儿'))
        await ctl.feed(_ear('partial', '明天'))
        assert ctl._text == '明天去哪儿', ctl._text
        await ctl.send_now()

        # 极短的老文本：这是改字现场，照新的来
        await ctl.feed(_ear('partial', '你'))
        await ctl.feed(_ear('partial', '您好'))
        assert ctl._text == '您好', ctl._text
        await ctl.send_now()
    finally:
        await ctl.aclose()
    print('t_partial_reset_keeps_prefix ok: 断点续接 / 改字 / 回退 / 短文本四条')


async def t_mic_watchdog() -> None:
    """静音有两个主人：闸门有 120s 兜底，ptt 的"没按"没有。

    兜底是为了救"丢失 stop 帧"的闸门；把它套在 ptt 上就成了"两分钟没人
    按，麦自己开了" —— 既冤枉又违背按住才听。
    """
    import numpy as np

    from ..asr.ear import _MUTE_MAX_S, Ear, _Mic

    block = np.zeros(160, dtype=np.int16)
    mic = _Mic(None, lambda x: None)

    # ① 闸门静音超时 → 强制放麦（旧行为，必须留着）
    mic.set_mute(True, 0.0)
    mic._mute_from -= _MUTE_MAX_S + 1
    mic._cb(block, len(block), None, None)
    assert mic._mute_until == 0.0, mic._mute_until

    # ② ptt 静音同样超时 → 一个字都不放
    mic.set_mute(True, 0.0, watchdog=False)
    mic._mute_from -= _MUTE_MAX_S + 1
    mic._cb(block, len(block), None, None)
    assert mic._mute_until == float('inf'), mic._mute_until

    # ③ 期望态连 watchdog 一起记在耳朵上（麦克风还没建时也不丢）
    ear = Ear(api_key='x')
    ear.mute(True, 0.0, watchdog=False)
    assert ear._muted is True and ear._mute_watchdog is False
    ear.mute(False, 0.0)
    assert ear._muted is False and ear._mute_watchdog is True
    print('t_mic_watchdog ok: 闸门静音有兜底, ptt 没按不设期限')


async def t_mic_edge_block() -> None:
    """松手时"正在采"的那一块必须送出: 整块丢弃 = 每句话结尾丢一段。

    旧采集粒度就是上传粒度(200ms): 松手一刻在采的那 200ms 被静音整块吞掉,
    这就是"松开前几个字识别不到"的直接产地。现在采集 40ms 一块, 且横跨
    静音起点的那块立刻整体交出(不等凑满上传包), 边界只剩一个采集粒度。
    """
    import numpy as np

    from ..asr.ear import _ACCUM_MS, _Mic

    got: list = []
    mic = _Mic(None, got.append)
    mic._loop = asyncio.get_running_loop()
    mic._src_rate = 16000
    step = int(16000 * _ACCUM_MS / 1000)
    t = 100.0

    # ① 说话中: 攒满一包 200ms 才交出去(上传粒度不变)
    for i in range(5):
        mic._feed(np.ones(step, dtype=np.int16), t + i * 0.04)
    await asyncio.sleep(0)
    assert len(got) == 1 and got[0].size == 3200, (len(got), got[0].size)

    # ② 松手: 横跨静音起点的那一块立刻交出, 哪怕只有 40ms、没凑满
    mic._mute_from, mic._mute_until = t + 0.2, float('inf')
    mic._feed(np.ones(step, dtype=np.int16), t + 0.2)
    await asyncio.sleep(0)
    assert len(got) == 2, len(got)
    assert got[1].size == step, got[1].size

    # ③ 边界之后: 一块都不许进(线上要零上传)
    mic._feed(np.ones(step, dtype=np.int16), t + 0.3)
    await asyncio.sleep(0)
    assert len(got) == 2, len(got)
    print('t_mic_edge_block ok: 松手那一块送出 / 之后零采集')


async def t_ear_upload_on_demand() -> None:
    """没有音频可传就收流: 段内补静音 / 闸门静音 / ptt 松开, 三条一条路。

    麦克风常开不等于一直上传 —— 关掉的是"线上有没有字节", 不是"麦开没开"。
    段内(开口到收流之间)队列空了还要补静音, 否则服务端看到时间轴断口。
    """
    import numpy as np

    from ..asr.ear import Ear
    from ..asr.routine import Asr

    class FakeWS:
        def __init__(self) -> None:
            self.frames: list = []
            self.closed = False

        async def send(self, data) -> None:
            self.frames.append(data)

        async def close(self) -> None:
            self.closed = True

    quiet = np.zeros(160, dtype=np.int16)
    ear = Ear(api_key='x')
    ear._q = asyncio.Queue(maxsize=8)
    ear._seq = 2

    # ① 段内: 队列短暂空 → 补静音包, 保时间轴连续
    ear._muted = False
    ear._last_sound = time.monotonic()
    ws = FakeWS()
    sender = asyncio.ensure_future(ear._send(ws))
    await asyncio.sleep(0.45)
    assert not sender.done(), '段内不许收流'
    assert ear.stats['silence'] >= 2, ear.stats
    assert not ws.closed
    sender.cancel()
    await asyncio.gather(sender, return_exceptions=True)

    # ② 闸门静音(TTS 出声期): 麦克风在丢帧 → 同样收流, 一个静音包都不补
    ear.mute(True, 0.0)
    before = ear.stats['silence']
    ws1 = FakeWS()
    await asyncio.wait_for(ear._send(ws1), 2.0)
    assert ws1.closed, '闸门静音后必须收流'
    assert ear.stats['silence'] == before, ear.stats

    # ③ ptt 松开: 队列里已采到的真音频照发, 之后一个字节不发; 空窗后收流
    ear.mute(True, 0.0, watchdog=False)
    ear._q.put_nowait(quiet)
    ws2 = FakeWS()
    sender2 = asyncio.ensure_future(ear._send(ws2))
    await asyncio.sleep(0.25)
    # 流先留着(最后几包回包还在路上), 但一个静音包都不许补
    assert len(ws2.frames) == 1 and not ws2.closed, (len(ws2.frames), ws2.closed)
    await asyncio.wait_for(sender2, 1.5)
    assert ws2.closed, '松开后必须收流'
    assert len(ws2.frames) == 1, len(ws2.frames)
    assert ear.stats['silence'] == before, ear.stats
    assert ear.uploading() is False

    # ④ 待命: 没按就卡在 _wait_awake; 按下只开麦, **开口**才建流
    ear._awake = asyncio.Event()
    ear._muted, ear._mute_watchdog = True, False
    ear._streaming = False
    idle = asyncio.ensure_future(ear._wait_awake())
    await asyncio.sleep(0.05)
    assert not idle.done(), '没按就该待命'
    ear.mute(False, 0.0)
    await asyncio.sleep(0.05)
    assert not idle.done(), '按下但还没出声: 不建流(建连也只为传音频)'
    ear._push(np.full(3200, 3000, dtype=np.int16))     # 开口(第一块)
    ear._push(np.full(3200, 3000, dtype=np.int16))     # 第二块: 开门
    await asyncio.wait_for(idle, 0.5)

    # ⑤ 闸门不许替 ptt 开麦(否则松手后整间屋子都成识别时长)
    asr = Asr()
    asr._ear = ear
    asr._mode, asr._ptt_down = 'ptt', False
    ear.mute(True, 0.0, watchdog=False)
    for state in ('start', 'end'):
        await asr._on_output('tts', {'kind': 'speech', 'state': state})
    assert ear._muted is True and ear.uploading() is False, '没按时闸门不许开麦'
    # 按住时闸门照常: 出声期关, 说完放开
    asr._ptt_down = True
    await asr._on_output('tts', {'kind': 'speech', 'state': 'start'})
    assert ear._muted is True
    await asr._on_output('tts', {'kind': 'speech', 'state': 'end'})
    assert ear._muted is False
    print('t_ear_upload_on_demand ok: 段内补静音 / 闸门与松开都收流')


async def t_ear_always_gate() -> None:
    """always: 麦克风常开, 但静音不入队 —— 没声音就没有流, 也没有上传.

    照原版 ``silence_gate_factory(threshold_db=-50)`` 同一判据: 空闲期只
    多一次 rms 计算; 开口之后(段内)静音照传, 服务端要靠那段尾巴判句尾。

    另有一条**比入队更严的开门门**: 底噪(-45 dBFS 上下)能过入队门却开不了门
    —— 否则环境噪声会让"建流→3s→收流→再建流"无限循环, 每次赔一次握手。
    """
    import numpy as np

    from ..asr.ear import Ear, _SILENCE_CLOSE_S

    quiet = np.zeros(3200, dtype=np.int16)
    noise = np.full(3200, 184, dtype=np.int16)     # ≈ -45 dBFS: 风扇/空调底噪
    loud = np.full(3200, 3000, dtype=np.int16)     # ≈ -21 dBFS: 人声
    ear = Ear(api_key='x')
    ear._q = asyncio.Queue(maxsize=8)
    ear._awake = asyncio.Event()
    ear._muted = False                  # always: 麦常开

    for _ in range(5):
        ear._push(quiet)
    assert ear._q.empty(), '空闲期的静音不许入队'
    assert ear.stats['gated'] == 5, ear.stats
    assert ear._dormant is True, '安静时线上不该有音频'

    for _ in range(5):
        ear._push(noise)
    assert ear._q.empty(), '底噪不许入队'
    assert ear._dormant is True and not ear._awake.is_set(), \
        '底噪过得了入队门也绝不许开门(否则就是 endless 的 up/down)'

    ear._push(loud)                     # 一下: 还不够, 单块咔哒不算说话
    assert ear._q.empty() and not ear._awake.is_set(), '单块不够开门'
    ear._push(loud)                     # 两下: 开口
    assert ear._q.qsize() == 2, '开门要补上攒着的两块(第一个字不许丢)'
    assert ear._dormant is False and ear._awake.is_set(), '开口要叫醒建流'

    ear._push(quiet)                    # 段内尾巴: 照传
    assert ear._q.qsize() == 3, '段内静音必须传(服务端靠它判句尾)'

    ear._last_sound -= _SILENCE_CLOSE_S + 0.1
    assert ear._dormant is True, '安静够久就该收流'
    while not ear._q.empty():
        ear._q.get_nowait()
    ear._push(quiet)                    # 该收流了: 静音不许再喂发送泵
    assert ear._q.empty(), '收流判据成立后静音仍入队, 发送泵就永远收不了流'
    print('t_ear_always_gate ok: 底噪不开门 / 单块不开门 / 段内尾巴照传')


async def t_ear_commit() -> None:
    """判停即收流: 发**尾包**(负序号)结束这段, 不等静音超时/尾巴空窗。

    原版 ``Recognizer.commit()`` 就是这一手 —— "让 send loop 发 NEG, 拿尾包
    后切段"。判停是段边界的权威: 文本已固定, 再传也没有字会进交付, 所以
    这一条流的剩余上传全是白花钱(always 下省掉判停到静音超时之间的那一段)。
    """
    import gzip
    import json
    import struct

    from ..asr import ear as ear_mod
    from ..asr.ear import Ear

    def seq_of(frame: bytes) -> int:
        assert frame[1] >> 4 == 0x02, frame[:4]      # AUDIO_ONLY_REQUEST
        return struct.unpack('>i', frame[4:8])[0]

    def is_last(frame: bytes) -> bool:
        return frame[1] & 0x0F == 0x03                # 负序号

    def resp(text: str, definite: bool, last: bool = False) -> bytes:
        body = {'result': {'text': text, 'utterances': [
            {'text': text, 'definite': definite}]}}
        msg = gzip.compress(json.dumps(body, ensure_ascii=False).encode())
        flags = 0x01 | (0x02 if last else 0)          # seq(+last)
        return (bytes([0x11, (0x09 << 4) | flags, 0x11, 0x00])
                + struct.pack('>i', 1) + struct.pack('>I', len(msg)) + msg)

    class FakeWS:
        def __init__(self) -> None:
            self.frames: list = []
            self.inbox: asyncio.Queue = asyncio.Queue()
            self.closed = False

        async def send(self, data) -> None:
            self.frames.append(data)

        async def recv(self):
            return await self.inbox.get()

        async def close(self) -> None:
            self.closed = True
            self.inbox.put_nowait(None)   # 叫醒 recv

    class CM:
        def __init__(self, ws) -> None:
            self.ws = ws

        async def __aenter__(self):
            return self.ws

        async def __aexit__(self, *exc):
            await self.ws.close()

    # ① 段内判停: 发负序号尾包, 但**不收流**(尾包还在路上)
    ear = Ear(api_key='x')
    ear._q = asyncio.Queue(maxsize=8)
    ear._commit = asyncio.Event()
    ear._seq = 2
    ear._muted = False
    ear._last_sound = time.monotonic()      # 段内: 不是待命
    ws = FakeWS()
    sender = asyncio.ensure_future(ear._send(ws))
    await asyncio.sleep(0.25)
    assert not sender.done() and not ws.closed, '段内不许自己收流'
    # 判停到尾包必须是**当拍**: 哨兵走队列唤醒发送泵, 不是等超时回来看事件
    # (轮询实现下这里至少 200ms, 正是上一版把段尾延迟白扔掉的那一截)
    t0 = time.monotonic()
    ear.commit()
    await asyncio.wait_for(sender, 1.0)
    took = time.monotonic() - t0
    assert took < 0.08, f'判停到尾包 {took * 1000:.0f}ms: 不该等轮询'
    assert is_last(ws.frames[-1]), '判停必须发负序号尾包'
    assert seq_of(ws.frames[-1]) < 0
    assert not ws.closed, '尾包发出后流要留着等回包'
    assert ear.stats['commits'] == 1 and ear._commit_sent_at > 0, ear.stats

    # ② 待命期判停: 仍然是尾包, 不是干等空窗再 close(ptt 松手就是这条)
    ear.mute(True, 0.0, watchdog=False)
    ear._commit.clear()
    ear._commit_sent_at = 0.0
    before = ear.stats['silence']
    ws2 = FakeWS()
    sender2 = asyncio.ensure_future(ear._send(ws2))
    await asyncio.sleep(0.05)
    ear.commit()
    await asyncio.wait_for(sender2, 2.0)
    assert ws2.frames and is_last(ws2.frames[-1]), '待命期判停也要发尾包'
    assert not ws2.closed and ear.stats['silence'] == before, ear.stats

    # ③ 判停之后这条流再吐的定稿不许进策略层(否则同一句被念两遍)
    ws3 = FakeWS()
    orig_connect = ear_mod.websockets.connect
    ear_mod.websockets.connect = lambda url, **kw: CM(ws3)
    ear3 = Ear(api_key='x')
    ear3._q = asyncio.Queue(maxsize=8)
    ear3._commit = asyncio.Event()
    got: list = []

    async def pump() -> None:
        async for ev in ear3._session():
            got.append(ev)

    task = asyncio.ensure_future(pump())
    try:
        await asyncio.sleep(0.1)                # init 已发, 发送泵已起
        ear3.commit()
        await asyncio.sleep(0.15)               # 尾包已发
        assert ear3._commit_sent_at > 0, '判停没传到发送泵'
        ws3.inbox.put_nowait(resp('判停之后的话', True))
        ws3.inbox.put_nowait(resp('', False, last=True))
        await asyncio.wait_for(task, 1.0)
        assert got == [], [str(e) for e in got]
    finally:
        task.cancel()
        ear_mod.websockets.connect = orig_connect
        await asyncio.gather(task, return_exceptions=True)
    print('t_ear_commit ok: 判停发尾包收流 / 判停后的文本不进策略层')


async def t_ear_session_on_demand() -> None:
    """整套 ``events()``: 没按不建流 / 按下才建 / 松手收流 / 不空转重连.

    "建连不等于上传"这条, 落到耳朵上是两个动作: 松手后先不收流(最后几包
    回包还在路上), 空窗一过就 close; 之后**不许**自动重连 —— 否则空闲期
    会变成每 8.5s 一次的重连风暴。
    """
    import numpy as np
    from websockets.exceptions import ConnectionClosedOK

    from ..asr import ear as ear_mod
    from ..asr.ear import Ear

    connects: list = []

    class FakeMic:
        def __init__(self, device, on_block) -> None:
            self.on_block = on_block
            self.muted = False

        def open(self) -> None:
            pass

        def set_mute(self, on, hangover, watchdog=True) -> None:
            self.muted = on

        def close(self) -> None:
            pass

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list = []
            self.closed = False
            self._done = asyncio.Event()

        async def send(self, data) -> None:
            self.sent.append(data)

        async def recv(self):
            await self._done.wait()
            raise ConnectionClosedOK(None, None)

        async def close(self) -> None:
            self.closed = True
            self._done.set()

    def fake_connect(url, **kw):
        ws = FakeWS()
        connects.append(ws)

        class CM:
            async def __aenter__(self):
                return ws

            async def __aexit__(self, *exc):
                await ws.close()

        return CM()

    orig_mic, orig_connect = ear_mod._Mic, ear_mod.websockets.connect
    ear_mod._Mic = FakeMic
    ear_mod.websockets.connect = fake_connect
    ear = Ear(api_key='x')
    ear.mute(True, 0.0, watchdog=False)     # ptt 装配默认: 未按
    loud = np.full(3200, 3000, dtype=np.int16)
    task = asyncio.ensure_future(ear.events().__anext__())
    try:
        await asyncio.sleep(0.15)
        assert connects == [], '没按就不许建流'

        ear.mute(False, 0.0)                # 按下
        await asyncio.sleep(0.15)
        assert connects == [], '按了但还没出声: 也不建流'

        ear._push(loud)                     # 开口
        ear._push(loud)                     # 第二块: 够开门
        await asyncio.sleep(0.15)
        assert len(connects) == 1, connects
        ws = connects[0]
        assert len(ws.sent) >= 1, '开口的音频必须发出去'

        base = len(ws.sent)
        ear._push(loud)                     # 还在说
        await asyncio.sleep(0.1)
        assert len(ws.sent) > base, (len(ws.sent), base)

        ear.mute(True, 0.0, watchdog=False)  # 松手: 空窗留着, 但零上传
        await asyncio.sleep(0.05)
        n = len(ws.sent)
        await asyncio.sleep(0.15)
        assert not ws.closed and len(ws.sent) == n, (ws.closed, len(ws.sent), n)
        await asyncio.sleep(0.6)
        assert ws.closed, '空窗一过必须收流'
        assert len(ws.sent) == n, '松开后一个字节都不许再发'

        await asyncio.sleep(0.3)
        assert len(connects) == 1, '待命期不许自动重连'

        ear.mute(False, 0.0)                # 再按: 开口才重新建流
        await asyncio.sleep(0.15)
        assert len(connects) == 1, '按下本身不建流(建连只为传音频)'
        ear._push(loud)                     # 再开口
        ear._push(loud)                     # 第二块: 够开门
        await asyncio.sleep(0.15)
        assert len(connects) == 2, len(connects)
    finally:
        await ear.close()
        await asyncio.gather(task, return_exceptions=True)
        ear_mod._Mic = orig_mic
        ear_mod.websockets.connect = orig_connect
    print('t_ear_session_on_demand ok: 待命不建流 / 开口才建 / 松手零上传')


async def main() -> None:
    only = [a for a in sys.argv[1:] if not a.startswith('-')]
    table = {n: f for n, f in globals().items()
             if n.startswith('t_') and inspect.iscoroutinefunction(f)}
    if only:
        unknown = [a for a in only if a not in table]
        assert not unknown, f'unknown cases: {unknown}'
        for n, f in table.items():
            if n in only:
                await f()
        print(f'ALL PASS ({len(only)} cases)')
        return
    for f in table.values():
        await f()
    print('ALL PASS')


if __name__ == '__main__':
    asyncio.run(main())
