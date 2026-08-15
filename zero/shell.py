"""业务侧编排 shell.

设计上:command queue + loop,``processLeftSiblings`` 算左兄弟阻塞
(``CanBlockRightSibling``:``wait`` 无条件阻塞;否则 scope 交集→串行,不交集→并行).
本模块把这套编排策略搬到 py 业务侧(zero),建在 ``routine.ctx`` 的 submit/start/await
+ conflict 原语上.kernel(``kernel/shell.Manager``)只管正确性不变量
(互斥 + 生命周期 + 总线 + 级联 stop),不内置编排策略----策略属于业务侧,跟将来可能
加的 DAG / FSM / 行为树平起平坐.

入参 routine 实例:编排的父 routine.Shell 用 ``routine.ctx`` 投递子 routine.

用法::

    shell = Shell(self)                       # self 是当前 routine 实例
    h1 = await shell.push('ui_noop', {'n': 'first'})   # 提交(建 handle),返回 handle
    h2 = await shell.push('quick', {'msg': 'parallel'})
    h3 = await shell.push('ui_noop', {'n': 'second'})
    shell.complete()                          # 标记加载完毕(对标 End command)
    results = await shell.join()              # 等全完成,返回 push 序结果

    # 批量便捷入口(等价于上面):
    results = await Shell(self).run(specs)

    # barrier(对标 'wait' 命令):
    await shell.push('a')
    await shell.push('wait', {'duration': 0.5})  # wait 等 a 完成,再 sleep 0.5s
    await shell.push('b')                         # b 等 wait 完成(无条件)

push 返回框架原生 ``RoutineHandle``(跟 ``ctx.submit`` 一致):``await handle`` 拿
结果,``handle.stop()`` 中途停,``handle.id`` 给 p2p/req 定向,``async for`` 迭代
body.语义对标 Go:``push`` 尽快 submit(create 态建 handle,对标 push→Ready
建订阅),``start`` 是调度点(等左兄弟完成后由 Shell 内部调,对标 loop 的
TryStart).故调用方拿到 handle 后可 ``await``/``stop``/迭代 body,但不应自己 start.

串并行语义(对标 ``CanBlockRightSibling``):每条命令跟其**左兄弟**逐个判
``ctx.conflict``(cone 交集)----冲突的左兄弟须 stop 后本命令才 start(串行,
顺序 = push 顺序);不冲突的并行.失败不中断后继:失败者照常放行(模块已释放,
后继的冲突前提消失).``join()`` 的结果[i] 放该命令的 StartError / 异常对象 /
正常返回值(不抛----统一收集,调用方按需检查)----跟原 ``auto_serial_parallel``
一致;但调用方直接 ``await handle`` 时按 handle 语义(异常会抛).
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from routine import Routine, RoutineHandle

# spec = (routine_name, kwargs).跟 Shell.run 入参对齐.
Spec = Tuple[str, Dict[str, Any]]


class Interrupted(Exception):
    """Shell.interrupt 打断的 entry 标记--放进 result,不外抛.

    对标 Shell.Interrupt 的 top-down 打断语义:被 interrupt 的命令不区分
    "正常完成/失败",统一标成 ``Interrupted`` 放进 ``join()`` 的结果列表--调用方
    据此知道哪些是被打断的(跟现有"收集不中断"语义一致:join 不抛,结果里混着
    正常值 / StartError / 异常对象 / Interrupted).
    """

    def __repr__(self) -> str:
        return '<interrupted>'


class Shell:
    """模块自动串并行编排 shell(业务侧,参考 Go Shell).

    入参 ``routine`` 是编排的父 routine(正在跑 ``start()`` 的那个).Shell 经
    ``routine.ctx`` 的 submit/start/await + conflict 投递子 routine:
    左兄弟冲突→串行,不冲突→并行.
    """

    def __init__(self, routine: 'Routine', *, shell_id: str = 'default',
                 auto_arm: bool = True, ondone=None):
        self._routine = routine
        # shell_id:物理隔离 key.'default'=代码主动 self.push() 进 normal_shell;
        # 'body'=XML body 派生进 body_shell(XmlRoutine.BODY_SHELL_ID).on_body_shell_done
        # 按它过滤;on_xml_event 里据它区分 body 派生 push vs normal_shell.kernel 不感知.
        self._shell_id = shell_id
        # done 回调(async callable(shell) | None):shell 整体 done(complete + 所有
        # entry 完成)后 spawn 调一次(幂等).供 XmlRoutine 这种 body_shell done ->
        # request_stop 回调驱动用.run 不再 override 阻塞等,靠回调让 routine 停.
        self._ondone = ondone
        self._entries: List[_Entry] = []
        self._complete: bool = False
        # shell 整体 done 标志 + Event(wait_done 等).所有 entry done 且 complete 后 fire.
        self._done_fired: bool = False
        self._done_evt: asyncio.Event = asyncio.Event()
        # armed=False 时 push 只 submit(父未 started,不能 start_child);arm() 后
        # 才 start.auto_arm=True(默认)构造即 arm----适配 auto_sp 这种"父已 started
        # 才用 Shell"的场景.XmlRoutine 这种 created 就流式 push,start 后才 start 的
        # 编排器用 auto_arm=False.
        self._armed: bool = auto_arm
        # interrupt 打断标志(对标 Shell.state=StateInterrupting).
        # _run 在等左兄弟 / start 前检查它,被打断的 entry 收尾成 Interrupted().
        self._interrupted: bool = False

    @property
    def _ctx(self):
        return self._routine.ctx

    @property
    def shell_id(self) -> str:
        return self._shell_id

    async def push(self, name: str,
                   kwargs: Optional[Dict[str, Any]] = None) -> 'RoutineHandle':
        """提交一条子 routine 命令,返回它的 ``RoutineHandle``.

        对标 ``Shell.AddCommand`` + ``processLeftSiblings``:push 尽快 submit
        (create 态建 handle,对标 push→Ready 建订阅).start 是调度点----本命令
        跟其左兄弟逐个判 ``CanBlockRightSibling``:冲突(cone 交集)的左兄弟须 stop
        后本命令才 start(串行);不冲突的并行.start 由 Shell 内部在左兄弟就绪后
        调(对标 loop 的 TryStart),调用方拿到 handle 后可 ``await``/``stop``/
        迭代 body,但不应自己 start.

        ``complete()`` 后再 ``push`` 抛 RuntimeError(命令加载已结束).

        modules 从 submit 返回的 ``handle.modules`` 拿(子 routine created() 返回,
        经 created 回报 → kernel → submitted 回执带回).这是占用真理源----编排器据此
        算冲突,跟 kernel Start 的 TryAcquire 一致(handle.modules = node.declared).
        static/dynamic 统一:created() 返固定 list 或按 kwargs 现算,都经同一条回报.

        ``wait`` 特殊待遇(对标 ``wait`` 命令,双向全局同步点):
        name=='wait' 的命令标记为 barrier----**等所有左兄弟完成**(无条件,对标
         BaseBlocker:任何左兄弟阻塞 wait),自己跑完后再放行右兄弟(对标
         WaitBlocker:wait 阻塞所有右兄弟).配合 ``Wait`` routine 的
        ``duration`` 形成声明式同步点::

            await shell.push('a')
            await shell.push('wait', {'duration': 0.5})  # wait 等 a 完成,再 sleep 0.5s
            await shell.push('b')                         # b 等 wait 完成(无条件)
        """
        if self._complete:
            raise RuntimeError('Shell already complete, cannot push')
        if self._interrupted:
            raise RuntimeError('Shell interrupted, cannot push')
        ctx = self._ctx
        is_barrier = (name in ['wait', 'WAIT'])
        # push 尽快 submit(create 态----拿 handle 给调用方;对标 push→尽快 Ready
        # 建订阅).start 是调度点,延后到 _Entry._run 里(等冲突左兄弟完成后再
        # start)----对标 Ready 后由 Shell loop 决定何时 TryStart.
        # create 不占模块,只 start 占----故提交期无冲突,start 期才有.
        # handle.modules 由 kernel 解析(static 缓存 / dynamic 现算),是占用真理源.
        handle = await ctx.submit(name, kwargs or {})
        mods = handle.modules if handle.modules is not None else []
        entry = _Entry(self, handle, mods, is_barrier)
        self._entries.append(entry)
        if self._armed:
            entry._schedule()
        return handle

    def arm(self) -> None:
        """标记父 routine 已 started,允许 Shell 开始 start 已 push 的 handle.

        ``auto_arm=False`` 时 push 只 submit(父未 started,``start_child`` 会拒);
        父 ``start()`` 被调后调本方法,schedule 所有 pending entry + 后续 push 立即
        schedule.幂等.用于 XmlRoutine 这类"created 就流式 push,start 后才 start"
        的编排器----对标 Shell "push 尽快 submit,loop 在 started 后才 TryStart".
        """
        if self._armed:
            return
        self._armed = True
        for entry in self._entries:
            if entry._task is None:
                entry._schedule()

    def reset(self) -> None:
        """清空 entries + 所有标志,让 Shell 可复用于下一批 push.

        agent 常驻单 Shell:每轮 FC(一批工具)push+complete+join 后调本方法,
        下一轮重新 push.工具内审批(``push_and_wait``)也用同一 Shell--审批和
        当轮工具进同一编排(共享 cone 互斥).前一批的 entry 已 join 完(task done),
        清掉引用即可;done_evt 重建供下一轮 await.
        """
        self._entries = []
        self._complete = False
        self._done_fired = False
        self._done_evt = asyncio.Event()
        self._interrupted = False

    async def wait(self) -> None:
        """barrier:等所有已 push 的命令完成(对标 ``wait`` 命令).

        ``wait`` 之后再 push 的命令自然在 ``wait`` 返回之后才 start(对标 wait
        阻塞所有右兄弟直到左兄弟全 stop).``wait`` 自身也参与 ``join()`` 的结果
        收集(不占位,只是同步点).
        """
        for entry in list(self._entries):
            await entry

    def complete(self) -> None:
        """标记命令加载完毕(对标 ``End`` command / ``SetComplete``).

        ``join()`` 要求先 ``complete()``----防止边 push 边 join 漏掉 in-flight 命令.
        """
        self._complete = True
        self._fire_done()
    async def join(self) -> List[Any]:
        """等所有 push 的命令完成,返回跟 push 顺序同序的结果列表.

        必须先 ``complete()``.结果[i] 是该命令的 result(StartError / 异常对象 /
        正常返回值)----失败统一收集成 result 对象(不抛),调用方按需检查,跟原
        ``auto_serial_parallel`` 语义一致.注意:调用方若直接 ``await handle``
        则按 handle 语义(异常会抛),跟本方法的"收集成对象"不同.
        """
        if not self._complete:
            raise RuntimeError('Shell.join requires complete() first')
        results: List[Any] = []
        for entry in self._entries:
            results.append(await entry)
        return results

    async def run(self, specs: List[Spec]) -> List[Any]:
        """便捷批量入口:push 全部 specs → complete → join.

        等价于::

            for name, kwargs in specs:
                await shell.push(name, kwargs)
            shell.complete()
            return await shell.join()
        """
        for name, kwargs in specs:
            await self.push(name, kwargs)
        self.complete()
        return await self.join()

    async def interrupt(self) -> None:
        """top-down 打断:停已 start 的子,撤未 start 的子,唤醒 join.

        跟正常 stop 的对照:
          - 正常 stop:bottom-up,父等子(``handle.wait`` 收 stopped)
          - interrupt:top-down,父推子,绕开等待立刻 stop/unsubmit 全部

        典型用法(编排者 stop hook)::

            async def stop(self):
                if self._shell is not None:
                    await self._shell.interrupt()
        """
        if self._interrupted:
            return
        self._interrupted = True
        for entry in list(self._entries):
            await entry._interrupt()
        self._fire_done()

    async def push_and_wait(self, name: str,
                            kwargs: Optional[Dict[str, Any]] = None) -> Any:
        """push 一条子 routine + 等它结束,返回结果(走串并行编排).

        对标老 routine3 ``Routine.push_and_wait``:push 进 shell 享受 cone 串并行
        编排(跟已 push 的左兄弟冲突则串行,不冲突并行),complete + join 等这条
        结束.单条便捷入口--等价于 ``push(name, kwargs); complete(); join()[0]``.

        失败(StartError / 异常 / Interrupted)**抛出**而非收成对象--区别于
        ``join`` 的"收集不抛"语义.调用方直接 ``result = await push_and_wait(...)``
        拿正常返回值,异常上抛(对标老 push_and_wait).

        典型用法(工具内部审批)::

            shell = Shell(self)
            approved = await shell.push_and_wait('ask', {'question': ..., 'options': [...]})
        """
        if self._complete:
            raise RuntimeError('Shell already complete, cannot push_and_wait')
        await self.push(name, kwargs)
        self.complete()
        results = await self.join()
        result = results[0]
        if isinstance(result, BaseException):
            raise result
        return result

    async def wait_done(self) -> List[Any]:
        """等 shell 整体 done(complete + 所有 entry 完成),返回 push 序结果.

        done 后 ondone 回调已 fired(若设了).供 Routine.run 默认实现 await 用--
        区别于 join():wait_done 不要求调用方先 complete,且 done 是单向事件(只 fire
        一次).多次 await 安全(Event 幂等).
        """
        await self._done_evt.wait()
        return [e._result for e in self._entries]

    def _fire_done(self) -> None:
        """所有 entry done 且 complete 后 fire 一次(幂等).

        _Entry._run finally 调 + complete() 调 + interrupt() 调--三处都检查,先到的
        满足条件即 fire.fire 后 spawn ondone 回调(不阻塞 entry finally).

        空 shell(complete 时无 entries,如 LLM 纯文本无工具调用)也 fire--
        没有 entry 要等,complete 即 done.
        """
        if self._done_fired:
            return
        if not self._complete:
            return
        # 空 shell: complete 即 done(无 entry 要等).
        if not self._entries:
            self._done_fired = True
            self._done_evt.set()
            if self._ondone is not None:
                asyncio.create_task(self._ondone(self))
            return
        if not all(e._done.is_set() for e in self._entries):
            return
        self._done_fired = True
        self._done_evt.set()
        if self._ondone is not None:
            asyncio.create_task(self._ondone(self))


class _Entry:
    """Shell 内部跟踪条目(handle + 编排状态).``push`` 把 handle 返回给调用方,
    ``_Entry`` 负责等左兄弟 + start handle + 收 result.

    push 已 submit(create 态建 handle----对标 push→Ready);``_run`` 负责 start
    调度:等冲突的左兄弟完成(对标 ``StartWait(sib, Stopped)``)→ ``handle.start()``
    → ``await handle``;result 放 StartError / 异常 / 返回值;``finally`` set
    ``_done`` 放行后继(失败已释放模块,后继冲突前提消失).

    ``is_barrier=True``(对标 ``wait`` 命令):全局同步点----**等所有左兄弟
    完成**(无条件,对标 BaseBlocker:任何左兄弟阻塞 wait),自己跑后再
    放行右兄弟(对标 WaitBlocker:wait 阻塞所有右兄弟).把编排切成
    "wait 前 / wait 后"两段.

    任何阶段异常(含 ``conflict`` 抛"tree not cached")都存进 result + set
    ``_done``----不让 awaiter 挂死.``join()`` 从 result 拿到异常对象.
    """

    def __init__(self, shell: Shell, handle: 'RoutineHandle',
                 mods: List[str], is_barrier: bool):
        self._shell = shell
        self.handle = handle
        self.name = handle.name
        self.mods = mods
        self.is_barrier = is_barrier
        self._done = asyncio.Event()
        self._result: Any = None
        self._task: Optional[asyncio.Task] = None
        # interrupt 打断标志--_run 检查它收尾成 Interrupted().
        self._interrupted: bool = False

    def _schedule(self) -> None:
        # push 在 async 上下文里调(routine.start 中),running loop 一定在.
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            ctx = self._shell._ctx
            # 左兄弟 = push 顺序在 self 之前的命令.snapshot index 防并发追加.
            idx = self._shell._entries.index(self)
            for prev in self._shell._entries[:idx]:
                # 对标 processLeftSiblings + CanBlockRightSibling(双向):
                #  - self 是 wait(barrier)→ 等所有左兄弟(无条件,不论 modules):
                #     BaseBlocker:任何左兄弟都能阻塞 wait(wait 等到所有左兄弟 stop).
                #  - prev 是 wait(barrier)→ 等它(无条件): WaitBlocker:
                #    wait 阻塞所有右兄弟.
                #  - 否则按 cone 交集(conflict)判定.
                if self.is_barrier or prev.is_barrier or \
                        ctx.conflict(self.mods, prev.mods):
                    await prev._done.wait()
                    # 等 left 期间被打断--不再 start,收尾成 Interrupted.
                    if self._interrupted:
                        self._result = Interrupted()
                        return
            # start 前被打断--撤 created node 的事 _interrupt 已做,这里收尾.
            if self._interrupted:
                self._result = Interrupted()
                return
            # 所有该等的左兄弟都完成----安全 start(无 cone 冲突).
            err = await self.handle.start()
            if err:
                self._result = err
            else:
                # handle.wait() 失败抛 RuntimeError----收进 result(不中断 join).
                self._result = await self.handle
                # 运行中被 interrupt:handle.stop(fire=True) 触发的 stopped 收到这里,
                # 覆盖成 Interrupted(跟"正常完成"区分).
                if self._interrupted:
                    self._result = Interrupted()
        except Exception as exc:
            self._result = exc
        finally:
            self._done.set()
            self._shell._fire_done()
    def __await__(self):
        return self._await_result().__await__()

    async def _await_result(self) -> Any:
        await self._done.wait()
        return self._result

    async def _interrupt(self) -> None:
        self._interrupted = True
        if self.handle.is_started():
            await self.handle.stop(fire=True)
        else:
            await self.handle.unsubmit(fire=True)
