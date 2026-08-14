"""启动 banner:业务侧自行渲染已注册 routine 的展示表格.

框架(routine)只打印一行客观统计(``print_summary``),不碰
description/modules/hidden 等业务字段----那些是业务侧的展示关心,放这里.
对齐原 ``runtime.print_routines`` 的审美:CJK 宽度对齐,``(is_passive, name)``
排序,柔和配色,hidden 行加 tag 并整体压暗.
"""
from __future__ import annotations

import shutil
import sys
from typing import Sequence

from routine import Routines


# --- CJK 宽度对齐 (no external deps) ---

_DIM = '\033[2m'
_BOLD = '\033[1m'
_RESET = '\033[0m'
_CYAN = '\033[36m'


def _is_wide(ch: str) -> bool:
    o = ord(ch)
    return (
        0x1100 <= o <= 0x115F
        or 0x2E80 <= o <= 0x303E
        or 0x3040 <= o <= 0x33BF
        or 0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xAC00 <= o <= 0xD7A3
        or 0xF900 <= o <= 0xFAFF
        or 0xFE30 <= o <= 0xFE4F
        or 0xFF00 <= o <= 0xFF60
        or 0xFFE0 <= o <= 0xFFE6
    )


def _disp_w(s: str) -> int:
    return sum(2 if _is_wide(c) else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + ' ' * max(0, w - _disp_w(s))


def _truncate(s: str, w: int) -> str:
    if w <= 0:
        return ''
    if _disp_w(s) <= w:
        return s
    out, cur = '', 0
    for ch in s:
        cw = 2 if _is_wide(ch) else 1
        if cur + cw > w - 1:
            return out + '...'
        out += ch
        cur += cw
    return out


def print_banner(routines: Routines, modules: Sequence[str] = ()) -> None:
    """打印 routine 展示表格.在 ``start_server`` 之前调用."""
    try:
        use_color = bool(getattr(sys, 'stdout', None) and sys.stdout.isatty())
        def c(s: str, code: str) -> str:
            return f'{code}{s}{_RESET}' if use_color and code else s

        cols = max(shutil.get_terminal_size((100, 24)).columns, 60)
        rs = list(routines.get_routines())

        enabled = sum(1 for r in rs if r.enable)
        hidden_n = sum(1 for r in rs if r.meta.get('hidden'))
        passive_n = sum(1 for r in rs if r.is_passive)

        print()
        print(f'  {c("zero", _BOLD + _DIM)}  '
              f'{c(f"{len(rs)} routines  ·  {enabled} enabled  ·  {hidden_n} hidden  ·  {passive_n} passive", _DIM)}')
        print()

        # Modules
        print(f'  {c("──", _DIM)} {c("Modules", _BOLD)}')
        mod_names = ' · '.join(modules)
        print(f'  {c(mod_names or "--", _DIM)}')
        print()

        # Routines table
        name_w = min(max((_disp_w(r.name) for r in rs), default=4), 24) + 2
        type_w = 8
        desc_w = max(cols - name_w - type_w - 8, 24)

        print(f'  {c("──", _DIM)} {c("Routines", _BOLD)}')
        print(f'  {c(_pad("NAME", name_w), _DIM)}  '
              f'{c(_pad("TYPE", type_w), _DIM)}  '
              f'{c("DESCRIPTION", _DIM)}')
        print(f'  {c("─" * (cols - 2), _DIM)}')

        for r in sorted(rs, key=lambda x: (x.is_passive, x.name)):
            name = r.name
            type_raw = 'passive' if r.is_passive else ('active' if r.enable else 'disabled')
            type_code = _CYAN if r.is_passive else (_DIM if not r.enable else '')
            desc_raw = (r.meta.get('description')
                        or (r.__doc__ or '').strip().split('\n')[0]
                        or '')
            desc_raw = ' '.join(desc_raw.split())
            tag = '[hidden] ' if r.meta.get('hidden') else ''
            # hidden 行整体压暗(业务字段,业务层自行决定怎么弱化展示).
            line_code = _DIM if r.meta.get('hidden') else ''
            desc = _truncate(tag + desc_raw, desc_w)

            print(f'  {c(_pad(name, name_w), line_code)}  '
                  f'{c(_pad(type_raw, type_w), type_code or line_code)}  '
                  f'{c(desc, line_code)}')

        print()
    except Exception:
        print(routines)
