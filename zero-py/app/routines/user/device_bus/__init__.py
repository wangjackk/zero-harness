"""device_bus ---- 设备事件总线统一收发包 (车端 zmqproxy).

- bus_listener: 被动常驻, 低频 evt 订阅 → pubsub 'voice'/'vision' +
  语义化 req 发设备 cmd(pose_enable/disable/status 推理即开即关门控,
  snap 车头相机拍照: rgb 流临时订阅最新帧存 JPG; 常驻 PUB + 回执确认
  + 丢包重发).
"""
from .listener import BusListener, BusListenInput

__all__ = ['BusListener', 'BusListenInput']
