"""test_ui —— 交互面渲染的离屏用例（offscreen 平台，不开真窗口）.

只验**渲染语义**, 不验像素. 主区是**LLM 边界流水**(不是对话框): 只放
真的进过模型的输入块与它写出来的 logos 原文, "我正在说的话"停在灰字行
上; 这些是 UI 与 pubsub 契约的接缝, 错了在真窗口里表现为"屏幕上有句话
模型其实没收到", 事后对不上账, 所以按离屏文本断言.

另验上下文侧栏三页(帧链/历史/系统提示词): 一份 recall 快照供三处,
长文页切过去才排、同一份不重建 —— 这些错了表现为"每来一条回执就把
读长文的人弹回顶部", 也是肉眼很晚才发现的.

跑法(cwd=zero-py)::

    PYTHONPATH="routine-py;$PYTHONPATH" ./.venv/Scripts/python.exe \
        -m app.routines.user.mossv2.tests.test_ui
"""
from __future__ import annotations

import inspect
import os
import sys
from typing import Any, Dict, List

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')   # 必须在 Qt 前

from PySide6.QtWidgets import QApplication            # noqa: E402

from ..ui import (                                    # noqa: E402
    ChatWindow, TextPanel, _SEP, context_payload, history_text,
    transcript_text,
)


def _lines(w: ChatWindow) -> list[str]:
    """非空行（Qt 的段落分隔会留空行，不算内容）."""
    return [ln for ln in w.view.toPlainText().splitlines() if ln.strip()]


def t_draft_is_not_transcript() -> None:
    """我正在说的话**不进主区**: 它还没进模型.

    反例症状(不修则出现): 语音 partial 在流水里长出一行"你: ...", 模型
    实际还没收到它 —— 屏幕上却已经有一句话, 事后对不上账.
    """
    w = ChatWindow('127.0.0.1:0')
    try:
        assert _lines(w) == []
        for part in ('今天', '今天天气', '今天天气怎么样'):
            w._on_heard({'kind': 'partial', 'text': part})
        assert _lines(w) == [], _lines(w)          # 流水不动
        assert w.hint.text() == '未入史 · 今天天气怎么样', w.hint.text()
        w._on_heard({'kind': 'final', 'text': '今天天气怎么样。'})
        assert w.hint.text() == '未入史 · 今天天气怎么样。', w.hint.text()
        # 下一拍真进了 <percepts> → 草稿转正: 灰字行消失, 原文进主区
        w._on_context(context_payload({'model_input': [
            {'role': 'user', 'content':
             '<percepts>\n<listen source="asr" created="D25 16:30:00+8">\n'
             '今天天气怎么样。\n</listen>\n</percepts>'}]}))
        assert w.hint.text() == '', w.hint.text()
        text = w.view.toPlainText()
        assert '<listen source="asr"' in text, text
        assert '你:' not in text and 'Moss:' not in text, text
    finally:
        w.deleteLater()


def t_typed_goes_draft_too() -> None:
    """打字和语音走同一条路: 回车后先进灰字行, 入史后才是主区的一块."""
    w = ChatWindow('127.0.0.1:0')
    try:
        w.inp.setText('手打一句')
        w._send()
        assert _lines(w) == [], _lines(w)
        assert '手打一句' in w.hint.text(), w.hint.text()
        # 入史 → 裸行进 <percepts>(打字的没有 tag, 原版同规矩)
        w._on_context(context_payload({'model_input': [
            {'role': 'user', 'content': '<percepts>\n手打一句\n</percepts>'}]}))
        assert w.hint.text() == '', w.hint.text()
        assert '<percepts>\n手打一句\n</percepts>' in w.view.toPlainText(), \
            w.view.toPlainText()
    finally:
        w.deleteLater()


def t_transcript_raw() -> None:
    """主区 = ``model_input`` 原文, 一字不改: 没有"你:/Moss:"前缀, 没有
    块头标题, 也不解 ``<percepts>`` 那层壳; 块之间只有一条等宽横线.

    转述看起来同样正常 —— 解一层壳、加一个前缀都不会报错, 只是屏幕上的
    那句话不再等于模型读到的那句. 要读人话就自己读, 别让界面替你翻译.
    """
    w = ChatWindow('127.0.0.1:0')
    try:
        items = [
            {'role': 'user', 'content': '<percepts>\n你好\n</percepts>'},
            {'role': 'assistant', 'content': '<say>在</say>'},
            {'role': 'user', 'content': '<observe>\nx: ok\n</observe>'},
        ]
        w._on_context(context_payload({'model_input': items}))
        text = w.view.toPlainText()
        for it in items:     # 原文逐字在里面
            assert it['content'] in text, text
        assert '你:' not in text and 'Moss:' not in text, text
        assert '[1]' not in text, text               # 没有块头标题
        assert text.count(_SEP) == 2, text           # 三块之间两条线
        # 同一份快照再来一次: 不重排(重排会把读到中间的人弹走)
        rev = w.view.document().revision()
        w._on_context(context_payload({'model_input': list(items)}))
        assert w.view.document().revision() == rev, '同一份原文不该重排'
    finally:
        w.deleteLater()


def t_heartbeat() -> None:
    """主区跟拍**不能只靠事件点灯**: 得有一条常开的心跳.

    实测故障(不修则出现): 语音不经过输入框(``send`` 不点灯), ``say`` 又
    没有回执帧(obs 只在非空时广播) —— 于是 UI 停在第一拍, 屏幕上只有第
    一句和后面越堆越长的流, 我后来说的话一条都不出现, 而 ``recall`` 里
    数据一条不少. 这类"界面与数据不一致"看起来完全正常, 只有对着两边
    数才看得出来.
    """
    w = ChatWindow('127.0.0.1:0')
    try:
        assert w._ctx_timer.isActive(), '心跳必须常开'
        assert not w._ctx_timer.isSingleShot(), \
            '单发定时器 = 等一个永远不会来的信号'
        assert w._ctx_kick.isSingleShot(), '点灯仍是防抖快拉'
    finally:
        w.deleteLater()


def t_stream_appends_raw() -> None:
    """它的输出流式追加成一块(不加前缀); 下一个快照里只出现一次.

    **故意不喂 ``_on_obs``**: ``say`` 没有回执帧, 纯说话的一拍根本不会有
    观测事件。上一版靠 ``_on_obs`` 清 ``_live``, 于是这条路径下快照画一遍、
    ``_live`` 再贴一遍 —— 同一句话两个块(真机上用户看到的就是这个)。
    """
    w = ChatWindow('127.0.0.1:0')
    try:
        for chunk in ('<say', '>我在', '</say>'):
            w._on_text(chunk)
        text = w.view.toPlainText()
        assert '<say>我在</say>' in text, text      # 拼成原文, 没插字
        assert 'Moss:' not in text, text
        w._on_context(context_payload({'model_input': [     # 下一拍: 已入史
            {'role': 'user', 'content': '<percepts>\n你好\n</percepts>'},
            {'role': 'assistant', 'content': '<say>我在</say>'},
        ]}))
        text = w.view.toPlainText()
        assert text.count('<say>我在</say>') == 1, text   # 一次, 不重影
    finally:
        w.deleteLater()


def t_say_without_receipt_no_double() -> None:
    """连着两拍都只是说话(没有回执帧) → 每句各一块, 不许重影.

    真机症状: 主区最底下出现**同一句回复两个块** —— 第二块是 ``_live``
    里没结账的那段, 与快照最后一条 assistant 是同一份。``model_input``
    里只有一条, 所以在历史页对不上, 只有主区多一块。
    """
    w = ChatWindow('127.0.0.1:0')
    try:
        turns = [
            ('666', '哈哈,这声 666 我收下了。'),
            ('666', '再 666 下去,我要怀疑你是不是在测我还在不在了。'),
        ]
        items: List[Dict[str, Any]] = []      # model_input 是累积的
        for said, reply in turns:
            w._on_heard({'kind': 'final', 'text': said})
            for i in range(0, len(reply), 5):       # 流式切块
                w._on_text(reply[i:i + 5])
            items = items + [
                {'role': 'user', 'content': f'<percepts>\n{said}\n'
                                            '</percepts>'},
                {'role': 'assistant', 'content': reply},
            ]
            w._on_context(context_payload({'model_input': items}))
        text = w.view.toPlainText()
        for _, reply in turns:
            assert text.count(reply) == 1, f'重影: {reply!r}\n{text}'
        assert text.count(_SEP) == 3, text     # 四块之间三条线
    finally:
        w.deleteLater()


def t_midstream_rebuild_keeps_live() -> None:
    """拍内重排: 快照里还没有这段(还没 commit) → ``_live`` 必须补上, 不许丢.

    与上一条是同一枚硬币的两面: 结账判据若写成"重排就清", 这一条就会把
    正在流的话抹掉; 写成"快照有没有它", 两条都对。
    """
    w = ChatWindow('127.0.0.1:0')
    try:
        w._on_text('我正在说')
        w._on_context(context_payload({'model_input': [
            {'role': 'user', 'content': '<percepts>\n在吗\n</percepts>'},
        ]}))
        assert '我正在说' in w.view.toPlainText(), w.view.toPlainText()
        w._on_text('，还没完')      # 重排后接得上(不另起一块)
        text = w.view.toPlainText()
        assert '我正在说，还没完' in text, text
        assert text.count(_SEP) == 1, text
    finally:
        w.deleteLater()


# ── 上下文侧栏三页(帧链 / 历史 / 系统提示词) ──

def t_three_tabs() -> None:
    """一份快照供三页: 帧链即时重建, 长文页**切过去才排** —— 历史几十条、
    提示词六千字, 藏在别的页签后面时不必重排."""
    w = ChatWindow('127.0.0.1:0')
    try:
        w._on_context(context_payload({
            'frames': [{'index': 0, 'previous': {'frames': []},
                        'percepts': {'user': '你好'},
                        'logos': '<say>在</say>'}],
            'model_input': [
                {'role': 'user', 'content': '<percepts>\n你好\n</percepts>'},
                {'role': 'assistant', 'content': '<say>在</say>'},
            ],
            'system': '你是 Moss.\n\n<协议>',
        }))
        assert '帧 1' in w.panel._stats.text(), w.panel._stats.text()
        # 当前是帧链页 → 长文页还没排(省一次全文重排)
        assert w.history_panel.view.toPlainText() == ''
        # 切到历史: 块头 + 原文照抄(不给模型的话添油加醋)
        w.tabs.setCurrentWidget(w.history_panel)
        hist = w.history_panel.view.toPlainText()
        assert '[1] 感知' in hist and '[2] 它' in hist, hist
        assert '你好' in hist and '<say>在</say>' in hist, hist
        assert '2 条' in w.history_panel._stats.text(), w.history_panel._stats.text()
        # 切到系统提示词: 装配前缀原文(一字不改)
        w.tabs.setCurrentWidget(w.system_panel)
        assert w.system_panel.view.toPlainText() == '你是 Moss.\n\n<协议>'
        assert '字' in w.system_panel._stats.text()
    finally:
        w.deleteLater()


def t_empty_context() -> None:
    """空快照(没连上/还没开拍)是常态: 三页给占位, 不炸也不装成有内容."""
    w = ChatWindow('127.0.0.1:0')
    try:
        w._on_context(context_payload({}))
        w.tabs.setCurrentWidget(w.history_panel)
        assert '0 条' in w.history_panel._stats.text(), w.history_panel._stats.text()
        assert '空' in w.history_panel.view.toPlainText()
        w.tabs.setCurrentWidget(w.system_panel)
        assert '0 字' in w.system_panel._stats.text(), w.system_panel._stats.text()
    finally:
        w.deleteLater()


def t_text_no_rebuild() -> None:
    """同一份原文不重建: 每 250ms 一份快照, 重建会把读到中间的人弹回顶部."""
    p = TextPanel('历史')
    try:
        p.set_text('甲\n乙', '2 行')
        rev = p.view.document().revision()
        p.set_text('甲\n乙', '2 行')          # 字面相同 → 不动
        assert p.view.document().revision() == rev, '同一份原文不该重建'
        p.set_text('甲\n乙\n丙', '3 行')       # 变了 → 重建
        assert p.view.document().revision() != rev
        assert p.view.toPlainText() == '甲\n乙\n丙'
    finally:
        p.deleteLater()


def t_history_labels() -> None:
    """块头: 模型侧 role 全是 user, 只看 role 分不清感知/观测/瞬态 ——
    按首行 tag 点明是哪一块, 人才读得懂模型看到了什么."""
    text = history_text([
        {'role': 'user', 'content': '<observe>\nx: ok\n</observe>'},
        {'role': 'user', 'content': '<dynamic_context>\npower: 80%\n'
                                    '</dynamic_context>'},
        {'role': 'assistant', 'content': '好'},
    ])
    assert '[1] 观测' in text, text
    assert '[2] 瞬态' in text, text
    assert '[3] 它' in text, text


def t_transcript_text() -> None:
    """原文流: 空条目跳过(不画空分隔线 —— 那会让人以为内容丢了); 非空
    块之间正好一条线, 正文前后不加任何东西."""
    assert transcript_text([]) == ''
    assert transcript_text([{'content': ''}, {'content': 'a'}]) == 'a'
    assert transcript_text([{'content': 'a'}, {'content': 'b'}]) \
        == f'a\n{_SEP}\nb'
    # 正文里的空行/缩进原封不动(块内是原文, 不是重排; 输入壳也不解)
    body = '<percepts>\n  <listen>你好</listen>\n</percepts>'
    assert transcript_text([{'content': body}]) == body


def t_multimodal_content() -> None:
    """带图的那一条: 文本块照抄, 图块只留一行 ``[image]``.

    ``model_input`` 里带图的 content 是多模态 blocks, ``image_url`` 是
    base64(一张截图几十万字符). 原样贴进界面会卡死 —— 这是唯一必须动手的
    地方, 也是最容易做错的地方: 拿整个 content 去做 ``str()`` 会打出一坨
    Python 字典, 而人看不出"这里本来有一张图".
    """
    items = [{
        'role': 'user',
        'content': [
            {'type': 'input_text', 'text': '<observe>\nread_image: a.png\n</observe>'},
            {'type': 'input_image', 'image_url': 'data:image/png;base64,' + 'A' * 5000},
        ],
    }]
    text = transcript_text(items)
    assert '<observe>' in text and 'read_image: a.png' in text, text
    assert text.count('[image]') == 1, text
    assert 'base64' not in text and 'AAAA' not in text, 'base64 不许进界面'
    # 历史页同一份内容: 块名仍能认出是观测块(不是"输入")
    assert '观测' in history_text(items), history_text(items)


def main() -> None:
    app = QApplication.instance() or QApplication([])   # noqa: F841
    table = {n: f for n, f in globals().items()
             if n.startswith('t_') and inspect.isfunction(f)}
    only = [a for a in sys.argv[1:] if not a.startswith('-')]
    if only:
        unknown = [a for a in only if a not in table]
        assert not unknown, f'unknown cases: {unknown}'
        for n in only:
            table[n]()
        print(f'ALL PASS ({len(only)} cases)')
        return
    for f in table.values():
        f()
    print('ALL PASS')


if __name__ == '__main__':
    main()
