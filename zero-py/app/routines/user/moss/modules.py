"""modules —— 身体的模块树: **独占资源**的拓扑(``kernel.create`` 时建树).

模块 = 同一时刻只容一个持有者的东西. routine 用 ``modules()`` 类方法声明,
kernel 在 **start** 时 ``TryAcquire`` 原子裁决: 撞上就抛 StartError, 由轨道
收成错误帧当场回灌 —— 不是排队等. 差别很实在: 排队是**静默**的, 模型会以为
两件事在同时发生; 冲突是**当场**的, 它这一拍就知道要改写法.

顶层节点 = 能力清单里的分组名(``prompt/capability`` 按它分组), 所以别随手
起名 —— 一个顶层节点就是身体上的一个部位.

现在两处:

- ``code`` —— 一台解释器一份命名空间, 一次一格. 跨轨写两个 code, 第二个在
  start 就被拒; 想并行就在**同一格**里 ``asyncio.gather``.
- ``car`` / ``wheels`` —— 底盘(``app.modules.MODULE_WHEELS``): 一套底盘同时
  只听一个动作, 动作族(move/turn/steer/shake/track)在 start 时互斥. ``car``
  是顶层分组名(清单里 car/* 就印在这一组), ``wheels`` 是真正被占的那个节点.
  父节点存在才有"部位"可言 ---- kernel 的冲突锥按祖先算, 挂在 ``car`` 下,
  以后加"手臂"之类不会跟底盘抢锥.

入口(清单 ``app/moss.yaml`` 的 ``modules:`` 段, 与这里的 ``MODULES`` 同形)与
测试都按这一份写, 别各写一套: **树里没有的模块, start 时直接 ``KeyError``**(当场炸, 不是静默) —— 声明与树必须成对, 否则 code 一格
都跑不起来.
"""
from __future__ import annotations

from typing import Any, Dict

__all__ = ['MODULES']

MODULES: Dict[str, Dict[str, Any]] = {
    'root': {'name': '根', 'children': ['code', 'car']},
    'code': {},
    'car': {'name': '车身', 'children': ['wheels']},
    'wheels': {'name': '底盘'},
}
