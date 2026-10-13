"""CarMap ---- 拉取小车地图, 按前端导航页同款渲染 PNG(返回本地路径).

配色与前端 prerender 逐格一致: 障碍橙 / 空闲亮白 / 空闲但处于 0.2m
膨胀禁行带暗黄 / 未知深灰; 叠加区域矩形+名称、停泊点(青)与当前位置
箭头(绿, AMCL 位姿) ---- AI 拿到的是与人看导航页相同信息密度的图.
4x 放大保栅格锐利, 文字清晰.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
import re
import tempfile
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import numpy as np
from httpx import AsyncClient
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from routine import Routine

from ..hiwonder.bridge_req import bridge_req
from . import zones

_SCALE = 4  # 放大倍数: 5cm/格 → 栅格锐利 & 文字可读

# 前端 prerender/buildBlocked 同款常量
_C_UNKNOWN = (62, 68, 74)      # 未知 深灰
_C_FREE = (235, 238, 240)      # 空闲可通行 亮
_C_INFLATED = (186, 162, 96)   # 空闲但处于障碍膨胀禁行带 暗黄
_C_OCC = (255, 120, 50)        # 障碍 橙
_C_ZONE_FILL = (99, 102, 241, 31)     # 区域填充(前端 rgba 0.12)
_C_ZONE_LINE = (129, 140, 248, 216)   # 区域边框(前端 rgba 0.85)
_C_ZONE_NAME = (165, 180, 252, 255)   # 区域名
_C_POI = (34, 211, 238, 255)          # 停泊点 青
_C_ROBOT = (34, 197, 94, 255)         # 当前位置箭头 绿

_POSE = '/amcl_pose'
_POSE_TYPE = 'geometry_msgs/PoseWithCovarianceStamped'
_NS = 'ros'      # 感知桥转发 namespace(与 ros_bridge._NS 一致)
_WAIT = 3.0      # AMCL 发布频率不高: 等一拍 + 往返余量


class CarMapInput(BaseModel):
    """car/map input(无参数)."""


class CarMapOutput(BaseModel):
    """car/map output."""

    path: str = Field(description='本地 PNG 路径(每次覆盖同一文件, 拿去直接看)')
    width: int = Field(description='宽 (格)')
    height: int = Field(description='高 (格)')
    pose: Optional[List[float]] = Field(
        None, description='当前位置 [x, y, yaw弧度](地图坐标); null=定位不可用')


def _parse_meta(ytext: str) -> Tuple[float, float, float]:
    """地图 yaml → (resolution, origin_x, origin_y)."""
    res = float(re.search(r'resolution:\s*([\d.]+)', ytext).group(1))
    mo = re.search(r'origin:\s*\[\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)', ytext)
    return res, float(mo.group(1)), float(mo.group(2))


def _font(size: int) -> ImageFont.ImageFont:
    """中文字体尽力而为: 系统雅黑缺失时回退 PIL 默认."""
    for name in ('msyh.ttc', 'msyh.ttf', 'simhei.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class CarMap(Routine):
    """获取小车地图(前端同款渲染): 四色栅格 + 区域标注 + 当前位置, 存 PNG 返回路径."""

    name = 'car/map'
    meta: ClassVar[Dict[str, Any]] = {
        'readonly': True,
        'description': (
            '获取小车地图(前端导航页同款渲染): 障碍橙 / 空闲亮白 / '
            '膨胀禁行带暗黄 / 未知深灰, 叠加区域矩形+名称+停泊点(青)与 '
            '当前位置箭头(绿, 需导航栈在跑, 定位不可用则不画). '
            '存本地固定 PNG 路径返回, 供 AI 直接看图理解布局 '
            '(栅格 5cm/格)'),
        'input_schema': CarMapInput.model_json_schema(),
        'output_schema': CarMapOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        origin = zones._origin()
        async with AsyncClient(timeout=10.0) as c:
            r_map = await c.get(origin + '/saved/map.pgm')
            r_map.raise_for_status()
            r_yaml = await c.get(origin + '/saved/map.yaml')
            r_yaml.raise_for_status()
        zs = await zones.fetch_zones()
        res, ox, oy = _parse_meta(r_yaml.text)
        pose = await self._pose()

        img = Image.open(io.BytesIO(r_map.content))
        w, h = img.width, img.height
        data = np.asarray(img, dtype=np.uint8)

        # 膨胀禁行带(前端 buildBlocked 同款): 障碍+未知, 0.2m 圆膨胀
        rad = math.ceil(0.20 / res)
        ys, xs = np.nonzero((data == 0) | (data == 205))
        blk = np.zeros((h, w), dtype=bool)
        for dy in range(-rad, rad + 1):
            for dx in range(-rad, rad + 1):
                if dx * dx + dy * dy > rad * rad:
                    continue
                ny, nx = ys + dy, xs + dx
                m = (ny >= 0) & (ny < h) & (nx >= 0) & (nx < w)
                blk[ny[m], nx[m]] = True

        rgb = np.full((h, w, 3), _C_UNKNOWN, dtype=np.uint8)
        free = data >= 254
        rgb[free & ~blk] = _C_FREE
        rgb[free & blk] = _C_INFLATED
        rgb[data == 0] = _C_OCC

        out = Image.fromarray(rgb, 'RGB').resize(
            (w * _SCALE, h * _SCALE), Image.NEAREST)
        self._overlay(out, zs, pose, res, ox, oy, w, h)

        path = os.path.join(tempfile.gettempdir(), 'hiwonder_map.png')
        out.save(path, 'PNG')
        return CarMapOutput(
            path=path, width=w, height=h,
            pose=None if pose is None else [pose[0], pose[1], pose[2]],
        ).model_dump()

    async def _pose(self) -> Optional[Tuple[float, float, float]]:
        """AMCL 最新位姿 (x, y, yaw); 桥不在/导航栈未跑返回 None(地图照常渲染)."""
        got: Optional[Dict[str, Any]] = None
        evt = asyncio.Event()

        async def on_pose(source, data: Dict[str, Any]) -> None:
            nonlocal got
            got = data
            evt.set()

        try:
            await self.subscribe(_POSE, on_pose, namespace=_NS)
        except Exception:
            return None
        try:
            await bridge_req(self, 'subscribe',
                             {'topic': _POSE, 'type': _POSE_TYPE, 'hz': 5.0})
            try:
                await asyncio.wait_for(evt.wait(), _WAIT)
            except asyncio.TimeoutError:
                pass   # 导航栈未跑: 无位姿
        except Exception:
            pass       # 桥不在跑: 无位姿
        finally:
            # 清理路径吞错: 不掩盖 run 主体抛出的原始异常
            try:
                await bridge_req(self, 'unsubscribe', {'topic': _POSE})
            except Exception:
                pass
            try:
                await self.unsubscribe(_POSE, namespace=_NS)
            except Exception:
                pass
        if got is None:
            return None
        p = got.get('pose', {}).get('pose', {})
        pos, ori = p.get('position', {}), p.get('orientation', {})
        x, y = pos.get('x'), pos.get('y')
        if x is None or y is None:
            return None
        qz, qw = float(ori.get('z', 0.0)), float(ori.get('w', 1.0))
        yaw = math.atan2(2.0 * qw * qz, 1.0 - 2.0 * qz * qz)
        return float(x), float(y), yaw

    def _overlay(self, out: Image.Image, zs: List[Dict[str, Any]],
                 pose: Optional[Tuple[float, float, float]],
                 res: float, ox: float, oy: float, w: int, h: int) -> None:
        """叠加区域矩形+名称+停泊点+当前位置箭头(RGBA 混合, 前端 draw 同款配色)."""

        def px(x: float, y: float) -> Tuple[float, float]:
            # 世界→像素: PGM 第0行在顶部(y 翻转), 再乘放大倍数
            return ((x - ox) / res * _SCALE,
                    (h - 1 - (y - oy) / res) * _SCALE)

        ov = Image.new('RGBA', out.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        font = _font(16 * _SCALE // 4 + 6)
        for z in zs:
            cx, cy = float(z.get('cx', 0.0)), float(z.get('cy', 0.0))
            zw, zh = float(z.get('w', 0.0)), float(z.get('h', 0.0))
            a = float(z.get('angle', 0.0))
            ca, sa = math.cos(a), math.sin(a)
            corners = []
            for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                lx, ly = dx * zw / 2, dy * zh / 2
                corners.append(px(cx + ca * lx - sa * ly,
                                  cy + sa * lx + ca * ly))
            d.polygon(corners, fill=_C_ZONE_FILL, outline=_C_ZONE_LINE)
            mx, my = px(cx, cy)
            d.text((mx, my), str(z.get('name', '')), font=font,
                   fill=_C_ZONE_NAME, anchor='mm')
            tx, ty = zones.zone_target(z)
            tx, ty = px(tx, ty)
            r = 5
            d.ellipse((tx - r, ty - r, tx + r, ty + r),
                      fill=_C_POI, outline=(255, 255, 255, 255))
        if pose is not None:
            # 当前位置箭头(绿): 像素 y 向下, 朝向角取反
            x, y, yaw = pose
            cxp, cyp = px(x, y)
            ang = -yaw
            ca, sa = math.cos(ang), math.sin(ang)
            tip, half, tail = 22.0, 9.0, 12.0
            pts = []
            for lx, ly in ((tip, 0.0), (-tail, half), (-tail * 0.4, 0.0),
                           (-tail, -half)):
                pts.append((cxp + lx * ca - ly * sa, cyp + lx * sa + ly * ca))
            d.polygon(pts, fill=_C_ROBOT, outline=(255, 255, 255, 255))
        out.paste(Image.alpha_composite(out.convert('RGBA'), ov).convert('RGB'))
