"""键盘后端单测(纯逻辑, 不起 kernel, 不碰真实键盘).

- pynput 名字映射: 键名 → ``keyboard.Key`` / ``KeyCode``;
- **没有兜底**: 模块里不该有轮询后端或键码表, 导入失败直接抛;
- 键名不认识: 建监听器当场 KeyError(而不是静默待命).

运行(cwd=zero-py)::

    ./.venv/Scripts/python.exe -m app.routines.user.mossv2.tests.test_keys
"""
from __future__ import annotations

import sys

from .. import key_listen
from ..key_listen import _pynput_key, _require_pynput, _same_key


def t_pynput_names() -> None:
    """pynput 可用时: 特殊键与字符键都能映射, 字符忽略大小写."""
    try:
        from pynput import keyboard
    except Exception:
        print('  (pynput 未安装, 跳过 pynput 名字用例)')
        return
    assert _pynput_key('f9') == keyboard.Key.f9
    assert _pynput_key('pageup') == keyboard.Key.page_up   # 别名表
    assert _pynput_key('a') == keyboard.KeyCode.from_char('a')
    assert _same_key(keyboard.KeyCode.from_char('A'),
                     keyboard.KeyCode.from_char('a'))
    assert not _same_key(keyboard.Key.f9, keyboard.Key.f10)
    try:
        _pynput_key('nope')
    except KeyError:
        pass
    else:
        raise AssertionError('unknown key should raise')


def t_no_fallback() -> None:
    """没有兜底后端: 轮询/键码表都不该还在模块里."""
    for gone in ('vk_of', '_PollWatcher', '_make_watcher', '_pynput_ok'):
        assert not hasattr(key_listen, gone), f'{gone} 应该已经删掉(不兜底)'


def t_import_failure() -> None:
    """pynput 导不进来 → RuntimeError(不是"记一行日志然后待命")."""
    old = sys.modules.get('pynput')
    sys.modules['pynput'] = None        # 让 import 直接失败
    try:
        try:
            _require_pynput()
        except RuntimeError as exc:
            assert 'pynput' in str(exc), exc
        else:
            raise AssertionError('missing pynput should raise')
    finally:
        if old is None:
            sys.modules.pop('pynput', None)
        else:
            sys.modules['pynput'] = old


def t_bad_key_raises() -> None:
    """键名 pynput 不认识: 建监听器当场抛, 不静默降级."""
    try:
        from pynput import keyboard  # noqa: F401
    except Exception:
        print('  (pynput 未安装, 跳过坏键名用例)')
        return
    try:
        key_listen._PynputWatcher('nope', lambda down: None)
    except KeyError:
        pass
    else:
        raise AssertionError('bad key should raise at watcher construction')


def main() -> None:
    t_pynput_names()
    t_no_fallback()
    t_import_failure()
    t_bad_key_raises()
    print('test_keys: 4 passed')


if __name__ == '__main__':
    main()
