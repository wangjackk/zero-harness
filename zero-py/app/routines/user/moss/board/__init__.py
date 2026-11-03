"""黑板: 一块看得见的板, 一支会等自己画完的笔.

分工和耳朵(asr)同构 —— 器官在根目录, 动词在 ``commands/``:

    board/routine.py   Board —— 被动常驻的连接持有者(**不在词表里**)
    board/client.py    线: EIO=4 / Socket.IO v4, 推 actions / 收 objects
    board/timing.py    一笔画多久(常数抄自黑板侧, 只为"等它画完再回执")
    commands/board/    十三个动词: bb_write / bb_line / bb_rect / ...

为什么连接要单独一个 routine 而不是藏在每个动词里: 它有自己的时间轴
(一条常驻 websocket)、自己的状态(对象表快照)、有两个消费者(每个动词
推动作 + bb_objects 读表) —— 三条都命中"开新 routine"的判据.

惰性连接: 装配只把 Board 拉起来, 第一次真要用(第一个 bb_*)才连黑板.
服务端没起时 moss 照常说话做事, 只是画不了 —— 不会拖垮启动.
"""
from __future__ import annotations

from .client import Client
from .routine import DEFAULT_URL, Board

__all__ = ['Board', 'Client', 'DEFAULT_URL']
