"""dynamic ---- 实验场包 (creator agent / 人工实验性 routine 的家).

玩法 (manifest 语义, 与其他目录条目一致):
  1. 新建 ``<name>.py`` 写 Routine 子类 (照 dynamic_demo.py 的形状)
  2. 在本 ``__all__`` re-export 该类 ---- 文件/manifest 编辑顺序随意
  3. 保存后 watcher 自动检测 (1s 轮询 + 0.5s 防抖) -> catalog.reload
     upsert 注册 (新名字无需先 register), kernel 路由立即可用

注意:
  - 文件名不要 ``_`` 前缀 / ``test_`` 前缀 (watcher 跳过不监控)
  - 顺序无关: import 失败 (manifest 先于文件/写到一半/语法错) 时 watcher
    每秒自动重试, 两边写齐后 ~1.5s 内自动注册, 无需任何手动触发
  - 卸载 = 从 ``__all__`` 撤下 (自动 deregister), 文件随后删不删都行
"""
from zero.routines.user.dynamic.dynamic_demo import DynamicDemo
from zero.routines.user.dynamic.text_reverse import TextReverse
from zero.routines.user.dynamic.pig_latin import PigLatin
from zero.routines.user.dynamic.word_stats import WordStats

__all__ = ['DynamicDemo', 'PigLatin', 'TextReverse', 'WordStats']
