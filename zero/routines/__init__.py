"""routine 注册表 ---- 引导扇区.

静态只注册 ``RoutinesLoader``(passive, kernel 自动拉起); 其余全部 routine 由
loader 运行时按 ``zero/routines.yaml`` 注册. 改 yaml 后重启进程生效.
"""
from routine import Routines

from .loader import RoutinesLoader


def get_routines() -> Routines:
    rs = Routines()
    rs.register(RoutinesLoader)
    return rs
