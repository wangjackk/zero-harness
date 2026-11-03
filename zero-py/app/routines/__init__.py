"""routine 注册表 ---- 引导扇区.

静态只注册 ``RoutinesLoader``(passive, kernel 自动拉起); 其余全部 routine 由
loader 运行时按**启动时指定的那份清单**注册(默认 app/routines.yaml, 见
main.py 位置参数). 改清单/源码后 watcher 热重载, 换清单要重启进程.
"""
from routine import Routines

from .loader import RoutinesLoader


def get_routines() -> Routines:
    rs = Routines()
    rs.register(RoutinesLoader)
    return rs
