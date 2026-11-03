"""phone/power ---- 手机电源/亮屏控制 (adb 桥).

背景: 手机端 routine 集 (android/*) 只覆盖 App 内操作, 没有任何"亮屏/熄屏"能力;
android/tap·swipe·open 都不唤醒熄屏(实测 4 种全无效). 唯一可用通道是桌面 adb.

本机实测约束 (Redmi K50 / rubens / Android 14, 生产包):
  - `input keyevent` 被拒: MIUI 未开「USB调试(安全设置)」-> SecurityException INJECT_EVENTS
  - `settings put` 被拒: 缺 WRITE_SECURE_SETTINGS, 所以 stay_on_while_plugged_in 改不动
  - `adb root` 不可用 (production build)
  - 仍然可用: `svc power stayon true` 能唤醒(约 6-9s 后未被前台持锁的应用会自动回息屏),
              `wm dismiss-keyguard` 能免注入权限解锁(无安全锁时才生效),
              `dumpsys power|window` 可读真实电源/锁屏状态
所以 wake 的语义 = "把屏点亮并(默认)解锁", 想稳住不灭屏要靠前台有 FLAG_KEEP_SCREEN_ON 的应用
(播放器等) 或本 routine 的 hold(周期性重申 stayon).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from routine import Routine

_DEFAULT_ADB = r'C:\Users\wjk\AppData\Local\Android\Sdk\platform-tools\adb.exe'
_MODEL_HINT = 'rubens'  # 本机 22041211AC 的 device codename; 换机可传 serial


class PhonePowerInput(BaseModel):
    action: Literal['state', 'wake', 'wake_unlock', 'unlock', 'sleep', 'hold'] = Field(
        default='state', description='state=读状态 | wake=亮屏 | wake_unlock=亮屏并解锁 | unlock=只解锁 | sleep=熄屏(本机大概率受限) | hold=周期性重申点亮, 稳住 N 秒')
    serial: Optional[str] = Field(default=None, description='adb serial; 省略则自动挑非 emulator 且 codename 匹配的手机')
    adb: str = Field(default=_DEFAULT_ADB, description='adb.exe 路径')
    timeout_ms: int = Field(default=8000, ge=500, le=60000, description='等待状态变化的超时')
    seconds: int = Field(default=60, ge=1, le=3600, description='hold 动作的持续时间')
    unlock: bool = Field(default=True, description='wake 时是否顺带 dismiss-keyguard')


class PhonePower(Routine):
    name = 'phone/power'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '手机电源控制(adb): 亮屏/解锁/熄屏/读屏-锁屏状态, 补 android/* 缺失的 wake 能力',
        'input_schema': PhonePowerInput.model_json_schema(),
    }

    async def _adb(self, inp: PhonePowerInput, *args: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            inp.adb, '-s', self._serial, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        return out.decode('utf-8', errors='replace')

    async def _resolve_serial(self, inp: PhonePowerInput) -> None:
        if inp.serial:
            self._serial = inp.serial
            return
        proc = await asyncio.create_subprocess_exec(
            inp.adb, 'devices', '-l',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
        cands: List[str] = []
        for ln in out.decode('utf-8', errors='replace').splitlines()[1:]:
            parts = ln.split()
            if not parts or 'device' not in parts[1:2]:
                continue
            if parts[0].startswith('emulator-'):
                continue
            cands.append(ln)
        if not cands:
            raise RuntimeError('没有可用的 adb 手机设备 (只有 emulator 或空)')
        pick = next((ln for ln in cands if _MODEL_HINT in ln), cands[0])
        self._serial = pick.split()[0]

    async def _state(self, inp: PhonePowerInput) -> Dict[str, Any]:
        pw = await self._adb(inp, 'shell', 'dumpsys', 'power')
        win = await self._adb(inp, 'shell', 'dumpsys', 'window')
        m = re.search(r'mWakefulness=(\w+)', pw)
        wake = m.group(1) if m else 'unknown'
        return {
            'wakefulness': wake,
            'screen_on': wake == 'Awake',
            'keyguard': 'isKeyguardShowing=true' in win,
        }

    async def _wait(self, inp: PhonePowerInput, pred) -> Dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + inp.timeout_ms / 1000
        st = await self._state(inp)
        while not pred(st) and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.4)
            st = await self._state(inp)
        return st

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = PhonePowerInput.model_validate(kwargs)
        await self._resolve_serial(inp)
        out: Dict[str, Any] = {'ok': True, 'serial': self._serial, 'action': inp.action}

        if inp.action == 'state':
            out.update(await self._state(inp))
            return out

        if inp.action in ('wake', 'wake_unlock'):
            before = await self._state(inp)
            out['before'] = before
            await self._adb(inp, 'shell', 'svc', 'power', 'stayon', 'true')
            st = await self._wait(inp, lambda s: s['screen_on'])
            out['screen_on'] = st['screen_on']
            if inp.unlock:
                await self._adb(inp, 'shell', 'wm', 'dismiss-keyguard')
                st = await self._wait(inp, lambda s: not s['keyguard'])
            out.update({'wakefulness': st['wakefulness'], 'keyguard': st['keyguard']})
            out['woke'] = st['screen_on'] and not before['screen_on']
            out['already_on'] = before['screen_on']
            out['note'] = ('屏幕已点亮' if st['screen_on'] else '未能点亮, 检查 adb 连接/设备是否授权') + \
                ('; 无前台持屏应用时约 6-9s 后会自动回息屏, 需 hold 或前台播放器' if st['screen_on'] and not st['keyguard'] else '')
            return out

        if inp.action == 'unlock':
            await self._adb(inp, 'shell', 'wm', 'dismiss-keyguard')
            st = await self._wait(inp, lambda s: not s['keyguard'])
            out.update(st)
            out['ok'] = not st['keyguard']
            if st['keyguard']:
                out['note'] = '锁屏未解除: 可能设了安全锁(PIN/图案), 需人工解锁'
            return out

        if inp.action == 'sleep':
            r = await self._adb(inp, 'shell', 'input', 'keyevent', '223')
            st = await self._state(inp)
            out.update(st)
            out['adb_output'] = r.strip()[:200]
            blocked = 'INJECT_EVENTS' in r or 'SecurityException' in r
            out['ok'] = not st['screen_on']
            if blocked and st['screen_on']:
                out['note'] = '熄屏被 MIUI 挡住(input 注入无权限): 打开设置-开发者选项-USB调试(安全设置) 后可解, 或直接按电源键'
            return out

        # hold: 周期性重申点亮
        t0 = asyncio.get_event_loop().time()
        samples = 0
        while asyncio.get_event_loop().time() - t0 < inp.seconds:
            await self._adb(inp, 'shell', 'svc', 'power', 'stayon', 'true')
            await asyncio.sleep(2.5)
            samples += 1
        st = await self._state(inp)
        out.update(st)
        out['samples'] = samples
        out['seconds'] = inp.seconds
        return out
