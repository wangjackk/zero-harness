"""Notify —— 拍外通知: 把"外面发生了事"送进下一拍, 不打断正在说的这一拍.

它是 ``declare`` 的**门面**, 不是第二条驱动线 —— 送进去的还是一条普通冲动,
照常过检录、仲裁、记账. 存在的理由只有一个: 外界的响应(HTTP 回调 / 构建
结束 / 文件变化 / 定时器)不该由"正在说话的那一拍"顺手处理, 也不该被它挡掉.

    <notify text="构建完成: 12 个错误"/>      # 缺省: 不打断, 下一拍处理
    <notify text="…" mode="next"/>            # 一定在下一拍被看见(插队)
    <notify text="…" mode="interrupt"/>       # 真的要打断现在(插话语义)
    <notify text="…" source="build"/>         # 同名来源的挂账续接成一条

**缺省为什么不抢**: 抢占的对象是"它正在说的话". "外面有个结果回来了"不
构成打断它的理由 —— 除非 caller 显式要(``mode="interrupt"``). 落法是
``Priority.BACKGROUND``: 有现任时一定输(``_challenge_incumbent`` 的第三条),
输了按 ``mode='notify'`` 挂账(``_dispatch``: 送达下一拍, 永不丢), 链终由
``_safe_chain`` 的 finally 造一条空冲动开下一届 —— 所以"空闲时立刻开一拍,
说话时说完再答"是同一个参数自然长出来的, 不用判断链在不在场.

source 缺省独立成 ``notify`` 源: **不能借 'user'** —— 同源挑战有 ×1.1 提权,
那等于自己给自己抢自己(一拍里发通知 → 抢断自己正在说的话).
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field
from routine.logger import setup_logger
from routine.routine import Routine

__all__ = ['Notify', 'NotifyInput']

_log = setup_logger('moss.notify')


class NotifyInput(BaseModel):
    """notify 入参: 一条事实 + 怎么处置它."""

    text: str = Field(
        min_length=1,
        description='要带进下一拍的事实(外界来的内容本身, 不要写成"请看上文")')

    source: str = Field(
        default='notify',
        description='来源名. 不同来源各成一条; 同名来源(如同一任务的多次'
                    '进度)在挂账时续接成一段')

    mode: str = Field(
        default='notify',
        description='notify(缺省: 不打断, 必送达下一拍) | next(一定在下一拍'
                    '被看见, 强制插队) | interrupt(立刻停下现在这一拍, 答这条)')


class Notify(Routine):
    """把外界事件申报进拍(不打断现在的这一拍)."""

    name = 'notify'

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '把外界发生的事送进下一拍: 默认不打断现在正在说的话, 说完就答它'
            '(空闲时立刻开一拍). mode="next" 强制排进下一拍, '
            'mode="interrupt" 才停下现在这一拍. 用于 code/外部回调收到结果'
            '后唤醒自己'),
        'input_schema': NotifyInput.model_json_schema(),
        'example': (
            '外部任务回来了, 让它接着想:\n'
            '<notify text="构建完成: 3 个错误, 都在 auth.py"/>\n'
            '一定要下一拍看见(不排队):\n'
            '<notify text="用户撤回了上一条" mode="next"/>\n'
            '真要打断现在:\n'
            '<notify text="紧急: 磁盘满了" mode="interrupt"/>'),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        from ..mindflow import Priority   # 运行期 import, 避让循环依赖

        inp = NotifyInput.model_validate(kwargs or {})
        rid = await self._mindflow_rid()
        if rid is None:
            # 门做不到它声称的事(申报) → 抛. 回执里说"没人收"是把故障
            # 伪装成事实, 模型会以为通知已经送达
            raise RuntimeError('mindflow not running —— 通知没处申报')

        body: Dict[str, Any] = {'source': inp.source, 'text': inp.text,
                                'complete': True}
        if inp.mode == 'interrupt':
            # 插话语义: 停身体(interrupt) + 照样回答(effort='normal').
            # 裸写 interrupt=True 会被 declare 补成 effort='none'(原版
            # interrupt primitive 的"停而不答"), 那样这条通知就白送了.
            body.update({'mode': 'default', 'interrupt': True,
                         'effort': 'normal',
                         'priority': int(Priority.FATAL)})
            content = '已打断现在, 正在处理这条'
        elif inp.mode == 'next':
            body.update({'mode': 'next',
                         'priority': int(Priority.BACKGROUND)})
            content = '已排进下一拍(不打断现在)'
        elif inp.mode == 'notify':
            body.update({'mode': 'notify',
                         'priority': int(Priority.BACKGROUND)})
            content = '已送到下一拍(不打断现在)'
        else:
            raise RuntimeError(
                f'mode takes notify|next|interrupt: got {inp.mode!r}')

        try:
            r = await self.req(rid, 'declare', body, timeout=10) or {}
        except Exception as exc:
            raise RuntimeError(f'declare failed: {exc}') from None
        verdict = str(r.get('verdict') or '')
        if verdict != 'accepted':
            # dropped/noop/ignored 都是"没送出去"(核丢弃 / 冷却 / 过期):
            # 申报是门的唯一职责, 没送成就是没做到
            raise RuntimeError(f'通知没送出去: {verdict or "空回执"}')
        return {'content': content, 'details': {'verdict': verdict,
                                                'source': inp.source,
                                                'mode': inp.mode}}

    async def _mindflow_rid(self) -> Optional[str]:
        """按名找裁判(常驻唯一). 门是短命实例, 不缓存."""
        try:
            for r in await self.get_running_routines():
                if r.get('name') == 'mindflow':
                    return str(r['id'])
        except Exception:
            _log.exception('mindflow lookup failed')
        return None
