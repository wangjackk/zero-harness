"""Shell —— 身体: 接住灵魂的字, 逐层解释, 查询表投递.

统一指令形态(``docs/orchestration.md``)的根节点. 一条指令只有一种走法:

    字流 → BodyScanner(视野只一层) → 每个标签 =
            create 实例 → body 字节边到边直投 → 闭合发 eof
    → 实例自己解释自己的 body(say 当文本, all 当子指令, ...)

没有"容器走另一条路", 也没有解析器认识的魔法名: ``<all>`` 与 ``<say>``
在第 1 步里完全一样, 差别只在它们各自的类怎么解释 body.

- 标签名 = ``chan:cmd`` 或裸 ``cmd``(默认轨 main); 闭合只认动词名
  (``<main:say>...</say>``).
- 裸文本 = 纯文本旁白(final answer): 给用户的回复, 身体不管 —— 不进轨
  不产帧. 出声说话走 ``say``.
- **投递服务(``route``)只有一份**: root 与容器都用它 —— 词表闸门 / 轨表
  闸门 / 编排参数注入都在这, 容器不各抄一份(错误口径与能力清单因此
  只有一处).
- **回执 = 谁投递谁收**: 实例完成时轨把回执 ``req`` 给发起者(请求并发
  处理, 不被收尾令堵在 mailbox 后面); 容器收齐后向上汇总一层, 最终全部
  落到 root 的账本(一条指令一条账, 扁平).
- **Tracer 账本**(原版 ``MShellEventTracer``/``pop_frame`` 同构, 内嵌非
  routine): 回执入账, 违规帧同入账; 帧边界整批 drain 成 obs 回灌灵魂.
  两个消费时机:
  - **帧边界**(``close``/``abort``): 冲尾 + 处死未决流 + **不等在途**
    (指令漂着 —— 原版 ``wait_tasks(to_be_observed=True)`` 只等观测类),
    账本整批 drain 回灌.
  - **迟到回执**(收尾后到达): 即时转发灵魂, 带旧拍号.
- **注意债(``owed``)**: 错误帧自带 / 动作 meta 的 ``observe`` /
  装配声明的 ``observe`` 名单 / 调用处 ``observe="true"`` 认领, 四者 or
  合并 —— 灵魂见 owed 点灯叫下一拍, 其余回执只记账不驱动.
- ``abort``(打断收尾, 原版 shell.clear 同构): 处死未决流 + 并行逐轨
  打断(未 start 的 destroy 无账, 在跑的 stop 优雅收场照常回执).
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, List, Optional, Set

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine, request

from .body import (
    BodyScanner, ChildBody, ChildClose, ChildOpen, Malformed, decode_body,
)
from .container import CONTAINERS

__all__ = ['Shell']

_log = setup_logger('moss.shell')

# 词法别名: `<_>` 是 `__scope__` 的简写(名字层, 不是解析器 magic)
_ALIAS = {'_': '__scope__'}


class ShellInput(BaseModel):
    """shell 入参(创建时的装配)."""

    verbs: List[str] = Field(
        default_factory=list,
        description='CTML 可调动词表(动作 routine 名, 全局闸门)')

    detail: List[str] = Field(
        default_factory=list,
        description='能力面详列名单(清单里印签名/参数/示例); 名单外只印名字, '
                    '用 help 按需取. 空 = 全详列')

    observe: List[str] = Field(
        default_factory=list,
        description='出厂回执名单(装配声明): 这些词每次调用都要回执, 等同 '
                    'meta.observe, 但由这具身体决定, 不写进通用 routine 的 meta')


class Shell(Routine):
    """身体: 逐层解释 + 查询表投递 + Tracer 总账."""

    name = 'shell'

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '身体: 收灵魂的字(send, mailbox 序), 按"一个标签 = 一个实例"'
            '逐层解释 —— create 实例, body 字节边到边直投, 闭合发 eof, '
            '实例自己解释 body(容器再往下扫一层); 裸文本 = 纯文本旁白'
            '(final answer, 不进轨不产帧); 投递服务 route 只有一份'
            '(词表/轨表闸门 + 编排参数), root 与容器共用; '
            'Tracer 总账: 回执入账, 债(owed) = 错误帧自带 / 动作 meta 的 '
            'observe / 装配的 observe 名单 / 调用处 observe="true"; close 不等在途'
            '(指令漂着, 迟到回执即时转发带旧拍号); 打断 = 生命周期原语'
            '(未 start destroy / 在跑 stop). '
            'chan: 轨道管理. strobe: 同步屏障. registry: 词表读写.'),
        'input_schema': ShellInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = ShellInput.model_validate(kwargs or {})
        self._verbs: Set[str] = set(inp.verbs)      # 全局词表(闸门)
        # 能力面详列名单(装配参数, 只影响清单渲染; 词表闸门只看 _verbs)
        self._detail: List[str] = list(inp.detail)
        # 出厂回执的装配侧声明. 声明"总要被看"是这具身体的判断(它才知道
        # 哪些词属于查询类), 不该让 routine 自己用 meta 记住某具身体的语法 ——
        # 跨身体共用的 routine(routine/doc、read_image)尤其不该.
        self._observe_decl: Set[str] = {v for v in inp.observe if v}
        self._lanes: Dict[str, Any] = {}            # lane 名 → channel handle(显式建)
        self._scan = BodyScanner()                  # 一个流一个扫描器, 收尾重开
        self._cur: Optional[Dict[str, Any]] = None  # 当前打开的实例(深度 1 唯一)
        self._touched: Dict[str, str] = {}          # 本轮触过的 lane → 轨 rid
        # --- Tracer 账本(原版 MShellEventTracer 同构, 全系统一本) ---
        self._trace: List[Dict[str, Any]] = []      # 流水(当前流周期内)
        self._trace_seq: int = 0                    # 单调索引(账不重排)
        self._ghost_id: Optional[str] = None        # 回灌目标(首个收尾令的来源)
        self._last_beat: Any = None                 # 上一拍号(迟到转发用)
        self._stream_closed: bool = True            # 无流在途(迟到判定)
        # 注意债的"认领"侧: 声明过 observe="true" 的动作 instance id
        self._observing: Set[str] = set()
        # 动作出厂声明(原版 ``CommandMeta`` 同构): "总要被看" / "不排队"
        self._registered: Set[str] = set()          # catalog 里注册过的名字
        # 参数白名单 / body 声明(空 = 扫不到 catalog, 闸门整体失效不挡主链路)
        self._params: Dict[str, Set[str]] = {}
        self._body_verbs: Set[str] = set()
        self._observe_verbs, self._call_soon = await self._scan_meta()
        self._kernel: str = ''                      # 代码内核 id(见 _boot_kernel)
        self._stopped = asyncio.Event()             # run 的取消灯(stop 里点)

    async def _scan_meta(self) -> tuple[Set[str], Set[str]]:
        """扫词表里动作的出厂声明: ``observe``(查询类) /
        ``call_soon``(清轨类). 查不到(catalog 未就绪 / 跨 hub 失败)降级
        为空集 —— 两条声明都只是编排参数, 缺了不影响主链路.

        只收"**投递时就该知道**"的属性: 它们改变的是这条指令怎么进轨
        (点不点灯 / 排不排队), 投递方必须当场决定. 至于动作要的**宿主**
        (如 code 的常驻内核), 那是动作自己的依赖, 它按名字自解析
        (``commands/code/code.py::_kernel_rid``) —— 声明一份、注入一份, 两处
        必须成对, 漏一处就是哑巴故障, 不如让需要的一方自己去找.

        顺手记下 catalog 里**注册过**的名字(``_registered``): 启动时要判断
        "本机到底装没装代码内核", 这比猜 create 会抛什么错可靠.
        """
        try:
            catalog = await self.ctx.get_routines()
        except Exception:
            _log.exception('catalog scan failed: meta declarations disabled')
            self._registered = set()
            return set(), set()
        self._apply_meta(catalog)
        return self._observe_verbs, self._call_soon

    def _apply_meta(self, catalog: Any) -> None:
        """按 catalog 重算两个声明集合(装配期与词表热变共用一份算法)."""
        metas = {str(r.get('name')): (r.get('meta') or {}) for r in catalog}
        self._registered = set(metas)
        # 两个来源取并: routine 自己的 meta(它知道自己是不是查询类) 与
        # 装配声明(身体知道哪些词该点灯, 而那个 routine 也许是别的身体也
        # 在用的通用件, meta 里不能带这具身体的语法).
        self._observe_verbs = {v for v in self._verbs
                               if metas.get(v, {}).get('observe')}
        self._observe_verbs |= {v for v in self._observe_decl
                                if v in self._verbs}
        self._call_soon = {v for v in self._verbs
                           if metas.get(v, {}).get('call_soon')}
        # 参数白名单与 body 声明: 投递口据此**当场拒**, 不把判断留给下游.
        # 理由是最坏的失败形态 —— pydantic 默认丢弃多余字段, 于是"参数名写
        # 错"变成一句静音: 不报错、不生效, 模型以为做了, 然后自己编一句结果.
        # 白名单只收 catalog 里注册过的名字(热挂/扫不到的不拦: 不知道就别挡).
        self._params: Dict[str, Set[str]] = {}
        self._body_verbs: Set[str] = set()
        for name, meta in metas.items():
            props = (meta.get('input_schema') or {}).get('properties') or {}
            self._params[name] = set(props)
            if meta.get('body'):
                self._body_verbs.add(name)
        if self._observe_verbs:
            _log.info('observe verbs: %s',
                      ', '.join(sorted(self._observe_verbs)))
        if self._call_soon:
            _log.info('call_soon verbs: %s',
                      ', '.join(sorted(self._call_soon)))

    async def _rescan_meta(self) -> None:
        """词表变了就重扫声明(``registry`` 的 reg/unreg 之后).

        不重扫 = 热挂的 routine 声明静默失效: 名字进了词表、能被叫起, 但
        ``observe`` / ``call_soon`` 还停在装配期快照上 —— 回执永远
        没债、清轨类永远不插队, 谁都不会报错. 扫不到(跨 hub 抖)则**保持
        旧值**: 清空等于把全部出厂声明一起降级, 比不刷更糟.
        """
        try:
            catalog = await self.ctx.get_routines()
        except Exception:
            _log.exception('catalog rescan failed: declarations kept as-is')
            return
        self._apply_meta(catalog)

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """常驻: run 退出 = routine 终止."""
        if 'code' in self._verbs:
            await self._boot_kernel()
        # 等 stop() 点的灯, 不是框架 3s 后的兜底 cancel(见 moss.run 同注)
        await self._stopped.wait()

    async def _boot_kernel(self) -> None:
        """拉起代码执行宿主(常驻, 见 ``code_kernel.py``).

        只在 ``code`` 进了词表时拉起: 没有这个 routine 的身体不必替它留一
        个空内核, 更不该在日志里喊 ERROR(那会把"本机没装 code"说成故障).
        词表里有 ``code`` 但本机没注册 ``code_kernel`` 时同理 —— 那是装配
        少装一个 routine, ``<code>`` 自己会报 "kernel not started", 不必每
        次启动都刷一条 ERROR.

        它是 shell 的子实例: shell 停 → 级联停 → 命名空间一起丢, 不需要
        另外的回收路径. 起不来只记日志, 让 ``<code>`` 自己报错 —— 一个可
        选的执行面不该拖垮整个编排面."""
        if self._registered and 'code_kernel' not in self._registered:
            _log.info('code kernel not installed: <code> will report it')
            return
        try:
            h = await self.create('code_kernel', {})
            await h.start()
            self._kernel = h.id
            _log.info('code kernel up: %s', h.id)
        except Exception:
            _log.exception('code kernel boot failed')

    async def stop(self) -> None:
        self._stopped.set()

    async def on_message(self, source, data: Any) -> None:
        """收灵魂递来的字 / 收尾令. 回执走 ``obs`` req(见下), 不在这条
        mailbox 序里 —— 回执要能插进收尾窗口, 不能被"收尾令"挡住."""
        if not isinstance(data, dict):
            raise TypeError(f'shell input must be dict, got {type(data).__name__}')
        if data.get('close'):
            await self._finish(getattr(source, 'id', ''),
                               data.get('beat'), abort=False)
            return
        if data.get('abort'):
            await self._finish(getattr(source, 'id', ''),
                               data.get('beat'), abort=True)
            return
        self._stream_closed = False   # 字到 = 新流开(迟到窗口关闭)
        await self._feed(str(data.get('text', '')))

    @request('obs')
    async def _obs_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """回执入口(轨/容器的唯一收件方, 原版 tracer ``on_task_done``
        同构): req 并发即时应答 —— 不排 mailbox 序, 帧边界收尾期间照常
        入账(否则错误帧会被推成下一拍的迟到债)."""
        for frame in list(data.get('obs') or []):
            self._record_trace(dict(frame))
        return {'ok': True}

    # --- 投递服务(唯一一份: root 与容器共用) ---

    @request('route')
    async def route(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """投递一条指令: 闸门 + 编排参数 + 入轨. 返回 ``rid``/``chan``.

        容器拿到子标签就往这投 —— 词表与轨表只有一份, 不各抄一遍.
        ``reply`` = 回执收件人(缺省发起方); 容器自己投递时显式传自己的 id.
        """
        cmd = str(data.get('cmd') or '')
        lane = str(data.get('chan') or 'main')
        args = dict(data.get('args') or {})
        reply = str(data.get('reply') or '')
        if not reply:
            reply = getattr(source, 'id', None) or str(source)
        return await self._route(cmd, lane, args, reply)

    async def _route(self, cmd: str, lane: str, args: Dict[str, Any],
                     reply: str) -> Dict[str, Any]:
        verb = _ALIAS.get(cmd, cmd)
        if verb not in self._verbs:
            raise RuntimeError(f'unknown verb: {cmd}')
        h = self._lanes.get(lane)
        if h is None:
            avail = ', '.join(sorted(self._lanes)) or 'none'
            raise RuntimeError(
                f'unknown lane: {lane} (available: {avail}; '
                f'create first: chan_create name="{lane}")')
        rid = h.id
        # 控制属性不下传动作(动作的 pydantic 输入不认识它):
        # 三侧同名 observe —— 标签属性 = 这次我要它的结果(调用期认领注意债,
        # 结果到了点灯); meta 的 observe = 出厂声明(每次都要). 两者 or 合并.
        claim = str(args.pop('observe', '')).strip().lower()
        if claim and claim != 'true':
            raise RuntimeError(
                f'observe takes "true" (or leave it out): got {claim!r}')
        self._check_args(verb, args)
        observe = claim == 'true' or verb in self._observe_verbs
        soon = verb in self._call_soon
        if verb in CONTAINERS:
            # 容器的编排参数(不属模型属性, 前缀 _ 隔离): _chan 是自己的
            # 轨实例 —— 收完 body 由它自己 release 入队(见 container._release)
            args.update({'_shell': self.id, '_reply': reply, '_lane': lane,
                         '_chan': rid})
        elif soon:
            # 清轨类也要宿主上下文(轨表只有 shell 有), 但不要回执地址
            # (回执由 channel 直接发给发起者)
            args.update({'_shell': self.id, '_lane': lane})
        r = await self.req(rid, 'verb',
                           {'name': verb, 'args': args, 'reply': reply,
                            'defer': verb in CONTAINERS and not soon,
                            'call_soon': soon})
        self._touched[lane] = rid
        if observe and r.get('rid'):
            self._observing.add(str(r['rid']))
        return {'rid': str(r['rid']), 'chan': rid,
                'defer': verb in CONTAINERS and not soon,
                # body 到此为止的(叶子): 它的字节可以还原实体; 容器的不行
                # —— 容器的 body 是下一层的标签源码(见 body.decode_body)
                'body_raw': verb in self._body_verbs
                            and verb not in CONTAINERS}

    def _check_args(self, verb: str, args: Dict[str, Any]) -> None:
        """参数名必须在那条 routine 的签名里 —— 写错当场拒.

        只在 catalog 里有这个名字时才判(热挂 / 扫不到的不拦: 不知道就别挡).
        文案给出可用参数名, 不留"猜一猜"的余地 —— 这个错原本的下场是一句
        静音(pydantic 丢掉多余字段), 模型会以为做了, 然后自己编一句结果.
        """
        allowed = self._params.get(verb)
        if allowed is None:
            return
        extra = sorted(k for k in args if k not in allowed)
        if not extra:
            return
        ok = ', '.join(sorted(allowed)) if allowed else '不接受任何参数'
        raise RuntimeError(
            f'{verb} 没有参数 {", ".join(extra)}(它收: {ok}); '
            f'参数名照清单那行的签名写, 内容写 body 不写参数')

    # --- 解释(深度 1) ---

    async def _feed(self, text: str) -> None:
        await self._events(self._scan.feed(text))

    async def _events(self, evs) -> None:
        for ev in evs:
            if isinstance(ev, ChildOpen):
                await self._open(ev)
            elif isinstance(ev, ChildBody):
                if self._cur is not None:
                    await self._deliver_body(ev.text)
            elif isinstance(ev, ChildClose):
                await self._close()
            elif isinstance(ev, Malformed):
                self._frame_error(f'malformed tag: {ev.text}')
            # Text: 裸文本 = 旁白(final answer), 不进轨不产帧

    async def _open(self, ev: ChildOpen) -> None:
        if self._cur is not None:
            # 深度 1 下不可能: 上一个子没闭合, 它的 body 吃掉了一切
            self._frame_error(f'unexpected tag: {ev.name}', ev.cmd)
            return
        try:
            r = await self._route(ev.cmd, ev.chan, dict(ev.kwargs), self.id)
        except Exception as exc:
            self._frame_error(str(exc), ev.cmd)
            return
        verb = _ALIAS.get(ev.cmd, ev.cmd)
        self._cur = {'rid': r['rid'], 'chan': r['chan'], 'name': ev.name,
                     'defer': bool(r.get('defer')),
                     # 叶子 body: 边到边还原实体(模型会在标记语言里转义
                     # `<`); 尾巴扣住半截实体, 等下一片
                     'raw': bool(r.get('body_raw')), 'tail': '',
                     # 不收 body 的名字, body 字节到这里就断(见 _deliver_body);
                     # catalog 不认识的名字不拦(不知道就别挡)
                     'body_ok': verb in self._body_verbs
                                or verb not in self._params,
                     'body_err': False}

    async def _deliver_body(self, text: str) -> None:
        """body 字节投给当前实例; 不收 body 的当场报一串, 之后丢掉.

        为什么不在开标签时就拒: ``<x/>`` 与 ``<x></x>`` 是同一条路(见
        ``body.py``), 开标签时刻还看不出有没有内容 —— 只有真的来了字节,
        才谈得上"内容放错地方". 实例已经建了、也可能已经在跑, 所以这里不是
        撤回而是**说清**: 这段没人读. 一句话的错比一次静音便宜.
        """
        cur = self._cur
        if cur is None:
            return
        if not cur['body_ok']:
            if not cur['body_err']:
                cur['body_err'] = True
                self._frame_error(
                    f'{cur["name"]} 不收 body: 内容只能写进参数, 这段没人读',
                    cur['name'])
            return
        if cur.get('raw'):
            text, cur['tail'] = decode_body(text, cur['tail'])
            if not text:
                return                   # 整片都是半截实体, 等下一片
        await self.send(cur['rid'], {'text': text})

    async def _close(self) -> None:
        cur, self._cur = self._cur, None
        if cur is None:
            return
        if cur.get('tail'):              # 流尾还扣着半截实体: 照原样交出去
            await self.send(cur['rid'], {'text': cur['tail']})
        # 先发 eof(body 与它同一 mailbox, 天然排在前面), 再握手等它入队:
        # 延迟入队的容器收完 body 才进队列, 不握手就继续投递会让后面的
        # 同轨标签抢在容器前面 —— 屏障语义当场失效(实测 5 组错 4 组).
        await self.send(cur['rid'], {'eof': True})
        if cur['defer']:
            try:
                await self.req(cur['rid'], 'ready', {})
            except Exception as exc:
                # 内部时序故障(不是模型说错了话): 不产 error 帧,
                # 只记下来 —— 闹到模型面前它也修不了.
                _log.warning('container never queued: %s (%s)', cur['name'], exc)

    # --- 账本 ---

    def _frame_error(self, text: str, verb: str = '') -> None:
        """违规/失败收成错误帧, **走报账同一条路**.

        协议违规是模型自己说错了的话(话没落地 = 契约未履行), 与远端
        动作失败同构 —— 所以不另开一条入账小路: 盖上 ``kind='error'``
        交给 ``_record_trace``, 债由那唯一一处判定(错误自带注意债,
        下一拍立刻矫正). 顺带治好一个潜在洞: 收尾后才报的错不再是
        堆在账本里无人取的死帧, 而是按迟到回执即时转发.
        """
        f: Dict[str, Any] = {'kind': 'error', 'text': text}
        if verb:
            f['verb'] = verb
        self._record_trace(f)

    def _record_trace(self, frame: Dict[str, Any]) -> None:
        """报账入账 + 盖"注意债"戳(``owed``) —— 点灯判据的唯一产地.

        四条债源同构(本质都是"需要被注意的结果"):
        - **错误帧自带债**: 裸异常一律算契约未履行;
        - **调用处认领**: ``observe="true"`` 的动作(一次性);
        - **出厂声明**: 动作 meta 的 ``observe``(查询类);
        - **结果自报**: 动作返回 ``{'observe': True}`` —— 原版
          ``CommandTaskResult(observe=True)``, 容器超时与 loop 收尾用它
          ("跑完了/超时了"是模型必须知道的事实).

        流已收尾(或从未开) → 迟到: 即时转发灵魂带旧拍号; 否则攒账本等
        帧边界整批 drain.
        """
        rid = frame.get('rid')
        claimed = rid is not None and rid in self._observing
        if claimed:
            self._observing.discard(rid)
        result = frame.get('result')
        self_reported = isinstance(result, dict) and bool(result.get('observe'))
        frame['owed'] = bool(frame.get('kind') == 'error'
                             or claimed or self_reported)
        if self._stream_closed and self._ghost_id is not None:
            asyncio.ensure_future(
                self.send(self._ghost_id,
                          {'obs': [frame], 'beat': self._last_beat}))
            return
        frame['idx'] = self._trace_seq
        self._trace_seq += 1
        self._trace.append(frame)

    async def _finish(self, reply_to: str, beat: Any = None,
                      abort: bool = False) -> None:
        """流收尾 = 帧边界: 冲尾 + 查未闭合 + 处死未决流, abort 则逐轨
        打断; **不等在途**(指令漂着), 账本整批 drain 回灌; 之后到达的
        回执即迟到(即时转发带旧拍号). 回灌带拍号."""
        self._ghost_id = reply_to or self._ghost_id
        evs = self._scan.close()
        if self._scan.unclosed:
            self._frame_error('unterminated tag in stream')
        self._scan = BodyScanner()   # 一轮一个流, 下轮重开
        await self._events(evs)
        if self._cur is not None:
            # 未决实例随流作废: 定点处死走轨(未 start destroy 无账,
            # 在跑 stop 优雅截断照常回执). abort 的全轨打断覆盖它.
            if not abort:
                try:
                    await self.req(self._cur['chan'], 'kill',
                                   {'rid': self._cur['rid']})
                except Exception as exc:
                    self._frame_error(f'stream kill failed: {exc}')
            self._cur = None
        if abort:
            # 并行打断: 每条轨是独立实例, 请求互不依赖 —— 串行时一轨
            # 卡住后面几轨连同整条收尾一起卡.
            rids = list(dict.fromkeys(self._touched.values()))
            if rids:
                results = await asyncio.gather(
                    *(self.req(rid, 'abort', {}) for rid in rids),
                    return_exceptions=True)
                for exc in results:
                    if isinstance(exc, BaseException):
                        self._frame_error(f'lane abort failed: {exc}')
        self._touched.clear()
        obs, self._trace = self._trace, []
        self._last_beat = beat
        self._stream_closed = True
        if reply_to:
            await self.send(reply_to, {'obs': [self._strip(f) for f in obs],
                                       'beat': beat})

    @staticmethod
    def _strip(rec: Dict[str, Any]) -> Dict[str, Any]:
        """出口剥离 idx(内部记账用, 调用方无需看见)."""
        return {k: v for k, v in rec.items() if k != 'idx'}

    # --- 轨表 ---

    async def _lane(self, lane: str):
        """lane 名 → channel handle(仅 chan 控制面调用: 建轨的唯一入口).

        存 handle 而不是 id: 删除轨要"谁启动谁停止"(``h.stop()``), 只留 id
        就得另找一条路去够实例. 一处存对, 三处(投递/查询/回收)都够得到.
        """
        h = self._lanes.get(lane)
        if h is not None:
            return h
        h = await self.create('channel', {'chan': lane})
        await h.start()
        self._lanes[lane] = h
        return h

    # --- 控制面(不入账) ---

    @request('chan')
    async def chan(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """轨道管理: ``create`` 建轨(幂等) / ``status`` 看轨(带运行态) /
        ``delete`` 回收空轨. 清轨走另一条控制面 ``clear``(下面), 因为那条
        带着"清道夫不清自己"的豁免, 不复刻一份.

        分族判据是对象: 本族每个动作都针对**一条具体的轨**; ``interrupt``
        (全停) 因此不在族里.
        """
        op = str(data.get('op', ''))
        name = str(data.get('name', ''))
        if op == 'create':
            if not name:
                raise RuntimeError('chan: missing name')
            await self._lane(name)
            return {'chan': name, 'lanes': sorted(self._lanes)}
        if op == 'status':
            names = [name] if name else sorted(self._lanes)
            missing = [n for n in names if n not in self._lanes]
            if missing:
                raise RuntimeError(
                    f'chan: unknown lane: {", ".join(missing)} '
                    f'(available: {", ".join(sorted(self._lanes)) or "none"})')
            rows = await asyncio.gather(
                *(self.req(self._lanes[n].id, 'status', {}) for n in names),
                return_exceptions=True)
            return {'lanes': [r if not isinstance(r, BaseException)
                              else {'chan': n, 'error': str(r)}
                              for n, r in zip(names, rows)]}
        if op == 'delete':
            if not name:
                raise RuntimeError('chan: missing name')
            if name == 'main':
                raise RuntimeError('chan: main lane cannot be deleted')
            h = self._lanes.get(name)
            if h is None:
                raise RuntimeError(f'chan: unknown lane: {name}')
            st = await self.req(h.id, 'status', {})
            busy = st.get('queued') or st.get('deferred') or st.get('soon') \
                or st.get('running')
            if busy:
                # 队列非空时 stop() 会卡在 _jobs.join() 上等活跑完 ——
                # 那叫"等", 不叫"回收". 让调用方先 clear, 它才知道代价.
                raise RuntimeError(
                    f'chan: lane still busy: {name} '
                    f'(queued={st.get("queued")}, deferred='
                    f'{st.get("deferred")}, soon={st.get("soon")}, running='
                    f'{(st.get("running") or {}).get("verb")}); '
                    f'clear it first: chan_clear name="{name}"')
            await h.stop()          # 已 started: 谁启动谁停止
            del self._lanes[name]
            return {'deleted': name, 'lanes': sorted(self._lanes)}
        raise RuntimeError(f'chan: unknown op: {op}')

    @request('registry')
    async def registry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """全局词表维护: ``op='list'`` 读 / ``op='reg'`` 增 / ``op='unreg'`` 摘.

        增删之后**重扫 meta**: 出厂声明(`observe` / `call_soon`)是按
        词表算的, 不跟着变就会"名字能叫、声明不生效"—— 静默失效.
        """
        op = str(data.get('op', ''))
        name = str(data.get('routine', ''))
        if op == 'list':
            pass   # 只读: 返回快照(prompt/capability 的数据源)
        elif op == 'reg':
            if not name:
                raise RuntimeError('registry: missing routine')
            self._verbs.add(name)
        elif op == 'unreg':
            self._verbs.discard(name)
        else:
            raise RuntimeError(f'registry: unknown op: {op}')
        if op in ('reg', 'unreg'):
            await self._rescan_meta()
        # detail 只是渲染参数(装配定, 热注册不改它): 快照一并回给能力面
        return {'verbs': sorted(self._verbs), 'detail': list(self._detail)}

    @request('clear')
    async def clear(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """清轨控制面(``clear``/``interrupt`` 指令的宿主侧).

        ``chans`` 空 = 全部轨. 逐轨并行 ``abort``(未 start 的撤销无账,
        在跑的优雅停止照常回执). 未知轨名按错误帧报 —— 与"词表外动词"
        同一口径, 模型写错轨名要看见.
        """
        names = [str(c) for c in (data.get('chans') or [])]
        missing = [n for n in names if n not in self._lanes]
        for n in missing:
            self._frame_error(f'unknown lane: {n}')
        rids = ([self._lanes[n].id for n in names if n in self._lanes]
                if names else [h.id for h in self._lanes.values()])
        # 豁免发起者自己(清道夫不清自己): 它是 call_soon 实例, 在跑的
        # 抢占型里找得到它 —— 由轨显式跳过, 让它跑完并正常回报.
        caller = getattr(source, 'id', None) or str(source)
        results = await asyncio.gather(
            *(self.req(rid, 'abort', {'except': [caller]}) for rid in rids),
            return_exceptions=True)
        names_of = {h.id: n for n, h in self._lanes.items()}
        cleared: List[str] = []
        for rid, res in zip(rids, results):
            name = names_of.get(rid, rid)
            if isinstance(res, BaseException):
                self._frame_error(f'clear {name} failed: {res}')
            else:
                cleared.append(name)
        return {'cleared': cleared}

    @request('strobe')
    async def strobe(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """同步屏障: req 严格串行于 mailbox 消费者."""
        return {'frames': len(self._trace)}
