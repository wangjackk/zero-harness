"""hiwonder ---- 幻尔 ROSOrin 智能视觉小车包(挂本地 RoutineHub).

ROS 通信分两层:
- 传输: ros_client 单例(非 routine, 跨热重载存活) ---- 常驻 rosbridge WS
  连接 + 断线自愈 + 订阅表; 地址取 config.yaml network.ros_bridge.
- 业务: ros_bridge 感知桥(动态订阅: routine req 桥挂 ROS 订阅, 消息经
  pubsub namespace 'ros' 按 ROS 原名转发(命名隔离), req 返回
  {topic, namespace}; req publish 反向发帧. 零业务纯中转) + car/ 车身
  控制子包(Twist 编码/代际互斥/零速保证, cmd_vel 直发不经 pubsub;
  zones 区域工具 + car/navigate/car/where 命名区域导航/反查).
  bridge_req 是桥寻址 req 小工具(rid 缓存 + 热重载失效重试).
后续新设备域(RPS 游戏等)平级建子包, 共享 ros_client/ros_bridge.
坐标系 REP 103: y 正 = 左移, angular.z 正 = 左转(turn 负值右转).
"""
from app.routines.user.hiwonder.car import (
    CarGaze, CarMap, CarMove, CarNavigate, CarNavigateManage, CarShake, CarStatus,
    CarSteer, CarStop, CarTrack, CarTurn, CarWakeTrack, CarWhere)
from app.routines.user.hiwonder.ros_bridge import RosBridge
from app.routines.user.hiwonder.snap import CamSnap

__all__ = ['CarGaze', 'CarMap', 'CarMove', 'CarNavigate', 'CarNavigateManage', 'CarShake',
           'CarStatus', 'CarSteer', 'CarStop', 'CarTrack', 'CarTurn',
           'CarWakeTrack', 'CarWhere', 'CamSnap', 'RosBridge']

