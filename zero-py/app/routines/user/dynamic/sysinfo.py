"""sysinfo ---- 本机体检: CPU / 内存 / 磁盘 / 进程 TopN (纯 stdlib + PowerShell).

零第三方依赖: 走 PowerShell CIM (Get-CimInstance) 采数, json 回传后在 Python 侧
归一化. 给 agent 一个"看当前机器状态"的原语, 用于诊断慢/卡/爆盘.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os  = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    ForEach-Object { [pscustomobject]@{
        drive = $_.DeviceID
        total_gb = [math]::Round($_.Size/1GB,1)
        free_gb  = [math]::Round($_.FreeSpace/1GB,1)
    } }
$procs = Get-Process | Sort-Object WS -Descending | Select-Object -First $env:SYSTOP |
    ForEach-Object { [pscustomobject]@{
        name = $_.ProcessName
        mem_mb = [math]::Round($_.WS/1MB,1)
        cpu_s  = [math]::Round($_.CPU,1)
    } }
[pscustomobject]@{
    host = $env:COMPUTERNAME
    os   = $os.Caption
    cpu_name = $cpu.Name
    cpu_load_pct = $cpu.LoadPercentage
    mem_total_gb = [math]::Round($os.TotalVisibleMemorySize/1MB,1)
    mem_free_gb  = [math]::Round($os.FreePhysicalMemory/1MB,1)
    uptime_h = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalHours,1)
    disks = @($disks)
    top_procs = @($procs)
} | ConvertTo-Json -Depth 4 -Compress
"""


class SysInfoInput(BaseModel):
    top: int = Field(default=5, ge=1, le=30, description='返回内存占用最高的前 N 个进程')


class SysInfo(Routine):
    name = 'sysinfo'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '本机体检 v2 (加低容量磁盘告警) [evolve-demo-B]',
        'input_schema': SysInfoInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = SysInfoInput.model_validate(kwargs)
        proc = await asyncio.create_subprocess_exec(
            'powershell.exe', '-NoProfile', '-NonInteractive', '-Command', _PS,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**__import__('os').environ, 'SYSTOP': str(inp.top)},
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f'powershell failed: {err.decode(errors="replace")[:400]}')
        raw = json.loads(out.decode('utf-8-sig', errors='replace'))
        return {
            'host': raw.get('host'),
            'os': raw.get('os'),
            'cpu': raw.get('cpu_name'),
            'cpu_load_pct': raw.get('cpu_load_pct'),
            'mem_total_gb': raw.get('mem_total_gb'),
            'mem_free_gb': raw.get('mem_free_gb'),
            'mem_used_pct': round(
                100 * (1 - raw['mem_free_gb'] / max(raw['mem_total_gb'], 0.01)), 1),
            'uptime_h': raw.get('uptime_h'),
            'disks': raw.get('disks'),
            'top_procs': raw.get('top_procs'),
            'low_disks': [d['drive'] for d in (raw.get('disks') or []) if d['free_gb'] < 60],
        }
