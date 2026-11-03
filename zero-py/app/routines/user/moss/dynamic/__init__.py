"""Moss dynamic 实验场: creator 与人工实验 routine 的落点.

每个实验 routine 一个 ``.py`` 文件, 并在这里 re-export + 加入 ``__all__``.
目录已列入 ``app/moss.yaml``, watcher 会在文件或 manifest 保存后自动
注册/重载; 撤销导出或删除文件即注销.

注册只让 kernel 认识名字. 要让 Moss 的 CTML 身体也能调用, 还需经
``shell/registry`` 把名字 reg 进当前词表; ``prompt/creator`` 教模型完成
这一步. 本包自带的 probe 只是空实验场的加载锚点与自验对象, 不进词表.
"""
from .probe import DynamicProbe
from .text_stats import TextStats
from .word_freq import WordFreq
from .car_square import CarSquare
from .car_dance import CarDance
from .car_zigzag import CarZigzag

__all__ = ['DynamicProbe', 'TextStats', 'WordFreq', 'CarSquare', 'CarDance', 'CarZigzag']
