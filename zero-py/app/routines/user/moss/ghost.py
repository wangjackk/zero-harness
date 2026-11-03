"""Ghost —— 灵魂: 有记忆的思考者, 不是 LLM, 也不是管道.

LLM 是它外接的脑子(``brain_factory`` 注入, 可换), 身体是它穿的壳(Shell).
灵魂自己有的是: **记忆(感知关键帧) + 思考** —— 一拍 think = 一段字流,
逐块转给身体(不解析, 解析归身体; 不执行, 执行归 channel). 把 ghost
做成纯管道就没有灵魂; 把 LLM 做成 ghost 就没有换脑的余地.
灵魂 = 使用者, 脑子是外设.

记忆 = Moments 观察器(原版 ``blueprint/moment.py`` 同构, 塌缩版):
每拍一个 Moment 关键帧 —— previous(缝合缝: 上拍回执 + 停止原因)
+ percepts(本拍输入, 按源 keyed) + dynamic_context(瞬态, 永不入史)
+ logos(本拍输出). 滑窗按帧计(默认 100 帧 = 原版 max_moments_size),
出窗即忘; epoch 压缩把窗内历史转生 recap 置顶. **反幻觉缝合**:
中断拍已执行的部分 logos 也入史(原版 executed_logos 同构 ——
执行了但模型没说完的话不记, 就成了纯幻觉).

**何时想/想几拍归 Mindflow(桥梁)**, 灵魂只管想: ``think`` 入参
``percepts`` = 本拍外界输入(按源 keyed, 可空 —— 回声帧不带新输入,
记忆里已有原文与观测; 兼容旧 ``text`` 单源), ``transients`` = 瞬态
快照(同源覆盖入槽, 不入史). 思考的产物(logos)与身体的回执(观测)
缝进帧链, 作为后续拍的底.

被抢占: 桥梁调 ``abort``(原版 attention.abort 同构 —— 事件置位,
立即生效), 在途 ``think`` 在下一个检查点(字流块边界/观测等待)抛
错退出, 桥梁走拍链中断同一条通路留痕收链.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine, request

from .moment import Moments

__all__ = ['Ghost']

_log = setup_logger('moss.ghost')

# Epoch 摘要指令(原版 recap 的供料; 原版 with_epoch_recap 全库无
# 注册方, 这里接通通路). 输入过 Brain 的同一套渲染(原版块形态),
# 所以这里按块名讲, 不按旧的前缀讲.
RECAP_SYSTEM = (
    '你是记忆压缩器. 以下是你(this assistant)的近期经历: '
    '<percepts>=外界输入(其中 <listen> 是听来的话, 裸文本是人打的字), '
    'assistant=你的话, <observe>=动作回执, <dynamic_context>=当时的状态, '
    '<recap>=更早的前情提要. 压缩为一段前情提要: 保留关键事实、'
    '承诺与未完成事项、当前状态. 直接输出提要正文, 不超过 200 字.')


class GhostInput(BaseModel):
    """ghost 入参: Moss 组装时注入."""

    shell_id: str = Field(description='身体(Shell)实例 id, 字流的目的地')
    system: str = Field(
        default='',
        description='系统前缀(Moss 装配时拼好一次注入, 前缀缓存友好)')

    max_moments: int = Field(
        default=100,
        description='记忆滑窗上限(帧, 一拍一帧; 原版 max_moments_size'
                    '=100 同参)')


class Ghost(Routine):
    """灵魂: 记忆(感知关键帧) + 思考 + 外接脑子. 字面理解归身体,
    动作归 channel."""

    name = 'ghost'

    # 脑子工厂(类属性注入, 与 Say.engine_factory 同构): 返回带
    # ``stream(history, system) -> AsyncIterator[str]`` 的脑子. None =
    # 无脑, think 即抛错(立即暴露).
    brain_factory: ClassVar[Callable[[], Any] | None] = None

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '灵魂: 记忆(感知关键帧 Moments: 缝合缝+percepts 按源+瞬态'
            '不入史+logos, 滑窗按帧) + 思考 + 外接脑子(brain_factory '
            '注入, 可换). think {percepts, transients, logos?, '
            'record_only?} = 一拍(三种: 正常拍脑子产流 / 反射拍 logos '
            '直投身体不调脑 / 空拍 record_only 只缝留痕不思考 —— '
            '原版 effort=none 同构): (可选)瞬态入槽 + 开帧(缝上拍回声)'
            ' → 字流逐块转身体 → logos 落帧 → 观测回灌(pending 回声: '
            '收尾 drain 批量 + 迟到 trace 即时转发; 门禁 = 拍号匹配'
            '且拍开着才唤醒 —— 在岗等待期迟到帧就地消化(原版 '
            ':1148 同构), 已收的只入史由下一届首帧缝合缝读到). interrupt '
            '{reason} = 拍链中断留痕(下一帧缝合缝). abort {reason} = '
            '中止在途拍(桥梁抢占调用, 检查点抛错退出, 异常路径补发'
            '打断收尾令 —— 原版 thinking 退出即 shell.clear 同构). '
            'epoch = 压缩窗内历史为 recap 前情提要. recall 读记忆'
            '(回合制条目 + 帧视图 + 瞬态 + dropped/epochs). '
            '何时想/想几拍归 mindflow.'),
        'input_schema': GhostInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = GhostInput.model_validate(kwargs or {})
        self._shell_id = inp.shell_id
        self._system = inp.system
        self._moments = Moments(inp.max_moments)   # 记忆: 关键帧链
        self._last_obs: List[Dict[str, str]] = []
        self._obs_event = asyncio.Event()          # 身体回灌观测的唤醒点
        self._abort_event = asyncio.Event()        # 在途拍中止(桥梁抢占置位)
        self._stopped = asyncio.Event()            # run 的取消灯(stop 里点)
        self._abort_reason = ''
        self._beat = 0   # 拍号: 观测门禁(被打断旧流的回声只入史不唤醒)
        self._beat_open = False   # 拍开闭(门禁窗口: think 体内才收观测)
        # 这一拍**开过口没有**(字流已投身体 / 反射拍已投): 桥梁用它判
        # "有没有东西可停" —— 还在想(首字未到)时抢占不是打断, 是白扔一拍
        self._beat_spoke = False
        # 本拍有没有"需要被注意的结果"(帧上的 owed 戳, 由 shell 盖):
        # 有 → think 返回 echo, 裁判点同一盏灯(与手动 /go 共用)
        self._echo_owed = False
        # 裁判地址: 从 think 请求的 source 里读(谁问我思考, 谁就是裁判),
        # 不留额外控制边. 迟到帧的债没人接盘时, 由灵魂回头敲裁判补一
        # 拍 —— 债晚到也兑付, 不靠"下一届碰巧说话"
        self._umpire_id: str | None = None
        # 火忘型后台任务(补拍敲裁判): 留引用, stop 时取消收尸 —— 不留
        # pending 任务给 loop 关停时打 destroyed 警告
        self._bg_tasks: set = set()
        self._brain = self.brain_factory() if self.brain_factory else None
        # 思考出口: 脑子肯说心里话(可选能力 ``on_reasoning``)就给它一个
        # 出口. 灵魂自己不用思考内容 —— 只广播给 UI, 让人看得见它正在想
        # (首字延迟那几秒, 这是唯一还活着的信号)
        if self._brain is not None and hasattr(self._brain, 'on_reasoning'):
            self._brain.on_reasoning = self._emit_reasoning

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """常驻(run 退出 = routine 终止); 身体由 Moss 创建并注入 id."""
        await self._stopped.wait()

    async def stop(self) -> None:
        """收摊: 点灯让 run 退出 + 取消并收尸火忘型后台任务(迟到债的补拍
        通知), 避免它们以 pending 状态漂到 loop 关停/GC."""
        self._stopped.set()
        for t in list(getattr(self, '_bg_tasks', ())):
            t.cancel()
        if getattr(self, '_bg_tasks', None):
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    async def on_message(self, source, data: Any) -> None:
        """身体回灌的观测帧(收尾 drain 批量 / 迟到 trace 即时转发):
        先入史(反幻觉优先 —— 被打断旧流的回声也是史, 攒进 pending
        缝进下帧), 再过门禁: **拍号匹配且拍开着**(本拍在等待观测,
        原版 attention 在岗同构)才唤醒本拍 —— 迟到帧在本拍等待期
        到达则就地消化(原版 :1148 在岗迟到回声下一帧消化同构);
        拍已收/异拍的只入史不串台(下一届首帧缝合缝读到). 非空才
        广播 UI(空观测 = 回声帧的空拍, 原版空回声不 append 同构).

        **注意债(owed)**: 帧上带 owed = 这条结果"需要被注意"(错误,
        或声明过 observe="true" 的成功). 拍开着 → 记进 echo, 本拍
        收尾时由裁判点灯续一拍; 拍已收 → 敲裁判补一拍(债晚到也
        兑付). 两条路都先 add_echoes —— 入史与点灯解耦."""
        if isinstance(data, dict) and 'obs' in data:
            obs = list(data['obs'])
            self._moments.add_echoes(obs)
            owed = any(isinstance(f, dict) and f.get('owed') for f in obs)
            if (data.get('beat') == self._beat and self._beat_open):
                # 门禁窗口内: 空观测也算信号(身体收尾了 = 拍完了,
                # 空回声帧的拍靠它收链); 迟到帧唤醒 = 在岗消化
                if owed:
                    self._echo_owed = True
                self._last_obs = obs
                self._obs_event.set()
            elif owed:
                # 拍已收: 这笔债没人接盘 —— 敲裁判补一拍(链活则同链
                # 续拍免擂台, 链死则落成下一届首拍; 灯幂等, 多敲无妨)
                self._knock_umpire()
            if obs:
                await self.publish('obs', obs, namespace='moss')

    def _knock_umpire(self) -> None:
        """迟到债补拍: 灵魂回调裁判点灯. fire-and-forget —— req 是
        RPC, 但不能阻塞 on_message 的 mailbox 消费(裁判拉取前帧也要
        先入史). 裁判地址未登记(直连 ghost 的单测)只静默跳过."""
        if self._umpire_id is None:
            return

        async def _knock() -> None:
            try:
                await self.req(self._umpire_id, 'beat', {'reason': 'late_obs'})
            except Exception:
                _log.exception('knock umpire failed')

        task = asyncio.ensure_future(_knock())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # --- 思考 ---

    async def _emit_reasoning(self, text: str) -> None:
        """心里话(脑子的 reasoning delta) → UI 广播.

        三条边界, 都是"思考不是话":
        - **不投身体**: 进 shell 会被 CTML 解析器当动作执行(草稿变命令).
        - **不进 logos**: 落帧就成了"它说过的话", 下一拍模型会把自己
          的草稿当既成事实(思考是过程, 不是承诺).
        - **拍开着才推**: epoch 摘要等拍外调用脑子时, 那是压缩劳动,
          不是此刻的思考, UI 不该看见.

        中止后不再推(抢占已生效, 灰字再长就是噪音)."""
        if not self._beat_open or self._abort_event.is_set():
            return
        await self.publish('think', text, namespace='moss')

    @request('think')
    async def think(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """思考一拍 = 开一个 Moment: (可选)瞬态入槽 → 开帧(把 pending
        回声缝成 previous —— 本拍的底) → 脑子产字流(逐块转身体 +
        广播) → logos 落帧 → 等观测回灌 → 返回观测(桥梁据此仲裁).

        字流与观测经 pubsub 广播(namespace ``moss``: topic ``text`` /
        ``obs``; 脑子肯说心里话时另有 ``think`` —— 只给 UI 看, 不进
        身体不入 logos) —— 出口(外挂 UI 进程等)订阅拿流, 灵魂不持 UI
        引用.

        中止检查点(原版 thinking 循环查 is_aborted 同构): 字流块边界
        + 观测等待. ``abort`` 置位后本拍抛错, 桥梁留痕收链. 抛错路径
        上已执行的部分 logos 照样落帧(反幻觉缝合: 身体已经说过的话,
        历史里必须有 —— 原版 executed_logos 同构), 并补发打断收尾令
        (原版 thinking 因 abort 退出即 shell.clear 同构): 未决流处死/
        在跑轨打断/解析器重开 —— 落下的回声带旧拍号, 只入史不
        唤醒下一拍.

        三种拍: 正常拍(脑子产流) / 反射拍(logos 直投, 原版
        command_only + effort='none': 不调脑, 无脑也能跑) / 空拍
        (record_only, 原版 interrupt primitive 的空帧: 只缝留痕落
        percepts, 不思考不出声不等观测 —— "停"的原因由这帧入史,
        无人回应它)."""
        # 裁判地址: 从"谁在问我要思考"里读 —— 裁判每拍都经 think 入口
        # 见到灵魂, 所以不需要启动期自报家门(也就不用一条额外控制边).
        # 拿到后留在手里: 债在拍已收时才到, 那时调用栈早散了.
        who = str(getattr(source, 'id', '') or '')
        if who:
            self._umpire_id = who
        self._abort_event.clear()   # 新拍清旧标志(旧拍已退出, 桥梁同步序保证)
        self._abort_reason = ''
        self._echo_owed = False     # 新拍清注意债(本拍重新累计)
        self._beat += 1             # 拍号(观测门禁)
        self._beat_open = True      # 拍开: 门禁窗口开(收观测/迟到唤醒)
        self._beat_spoke = False    # 本拍还没开口(等首字/等脑子的窗口)
        # percepts 按源 keyed(原版 Moment.percepts 同构); 兼容旧 text
        percepts = {str(k): str(v)
                    for k, v in (data.get('percepts') or {}).items()}
        text = data.get('text')
        if text is not None and not percepts:
            percepts = {'user': str(text)}
        # 各源的元信息(低置信字/响度/是否收尾): 与 percepts 同 key 走 ——
        # 只留真给了属性的源, 正文没到的源不留(渲染时没有正文就没这行)
        attrs = {str(k): dict(v) for k, v in
                 (data.get('percept_attrs') or {}).items()
                 if isinstance(v, dict) and v}
        # 瞬态快照先行入槽(原版 dynamic_context 帧前取最新同构):
        # 同源覆盖, 永不进史 —— 本拍脑子看得到, 历史里不留
        self._moments.set_dynamic(
            {str(k): str(v) for k, v in (data.get('transients') or {}).items()})
        moment = self._moments.new_moment(percepts, attrs)
        # 空拍(原版 interrupt primitive: notify 接管 + effort='none'):
        # 开帧缝留痕即收 —— 停就是停, 不思考不说话
        if data.get('record_only'):
            self._beat_open = False
            return {'obs': []}
        logos = str(data.get('logos') or '')
        self._obs_event.clear()
        say: List[str] = []
        committed = False
        closed = False
        try:
            if logos:
                # 反射拍(原版 command_only + effort='none'): logos 直投
                # 身体不经脑子 —— 反射弧无脑也能跑(急停不需要智慧)
                await self.send(self._shell_id,
                                {'text': logos, 'beat': self._beat})
                self._beat_spoke = True
                await self.publish('text', logos, namespace='moss')
            else:
                if self._brain is None:
                    raise RuntimeError('no brain wired')   # 帧已开: 输入留痕
                async for chunk in self._brain.stream(self._moments.feed(),
                                                      self._system):
                    if self._abort_event.is_set():
                        raise RuntimeError(self._abort_reason or 'aborted')
                    if not chunk:
                        continue
                    say.append(chunk)
                    await self.send(self._shell_id, {'text': chunk})
                    self._beat_spoke = True
                    await self.publish('text', chunk, namespace='moss')
            if self._abort_event.is_set():
                raise RuntimeError(self._abort_reason or 'aborted')
            await self.send(self._shell_id,
                            {'close': True, 'beat': self._beat})
            closed = True
            self._moments.commit_logos(moment, logos if logos else ''.join(say))
            committed = True
            try:
                # 观测等待与中止竞争(原版 wait 观测 vs abort 双结算)
                obs_wait = asyncio.ensure_future(self._obs_event.wait())
                abort_wait = asyncio.ensure_future(self._abort_event.wait())
                done, _ = await asyncio.wait(
                    {obs_wait, abort_wait}, timeout=15,
                    return_when=asyncio.FIRST_COMPLETED)
                if abort_wait in done:
                    raise RuntimeError(self._abort_reason or 'aborted')
                if not done:
                    raise RuntimeError(
                        'turn timeout: shell did not return observations')
            finally:
                # **取消必须收尸**: 只 cancel 不 await, 两个 waiter 会以
                # pending 状态漂到 loop 关停/GC —— 正是 "Task was
                # destroyed but it is pending!"(coro=Event.wait()) 的
                # 来源. gather 等它们真正结束再走, 一拍一清, 不留尾巴
                obs_wait.cancel()
                abort_wait.cancel()
                await asyncio.gather(obs_wait, abort_wait,
                                     return_exceptions=True)
        except Exception:
            if not committed:
                # 反幻觉缝合: 中断拍已执行的部分 logos 落帧(空则无条目)
                self._moments.commit_logos(moment, logos if logos else ''.join(say))
            if not closed:
                # 打断收尾(原版 thinking 退出即 shell.clear 同构): 补发
                # 收尾令给身体 —— 未决流处死/在跑轨打断/解析器重开.
                # 回声带旧拍号, 到达时只入史(门禁), 不冒充下一拍
                try:
                    await self.send(self._shell_id,
                                    {'abort': True, 'beat': self._beat})
                except Exception:
                    _log.exception('shell teardown on abort failed')
            self._beat_open = False   # 拍关: 迟到回声只入史不唤醒
            raise
        self._beat_open = False   # 拍收: 门禁窗口关, 迟到回声只入史
        # echo = 本拍有"需要被注意的结果"(错误 / 声明过的动作完成).
        # 判据是帧上的 owed 戳, 不是 obs 内容 —— "结果存在"不点灯,
        # "有人等这个结果"才点灯
        return {'obs': self._last_obs, 'echo': self._echo_owed}

    @request('abort')
    async def abort(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """中止在途拍(桥梁抢占调用): 置位事件, ``think`` 在下一个检查
        点抛错退出(原版 attention.abort 同构 —— 立即生效, 协作退出)."""
        self._abort_reason = str(data.get('reason', 'aborted'))
        self._abort_event.set()
        return {}

    @request('state')
    async def state(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """在途拍状态(桥梁判"有没有东西可停"用, 只读).

        ``spoke=False`` 且 ``open=True`` = **还在想**: 脑子还没吐第一个
        字(或首字未到), 这一拍没有任何可以停的东西. 桥梁据此不抢占 ——
        抢占的语义是"停它正在说的话", 而不是"杀掉它还没想完的脑子".

        为什么必须有这一刻: 首字延迟可以有八九秒, 用户等不到回音就会
        再补一句"?"—— 那句补话一旦按抢占处理, 原拍在第一个检查点就
        死了, 一个字都没说过, 于是**两条消息一起白等**(实测 2026-10-21
        01:49: 首字 8.3s, "?"+"666" 把整拍打断, 只回了一句"刚才被打断
        了")."""
        return {'beat': self._beat, 'open': self._beat_open,
                'spoke': self._beat_spoke}

    @request('interrupt')
    async def interrupt(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """拍链中断留痕(桥梁调用): reason 攒进 pending 回声 —— 下一帧
        的缝合缝(原版 abort reason 织进下一帧 moment.previous
        .stop_reason 同构), 下一拍的底, 广播 UI."""
        reason = str(data.get('reason', ''))
        self._moments.set_stop_reason(reason)
        await self.publish('interrupt', reason, namespace='moss')
        return {}

    @request('epoch')
    async def epoch(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """Epoch 压缩(原版 new_epoch: compact + recap): 用脑子把窗内
        历史摘要成 recap, 清窗置顶. 原版机制就位但供料未接线
        (with_epoch_recap 全库无注册方), 这里通路接通 —— 触发时机
        留待真被 100 帧打穿(心跳/装配方谁都能调). 空 recap/空窗/
        摘要失败不动作(无事可压/不压坏记忆)."""
        turns = self._moments.history_turns()
        if not self._moments.frames_view() or self._brain is None:
            return {'recap': ''}
        chunks: List[str] = []
        try:
            async for chunk in self._brain.stream(turns, RECAP_SYSTEM):
                chunks.append(chunk)
        except Exception:
            _log.exception('recap generation failed, epoch aborted')
            return {'recap': ''}
        recap = ''.join(chunks).strip()
        if not recap:
            return {'recap': ''}
        self._moments.new_epoch(recap)
        await self.publish('epoch', recap, namespace='moss')
        return {'recap': recap}

    @request('recall')
    async def recall(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """读记忆 + 系统前缀(测试/上层回读用). history 即回合制条目
        (原版 to_history_turns: recap 置顶 + 各帧 + pending 尾缀,
        与脑子每拍吃的持久记忆同源), frames = 帧视图(for_saving,
        丢瞬态), pending = 待缝回声(漂到未归账 + need_observe 火种
        旗), recap = epoch 前情提要, transients = 瞬态槽(各源最新,
        脑子另看), dropped = 出窗帧数, epochs = 压缩次数.

        ``model_input`` = 脑子对 ``feed()`` 的渲染: **脑子自己报**"我
        这一拍读到的原样"(可选能力 ``render_history``, 脑子不提供就
        是空表). 回读走脑子自己的渲染通路 —— 界面显示与喂进去的是同
        一份产物, 不照着块规则再拼一遍(拼一遍就会拼歪, 而拼歪的那份
        还看不出错)."""
        history = self._moments.history_turns()
        render = getattr(self._brain, 'render_history', None)
        return {'history': history,
                'frames': self._moments.frames_view(),
                'pending': self._moments.pending_view(),
                'recap': self._moments.recap,
                'transients': self._moments.transients(),
                'dropped': self._moments.dropped,
                'epochs': self._moments.epochs,
                'model_input': render(self._moments.feed()) if render else [],
                'system': self._system}
