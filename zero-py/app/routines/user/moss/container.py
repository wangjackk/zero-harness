"""Container —— 时序容器指令(`<all>` / `<any>` / `<_>`), **也是普通实例**.

统一形态的试金石: 容器不走任何旁路, 它就是"一个把 body 解释成子投递的
实例"(``docs/orchestration.md``). 所以它跟 ``say`` 一样被 create、一样从
发起者那里边到边收 body、一样在轨道队列里占一个位置.

body 解释: 容器持一个深度 1 的 ``BodyScanner``, 把 body 里的每个子标签
投递给 shell 的 ``route``(词表/lane 闸门只有那一份), 子回执照 ``reply``
打回自己这儿.

等待语义(原版 ``wait_close`` 三量词逐字):

- ``all``: 等全部子
- ``any``: 任一完成即**取消其余**(原版 ``_clear_all_sub_tasks``)
- ``_``  : 只等**本 lane** 的子; 一个都没有则容错等全部(原版 flow 分支)

属性 ``timeout="3"``: 到时不再等, 取消剩余(与 any 的收尾同一套), 回执
带 ``timed_out`` 且**自带注意债**(超时是模型该看见的事实).

``loop`` (原版 ``loop(times, ctml__)``) 是唯一"body 跑多次"的容器:
body 原文留档(基类的 ``body_text``), 每轮用新扫描器重放一次 —— 子标签重新
route, rid 全新, 所以"同一批指令跑 N 遍"不需要任何缓存或克隆机制. 终止:
``times`` 到 / 硬顶 100 / 本轮任一子失败 / 某子声明 ``observe="true"``.
收尾回执自带注意债(原版 ``CommandTaskResult(observe=True)``: "loop done
at N" 是模型必须知道的事实, 否则不知道循环结束).

**占道不需要任何机制**: 容器实例本身排在 lane 队列里, ``run`` 要等子
完成才返回, 于是同 lane 后续动作在 FIFO 上自然等它 —— 原版"scope exit
是 blocking 命令"的等价物, 队列本身就是闸.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field
from routine.logger import setup_logger
from routine.routine import request

from .body import (
    BodyScanner, ChildBody, ChildClose, ChildOpen, Malformed, decode_body,
)
from .ctml import CtmlRoutine

__all__ = ['All', 'AnyOf', 'Flow', 'Loop', 'CONTAINERS']

# 循环硬顶(原版同值: 无限循环也只跑 100 轮)
_LOOP_CAP = 100

# 容器族共享的备注(结构/占道/跨轨栅栏) —— 每个容器都声明同一份, 能力
# 清单渲染时**相同文本只印一次**(见 prompt/capability._item). 于是容器
# 族整体不在词表时, 这段也跟着消失: 提示词跟能力走, 没有"本机没有容器
# 却讲一堆容器语法"的段落.
_CONTAINER_EXAMPLE = (
    '容器也是普通 routine, 只是它的 body 里放的是**子 routine 的标签**(不是\n'
    '文本). 默认挂当前轨: 它之后的同轨 routine 要等它关闭才继续.\n'
    '可加 timeout(秒): 到点就不再等, 取消剩余(结果里带 timed_out);\n'
    'body 里只放 routine 标签, 裸文本会被丢弃.\n'
    '\n'
    '跨轨栅栏 —— 让 b 等 a 做完再开始: 容器投到**要等的那条轨**(b)上,\n'
    'body 里只放 a 轨要等的 routine, b 自己的 routine 写在容器**之后**.\n'
    '别把 b 自己的 routine 放进容器的 body 里 —— body 里的 routine 一发出就投递,\n'
    '放进去只会和 a 并行(写在后面不等于等在后面).'
)

_log = setup_logger('moss.container')


class ContainerInput(BaseModel):
    """容器的编排参数(标签属性).

    声明成 schema 不是为了校验(容器自己 ``float(...)`` 转), 是为了**让名字
    出现在能力清单上**: 投递口的参数白名单与模型看到的签名同源于
    ``input_schema`` —— 不在这里写, ``timeout`` 就成了"用了会被当场拒、
    可你从没被告知有它"的暗参数.
    """

    timeout: Optional[float] = Field(
        default=None, description='最多等多少秒(到点取消剩余, 结果带 timed_out)')


class LoopInput(ContainerInput):
    """loop 多一个轮次参数."""

    times: Optional[int] = Field(
        default=None, description='重复多少轮(省略=1, 负数=最多 100 轮)')


class Container(CtmlRoutine):
    """时序容器: body = 一批子 routine 标签, run = 等它们."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '时序容器: body 里的 routine 全部/任一完成后返回',
        # 收 body: 下一层的标签写在标签中间(见 ctml.CtmlRoutine 的说明).
        # 四个子类用 **Container.meta 展开, 所以这一行是整族共有的声明.
        'body': True,
        'input_schema': ContainerInput.model_json_schema(),
        'example': _CONTAINER_EXAMPLE,
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        await super().on_created(rid, kwargs)
        k = dict(kwargs or {})
        self._shell: str = str(k.pop('_shell', ''))    # 路由服务(唯一词表)
        self._reply: str = str(k.pop('_reply', ''))    # 发起者(向上汇总)
        self._lane: str = str(k.pop('_lane', 'main'))  # 锚定轨(flow 过滤 + 占道)
        self._chan: str = str(k.pop('_chan', ''))      # 自己的轨实例(收尾入队)
        self._timeout: float = max(0.0, float(k.pop('timeout', 0) or 0))
        self._scan = BodyScanner()
        self._kids: Dict[str, Dict[str, Any]] = {}     # rid -> kid(见 _open)
        self._cur: Optional[Dict[str, Any]] = None     # 当前打开的子
        self._arrivals: List[Dict[str, Any]] = []      # 到货未记账的回执
        self._arrived = asyncio.Event()                # 有新到货
        self._queued = asyncio.Event()                 # 已排进 lane(占道生效)
        self._failed = False                           # 有子失败(loop 终止条件)
        self._timed_out = False
        self._cancelled = 0
        self._t0 = 0.0                                 # 闭标签时刻(timeout 起点)

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await self._body_ready.wait()
        await self._settle()
        details: Dict[str, Any] = {'children': len(self._kids),
                                   'cancelled': self._cancelled}
        if not self._timed_out:
            # 子回执各有各的帧, 容器这一条只交代"都收齐了" —— 说多了是
            # 复读. 超时才值得单独一句话(模型据此决定等不等/还投不投).
            return {'details': details}
        # 超时是模型该看见的事实: 自带注意债(见 shell._record_trace)
        details['timed_out'] = True
        return {'content': '等超时了: 子没全回来', 'details': details,
                'observe': True}

    async def stop(self) -> None:
        """被打断(或被父容器取消): 子一并收掉, run 立刻返回."""
        self.abort()                  # body 不再收, 等 body 的 run 放行
        self._arrived.set()           # 放开 _settle 的等待
        self._queued.set()            # 放开父的闭标签握手(它等不到入队了)
        await self._cancel_pending(everyone=True)

    # --- body 面(body = 子 routine 的标签, 边到边扫边投) ---

    async def on_body_chunk(self, text: str) -> None:
        """body 到一段就扫一段: 子标签一露头就 route(不等整段)."""
        await self._events(self._scan.feed(text))

    async def on_body_done(self) -> None:
        """闭标签: 收尾扫描, 然后自己入队(占道的起点)."""
        self._t0 = time.perf_counter()
        evs = self._scan.close()
        await self._events(evs)
        if self._scan.unclosed:
            await self._error('unterminated tag in body')
        # 入队先于唤醒 run: 反过来时空 body 可能整个跑完才被排进队列
        # (已收摊的实例还要被 worker 取一次).
        await self._release()

    @request('obs')
    async def _obs_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """子回执(谁能投递谁收回执): req 并发, 不排 body 的 mailbox 序.

        **只交货, 不判完成**: 完成由 ``run`` 自己从到货里标 —— 若在这里
        就置完成, ``run`` 会抢在本请求的回执前面收摊, 调用方收到的是
        "stopped before replying"(帧明明已送到, 却报丢)。先向上转交
        (账不能丢), 再报有一批到货(finally 保证等的人一定会被放开).
        """
        frames = list(data.get('obs') or [])
        if frames:
            try:
                await self._forward(frames)
            finally:
                self._arrivals.extend(frames)
                self._arrived.set()
        return {'ok': True}

    # --- 子投递 ---

    async def _events(self, evs) -> None:
        for ev in evs:
            if isinstance(ev, ChildOpen):
                await self._open(ev)
            elif isinstance(ev, ChildBody):
                kid = self._cur
                if kid is not None:
                    text = ev.text
                    if kid['raw']:
                        # 叶子 body: 边到边还原实体(见 body.decode_body);
                        # 容器自己的 body 不解码 —— 它是下一层的标签源码
                        text, kid['tail'] = decode_body(text, kid['tail'])
                    if text:
                        await self.send(kid['rid'], {'text': text})
            elif isinstance(ev, ChildClose):
                await self._close()
            elif isinstance(ev, Malformed):
                await self._error(f'malformed tag: {ev.text}')
            # Text: 容器层的裸文本没有语义(旁白归 root), 丢弃

    async def _open(self, ev: ChildOpen) -> None:
        try:
            r = await self.req(self._shell, 'route', {
                'cmd': ev.cmd, 'chan': ev.chan, 'args': ev.kwargs,
                'reply': self.id})
        except Exception as exc:
            await self._error(f'{ev.cmd}: {exc}')
            return
        # chan = lane 名(flow 过滤用), chan_rid = 轨实例(取消时寻址),
        # defer = 子容器(收完 body 才入队, 闭标签要握手等它排进队列)
        kid = {'rid': str(r['rid']), 'chan': ev.chan,
               'chan_rid': str(r.get('chan') or ''),
               'defer': bool(r.get('defer')), 'done': False,
               # 叶子 body 才还原实体(容器的是下一层源码), 尾巴留半截实体
               'raw': bool(r.get('body_raw')), 'tail': ''}
        self._kids[kid['rid']] = kid
        self._cur = kid

    async def _close(self) -> None:
        kid, self._cur = self._cur, None
        if kid is None:
            return
        if kid['tail']:              # 流尾还扣着半截实体: 照原样交出去
            await self.send(kid['rid'], {'text': kid['tail']})
        # 只发 eof: 子容器收完 body 后自己入队(见 _release)
        await self.send(kid['rid'], {'eof': True})
        if kid['defer']:
            try:
                await self.req(kid['rid'], 'ready', {})
            except Exception as exc:
                # 内部时序故障: 不设 _failed(别顺带终止 loop), 也不产
                # error 帧(模型修不了这个)
                _log.warning('child container never queued: %s', exc)

    async def _release(self) -> None:
        """收完 body 才入队(占道的起点).

        必须由**自己**在 eof 处理完之后发: shell 若发完 eof 就 release,
        容器会先被 worker 取到, 它此后投递的子(loop 的展开)就永远排在
        这条串行队列后面 —— 自己等自己.

        ``_queued`` 是给发起者的收条: 闭标签处握手等它, 之后投的同轨
        标签才一定排在本实例**后面**(跨轨屏障的全部依据).
        """
        try:
            if self._chan:
                await self.req(self._chan, 'release', {'rid': self.id})
        except Exception:
            _log.exception('release failed: %s', self.name)
        finally:
            self._queued.set()

    @request('ready')
    async def _ready_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """闭标签握手: 等自己真正排进 lane, 不只是等 body 收完.

        发起者(父容器或 root shell)在 eof 之后问这一声才继续向下投递,
        于是同轨后续标签必然排在后面 —— "b 等 a 做完再开始"靠的就是
        这个顺序; 少这一声就等于把顺序交给调度偶然性.
        """
        await self._queued.wait()
        return {'queued': True}

    async def _forward(self, frames: List[Dict[str, Any]]) -> None:
        """向上汇总: 一帧一层一次请求(不逐帧拆)."""
        if frames and self._reply:
            await self.req(self._reply, 'obs', {'obs': frames})

    async def _error(self, text: str) -> None:
        """语法/投递错误: 由看见它的这一层报(错误归属清晰)."""
        _log.warning('container %s: %s', self.name, text)
        self._failed = True     # 坏了就不再重放(loop 的终止条件之一)
        await self._forward([{'kind': 'error', 'text': text}])

    async def _replay(self) -> None:
        """把 body 原文重新扫一遍并投递(loop 的唯一机制).

        子标签重新 ``route`` → 新实例、新 rid: "同一批指令再跑一遍"
        不需要克隆/缓存任何执行态. 上一轮的 ``_cur`` 已由 ``_close``
        清空, 所以重放从干净的本层视野开始.
        """
        scan = BodyScanner()
        evs = scan.feed(self.body_text)
        evs += scan.close()
        await self._events(evs)
        if scan.unclosed:
            await self._error('unterminated tag in body')

    # --- 等待 ---

    def _group(self) -> List[Dict[str, Any]]:
        """本容器真正要等的子: flow 只看本 lane(空则容错看全部)."""
        group = list(self._kids.values())
        if self.name == '__scope__':
            return [k for k in group if k['chan'] == self._lane] or group
        return group

    def _mark(self) -> None:
        """把到货的回执记成完成(唯一置位处 = run 侧, 不在请求里)."""
        for f in self._arrivals:
            kid = self._kids.get(str(f.get('rid') or ''))
            if kid is not None:
                kid['done'] = True
                if f.get('kind') == 'error':
                    self._failed = True
        self._arrivals.clear()

    def _enough(self) -> bool:
        """量词判据(原版 wait_close 的三个分支共用同一份"看谁完成")."""
        if self._aborted:        # 被打断: 不再等(子已由 stop 收掉)
            return True
        group = self._group()
        if not group:            # 空容器当场结束(容错, 不与空集僵持)
            return True
        if self.name == 'any':
            return any(k['done'] for k in group)
        return all(k['done'] for k in group)

    async def _settle(self) -> None:
        self._mark()             # body 期间先到的回执(子可能先完成)
        if self._timeout > 0:
            # 剩余时间从**闭标签**算起: 容器排在 lane 队列里等前序动作的
            # 那段时间不该算进"子等了多久"(子早就开跑了; 同 lane 的子更是
            # 在容器入队前就跑完了)
            remain = self._timeout - (time.perf_counter() - self._t0)
            if remain <= 0 and not self._enough():
                self._timed_out = True
                await self._cancel_pending()
                return
            try:
                await asyncio.wait_for(self._wait_quorum(), max(remain, 0.001))
            except asyncio.TimeoutError:
                self._timed_out = True
                await self._cancel_pending()
            return
        await self._wait_quorum()

    async def _wait_quorum(self) -> None:
        while not self._enough():
            await self._arrived.wait()
            self._arrived.clear()
            self._mark()
        if self.name == 'any':
            # 原版 any 分支: 定了一家就掐掉其余(未完成的真取消)
            await self._cancel_pending()

    async def _cancel_pending(self, everyone: bool = False) -> None:
        """取消未完成的子(原版 ``_clear_all_sub_tasks``): 走轨的 kill
        生命周期原语, 不发明 cancel 消息. 在跑的 stop 后照常回执."""
        group = list(self._kids.values()) if everyone else self._group()
        victims = [k for k in group if not k['done']]
        if not victims:
            return
        self._cancelled += len(victims)
        await asyncio.gather(*[
            self.req(k['chan_rid'], 'kill', {'rid': k['rid']})
            for k in victims], return_exceptions=True)


class All(Container):
    """`<all>`: body 内全部完成才返回."""

    name = 'all'

    meta: ClassVar[Dict[str, Any]] = {
        **Container.meta,
        'description': '时序容器: body 里的 routine **全部**完成后才继续本轨',
    }


class AnyOf(Container):
    """`<any>`: body 内任一完成即返回, 其余取消."""

    name = 'any'

    meta: ClassVar[Dict[str, Any]] = {
        **Container.meta,
        'description': '时序容器: body 里的 routine **任一**完成就继续, 其余取消(竞速)',
    }


class Flow(Container):
    """`<_>`(=`<__scope__>`): 只等同 lane 的子, 一个都没有则等全部."""

    name = '__scope__'

    meta: ClassVar[Dict[str, Any]] = {
        **Container.meta,
        'description': ('时序容器: 只等**同轨**的 body 里的 routine(分轨的不等); '
                        '一个都没有则等全部'),
    }


class Loop(Container):
    """`<loop times="3">`: body 重复跑 N 轮(原版 ``loop(times, ctml__)``).

    **展开而不是重入**: 原版 loop 先 ``async for token in ctml__`` 收全
    body, 再一次性展开成任务交给命令栈 —— 本类照做, 只是"展开"发生在
    ``eof`` 处理里: 每轮用新扫描器重扫 body 原文重新投递(新实例/新 rid),
    全部轮次的子**先于 loop 自己入队**, 于是 run 只等、不再投递.

    这不是实现花样, 而是本结构的硬约束: 容器一旦被 lane 的 worker 取到
    就占住了这条串行队列, 此刻再往同 lane 投子会永远排在后面(自己等
    自己). 原版没有这个问题, 因为 loop 根本不是 channel 上的任务, 而是
    命令栈的展开器 —— 展开前置是它语义的一部分, 不是对它的偏离.

    展开方式的边界(与逐轮展开的唯一差别): 轮次之间没有屏障, 跨轨的
    第 N 轮可能早于另一轨的第 N-1 轮; 单轨内完全等价(队列本身串行).
    终止: ``times`` 到 / 硬顶 100(``times<0`` 即取此值) / 有子失败
    (停止等待并撤销剩余). 收尾回执自带注意债 —— "loop done at N" 是
    模型必须知道的事实(原版 ``CommandTaskResult(observe=True)``).
    """

    name = 'loop'

    meta: ClassVar[Dict[str, Any]] = {
        **Container.meta,
        'description': ('循环: 把 body 里的 routine 重复执行 times 轮'
                        '(负数 = 最多 100 轮; 省略 = 1 轮), 全部轮次完成'
                        '才继续本轨后续'),
        'input_schema': LoopInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        await super().on_created(rid, kwargs)
        self._times: int = int(kwargs.get('times', 1) or 0)
        self._rounds: int = 0     # 实际展开的轮数(run 的收尾报告)

    async def on_body_chunk(self, text: str) -> None:
        """只攒不投递（基类已留档）—— 展开要等 body 收全（原版同）.

        必须显式覆盖：不覆盖就继承 ``Container.on_body_chunk``，边到边投
        一遍，``on_body_done`` 再展开 N 轮 = N+1 遍.
        """

    async def on_body_done(self) -> None:
        """整段收完: 按轮重放 body, 然后自己入队(全部轮次的子在前)."""
        self._t0 = time.perf_counter()
        await self._expand()
        await self._release()      # 全部轮次的子先于自己入队

    def _enough(self) -> bool:
        if self._failed:      # 有子失败: 不再等剩下的(原版失败即返回)
            return True
        return super()._enough()

    def _wanted_rounds(self) -> int:
        if self._times == 0 or not self.body_text:
            return 0
        return _LOOP_CAP if self._times < 0 else min(self._times, _LOOP_CAP)

    async def _expand(self) -> None:
        """按轮重放 body; 某一轮里出现词法/投递错误就停在那一轮."""
        rounds = self._wanted_rounds()
        for r in range(rounds):
            if self._failed:
                break
            await self._replay()
            self._rounds = r + 1

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await self._body_ready.wait()
        if not self._kids:
            return {'content': 'body 是空的, 一轮也没跑',
                    'details': {'loops': 0}}
        await self._settle()
        if self._failed:
            await self._cancel_pending(everyone=True)
        details = {'loops': self._rounds, 'cancelled': self._cancelled}
        content = f'跑了 {self._rounds} 轮'
        if self._cancelled:
            content += f', 取消 {self._cancelled} 个'
        return {'content': content, 'details': details, 'observe': True}


# 词表里的容器名(装配注入; 不是解析器保留字 —— 扫器根本不认识它们).
# 唯一的出处是上面四个类, 所以列出来由类名算, 不另抄一份字面量.
CONTAINERS = frozenset(c.name for c in (All, AnyOf, Flow, Loop))
