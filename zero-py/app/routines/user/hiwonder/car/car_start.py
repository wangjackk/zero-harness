"""CarStart ---- 一键拉起小车服务(SSH → /tmp/start_*.sh, 手册 §2).

唯一 SSH 链路 routine(引导): rosbridge 没起时 WS 不可达, 只能 SSH 拉起.
- start_chassis.sh: 底盘 ROS 节点(controller + odom), 控制链路必需
- start_rosbridge.sh: rosbridge 网关, WS 控制(本包其余 routine)必需
均 setsid 常驻, 断开 SSH 不受影响. 底板断电重启清空 /tmp 脚本(手册 §7)
---- 本 routine 自动检测缺失并从本地 scripts/ 上传, 无需手动处理.

一键闭环: 缺脚本 → 上传; 没起 → 拉起; 起完 → 回连 WS 9090 验证控制面
真正可用(脚本 exit 0 ≠ rosbridge 已 bind), 验证不过即报错.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine import Routine

from .. import ros_client

DEFAULT_TARGET = 'hiwonder108'
_SCRIPTS_DIR = Path(__file__).parent / 'scripts'
_WS_VERIFY_ATTEMPTS = 4
_WS_VERIFY_INTERVAL = 2.0


class CarStartInput(BaseModel):
    """car/start input."""

    rosbridge: bool = Field(
        True, description='是否启动 rosbridge(WS 控制必需, 已在跑则跳过); 默认是')
    target: str = Field(
        DEFAULT_TARGET, description='SSH 目标(别名或 user@host); 默认 hiwonder108')


class CarStartOutput(BaseModel):
    """car/start output."""

    commands: List[str] = Field(description='远端执行的命令列表(含上传)')
    output: str = Field(description='远端输出(CHASSIS_READY/ROSBRIDGE_READY 即成功)')
    ws_ok: bool = Field(description='rosbridge WS 回连验证结果(rosbridge=False 时恒 True)')


async def _ssh(target: str, cmd: str, timeout: float = 90) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        'ssh', target, cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, out_b.decode('utf-8', errors='replace').strip()


async def _ensure_script(target: str, name: str) -> str | None:
    """远端 /tmp 缺脚本则从本地 scripts/ 上传. 返回上传命令(无需上传为 None)."""
    rc, _ = await _ssh(target, f'test -f /tmp/{name}', timeout=15)
    if rc == 0:
        return None
    local = _SCRIPTS_DIR / name
    if not local.is_file():
        raise FileNotFoundError(f'/tmp/{name} 缺失且本地无副本: {local}')
    proc = await asyncio.create_subprocess_exec(
        'scp', str(local), f'{target}:/tmp/{name}',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(
            f'上传 {name} 失败 (exit {proc.returncode}): '
            f'{out_b.decode("utf-8", errors="replace").strip()}')
    return f'scp {name} → /tmp/{name}'


async def _verify_ws() -> bool:
    """回连 rosbridge WS 验证控制面可用(拉起后 bind 需几秒, 有限重试)."""
    import websockets
    for _ in range(_WS_VERIFY_ATTEMPTS):
        try:
            ws = await asyncio.wait_for(
                websockets.connect(ros_client.url()), timeout=5.0)
            await ws.close()
            return True
        except Exception:
            await asyncio.sleep(_WS_VERIFY_INTERVAL)
    return False


class CarStart(Routine):
    """一键启动小车: 缺脚本自动上传 + 拉起底盘/rosbridge + WS 回连验证."""

    name = 'car/start'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '一键启动小车: 自动补传缺失的 /tmp 脚本 + 拉起底盘节点与 rosbridge'
            '(SSH 常驻, 断连不退) + 回连 WS 验证控制面可用'),
        'input_schema': CarStartInput.model_json_schema(),
        'output_schema': CarStartOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarStartInput.model_validate(kwargs)
        names = ['start_chassis.sh']
        if inp.rosbridge:
            names.append('start_rosbridge.sh')

        commands: List[str] = []
        outputs: List[str] = []
        for name in names:
            uploaded = await _ensure_script(inp.target, name)
            if uploaded is not None:
                commands.append(uploaded)
                outputs.append(f'[uploaded] {name}')
            cmd = f'zsh /tmp/{name}'
            commands.append(cmd)
            rc, output = await _ssh(inp.target, cmd)
            if rc != 0:
                raise RuntimeError(f'小车命令失败 (exit {rc}): {cmd}\n{output}')
            outputs.append(output)

        ws_ok = True
        if inp.rosbridge:
            ws_ok = await _verify_ws()
            if not ws_ok:
                raise RuntimeError(
                    f'rosbridge 脚本已执行但 WS {ros_client.url()} '
                    f'{_WS_VERIFY_ATTEMPTS} 次回连均失败 ---- 检查小车网络/IP '
                    f'是否仍与 config.yaml network.ros_bridge 一致')

        return CarStartOutput(
            commands=commands, output='\n'.join(outputs).strip(), ws_ok=ws_ok,
        ).model_dump()
