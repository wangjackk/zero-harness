"""CamSnap ---- 车头相机拍照(薄壳): bus_listener req snap 的 routine 面.

拍照逻辑内聚在 bus_listener(rgb 流临时订阅 + 排干留最新 + JPG 落盘);
本 routine 只做 req 寻址转发, 让拍照进 submit/工具面 ---- 与 screenshot
对称: snap=车头相机看世界, screenshot=本机屏幕. bus_listener 不在跑 =
车端总线不可用, 直接报错.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

_log = setup_logger('hiwonder.snap')


class SnapInput(BaseModel):
    """snap input."""

    out: Optional[str] = Field(
        default=None,
        description='输出 JPG 路径; 可不填')


class CamSnap(Routine):
    """车头相机拍照: 转发 bus_listener req snap, 透传结果."""

    name = 'snap'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '车头相机拍照',
        'input_schema': SnapInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = SnapInput.model_validate(kwargs or {})
        rid = None
        for r in await self.get_running_routines():
            if str((r or {}).get('name') or '') == 'bus_listener':
                rid = str(r.get('id') or '').strip() or None
                break
        if rid is None:
            raise RuntimeError('bus_listener not running')
        # req 超时盖过 snap 内部两轮收帧(补救重试)+ 落盘(内部等帧 3s 固定)
        rep = await self.req(rid, 'snap', inp.model_dump(), timeout=16.0)
        rep = rep or {}
        if not rep.get('ok'):
            raise RuntimeError(rep.get('error') or 'snap failed')
        return {'content': rep.get('content'),
                'details': rep.get('details')}


__all__ = ['CamSnap', 'SnapInput']
