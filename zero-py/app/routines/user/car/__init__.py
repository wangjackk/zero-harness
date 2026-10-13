"""car ---- 小车车身控制 routines(手册 §1 主链路 cmd_vel).

共享骨架 motion.py + 车控业务 ctrl.py(Twist 编码/代际互斥/零速
保证, 经 ros_client 单例直发). 底盘与 rosbridge 由车端 systemd 自启
(zero-chassis / zero-rosbridge, 上电即控制面就绪), 无需 SSH 引导面.
zones.py 是区域数据工具(区域→坐标 / 坐标→区域, 车上 :8001 区域表),
car/navigate/car/where 基于它做命名区域导航与区域反查.
感知在上级 ros_bridge 桥, 不在本包.
"""
from app.routines.user.car.gaze import CarGaze
from app.routines.user.car.map import CarMap
from app.routines.user.car.move import CarMove
from app.routines.user.car.navigate import CarNavigate
from app.routines.user.car.navigate_manage import CarNavigateManage
from app.routines.user.car.shake import CarShake
from app.routines.user.car.status import CarStatus
from app.routines.user.car.steer import CarSteer
from app.routines.user.car.stop import CarStop
from app.routines.user.car.track import CarTrack
from app.routines.user.car.turn import CarTurn
from app.routines.user.car.wake_track import CarWakeTrack
from app.routines.user.car.where import CarWhere

__all__ = ['CarGaze', 'CarMap', 'CarMove', 'CarNavigate', 'CarNavigateManage', 'CarShake', 'CarStatus',
           'CarSteer', 'CarStop', 'CarTrack', 'CarTurn', 'CarWakeTrack', 'CarWhere']
