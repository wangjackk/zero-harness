"""一笔画多久 —— 时长常数抄自黑板侧, 只为一件事: 等这一笔画完再回执.

来源（``blackboard-app/web/src/board/``）::

    board.ts    STEP = 0.155                 一个字起笔的间隔
                write 每字 dur = STEP * 1.45
                point  dur = 0.22
                pushPath dur = clamp(点数 * 0.0075, 0.3, 1.6)
                arrow  每笔 clamp(点数 * 0.006, 0.16, 1.2), 三笔同时起
    geometry.ts line        n = clamp(len/12, 12, 90) 点
                underline   34 段 → 35 点(撞到 0.3 下限)
                circlePts   ring 用, 151 点(与大小无关)
                shapeCirclePts  circle / ellipse: n = clamp(round(平均半径px * 1.1), 56, 240)
                polyPts     rect / poly: 每个角 9 段 + 每段按长度/6 采样

两个直接的后果:
- **圆/椭圆/矩形/折线的时长跟大小有关**(点数按半径/周长算), 不再是固定值;
- **ring 是固定的 1.13s**(151 点), 与它圈的对象多大无关。

为什么要把常数抄过来: 黑板只用 ``bb:objects`` 回灌"对象已登记"(动作到达后
约 220ms), 它**没有**"画完了"这个信号, 而入场动画时长又是写死的、不可配。
我们这边要"一个标签 = 一笔, 画完才轮下一笔", 只能按同一套公式估。

抄偏了只是节奏差一点, 不会画错 —— 和"锚点算错"是两回事, 所以这里不值得
为精确度去引入跨进程的共享配置。黑板侧改公式时同步这一处即可。
"""
from __future__ import annotations

from typing import Any, Dict, List

__all__ = ['estimate', 'write_seconds']

#: 一个字起笔的间隔(黑板侧 STEP)
_STEP = 0.155
#: 一个字落笔要多久(黑板侧 dur = STEP * 1.45)
_GLYPH = _STEP * 1.45
#: 画完再歇一下, 两笔不首尾相接
_GAP = 0.05
#: 名义画布(黑板默认 1600×900): 位置按轴折像素, **长度按短边**折像素
_CANVAS = (1600.0, 900.0)
_SHORT = 900.0
_NOMINAL_PX = 640.0
#: 一次采样走多远(黑板侧 polyPts 的直边步长)
_STEP_PX = 6.0


def write_seconds(text: str) -> float:
    """一行字写完要多久(逐字起笔, 末字再落笔)."""
    n = len(text)
    return _STEP * max(0, n - 1) + _GLYPH


def _path_seconds(points: int) -> float:
    """黑板侧 pushPath: 按点数走, 夹在 0.3~1.6."""
    return min(1.6, max(0.3, points * 0.0075))


def _px(a: Any, b: Any) -> float:
    """两个锚点之间的大致像素距离; 任一端是引用(below:a1)就算不出来."""
    pts = []
    for a_ in (a, b):
        if isinstance(a_, (list, tuple)) and len(a_) == 2:
            pts.append((float(a_[0]) * _CANVAS[0], float(a_[1]) * _CANVAS[1]))
        else:
            return _NOMINAL_PX
    (x1, y1), (x2, y2) = pts
    return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5


def _line_points(px: float) -> int:
    return max(12, min(90, round(px / 12)))


def _arc_points(rx_px: float, ry_px: float) -> int:
    """黑板侧 shapeCirclePts: 平均半径越大采样越密(56~240 点)."""
    return max(56, min(240, round((rx_px + ry_px) / 2 * 1.1)))


def _radius(act: Dict[str, Any], key: str, default: float) -> float:
    try:
        return abs(float(act.get(key) or default)) * _SHORT
    except (TypeError, ValueError):
        return default * _SHORT


def _poly_seconds(total_px: float, corners: int) -> float:
    """折线/多边形: 每个角 9 段小弧 + 直边按 6px 一步采样."""
    return _path_seconds(corners * 9 + round(total_px / _STEP_PX))


def _poly_length(points: List[Any]) -> float:
    """折线的总长; 有引用锚点(算不出像素)就退到名义长度."""
    total = 0.0
    for a, b in zip(points, points[1:]):
        d = _px(a, b)
        if d == _NOMINAL_PX:
            return _NOMINAL_PX * 0.6 * max(1, len(points) - 1)
        total += d
    return total


def estimate(act: Dict[str, Any]) -> float:
    """这一笔画完大概多久(秒). 擦除/像素是 0: 到了就生效."""
    do = str(act.get('do') or '')
    if do == 'write':
        dur = write_seconds(str(act.get('text') or ''))
    elif do == 'line':
        n = _line_points(_px(act.get('from'), act.get('to')))
        dur = _path_seconds(n + 1)
    elif do == 'arrow':
        n = _line_points(_px(act.get('from'), act.get('to')))
        dur = min(1.2, max(0.16, (n + 1) * 0.006))     # 杆最长, 三笔同时起
    elif do in ('circle', 'ellipse'):
        rx = _radius(act, 'rx' if do == 'ellipse' else 'r', 0.08)
        ry = (_radius(act, 'ry', 0.08) if do == 'ellipse' else rx)
        dur = _path_seconds(_arc_points(rx, ry) + 1)
    elif do == 'rect':
        w = _radius(act, 'w', 0.2)
        h = _radius(act, 'h', 0.2)
        dur = _poly_seconds(2 * (w + h), 4)
    elif do == 'poly':
        pts = act.get('points') or []
        if not isinstance(pts, list) or len(pts) < 2:
            return 0.0
        dur = _poly_seconds(_poly_length(pts), len(pts))
    elif do == 'ring':
        dur = _path_seconds(151)                      # circlePts 固定 151 点
    elif do == 'underline':
        dur = _path_seconds(35)
    elif do == 'point':
        dur = 0.22
    else:                                             # erase / pixel
        return 0.0
    return dur + _GAP
