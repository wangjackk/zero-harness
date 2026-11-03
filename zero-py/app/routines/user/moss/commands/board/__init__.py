"""board 族: 黑板上的十二笔 + 一眼.

写 / 线 / 箭 / 矩形 / 折线 / 圆 / 椭圆 / 强调圈 / 下划线 / 点 / 擦 / 像素 / 看

分族判据是**对象**: 族内每一笔都落在**同一块黑板**上 —— 所以没有"哪块板"
的参数(板由装配决定). 这跟 chan 族正好相反: chan 每笔针对不同的轨, 都要
``name``; 黑板只有一块.

为什么一 do 一扇门而不是一个 ``<bb do="..."/>``: 十二个 do 的字段几乎不重叠
(write 要 text/at/size, line 要 from/to/dash, rect 要 at/w/h/round), 名字
写死, 模型不必记"哪个 do 要哪些键"; 更要紧的是**只有 write / pixel 收 body**
—— body 有无是**逐类声明**的(``meta['body']``, 默认不收; code 面
``call(body=…)`` 的闸门读它), 做成一个带 do 的 routine 就只能整族全收 ——
于是 ``do="circle"`` 那段 body 没人读.

两处容易混, 名字上就分开了:
- ``bb_circle``(几何圆: 圆心+半径) vs ``bb_ring``(批注圈: 圈住某个对象);
- ``bb_line`` / ``bb_poly``: 要形状用 poly / rect, **别拿多条 line 拼** ——
  每笔各自收细, 角上一定断.

共性(都在 ``base.py``): 找那块板 → 交一笔 → **等这一笔画完再回执**.
等的是入场动画时长(``board/timing.py``), 所以标签顺序就是画面顺序:

    <main:bb_write id="a1" at="0.12,0.28">斜率就是变化率</main:bb_write>
    <main:bb_ring around="a1"/>
    <main:bb_circle id="c1" at="below:a1" r="0.09"/>

并发不归这里管: 要在另一处同时画, 换一条轨(``chan_create``)就是了.
"""
from __future__ import annotations

from .arrow import BbArrow
from .base import BbCommand
from .circle import BbCircle
from .ellipse import BbEllipse
from .erase import BbErase
from .line import BbLine
from .objects import BbObjects
from .pixel import BbPixel
from .point import BbPoint
from .poly import BbPoly
from .rect import BbRect
from .ring import BbRing
from .underline import BbUnderline
from .write import BbWrite

__all__ = ['BbCommand', 'BbWrite', 'BbLine', 'BbArrow', 'BbRect', 'BbPoly',
           'BbCircle', 'BbEllipse', 'BbRing', 'BbUnderline', 'BbPoint',
           'BbErase', 'BbPixel', 'BbObjects']
