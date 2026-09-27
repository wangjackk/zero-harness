"""screenshot ---- 屏幕截图 (零 Python 依赖: 走 Windows .NET CopyFromScreen).

为什么不加 mss/Pillow 依赖: zero-py venv 只带 numpy/sounddevice, 截图是系统能力,
Windows 自带 System.Drawing/System.Windows.Forms 就够 ---- routine 里 subprocess
调 powershell, 不污染应用依赖树.

要点:
- DPI aware: 进程默认非 DPI 感知时 CopyFromScreen 拿到的是被系统虚拟化缩放过的
  位图 (高分屏上糊). 显式 SetProcessDPIAware 后拿到物理像素.
- monitor: 0 = 所有屏幕拼成的虚拟桌面; 1..n = 第 n 块屏 (物理像素坐标).
- region: "x,y,w,h" 在 monitor 决定的范围上再裁一刀.
- scale: 只缩小不放大, HighQualityBicubic.

输出: PNG/JPG 落盘, 返回路径 + 尺寸 + 字节数 (不返回像素 ---- 见 skill 讨论).
"""
from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine

# zero-py/app/routines/user/dynamic/screenshot.py -> parents[4] = zero-py/
_ZERO_PY = Path(__file__).resolve().parents[4]
_DEFAULT_DIR = _ZERO_PY / 'runtime' / 'screenshots'

_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DpiShim { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
"@
[DpiShim]::SetProcessDPIAware() | Out-Null

$monitor = [int]$env:SHOT_MONITOR
$region  = $env:SHOT_REGION
$scale   = [double]$env:SHOT_SCALE
$out     = $env:SHOT_OUT

$screens = [System.Windows.Forms.Screen]::AllScreens
if ($monitor -le 0) {
    $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
} else {
    if ($monitor -gt $screens.Count) { throw "monitor out of range" }
    $b = $screens[$monitor - 1].Bounds
}

$full = New-Object System.Drawing.Bitmap($b.Width, $b.Height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$g = [System.Drawing.Graphics]::FromImage($full)
$g.CopyFromScreen($b.X, $b.Y, 0, 0, $full.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
$g.Dispose()

$src = $full
if ($region) {
    $p = $region.Split(',')
    if ($p.Count -ne 4) { throw "region must be x,y,w,h" }
    $r = New-Object System.Drawing.Rectangle([int]$p[0], [int]$p[1], [int]$p[2], [int]$p[3])
    if ($r.Right -gt $full.Width -or $r.Bottom -gt $full.Height) { throw "region exceeds capture" }
    $src = $full.Clone($r, $full.PixelFormat)
}

if ($scale -gt 0 -and $scale -lt 1.0) {
    $w = [int][Math]::Max(1, [Math]::Round($src.Width * $scale))
    $h = [int][Math]::Max(1, [Math]::Round($src.Height * $scale))
    $dst = New-Object System.Drawing.Bitmap($w, $h)
    $gd = [System.Drawing.Graphics]::FromImage($dst)
    $gd.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $gd.DrawImage($src, 0, 0, $w, $h)
    $gd.Dispose()
    $src = $dst
}

$ext = [System.IO.Path]::GetExtension($out)
if ($ext -match '(?i)^\.jpe?g$') {
    $fmt = [System.Drawing.Imaging.ImageFormat]::Jpeg
} else {
    $fmt = [System.Drawing.Imaging.ImageFormat]::Png
}
$src.Save($out, $fmt)
Write-Output ("OK " + $src.Width + " " + $src.Height)
"""


class ScreenshotInput(BaseModel):
    """screenshot 入参."""

    monitor: int = Field(
        default=0, description='0 = 全部屏幕拼成的虚拟桌面; 1..n = 第 n 块屏幕')
    region: Optional[str] = Field(
        default=None, description='可选取景框 "x,y,w,h" (在 monitor 范围内再裁剪)')
    scale: float = Field(
        default=1.0, gt=0, le=1, description='缩小比例 (0,1]; 1 = 原始物理像素, 只缩不放')
    out: Optional[str] = Field(
        default=None, description='输出路径; 缺省 runtime/screenshots/shot_<ts>.png')
    timeout: float = Field(default=30, gt=0, description='子进程超时秒数')


class ScreenshotOutput(BaseModel):
    """screenshot 出参."""

    path: str = Field(description='落盘图片绝对路径')
    width: int = Field(description='图片宽 (像素)')
    height: int = Field(description='图片高 (像素)')
    bytes: int = Field(description='文件字节数')
    monitor: int = Field(description='实际使用的屏幕序号')
    elapsed_ms: float = Field(description='耗时毫秒')


class Screenshot(Routine):
    """截屏落盘, 返回路径与尺寸."""

    name = 'screenshot'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '屏幕截图(零依赖, Windows .NET CopyFromScreen): 截整屏/指定屏/可选取景框, '
            '缩放到指定比例, 存 PNG/JPG, 返回路径+宽高+字节数. '
            '像素本身不返回 ---- 要"看"由调用方选渲染方式.'),
        'input_schema': ScreenshotInput.model_json_schema(),
        'output_schema': ScreenshotOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = ScreenshotInput.model_validate(kwargs)
        t0 = time.time()
        if inp.out:
            out = Path(inp.out).expanduser().resolve()
        else:
            stamp = time.strftime('%Y%m%d_%H%M%S')
            out = _DEFAULT_DIR / ('shot_' + stamp + '_' + str(int(time.time() * 1000) % 1000)
                                  + '.png')
        out.parent.mkdir(parents=True, exist_ok=True)

        encoded = base64.b64encode(_PS.encode('utf-16-le')).decode('ascii')
        env = dict(os.environ)
        env.update({
            'SHOT_MONITOR': str(inp.monitor),
            'SHOT_REGION': inp.region or '',
            'SHOT_SCALE': str(inp.scale),
            'SHOT_OUT': str(out),
        })
        proc = await asyncio.create_subprocess_exec(
            'powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=inp.timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise RuntimeError('screenshot timeout after ' + str(inp.timeout) + 's')

        p_out = (out_b or b'').decode('utf-8', 'ignore')
        p_err = (err_b or b'').decode('utf-8', 'ignore')
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError('screenshot failed (rc=' + str(proc.returncode) + '): '
                               + p_err.strip()[:500] + ' ' + p_out.strip()[:200])

        tag = p_out.strip().split()
        w, h = (int(tag[1]), int(tag[2])) if len(tag) >= 3 else (0, 0)
        return ScreenshotOutput(
            path=str(out), width=w, height=h, bytes=out.stat().st_size,
            monitor=inp.monitor, elapsed_ms=round((time.time() - t0) * 1000, 1),
        ).model_dump()


__all__ = ['Screenshot', 'ScreenshotInput', 'ScreenshotOutput']
