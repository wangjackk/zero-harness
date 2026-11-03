"""UI 思考流渲染: ghost 的 ``think`` 广播 → "想: " 灰字斜体行.

只测渲染层(不连 hub): 直接 emit ``_bridge`` 的两个信号, 看文档里长出
什么. 三件事:
1. 思考流式追加在同一行, 灰斜体, 与正文分块;
2. 正文一到就收思考行 —— 正文不染灰斜体(insertText 吃 cursor 当前
   格式, 这里最容易把思考的样式串到正文上);
3. 第二拍思考另起一行(不接在上一拍灰字后面).

注意主区**不给正文加前缀**(它是 LLM 边界流水, 不是对话框): 正文就是
logos 原文, 谁也说不清那一块该叫"谁".

用法: ``python -m app.routines.user.mossv2.tests.test_ui_think``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')   # 无显示器冒烟

_HERE = Path(__file__).resolve().parent
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from ..ui import ChatWindow, _bridge

_app = QApplication.instance() or QApplication(sys.argv)


def _fmt_at(doc, needle: str, nth: int = 0):
    """某个文本片段第一个字符的字符格式(片段不存在 → 断言先炸)."""
    pos = -1
    for _ in range(nth + 1):
        pos = doc.toPlainText().find(needle, pos + 1)
        assert pos >= 0, f'文档里没有 {needle!r}(第 {nth + 1} 次)'
    c = QTextCursor(doc)
    c.setPosition(pos + 1)
    return c.charFormat()


def t_think_line() -> None:
    """思考流式追加一行: 灰 + 斜体, 前缀只出现一次."""
    win = ChatWindow('127.0.0.1:0')
    doc = win.view.document()
    _bridge.think.emit('先想一下')
    _bridge.think.emit('该怎么答.')
    text = doc.toPlainText()
    assert text.count('想: ') == 1, text
    assert '想: 先想一下该怎么答.' in text, text
    assert _fmt_at(doc, '先想一下').fontItalic() is True, '思考应为斜体'
    assert _fmt_at(doc, '先想一下').foreground().color().name() == '#8a8f9a'
    print('t_think_line ok: one grey italic line, chunks appended')


def t_text_closes_think() -> None:
    """正文一到: 思考收行, 正文另起一块且不吃灰斜体."""
    win = ChatWindow('127.0.0.1:0')
    doc = win.view.document()
    _bridge.think.emit('算一下')
    _bridge.text.emit('答案')
    _bridge.text.emit('是 42')
    text = doc.toPlainText()
    assert '想: 算一下' in text, text
    assert '答案是 42' in text, text      # 原文照抄, 不加 "Moss: "
    assert 'Moss:' not in text, text
    assert win._thinking is False, '正文到达后思考行必须收'
    fmt = _fmt_at(doc, '答案是')
    assert fmt.fontItalic() is False, '正文不该继承思考的斜体'
    assert fmt.foreground().color().name() != '#8a8f9a', '正文不该是灰字'
    print('t_text_closes_think ok: body closes think, no style leak')


def t_second_beat_new_line() -> None:
    """第二拍的思考另起一行(不接上一拍灰字)."""
    win = ChatWindow('127.0.0.1:0')
    doc = win.view.document()
    _bridge.think.emit('第一拍想')
    _bridge.text.emit('第一拍说')
    _bridge.think.emit('第二拍想')
    text = doc.toPlainText()
    assert text.count('想: ') == 2, text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert any(ln.startswith('想: 第一拍想') for ln in lines), lines
    assert any(ln.startswith('想: 第二拍想') for ln in lines), lines
    print('t_second_beat_new_line ok: each beat own think line')


def main() -> None:
    t_think_line()
    t_text_closes_think()
    t_second_beat_new_line()
    print('ALL PASS')


if __name__ == '__main__':
    main()
