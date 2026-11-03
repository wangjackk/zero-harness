"""code: 一个 body 是源码的 routine —— 单独成包, 只为把示例文案挪出 py.

为什么是包而不是 ``commands/code.py``: 它的 ``meta['example']`` 是十来行
"这一格里能写什么"的说明, 混在类定义里会把契约淹掉. 现在 py 只剩契约与机制,
文案在 ``example.md`` —— 改话术碰 md, 改行为碰 py, 两边不互相打扰.

判据(不是每个 routine 都要这样): **多行成段**的 example 才进 md. 一两行的
短句留在 meta 里的字符串, 别为一行字开一个文件 —— 文件系统也是一种注释,
用多了读的人要跳来跳去.
"""
from __future__ import annotations

from .code import Code

__all__ = ['Code']
