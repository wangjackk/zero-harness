# -*- coding: utf-8 -*-
"""wechat_send ---- 用桌面自动化给微信联系人发消息 (无固定坐标, OCR 文字锚点).

能力: 找到微信 (Weixin.exe) 主窗口 -> OneOCR 识别界面文字+坐标 -> 模拟鼠标/键盘
打开指定联系人的会话并发送文本. 与 sodamusic-control 同套路, 驱动的是 Qt 版微信.

为什么是 routine 而不是纯 skill: 需要真实执行逻辑 (截屏/OCR/模拟输入).

实现: 真正的 GUI 逻辑在 ``_wechat_gui.py`` (以 '_' 前缀, watcher 会跳过),
由本 routine 用「装了 Pillow+numpy 的 python」子进程拉起 ---- OCR (pi_ocr) 与截图需要
Pillow. 默认优先用 routine 自己的运行 venv (已装 Pillow/numpy/pynput), 再退回 ~/.zero/kernel-venv.
鼠标/键盘模拟优先走 pynput (装了就即用), 否则回退纯 ctypes.

前置条件:
  - 微信桌面端 (Weixin.exe) 已登录并开着窗口
  - 机器上存在一个带 Pillow+numpy 的 python (默认探测 ~/.zero/kernel-venv,
    可用环境变量 WECHAT_SEND_PYTHON 或入参 python 覆盖)

注意: 会短暂占用鼠标键盘与前台焦点; 偶尔需要重试(内部已做 3 次).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine

_HELPER = Path(__file__).resolve().parent / '_wechat_gui.py'


def _has_deps(py: str) -> bool:
    try:
        r = subprocess.run([py, '-c', 'import PIL, numpy'], capture_output=True, timeout=25)
        return r.returncode == 0
    except Exception:
        return False


def _find_python(explicit: Optional[str] = None) -> Optional[str]:
    cands: List[str] = []
    if explicit:
        cands.append(explicit)
    for env in ('WECHAT_SEND_PYTHON', 'PI_OCR_PYTHON'):
        v = os.environ.get(env)
        if v:
            cands.append(v)
    home = Path.home()
    cands += [
        sys.executable,                       # 优先 routine 自己的运行 venv
        str(home / '.zero' / 'kernel-venv' / 'Scripts' / 'python.exe'),
        str(home / '.zero' / 'kernel-venv' / 'bin' / 'python'),
        shutil.which('python') or '',
        shutil.which('python3') or '',
    ]
    seen = set()
    for c in cands:
        if not c or c in seen:
            continue
        seen.add(c)
        if Path(c).exists() and _has_deps(c):
            return c
    return None


class WeChatSendInput(BaseModel):
    """wechat_send 入参."""

    to: str = Field(description='微信联系人/群的显示名, 例如 "前行"')
    message: str = Field(default='', description='要发送的文本 (支持中文/emoji, 走剪贴板粘贴; 与 image 二选一)')
    image: Optional[str] = Field(
        default=None, description='要发送的图片文件绝对路径(png/jpg); 给了它就发图, 忽略 message')
    python: Optional[str] = Field(
        default=None, description='可选: 具备 Pillow+numpy 的 python 解释器绝对路径')
    scale: float = Field(default=2, gt=0, le=4, description='OCR 放大倍数 (小字用 2~3)')
    timeout: float = Field(default=120, gt=0, description='子进程超时秒数')


class WeChatSendOutput(BaseModel):
    """wechat_send 出参."""

    ok: bool = Field(description='是否成功发出')
    to: str = Field(description='目标联系人')
    message_len: int = Field(default=0, description='发送文本长度')
    image: Optional[str] = Field(default=None, description='发送的图片路径(若发的是图)')
    send_via: Optional[str] = Field(default=None, description='发送方式: button|enter')
    confirmed: bool = Field(default=False, description='回读是否确认到消息气泡')
    window_rect: List[int] = Field(default_factory=list, description='微信窗口 [l,t,r,b]')
    to_anchor: Optional[List[int]] = Field(default=None, description='联系人锚点坐标')
    send_anchor: Optional[List[int]] = Field(default=None, description='发送按钮锚点坐标')
    elapsed_ms: float = Field(default=0, description='耗时毫秒')
    log: List[str] = Field(default_factory=list, description='分步日志')
    error: Optional[str] = Field(default=None, description='失败原因')


class WeChatSend(Routine):
    """给微信联系人发送一条文本消息 (桌面自动化 + OneOCR 文字锚点)."""

    name = 'wechat_send'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '给微信联系人(显示名)发送一条文本消息. 无固定坐标: 截微信窗口 -> OneOCR 识别 '
            '文字+像素坐标 -> 模拟鼠标键盘打开会话并发送. 需微信桌面端已登录且开着窗口, '
            '过程中会短暂占用鼠标键盘. 当用户说"用微信给 X 发消息/发个微信"时使用.'),
        'input_schema': WeChatSendInput.model_json_schema(),
        'output_schema': WeChatSendOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = WeChatSendInput.model_validate(kwargs)
        if not inp.message and not inp.image:
            return WeChatSendOutput(ok=False, to=inp.to,
                                    error='message 和 image 至少要给一个').model_dump()
        if inp.image and not Path(inp.image).is_file():
            return WeChatSendOutput(ok=False, to=inp.to,
                                    error='图片文件不存在: %s' % inp.image).model_dump()

        if not _HELPER.is_file():
            return WeChatSendOutput(ok=False, to=inp.to,
                                    error='helper not found: %s' % _HELPER).model_dump()

        # 解释器探测是同步 IO ---- 丢线程池, 不堵事件循环
        py = await asyncio.to_thread(_find_python, inp.python)
        if not py:
            return WeChatSendOutput(
                ok=False, to=inp.to,
                error='未找到带 Pillow+numpy 的 python; 设 WECHAT_SEND_PYTHON 或传 python=...',
            ).model_dump()

        cmd = [py, str(_HELPER), '--to', inp.to, '--message', inp.message,
               '--scale', str(inp.scale)]
        if inp.image:
            cmd += ['--image', inp.image]
        # 全程用 asyncio 子进程 (create_subprocess_exec + wait_for), 不让 ~10s 的
        # GUI/OCR 阻塞 kernel 共享事件循环
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(_HELPER.parent))
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=inp.timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return WeChatSendOutput(ok=False, to=inp.to,
                                    error='timeout after %ss' % inp.timeout).model_dump()

        stdout = (out_b or b'').decode('utf-8', 'ignore')
        stderr = (err_b or b'').decode('utf-8', 'ignore')

        data: Dict[str, Any] = {}
        for line in reversed(stdout.strip().splitlines()):
            line = line.strip()
            if line.startswith('{'):
                try:
                    data = json.loads(line)
                    break
                except Exception:
                    continue
        if not data:
            return WeChatSendOutput(
                ok=False, to=inp.to,
                error='helper 无 JSON 输出 (rc=%s): %s' % (proc.returncode, stderr[-400:]),
            ).model_dump()

        return WeChatSendOutput(
            ok=bool(data.get('ok')),
            to=data.get('to', inp.to),
            message_len=int(data.get('message_len', 0) or 0),
            image=data.get('image'),
            send_via=data.get('send_via'),
            confirmed=bool(data.get('confirmed', False)),
            window_rect=list(data.get('window_rect') or []),
            to_anchor=data.get('to_anchor'),
            send_anchor=data.get('send_anchor'),
            elapsed_ms=float(data.get('elapsed_ms', 0) or 0),
            log=list(data.get('log') or []),
            error=data.get('error'),
        ).model_dump()


__all__ = ['WeChatSend', 'WeChatSendInput', 'WeChatSendOutput']
