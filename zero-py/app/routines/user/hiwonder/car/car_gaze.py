"""CarGaze ---- 拟人注视会话(agent react 期持续视觉跟踪用户).

事件自治形态: 订阅 pubsub topic 'agent_status'(xml_agent 每轮 react
广播 reacting/idle), reacting 起注视会话, idle 收目光. agent/act 完全
不知道本 routine 的存在 ---- 具身伴随能力自我管理生命周期, 不侵入
通用对话管线.

会话内循环 call car/track(后台注视档 5, 垫底一切默认档用户), track 因
无人/超时自然结束就立即重开. idle 事件同步 bump 动作代际打断在跑的
track(下一拍自退, finally 零速), 不等本轮 max_run_s 自然耗完.
显式动作占 wheels 时 track start 撞 StartError(平级/低档不抢占) ----
让位短歇重试, 动作释放后无缝恢复注视, 会话不因让位而断.

超时兜底(事件面丢失自愈): reacting 后 MAX_SESSION_S 内无 idle/新
reacting 刷新, 强制收会话 ---- idle 广播在 agent 被硬杀等极端路径下
可能丢失, 伴随方靠自己的兜底语义保证不悬挂.

car/track 不可用(车离线/未注册, SubmitError)时退回待机等下一个
reacting, 绝不退出(passive 常驻); 档位不足(StartError, 罕见)短歇重试.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict

from routine import PassiveConfig, Routine, StartError, subscribe
from routine.logger import setup_logger

from . import car_ctrl

_log = setup_logger('car.gaze')

# 单轮 track 时长(car/track 无人/超时自然结束立即重开)
TRACK_S = 30
# 会话兜底上限(reacting 起, 无刷新强制收)
MAX_SESSION_S = 180
# StartError 重试间隔
RETRY_S = 2.0


class CarGaze(Routine):
    """react 期注视: 订阅 agent_status 自治起停, 循环维持 car/track."""

    name = 'car/gaze'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'agent react 期持续注视用户(订阅 agent_status 自治起停)',
        'hidden': True,
    }
    is_passive = PassiveConfig(kwargs={})

    def __init__(self):
        super().__init__()   # 基类构造器建 @subscribe 分发表(_subscribe_handlers)
        self._evt: asyncio.Event | None = None   # run 循环建立(loop-bound)
        self._active = False
        self._deadline = 0.0
        self._main_id: str | None = None   # 启动拉齐, agent_focus 事件维护

    async def _fetch_main(self) -> str | None:
        """启动拉齐: 从 xml_agent manager req 当前 main(focus).

        之后靠 agent_focus 事件维护缓存, 不再逐次 req. 冷启竞态(manager
        未起/无 main)返回 None ---- 丢弃直到首次 set_main 广播.
        """
        for r in await self.get_running_routines():
            if str(r.get('name') or '') != 'xml/agent/manager':
                continue
            mrid = str(r.get('id') or '').strip()
            if not mrid:
                return None
            try:
                rep = await self.req(mrid, 'get_main', {}, 3)
            except Exception as exc:
                _log.warning('get_main from manager failed: %r', exc)
                return None
            return str((rep or {}).get('agent_id') or '') or None
        return None

    @subscribe('agent_focus', namespace='xml_agent')
    async def on_agent_focus(self, source, data: dict) -> None:
        """manager 指派/切换 main 时刷新本地缓存(免逐次 req)."""
        self._main_id = str((data or {}).get('agent_id') or '') or None
        _log.info('gaze main agent: %s', self._main_id)

    @subscribe('agent_status', namespace='xml_agent')
    async def on_agent_status(self, source, data: dict) -> None:
        status = data.get('status')
        if status not in ('reacting', 'idle'):
            return
        # main 过滤: reacting/idle 对称判定, 防 sub-agent 的 idle 误收
        # main 的会话(bump 掉在跑注视). 无 main 时丢弃(同 voice 死信语义).
        if self._main_id != str(data.get('agent_id') or ''):
            _log.info('gaze skip %s: non-main agent %s',
                      status, data.get('agent_id'))
            return
        if status == 'reacting':
            # 新会话/连续对话刷新: 幂等续命
            self._deadline = asyncio.get_running_loop().time() + MAX_SESSION_S
            self._active = True
            self._evt.set()
        elif self._active:
            # 同步 bump 代际: 打断在跑的 track(下一拍自退, finally 零速),
            # run 循环经 _evt 亦即时感知, 不等本轮 max_run_s 耗完.
            car_ctrl.bump_generation()
            self._active = False
            self._evt.set()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        self._evt = asyncio.Event()
        self._main_id = await self._fetch_main()
        loop = asyncio.get_running_loop()
        while True:
            await self._evt.wait()
            self._evt.clear()
            if not self._active:
                continue
            _log.info('gaze session open')
            while self._active:
                if loop.time() >= self._deadline:
                    # 兜底: idle 丢失(广播方硬杀等), 强制收会话
                    _log.warning('gaze session deadline hit, idle lost?')
                    car_ctrl.bump_generation()
                    break
                try:
                    # 后台注视档位 5 垫底: 显式动作(默认 10)占 wheels 时
                    # StartError 让位短歇重试(动作释放后无缝恢复注视);
                    # 唤醒粗调 track(默认 10)可 FORCE 驱逐本 track.
                    await self.call('car/track', {'max_run_s': TRACK_S},
                                    priority=5)
                except StartError:
                    # 档位不足(显式动作在占 wheels): 让位, 短歇后重试,
                    # 会话不因让位而断 ---- 仍由 idle/deadline 收口.
                    await asyncio.sleep(RETRY_S)
                except Exception as exc:
                    # SubmitError(车离线): 无可跟踪对象, 退回待机等下个
                    # reacting(passive 常驻不退出); 其余异常同路, 避免空转
                    # 轰炸 ---- 会话由下一个 reacting 重新拉起.
                    _log.info('gaze session closed by: %s', exc)
                    break
            self._active = False
            _log.info('gaze session closed')
