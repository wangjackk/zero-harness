"""Channel —— 串行轨 routine: 一条队列 + 一个消费者.

串行是轨的全部语义: ``verb`` 入队即回(**create 就在入队口** ——
shell 只递名字和入参, 实例永远在轨的手里), worker 按到达序逐个
``start + wait``. 跨轨并行 = 各持各的队列, 天然并行, 不需要任何
并行机制.

流式动作(say)与原子动作同一条队列: 开标签入队时实例即 create
(体增量由 shell 拿回执 rid 直投 —— created 态即可收消息),
**start 仍由 worker 按到达序调度** —— 同轨 FIFO 对两种形态
一视同仁, 不因流式越权插队.

轨不管名单(verb 闸门在 shell 的全局词表), 也不管互斥(动作自带
modules 声明, 锥冲突由 kernel TryAcquire 拒绝), 也不管结果:
**完成即回执** —— worker 把 run 的返回/异常当场 ``send`` 给**发起者**
(谁投递谁收回执; 原版 tracer ``on_task_done`` 回调同构, 收件人从
"全局账本"改成"投递方"). 发起者收齐后向上汇总一层, 账本最终仍
收敛在 root(一条指令一条账, 扁平).

打断走生命周期权威原语(不用 abort 消息旁路): 未 start 的
``destroy``(谁创造谁销毁), 在跑的 ``stop``(谁启动谁停止 ——
动作自己的 stop hook 优雅收场, run 正常返回照常报账; 不收场的
STOP_TIMEOUT 兜底杀, 原版 INTERRUPTED 同构).

**call_soon**(原版 ``PyCommand(call_soon=True)``): 指令可以声明"不排队,
立刻跑" —— 只有"清空排程"这一类语义需要它(``clear``/``interrupt``:
排在排程里等前面跑完, 就什么也清不掉了). 这是调度属性, 不是形态旁路:
同一个 verb 入口、同一个实例生命周期、同一条回执链路, 只是不入队.
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine, request

from .frames import failure, receipt

__all__ = ['Channel']

_log = setup_logger('moss.channel')


class ChannelInput(BaseModel):
    """channel 入参: 轨道名."""

    chan: str = Field(description='lane 名')


class Channel(Routine):
    """串行轨: 一条队列 + 一个消费者, 全部语义就是同串 + 报账."""

    name = 'channel'

    meta: ClassVar[Dict[str, Any]] = {
        'description': '动作串行轨道',
        'input_schema': ChannelInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = ChannelInput.model_validate(kwargs or {})
        self._chan: str = inp.chan
        self._jobs: asyncio.Queue = asyncio.Queue()
        self._current_rid: str | None = None       # 在跑动作实例 id(abort/kill 用)
        self._current_handle: Any = None           # 在跑动作 handle(stop 用)
        self._reply_of: Dict[str, str] = {}        # rid → 发起者(回执收件人)
        self._deferred: Dict[str, Any] = {}        # rid → handle(已 create, 等 release)
        self._soon: set = set()                    # call_soon 的活(不入队)
        self._soon_handles: Dict[str, Any] = {}    # rid → handle(在跑的抢占实例)

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """单消费者: 队列有活就跑; 理论上不退出 —— run 挂着即
        轨常驻(单例时间轴). 唯一出口是 stop 哨兵(stop() 排空
        已受理动作后塞入, 框架优雅收割的配合, 非正常返回路径)."""
        while True:
            handle = await self._jobs.get()
            try:
                if handle is None:   # stop 哨兵
                    return
                await self._run_one(handle)
            finally:
                self._jobs.task_done()

    async def _run_one(self, handle: Any, soon: bool = False) -> None:
        """跑一个实例并报账. worker(串行槽位)与 call_soon(不入队)
        共用 —— 差别只在谁占 ``_current_*``(那是串行语义的"在跑"槽位,
        抢占型实例不该占它, 否则会把自己变成被 abort 的对象)."""
        if not soon:
            self._current_rid = handle.id
            self._current_handle = handle
        try:
            await handle.start()
            result = await handle
            # 回执载荷在这里成形(content/details/images/observe, 见
            # frames.receipt): 信封的键(kind/verb/chan/rid)由本处写, 门
            # 只回答"结果说了什么".
            # 载荷不合规(receipt 抛错)即落到下面的 except —— 照样变成错误帧.
            await self._report(handle.id, {
                'kind': 'action', 'verb': handle.name,
                'chan': self._chan, 'rid': handle.id,
                'result': receipt(handle.name, result)})
        except Exception as exc:
            # 动作失败不杀 worker —— 队列里还有后续动作要走;
            # 失败当场回执(含锥冲突 StartError), 迟早随账本回灌.
            # 字段与成功帧同构(verb/chan/rid), 只换载荷键 —— 错误
            # 是结果的一种(Go 的 error 即值): 同一本账、同一缝合缝,
            # 不另立通道. rid 供 observe 声明集合按动作对号清账.
            # text 走 frames.failure: 校验错误脱掉 pydantic 外衣再进 prompt
            # (类名/入参 dump/文档链接是给 debug 的人的, 模型只要知道哪个
            # 字段不合法), 详见 frames.failure.
            _log.exception('verb failed: %s', handle.name)
            await self._report(handle.id, {
                'kind': 'error', 'verb': handle.name,
                'chan': self._chan, 'rid': handle.id,
                'text': failure(exc)})
        finally:
            if not soon:
                self._current_rid = None
                self._current_handle = None

    def _spawn(self, handle: Any) -> None:
        """call_soon: 立刻起跑, 不等队列(原版 ``call_soon=True``).

        handle 留在 ``_soon_handles`` 里: 抢占型实例不进队列、不占
        ``_current_*``, 但**仍在跑** —— 清轨与定点处死要能找到它,
        否则 ``clear`` / ``interrupt`` 这类指令停不下来(只能等 STOP_TIMEOUT).
        """
        task = asyncio.ensure_future(self._run_one(handle, soon=True))
        self._soon.add(task)
        self._soon_handles[handle.id] = handle

        def _done(t: asyncio.Task) -> None:
            self._soon.discard(t)
            self._soon_handles.pop(handle.id, None)
            if t.cancelled():
                return
            exc = t.exception()   # 不取会变成 "never retrieved" 噪音
            if exc is not None:
                _log.error('call_soon task failed: %s: %s', handle.name, exc)

        task.add_done_callback(_done)

    async def stop(self) -> None:
        """已受理的动作跑完再退(串行语义的收尾).

        抢占型实例同样要收: 它们是活着的任务, 不通知就会一直等
        (父必须等子, 而子得先知道自己该走了).
        """
        await self._jobs.join()
        for rid, h in list(self._soon_handles.items()):
            try:
                await h.stop(fire=True)
            except Exception:
                _log.exception('stop soon instance failed: %s', rid)
        if self._soon:
            await asyncio.gather(*list(self._soon), return_exceptions=True)
        await self._jobs.put(None)

    async def _report(self, rid: str, frame: Dict[str, Any]) -> None:
        """完成即回执(原版 tracer ``on_task_done`` 同构, 收件人 = 发起者).

        用 ``req`` 而不是 ``send``: req handler 是**并发**的, 不排发起者的
        mailbox 序 —— 否则回执会排在"收尾令"后面变成迟到帧(本该随本拍
        drain 的错误被推到下一拍). 发起者此时可能正在等这批回执, 但 req
        handler 独立 spawn, 不构成等待环. 发不出去只记日志: 账面损失
        可见, 不阻塞执行."""
        reply = self._reply_of.pop(rid, None)
        if not reply:
            _log.error('reply dropped, initiator unknown: %r', frame)
            return
        try:
            await self.req(reply, 'obs', {'obs': [frame]})
        except Exception as exc:
            # 发起者已收摊(容器先返回并销毁, 迟到的第二帧就无处可送):
            # 账面损失可见, 不阻塞执行
            _log.error('reply failed: %s <- %r', exc, frame)

    # --- 动作面(shell 唯一调用方, 入队即回) ---

    @request('verb')
    async def _verb_entry(self, source: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """入队即回: 实例此刻 create(handle 留在轨手里, start 由
        worker 按到达序调度). 回执 ``rid``(体增量用 rid 直投 —— created
        态即可收) + ``chan``(发起者取消时要按轨寻址). 闸门在 shell
        全局词表, 轨收到什么排什么; ``reply`` = 发起者(谁投递谁收回执,
        缺省回到投递方).

        ``defer=True``: 只 create 不入队, 等 ``release`` —— 给"要收完整
        body 才能开工"的实例用(容器)。它的 body 期间子实例已循同一轨
        排队(可能与本实例同轨), 若它先入队就会挡住自己的子。

        ``call_soon=True``: 立刻起跑, 不入队(原版 ``PyCommand`` 同参数)。
        给"清空排程"这一类指令用 —— 它们排在排程里就失去意义。
        """
        name = str(data.get('name', ''))
        if not name:
            raise RuntimeError('verb: missing name')
        handle = await self.create(name, dict(data.get('args') or {}))
        reply = str(data.get('reply') or '')
        self._reply_of[handle.id] = reply or getattr(source, 'id', None) or str(source)
        soon = bool(data.get('call_soon'))
        defer = bool(data.get('defer')) and not soon
        if soon:
            self._spawn(handle)
        elif defer:
            self._deferred[handle.id] = handle
        else:
            self._jobs.put_nowait(handle)
        return {'queued': not (defer or soon), 'defer': defer, 'soon': soon,
                'rid': handle.id, 'chan': self._chan}

    @request('release')
    async def _release_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """延迟入队的实例开始排队(顺序 = release 时刻, 即在它 body 内
        已投递的子之后 —— 容器不会挡住自己的子)."""
        rid = str(data.get('rid', ''))
        handle = self._deferred.pop(rid, None)
        if handle is None:
            return {'released': False}
        self._jobs.put_nowait(handle)
        return {'released': True}

    # --- 控制面(不入队, 即时应答) ---

    @request('status')
    async def _status_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """轨的即时状态(只读, 不碰队列): 排队几个 / 在跑谁 / 抢占几个.

        删除轨前的"还有没有活"就凭这几项(``chan_delete`` 的判据): 队列非空
        时删轨会卡在 ``stop()`` 的 ``_jobs.join()`` 上, 那不是"回收", 是
        把调用方挂住. ``deferred`` 是收 body 中的容器(还没入队) —— 也是活.
        """
        cur = self._current_handle
        return {'chan': self._chan,
                'queued': self._jobs.qsize(),
                'running': ({'rid': self._current_rid, 'verb': cur.name}
                            if cur is not None else None),
                'soon': len(self._soon_handles),
                'deferred': len(self._deferred)}

    @request('abort')
    async def _abort_entry(self, source: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """打断全轨(原版 shell.clear → interpreter.close(cancel_managing=
        True) 同构): 未 start 的 ``destroy``(撤销 create, 无账 ——
        反幻觉: 没执行过就不留痕), 在跑的 ``stop``(优雅停止: 动作自己
        的 stop hook 收场, run 正常返回 → 照常报账; 不收场的
        STOP_TIMEOUT 兜底杀 —— 原版在跑任务 fail(INTERRUPTED) 同构).
        打断是生命周期权威原语, 不再发明 abort 消息旁路."""
        victims: list = []
        # 延迟入队的(还没 release)一并处死: 它们没执行过, 无账
        victims.extend(self._deferred.values())
        self._deferred.clear()
        while True:
            try:
                h = self._jobs.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._jobs.task_done()
            if h is None:
                self._jobs.put_nowait(None)   # stop 哨兵原位保留
            else:
                victims.append(h)
        for h in victims:
            self._reply_of.pop(h.id, None)   # 没执行过就不留账
            try:
                await h.destroy(fire=True)
            except Exception:
                _log.exception('destroy on abort failed: %s', h.id)
        if self._current_handle is not None:
            try:
                await self._current_handle.stop(fire=True)
            except Exception:
                # 已完成/正在停: 良性竞态, 回执走正常报账
                _log.exception('stop on abort failed: %s', self._current_rid)
        # call_soon 实例(不入队): 不占槽位也不在队列里, 但仍在跑 —— 全轨
        # 打断必须覆盖它们. ``except`` = 豁免名单(清道夫不清自己: 发起
        # clear 的实例由 shell 显式递上来, 由它自己跑完并回报).
        spared = {str(r) for r in (data.get('except') or [])}
        for rid, h in list(self._soon_handles.items()):
            if rid in spared:
                continue
            try:
                await h.stop(fire=True)
            except Exception:
                _log.exception('stop soon on abort failed: %s', rid)
        _log.info('channel %s aborted: dropped=%d', self._chan, len(victims))
        return {'dropped': len(victims)}

    @request('kill')
    async def _kill_entry(self, source: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """定点处死一个动作(shell 处死未决流): 未 start 的 destroy
        (无账), 在跑的 stop(优雅截断, 回执照常报账). handle 在轨
        手里 —— shell 只递 rid, 不直接碰别人的实例(谁 create 谁销毁)."""
        rid = str(data.get('rid', ''))
        victim = self._deferred.pop(rid, None)   # 还没 release 的
        if victim is not None:
            self._reply_of.pop(rid, None)
            try:
                await victim.destroy(fire=True)
                return {'killed': True, 'how': 'destroyed'}
            except Exception:
                _log.exception('kill deferred failed: %s', rid)
                return {'killed': False, 'how': 'error'}
        victim = None
        buf: list = []
        while True:
            try:
                h = self._jobs.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._jobs.task_done()
            buf.append(h)
        for h in buf:
            if h is None or h.id != rid:
                self._jobs.put_nowait(h)
            else:
                victim = h
        if victim is not None:
            self._reply_of.pop(victim.id, None)   # 没执行过就不留账
            try:
                await victim.destroy(fire=True)
                return {'killed': True, 'how': 'destroyed'}
            except Exception:
                _log.exception('kill destroy failed: %s', rid)
                return {'killed': False, 'how': 'error'}
        if self._current_rid == rid and self._current_handle is not None:
            try:
                await self._current_handle.stop(fire=True)
                return {'killed': True, 'how': 'stopped'}
            except Exception:
                _log.exception('kill stop failed: %s', rid)
                return {'killed': False, 'how': 'error'}
        soon = self._soon_handles.get(rid)   # 抢占型(不在队列也不占槽位)
        if soon is not None:
            try:
                await soon.stop(fire=True)
                return {'killed': True, 'how': 'stopped'}
            except Exception:
                _log.exception('kill soon failed: %s', rid)
                return {'killed': False, 'how': 'error'}
        return {'killed': False, 'how': 'missing'}
