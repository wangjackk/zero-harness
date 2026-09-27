# -*- coding: utf-8 -*-
"""_wechat_gui.py --- WeChat 桌面端的 GUI 自动化内核 (无固定坐标).

策略: 截图 WeChat 窗口 -> OneOCR 拿"文字 + 像素坐标" -> 把词当可点锚点 ->
ctypes 模拟鼠标/键盘. 与 sodamusic-control 同套路, 但驱动的是 Qt 版微信 (Weixin.exe).

以 '_' 前缀命名: dynamic watcher 跳过该文件, 不会被当成 routine 扫描.

由 dynamic/wechat_send.py routine 用「装了 Pillow+numpy 的 python」子进程调用.
CLI:  python _wechat_gui.py --to 前行 --message "..." [--scale 2] [--dry-run]
stdout: 单行 JSON {ok, ...}
"""
from __future__ import annotations

import argparse, ctypes, ctypes.wintypes as wt, json, os, sys, tempfile, time

LOG: list = []


def log(msg: str) -> None:
    LOG.append(msg)


# ---------------------------------------------------------------- OCR backend
def ensure_pi_ocr():
    cands = [
        os.environ.get('PI_OCR_DIR'),
        os.path.expanduser('~/.zero/skills/windows-ocr/scripts'),
        os.path.expanduser('~/.zero/agent-workspaces/pi_23/skills/windows-ocr/scripts'),
        os.path.expanduser('~/.zero/agent-workspaces/pi_23/.pi/skills/windows-ocr/scripts'),
    ]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'pi_ocr.py')) and c not in sys.path:
            sys.path.insert(0, c)
    import pi_ocr  # noqa
    return pi_ocr


try:
    from PIL import ImageGrab
except Exception as e:  # pragma: no cover
    print(json.dumps({'ok': False, 'error': 'Pillow unavailable: %s' % e}))
    raise SystemExit(2)

u = ctypes.windll.user32

# 输入后端: 优先 pynput (已装则用), 否则回退纯 ctypes. 鼠标/键盘都走这里.
try:
    from pynput.mouse import Controller as _PynMouse, Button as _PynButton
    from pynput.keyboard import Controller as _PynKeyboard, Key as _PynKey
    _MOUSE = _PynMouse()
    _KEYBOARD = _PynKeyboard()
    HAVE_PYNPUT = True
except Exception as _e:  # pragma: no cover
    HAVE_PYNPUT = False
    _PYNPUT_ERR = _e


# ---------------------------------------------------------------- input sim
PUL = ctypes.POINTER(ctypes.c_ulong)


class _KI(ctypes.Structure):
    _fields_ = [('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', PUL)]


class _MI(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', ctypes.c_ulong),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong), ('dwExtraInfo', PUL)]


class _IU(ctypes.Union):
    _fields_ = [('ki', _KI), ('mi', _MI)]


class _INP(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('u', _IU)]


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56


def _send(inp):
    u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INP))


def click(x, y):
    if HAVE_PYNPUT:
        _MOUSE.position = (int(x), int(y))
        time.sleep(0.05)
        _MOUSE.press(_PynButton.left)
        time.sleep(0.04)
        _MOUSE.release(_PynButton.left)
        return
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.04)
    u.mouse_event(0x0004, 0, 0, 0, 0)


def _vk_tap(vk):
    _send(_INP(type=1, u=_IU(ki=_KI(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))))
    time.sleep(0.02)
    _send(_INP(type=1, u=_IU(ki=_KI(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))))


def move(x, y):
    """只移动鼠标到 (x,y), 不点击 (用于把指针移出比对区域, 免污染像素 diff)."""
    if HAVE_PYNPUT:
        _MOUSE.position = (int(x), int(y))
    else:
        u.SetCursorPos(int(x), int(y))


def enter():
    if HAVE_PYNPUT:
        _KEYBOARD.press(_PynKey.enter)
        _KEYBOARD.release(_PynKey.enter)
        return
    _vk_tap(VK_RETURN)


def ctrl_v():
    if HAVE_PYNPUT:
        with _KEYBOARD.pressed(_PynKey.ctrl):
            _KEYBOARD.press('v')
            _KEYBOARD.release('v')
        return
    _send(_INP(type=1, u=_IU(ki=_KI(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))))
    time.sleep(0.03)
    _vk_tap(VK_V)
    _send(_INP(type=1, u=_IU(ki=_KI(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))))


def clear_input():
    """清空输入框残留文本: Ctrl+A -> Delete (在输入框聚焦时调)."""
    if HAVE_PYNPUT:
        with _KEYBOARD.pressed(_PynKey.ctrl):
            _KEYBOARD.press('a')
            _KEYBOARD.release('a')
        _KEYBOARD.press(_PynKey.delete)
        _KEYBOARD.release(_PynKey.delete)
        return
    _send(_INP(type=1, u=_IU(ki=_KI(wVk=VK_CONTROL, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))))
    time.sleep(0.02)
    _vk_tap(0x41)   # 'A'
    time.sleep(0.02)
    _send(_INP(type=1, u=_IU(ki=_KI(wVk=VK_CONTROL, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))))
    _vk_tap(0x2E)   # Delete


def type_text(text: str, per_char=0.004):
    """逐 UTF-16 码元用 KEYEVENTF_UNICODE 注入, 支持中文/emoji, 不碰剪贴板."""
    units = text.encode('utf-16-le')
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i:i + 2], 'little')
        _send(_INP(type=1, u=_IU(ki=_KI(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=None))))
        _send(_INP(type=1, u=_IU(ki=_KI(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))))
        time.sleep(per_char)


def set_clipboard(text: str) -> bool:
    """兜底: 用 powershell Set-Clipboard (base64 传参, 免转义/编码坑)."""
    import base64
    b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
    ps = ("Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString("
          "[Convert]::FromBase64String('%s')))" % b64)
    try:
        r = __import__('subprocess').run(['powershell', '-NoProfile', '-Sta', '-Command', ps],
                                         capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception as e:
        log('set_clipboard failed: %s' % e)
        return False


def get_clipboard_text():
    """尽力读取当前剪贴板文本(用于还原); 失败/非文本返回 None."""
    import base64, subprocess
    ps = ("$s = Get-Clipboard -Raw -Format Text -ErrorAction SilentlyContinue; "
          "if ($null -eq $s) { '' } else { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($s)) }")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Sta', '-Command', ps],
                           capture_output=True, timeout=15)
        b64 = r.stdout.decode('ascii', 'ignore').strip()
        return base64.b64decode(b64).decode('utf-8') if b64 else None
    except Exception:
        return None


def set_image_clipboard(path: str) -> bool:
    """把图片文件塞进剪贴板 (STA powershell + System.Drawing)."""
    import subprocess
    p = path.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Windows.Forms; "
          "Add-Type -AssemblyName System.Drawing; "
          "$img=[System.Drawing.Image]::FromFile('%s'); "
          "[System.Windows.Forms.Clipboard]::SetImage($img); $img.Dispose()" % p)
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Sta', '-Command', ps],
                           capture_output=True, timeout=20)
        if r.returncode != 0:
            log('set_image_clipboard rc=%s err=%s' % (r.returncode, (r.stderr or b'')[-200:]))
        return r.returncode == 0
    except Exception as e:
        log('set_image_clipboard failed: %s' % e)
        return False


def _grab_rgb(box):
    return ImageGrab.grab(bbox=box).convert('RGB')


def _mean_abs_diff(a, b):
    """两张同尺寸图平均像素差 (图片没有文字可比对, 用它判断输入区是否变化)."""
    import numpy as np
    A = np.asarray(a, dtype='int16')
    B = np.asarray(b, dtype='int16')
    if A.shape != B.shape:
        return 999.0
    return float(np.abs(A - B).mean())


# ---------------------------------------------------------------- window mgmt
class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wt.DWORD), ("flags", wt.DWORD), ("showCmd", wt.DWORD),
                ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
                ("rcNormalPosition", wt.RECT)]


def find_wechat():
    """找微信主窗口. 最小化时用 GetWindowPlacement 的"正常位置"尺寸判定, 返回 iconic 标志."""
    res = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        n = u.GetWindowTextLengthW(hwnd)
        tb = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, tb, n + 1)
        if tb.value != '微信':
            return True
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        wp = _WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(_WINDOWPLACEMENT)
        u.GetWindowPlacement(hwnd, ctypes.byref(wp))
        r = wp.rcNormalPosition
        if (r.right - r.left) > 300 and (r.bottom - r.top) > 300:
            res.append(dict(hwnd=hwnd, pid=pid.value,
                            rect=(r.left, r.top, r.right, r.bottom),
                            iconic=bool(u.IsIconic(hwnd))))
        return True

    u.EnumWindows(cb, 0)
    return res


def focus(hwnd):
    """把窗口切到前台. 优先 AttachThreadInput(不发送裸 ALT, 避免被当成菜单键吞掉第一次点击)."""
    u.ShowWindow(hwnd, 9)
    if u.GetForegroundWindow() == hwnd:
        return True
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    tgt = u.GetWindowThreadProcessId(hwnd, None)
    try:
        u.AttachThreadInput(cur, tgt, True)
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.AttachThreadInput(cur, tgt, False)
    except Exception as e:
        log('focus(attach) err: %s' % e)
    time.sleep(0.25)
    if u.GetForegroundWindow() != hwnd:
        u.keybd_event(0x12, 0, 0, 0)
        u.keybd_event(0x12, 0, 2, 0)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.25)
    return u.GetForegroundWindow() == hwnd


def _norm(s):
    """归一化: 只留字母/数字/CJK, 去掉空白与 emoji —— 用于"消息指纹"比对."""
    return ''.join(ch for ch in s if ch.isalnum())


def _probe_keys(message, n=6, step=18):
    """把消息归一化后切成分布更均匀的 n 元探针(含首/尾).

    长消息在输入框里会换行并可能滚动, 首字符常常滚出可视区 —— 只取前 6 字
    会误判"没输入进去"。多点取样, 只要有一片可见即算通过。
    """
    s = _norm(message)
    if not s:
        return []
    if len(s) <= n:
        return [s]
    span = len(s) - n
    offs = sorted(set(list(range(0, span + 1, step)) + [span]))
    return [s[o:o + n] for o in offs]


def _near_hit(joined, probe, tol=1):
    """probe 在 joined 里以 <= tol 个字符差异命中(容忍 OCR 个别误读, 如 AI->Al)."""
    if len(probe) > len(joined):
        return False
    for i in range(len(joined) - len(probe) + 1):
        seg = joined[i:i + len(probe)]
        if seg == probe or sum(a != b for a, b in zip(seg, probe)) <= tol:
            return True
    return False


def _band_norm(pi_ocr, box, scale):
    """抓取 box 区域 -> OCR -> 归一化文本(按 y,x 顺序拼接)."""
    img = ImageGrab.grab(bbox=box)
    out = os.path.join(tempfile.gettempdir(), '_wc_input.png')
    img.save(out)
    boxes = pi_ocr.ocr_boxes(out, scale=scale)
    joined = _norm(''.join(b['text'] for b in sorted(boxes, key=lambda b: (b['y'], b['x']))))
    return boxes, joined


def input_has_text(pi_ocr, rect, message, scale=2):
    """OCR 输入框区域, 判断刚才粘进去的文本是否真的到了输入框.

    注意: 该窄带(0.74~0.88H)始终落在输入框内部(框变高时向上扩, 但这条带仍在框内,
    不会误采到上方聊天区), 所以对任意长度都安全 —— 问题只是长文本会换行/滚动,
    前几个字可能不在带内, 故改用分布在首尾的多个探针.
    """
    L, T, R, B = rect
    W, H = R - L, B - T
    box = (L + int(0.36 * W), T + int(0.74 * H), R - int(0.02 * W), T + int(0.88 * H))
    probes = _probe_keys(message)
    if not probes:
        return True
    try:
        _, joined = _band_norm(pi_ocr, box, scale)
    except Exception as e:
        log('input verify ocr failed: %r' % e)
        return False
    hit = [p for p in probes if p in joined]
    if not hit:
        hit = [p for p in probes if _near_hit(joined, p)]
    log('input verify probes=%d hit=%d joined=%r' % (len(probes), len(hit), joined[:70]))
    return bool(hit)


# ---------------------------------------------------------------- OCR anchoring
class Anchor:
    def __init__(self, boxes, rect):
        self.boxes = boxes
        self.L, self.T, self.R, self.B = rect
        self.W = self.R - self.L
        self.H = self.B - self.T

    def find(self, pred):
        hit = [b for b in self.boxes if pred(b)]
        return hit

    def text_is(self, t):
        return [b for b in self.boxes if b['text'].strip() == t]

    def contains(self, sub):
        return [b for b in self.boxes if sub in b['text']]


def ocr_window(pi_ocr, rect, scale=2, out=None):
    if out is None:
        out = os.path.join(tempfile.gettempdir(), '_wc_ocr_tmp.png')
    img = ImageGrab.grab(bbox=rect)
    img.save(out)
    boxes = pi_ocr.ocr_boxes(out, scale=scale)
    L, T, R, B = rect
    for b in boxes:
        b['ax'] = L + int(b['x'])
        b['ay'] = T + int(b['y'])
        b['acx'] = b['ax'] + int(b['w']) // 2
        b['acy'] = b['ay'] + int(b['h']) // 2
    return Anchor(boxes, rect)


# ---------------------------------------------------------------- main flow
def run(to, message, scale=2, dry=False, image=None):
    """入口: 若微信是最小化的, 先记下, 跑完(_run_impl 里会 focus 恢复)再还原成最小化."""
    wins = find_wechat()
    was_iconic = bool(wins and wins[0].get('iconic'))
    hwnd = wins[0]['hwnd'] if wins else None
    try:
        return _run_impl(to, message, scale, dry, image)
    finally:
        if was_iconic and hwnd is not None:
            try:
                u.ShowWindow(hwnd, 6)      # SW_MINIMIZE: 还原用户原来的最小化状态
            except Exception:
                pass


def _run_impl(to, message, scale=2, dry=False, image=None):
    t0 = time.time()
    pi_ocr = ensure_pi_ocr()
    log('ocr_backend=%s input_backend=%s' % (pi_ocr._OCR_BACKEND, 'pynput' if HAVE_PYNPUT else 'ctypes'))

    wins = find_wechat()
    if not wins:
        return {'ok': False, 'error': 'WeChat window (Weixin.exe / title=微信) not found. 请先登录并打开微信.'}
    win = wins[0]
    hwnd, rect = win['hwnd'], win['rect']
    log('window rect=%s hwnd=%s' % (rect, hwnd))
    focus(hwnd)
    time.sleep(0.7)
    log('fg=%s hwnd=%s' % (u.GetForegroundWindow(), hwnd))

    a = ocr_window(pi_ocr, rect, scale)
    L, T, R, B = rect
    W, H = R - L, B - T

    def in_left(b):
        return L + 0.02 * W <= b['ax'] <= L + 0.35 * W

    # 1) 打开会话: 先会话列表 -> 搜索框; 打开后校验顶部标题, 失败则重试
    def list_rows():
        a_ = ocr_window(pi_ocr, rect, scale)
        r_ = [b for b in a_.text_is(to) if in_left(b)]
        r_.sort(key=lambda b: b['ay'])
        return a_, r_

    def search_once():
        a_ = ocr_window(pi_ocr, rect, scale)
        sb = [b for b in a_.contains('搜索') if b['ax'] < L + 0.35 * W]
        if not sb:
            return None, None
        sb = sb[0]
        click(sb['acx'] + 40, sb['acy'])
        time.sleep(0.4)
        if not set_clipboard(to):
            type_text(to)
        else:
            ctrl_v()
        time.sleep(1.3)
        a2 = ocr_window(pi_ocr, rect, scale)
        cands = [b for b in a2.boxes if b['text'].strip() == to
                 and b['ay'] > sb['acy'] + 20 and b['ax'] < L + 0.35 * W]
        if not cands:
            cands = [b for b in a2.boxes if b['text'].strip().startswith(to)
                     and b['ay'] > sb['acy'] + 20 and b['ax'] < L + 0.35 * W]
        cands.sort(key=lambda b: b['ay'])
        return (cands[0] if cands else None), sb

    def header_ok():
        a_ = ocr_window(pi_ocr, rect, scale)
        h = [b for b in a_.text_is(to) if b['ax'] >= L + 0.34 * W and b['ay'] < T + 0.18 * H]
        return a_, bool(h)

    to_anchor = None
    a, rows = list_rows()
    log('list rows=%d' % len(rows))
    if rows:
        to_anchor = rows[0]
    else:
        to_anchor, sb = search_once()
        if to_anchor is None:
            return {'ok': False, 'error': "找不到联系人 '%s' (列表和搜索都无结果)" % to}
        log('resolved via search box')

    cx = L + 0.18 * W
    header_found = False
    for attempt in range(3):
        click(cx, to_anchor['acy'])
        time.sleep(1.2)
        a, header_found = header_ok()
        log('open attempt %d: header_found=%s fg=%s' % (attempt + 1, header_found, u.GetForegroundWindow()))
        if header_found:
            break
        if attempt == 1 and not rows:
            # 兜底: 再搜一次(列表可能刷新)
            nx, _ = search_once()
            if nx:
                to_anchor = nx
        time.sleep(0.6)

    if not header_found:
        dbg = [{'rel': [int(b['x']), int(b['y'])], 'text': b['text']} for b in a.boxes]
        return {'ok': False, 'error': "3 次尝试后仍未打开 '%s' 的会话" % to, 'boxes': dbg[:40]}

    if dry:
        return {'ok': True, 'dry_run': True, 'to': to, 'window_rect': list(rect),
                'to_anchor': [to_anchor['acx'], to_anchor['acy']],
                'elapsed_ms': round((time.time() - t0) * 1000, 1), 'log': LOG}

    # 3) 定位输入框: 占位符 -> 发送按钮左移 -> 比例兜底
    ph = [b for b in a.boxes if ('语音输入' in b['text']) or ('Ctrl' in b['text'])]
    send_box = [b for b in a.boxes if b['text'].strip() == '发送']
    send_anchor = send_box[0] if send_box else None
    if ph:
        ix, iy = ph[0]['acx'], ph[0]['acy']
        log('input via placeholder %s' % ((ix, iy),))
    elif send_anchor:
        ix, iy = send_anchor['acx'] - 260, send_anchor['acy'] - 90
        log('input via 发送-offset %s' % ((ix, iy),))
    else:
        ix, iy = L + 0.66 * W, T + 0.80 * H
        log('input via fraction %s' % ((ix, iy),))
    click(ix, iy)
    time.sleep(0.4)
    clear_input()          # 先清掉输入框里可能残留的字符(否则会和消息拼一起)
    time.sleep(0.15)

    # 4) 输入: 文本 或 图片. 剪贴板粘贴最稳 -> 回读/像素比对确认真的进去了 (防"客户端半死")
    prev_clip = get_clipboard_text()
    img_box = (L + int(0.36 * W), T + int(0.70 * H), R - int(0.02 * W), T + int(0.90 * H))
    empty_shot = None
    ok_input = False
    for attempt in range(2):
        click(ix, iy)
        time.sleep(0.4)
        clear_input()          # 清掉输入框残留, 否则会和消息/图片拼一起
        time.sleep(0.15)
        if empty_shot is None:
            move(L + int(0.5 * W), T + int(0.08 * H))   # 指针移出比对区(否则鼠标光标会污染像素 diff)
            time.sleep(0.25)
            empty_shot = _grab_rgb(img_box)
        if image:
            if not set_image_clipboard(image):
                return {'ok': False, 'to': to, 'send_via': None, 'confirmed': False,
                        'window_rect': list(rect),
                        'error': '无法把图片放进剪贴板: %s' % image,
                        'stage': 'image_clipboard', 'log': LOG}
            ctrl_v()
            log('pasted image via clipboard (attempt %d): %s' % (attempt + 1, os.path.basename(image)))
            time.sleep(1.3)
            diff = _mean_abs_diff(empty_shot, _grab_rgb(img_box))
            if diff > 12:
                ok_input = True
                log('image verify ok (input region diff=%.1f)' % diff)
                break
            log('image verify FAILED (attempt %d, diff=%.1f)' % (attempt + 1, diff))
        else:
            if set_clipboard(message):
                ctrl_v()
                log('pasted via clipboard (attempt %d)' % (attempt + 1))
            else:
                type_text(message)
                log('typed via unicode (attempt %d)' % (attempt + 1))
            time.sleep(0.7)
            if input_has_text(pi_ocr, rect, message, scale):
                ok_input = True
                break
            log('input verify FAILED (attempt %d)' % (attempt + 1))
    if not ok_input:
        if prev_clip is not None:
            set_clipboard(prev_clip)
        return {'ok': False, 'to': to, 'send_via': None, 'confirmed': False,
                'window_rect': list(rect),
                'error': ('输入框没收到图片' if image else '输入框没收到文本')
                         + '(微信可能卡死/未激活) -> 已中止, 未发送. '
                           '建议重启微信客户端: Stop-Process -Name Weixin -Force; '
                           "Start-Process 'D:\\Weixin\\Weixin.exe'",
                'stage': 'input_verify', 'log': LOG}

    # 5) 发送: 点 OCR 找到的 '发送' 按钮, 否则回车
    a2 = ocr_window(pi_ocr, rect, scale)
    sb2 = [b for b in a2.boxes if b['text'].strip() == '发送']
    used = None
    if sb2:
        send_anchor = sb2[0]
        used = 'button'
        click(send_anchor['acx'], send_anchor['acy'])
        log('clicked 发送 @(%d,%d)' % (send_anchor['acx'], send_anchor['acy']))
    else:
        used = 'enter'
        enter()
        log('发送 button not found, used Enter')
    time.sleep(0.9)

    if prev_clip is not None:
        set_clipboard(prev_clip)

    # 6) 回读确认
    if image:
        # 图片没有文字可比对: 发送后输入区应回到"空"(与粘贴前一致) 即视为已发出
        move(L + int(0.5 * W), T + int(0.08 * H))
        time.sleep(0.3)
        back = _mean_abs_diff(_grab_rgb(img_box), empty_shot)
        confirm = back < 10
        log('confirm_image: input region diff vs empty=%.1f -> %s' % (back, confirm))
    else:
        # 多探针; 长消息前缀会滚出气泡可视区, 只用前 6 字会漏判
        probes = _probe_keys(message)
        a3 = ocr_window(pi_ocr, rect, scale)
        j3 = _norm(''.join(b['text'] for b in sorted(a3.boxes, key=lambda b: (b['y'], b['x']))))
        confirm = any(p in j3 for p in probes) if probes else True
        log('confirm_bubble=%s (probes=%d key=%r)' % (confirm, len(probes), probes[0] if probes else ''))

    return {'ok': True, 'to': to, 'message_len': len(message), 'image': image,
            'window_rect': list(rect),
            'to_anchor': [to_anchor['acx'], to_anchor['acy']],
            'send_anchor': [send_anchor['acx'], send_anchor['acy']] if send_anchor else None,
            'send_via': used, 'confirmed': confirm,
            'elapsed_ms': round((time.time() - t0) * 1000, 1), 'log': LOG}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--to', required=True)
    ap.add_argument('--message', default='')
    ap.add_argument('--image', default=None, help='图片文件绝对路径 (与 --message 二选一)')
    ap.add_argument('--scale', type=float, default=2)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    try:
        res = run(args.to, args.message, args.scale, args.dry_run, args.image)
    except Exception as e:
        import traceback
        res = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e),
               'trace': traceback.format_exc()[-1500:], 'log': LOG}
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1:])
