# -*- coding: utf-8 -*-
"""_wechat_probe.py --- 微信"收消息"探测器: 无副作用地看屏幕, 检测未读并取内容.

'_' 前缀 -> dynamic watcher 跳过, 不当 routine 扫描.

核心:
  - 用 PrintWindow(PW_RENDERFULLCONTENT) 抓微信窗口 => 窗口被别的程序盖住也能抓, 不需要抢焦点;
  - 左栏红色未读角标(像素扫描) + 会话列表文字(OneOCR) 定位目标会话;
  - 目标会话"从无未读 -> 有未读"= 来新消息; 内容取该行预览文本(或可选点开读完整气泡).

probe(target) 返回 dict, 供 routine 轮询调用.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
import time
from ctypes import wintypes

import numpy as np
from PIL import Image

u = ctypes.windll.user32
g = ctypes.windll.gdi32


# ---------------------------------------------------------------- OCR 后端
def _pi_ocr():
    cands = [
        os.environ.get('PI_OCR_DIR'),
        os.path.expanduser('~/.zero/skills/windows-ocr/scripts'),
        os.path.expanduser('~/.zero/agent-workspaces/pi_23/skills/windows-ocr/scripts'),
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'pi_ocr.py')) and c not in sys.path:
            sys.path.insert(0, c)
    import pi_ocr
    return pi_ocr


# ---------------------------------------------------------------- 抓窗口
class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", wt.POINT),
                ("ptMaxPosition", wt.POINT), ("rcNormalPosition", wt.RECT)]


def find_wechat():
    """找微信主窗口. 返回 [(hwnd, normal_rect, iconic)]. 最小化时用正常位置尺寸(GetWindowPlacement)."""
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        tb = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, tb, n + 1)
        if tb.value != '微信':
            return True
        wp = _WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
        u.GetWindowPlacement(hwnd, ctypes.byref(wp))
        r = wp.rcNormalPosition
        if (r.right - r.left) > 300 and (r.bottom - r.top) > 300:
            res.append((hwnd, (r.left, r.top, r.right, r.bottom), bool(u.IsIconic(hwnd))))
        return True

    u.EnumWindows(cb, 0)
    return res


def capture_window(hwnd, rect=None, iconic=None):
    """PrintWindow 抓窗口(不抢焦点). 最小化时: SW_SHOWNOACTIVATE 显示->抓->SW_MINIMIZE.
    rect = 正常位置 (l,t,r,b); 缺省用 GetWindowRect."""
    if iconic is None:
        iconic = bool(u.IsIconic(hwnd))
    if rect is None:
        r = wt.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(r))
        rect = (r.left, r.top, r.right, r.bottom)
    L, T, R, B = rect
    w, h = R - L, B - T
    restored = False
    if iconic:
        u.ShowWindow(hwnd, 4)      # SW_SHOWNOACTIVATE: 显示但不抢焦点
        # 不用 sleep: PrintWindow 会同步触发生成, 实测 0 延迟也能抓到内容(整段 ~20ms, 几乎看不出闪)
        restored = True
    hdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mdc, bmp)
    ok = u.PrintWindow(hwnd, mdc, 2)  # PW_RENDERFULLCONTENT
    bmi = _BMI()
    bi = bmi.bmiHeader
    bi.biSize = ctypes.sizeof(_BMIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = (ctypes.c_char * (w * h * 4))()
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(hwnd, hdc)
    if restored:
        u.ShowWindow(hwnd, 6)      # SW_MINIMIZE: 还原成最小化
    im = Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1)
    return bool(ok), im


# ---------------------------------------------------------------- 检测
def _red_badges(arr, W, H):
    """头像列的红点角标 y 中心列表 (左栏竖导航那列红点不算)."""
    Rc, Gc, Bc = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    red = (Rc > 150) & (Gc < 110) & (Bc < 110) & ((Rc - Gc) > 70) & ((Rc - Bc) > 60)
    m = red.copy()
    m[:, :int(0.07 * W)] = False
    m[:, int(0.17 * W):] = False
    rc = m.sum(axis=1)
    rows = np.where(rc > 0)[0]
    out = []
    if len(rows):
        a = p = rows[0]
        for y in rows[1:]:
            if y - p > 8:
                out.append(int((a + p) // 2))
                a = y
            p = y
        out.append(int((a + p) // 2))
    return out, int(red[:, :int(0.07 * W)].sum())   # 第二个=左侧总未读红点数(粗略)


def _ocr_left(img, W, H):
    # 只 OCR 左栏(会话列表), 大幅减少像素量/耗时; crop 从 x=0 起, 坐标不变
    crop = img.crop((0, 0, int(0.36 * W), H))
    tmp = os.path.join(tempfile.gettempdir(), '_wx_probe.png')
    crop.save(tmp)
    pi = _pi_ocr()
    boxes = pi.ocr_boxes(tmp, scale=2)
    return [b for b in boxes if int(b['x']) < 0.33 * W and int(b['y']) > 0.05 * H]


def probe(target, want_full=False):
    """看一帧: 目标会话是否出现未读 + 预览内容. 无任何输入/焦点副作用."""
    wins = find_wechat()
    if not wins:
        return {'ok': False, 'error': 'WeChat window not found'}
    hwnd, rect, iconic = wins[0]
    L, T, R, B = rect
    W, H = R - L, B - T
    ok, img = capture_window(hwnd, rect, iconic)
    arr = np.asarray(img).astype(np.int16)
    badges, total_red = _red_badges(arr, W, H)
    boxes = _ocr_left(img, W, H)
    name_boxes = [b for b in boxes if b['text'].strip() == target and 0.10 * W < int(b['x']) < 0.33 * W]
    row = None
    if name_boxes:
        nb = sorted(name_boxes, key=lambda b: b['y'])[0]
        ry = int(nb['y'])
        previews = [b for b in boxes if ry < int(b['y']) <= ry + 32 and 0.10 * W < int(b['x']) < 0.30 * W]
        previews.sort(key=lambda b: b['y'])
        row = {
            'y': ry,
            'preview': previews[0]['text'] if previews else '',
            'badge': any(abs(by - (ry + 4)) <= 16 for by in badges),
        }
    return {
        'ok': True, 'hwnd': hwnd, 'rect': list(rect), 'iconic': iconic,
        'target': target, 'row': row, 'badges': badges, 'total_red': total_red,
        'capture_ok': ok,
    }


if __name__ == '__main__':
    import json
    import sys as _s
    tgt = _s.argv[1] if len(_s.argv) > 1 else '前行'
    _s.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(probe(tgt), ensure_ascii=False))
