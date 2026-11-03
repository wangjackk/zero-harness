"""chan 族: 轨道管理四扇门(开轨 / 看轨 / 清轨 / 回收轨).

分族判据是对象: 族内每个动作都针对**一条具体的轨**(都要 ``name``).
``interrupt`` 因此不在族里 —— 它的对象是"所有轨", 没有 name 可说,
留在 ``commands/control.py``.
"""
from __future__ import annotations

from .base import ChanCommand
from .clear import ChanClear
from .create import ChanCreate
from .delete import ChanDelete
from .status import ChanStatus

__all__ = ['ChanCommand', 'ChanCreate', 'ChanStatus', 'ChanClear', 'ChanDelete']
