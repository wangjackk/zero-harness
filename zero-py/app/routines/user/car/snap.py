"""CarSnap ---- 车头相机拍照(薄壳): 车载相机, bus_listener req snap 的 routine 面.

拍照逻辑内聚在 bus_listener(rgb 流临时订阅 + 排干留最新 + JPG 落盘);
本 routine 只做 req 寻址转发, 让拍照进 create/工具面 ---- 与 screenshot
对称: snap=车头相机看世界, screenshot=本机屏幕. bus_listener 不在跑 =
车端总线不可用, 直接报错.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

_log = setup_logger('car.snap')


class CarSnapInput(BaseModel):
    """car/snap input."""

    out: Optional[str] = Field(
        default=None,
        description='输出 JPG 路径; 可不填')


class CarSnap(Routine):
    """车头相机拍照: 转发 bus_listener req snap, 透传结果."""

    name = 'car/snap'
    meta: ClassVar[Dict[str, Any]] = {
        # 查询类: 拍就是为了这一张。不声明 = 结果只记账不点灯, 要等下一次
        # 输入才进 <observe>, 而清单已不印 ⟨observe⟩ 标记 —— 模型会以为拍到了。
        'observe': True,
        'description': (
            '车头相机拍照: 拍一张现在的照片(拍到的图随结果附上, 不用再 read_image). '
            '车端 bus_listener 不在跑时直接报错'),
        'input_schema': CarSnapInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CarSnapInput.model_validate(kwargs or {})
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
        details = rep.get('details') or {}
        out: Dict[str, Any] = {'content': rep.get('content'), 'details': details}
        # 图随回执载荷走(brain._with_images 组多模态 block 附在 <observe> 那条
        # 消息上): 拍一下就看得到, 不用再补一次 read_image. 模型不吃图时
        # brain 不给块, content 里的路径还在.
        path = str(details.get('path') or '').strip()
        if path:
            out['images'] = [path]
        return out


__all__ = ['CarSnap', 'CarSnapInput']
