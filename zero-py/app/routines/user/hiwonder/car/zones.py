"""zones ---- 区域数据(车上 :8001 /api/zones) + 区域判定纯函数.

区域标定与停泊点在前端导航页维护(存车 maps/zones.json, :8001 读写);
本模块只读拉取 + 两类判定: 区域→停泊坐标(导航), 坐标→区域名(定位
反查). 矩形支持任意旋转角(与前端标定数学一致). 主机复用 ros_bridge
配置(同一台车).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from httpx import AsyncClient

from app.config import network


def _origin() -> str:
    """车上服务基址: 复用 ros_bridge 配置的主机(同一台车)."""
    return f'http://{urlparse(network().ros_bridge).hostname}:8001'


def _api_url() -> str:
    """zones API 地址."""
    return _origin() + '/api/zones'


async def fetch_zones() -> List[Dict[str, Any]]:
    """拉取区域列表(空列表 = 未标定; 车不可达抛 httpx 异常, caller 可见)."""
    async with AsyncClient(timeout=5.0) as c:
        r = await c.get(_api_url())
        r.raise_for_status()
        return r.json().get('zones', [])


def zone_target(z: Dict[str, Any]) -> Tuple[float, float]:
    """区域导航目标: 停泊点(前端设点), 未设置回落区域中心."""
    p = z.get('point')
    if isinstance(p, dict) and 'x' in p and 'y' in p:
        return float(p['x']), float(p['y'])
    return float(z.get('cx', 0.0)), float(z.get('cy', 0.0))


def point_in_zone(x: float, y: float, z: Dict[str, Any]) -> bool:
    """点是否在区域内(旋转矩形: 平移到中心, 逆旋转, 半宽长判定)."""
    dx, dy = x - float(z.get('cx', 0.0)), y - float(z.get('cy', 0.0))
    a = float(z.get('angle', 0.0))
    ca, sa = math.cos(-a), math.sin(-a)
    lx, ly = ca * dx - sa * dy, sa * dx + ca * dy
    return abs(lx) <= float(z.get('w', 0.0)) / 2.0 and abs(ly) <= float(z.get('h', 0.0)) / 2.0


def find_zone(name: str, zones: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """按名称找区域: 精确优先, 其次子串(大小写不敏感)."""
    low = name.strip().lower()
    for z in zones:
        if str(z.get('name', '')).lower() == low:
            return z
    for z in zones:
        if low in str(z.get('name', '')).lower():
            return z
    return None


def locate(x: float, y: float, zones: List[Dict[str, Any]]) -> Optional[str]:
    """坐标→区域名: 第一个命中(区域重叠时按列表序)."""
    for z in zones:
        if point_in_zone(x, y, z):
            return str(z.get('name', ''))
    return None
