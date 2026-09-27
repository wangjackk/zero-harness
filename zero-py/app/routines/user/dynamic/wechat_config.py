# -*- coding: utf-8 -*-
"""wechat_config ---- 按名字配置常驻的 wechat_watch(不用知道它的 rid).

用法::

    await run_routine('wechat_config', kwargs={'agent': 'pi_1'})   # 绑定推送目标
    await run_routine('wechat_config', kwargs={'agent': ''})       # 解绑(=静默, 只落 inbox)
    await run_routine('wechat_config', kwargs={})                  # 只查状态

内部: get_running_routines() 找到 wechat_watch 的 rid, 再 p2p req 它的
'set_agent' / 'config' / 'status' handler ---- 即"另一个 routine req 进来设置
要发的 agent id" 的落地点。
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine


class WeChatConfigInput(BaseModel):
    agent: Optional[str] = Field(
        default=None,
        description="设 wechat_watch 推送到哪个 agent_id; 传空串 '' 解绑(静默); 不传=不改")
    target: Optional[str] = Field(default=None, description='改监视的微信联系人显示名')
    interval: Optional[float] = Field(default=None, gt=0.2, description='改轮询间隔秒')


class WeChatConfig(Routine):
    """配置常驻的 wechat_watch(按名字寻址, 内部 req 进去)."""

    name = 'wechat_config'
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '配置 wechat_watch: 设置/解绑"收到微信后推送到哪个 agent_id", 或改监视对象/间隔；'
            '不传参则只查状态。内部按名字找到 wechat_watch 的 rid 后 p2p req 进去。'),
        'input_schema': WeChatConfigInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = WeChatConfigInput.model_validate(kwargs)
        running = await self.get_running_routines()
        rid = next((str(r.get('id')) for r in running
                    if r.get('name') == 'wechat_watch'), None)
        if not rid:
            return {'ok': False, 'error': 'wechat_watch not running (没在跑?)'}

        data: Dict[str, Any] = {}
        if inp.agent is not None:
            data['agent'] = inp.agent
        if inp.target:
            data['target'] = inp.target
        if inp.interval:
            data['interval'] = inp.interval

        if data:
            status = await self.req(rid, 'config', data)
        else:
            status = await self.req(rid, 'status', {})
        return {'ok': True, 'wechat_watch_rid': rid, 'status': status}


__all__ = ['WeChatConfig', 'WeChatConfigInput']
