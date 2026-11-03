"""CodeKernel —— 代码格子的执行宿主: 一份命名空间, 一格一格跑.

``<code>`` 只收 body 与报账, 源码交到这里执行. 分开的理由只有一条:
**一份活着的 globals**. 一个标签 = 一个实例, 实例里的 ns 随实例消失, 等于
每个 cell 都重启一次解释器; 而 IPython 的手感有一半来自"上一格定义的变量
下一格还在". 所以 ns 必须活得比任何单个 code 实例久, 又不能是模块级全局
(SDK 铁律, 且多会话串味) —— 归宿就是一个常驻实例.

**串行靠锁兑现, 不靠概率**: ``@request`` 的 handler 是**并发**执行的(容器
收帧正是靠这一点), 而两个 cell 同时写同一份 ns 会互相覆盖(``x = x + 1``
丢更新). 所以这里有一把锁, 跨 lane 的 code 也排成一列.

**子实例的父 = 建它的实例**: 框架只认这一条边(父停子停), 不提供"替调用方
代建"的口子. 所以格里的 ``await call(...)`` 建出来的动作挂在**内核**名下,
而不是挂在发起那一格的 code 上 ---- 内核比两者活得久, 框架不会在格收尾时
把它们一并收走. 谁要的这份账由内核自己记(``_owner_of`` / ``_reap``): 格收尾
收它名下的, ``spawn`` 起的后台任务收场时收它名下的. 少记一笔, 那一批实例
就一直活到内核停.

消息面:
    ``exec``    ``{cell, src}`` → ``{ok, stdout, result, bg?, bg_failed?}``
    ``cancel``  ``{cell}``      → 取消该格(未进锁的先记下, 进锁即放弃)
"""
from __future__ import annotations

import ast
import asyncio
import contextvars
import inspect
import io
import json
import traceback
from typing import Any, ClassVar, Dict, List, Optional, Set

from routine.logger import setup_logger
from routine.handle import RoutineHandle
from routine.routine import Routine, request

from .container import CONTAINERS

__all__ = ['CodeKernel', 'CallError']

_log = setup_logger('moss.code')

_FILE = '<code>'          # 编译文件名(报错行号只认它)
_OUT = '__result__'       # 末表达式的结果落点(模型不可见)
_STDOUT_CAP = 8000        # stdout 截断(obs 帧走 pubsub, 不搬大件)
_REPR_CAP = 2000          # 不可 JSON 化的结果转 repr 后的截断
_ECHO_CAP = 200           # 语法错回显的行宽(超长只留出错点附近)
# 也在本内核上执行的 routine: 在格里叫它们 = 环等自己(见 _create 的闸门).
# 判据是"执行宿主是谁", 所以将来若有第二个走内核的 routine, 加进这里.
_SAME_KERNEL = frozenset({'code'})


class CallError(RuntimeError):
    """动作失败: 抛给代码判断, 不处理就冒到 cell 顶层转成失败结果."""

    def __init__(self, verb: str, text: str) -> None:
        super().__init__(f'{verb}: {text}')
        self.verb = verb
        self.text = text


def _attach_body(h: RoutineHandle, verb: str, accepts: bool) -> None:
    """在框架句柄上挂 body 的两个动词 ---- 这是 moss 这侧的事.

    body 协议(``{'text'}`` 增量 / ``{'eof'}`` 收尾)是 moss 的, 框架的
    handle 只认 ``send(任意数据)``, 所以动词挂在本侧建出来的实例上,
    框架不因此多懂一个概念. 不收 body 的 routine 收到 body 会静默丢掉
    ---- 那是最坏的失败, 所以直接报错.
    """
    if not accepts:
        async def refuse(_text: Any = None) -> None:
            raise CallError(verb, 'takes no body (pass it as params)')

        h.feed = refuse          # type: ignore[attr-defined]
        h.eof = refuse           # type: ignore[attr-defined]
        return

    async def feed(text: Any) -> None:
        """递一段 body(与标签中间那段同一条消息面)."""
        await h.send({'text': str(text)})

    async def eof() -> None:
        """收流: body 到此为止(等价于闭标签)."""
        await h.send({'eof': True})

    h.feed = feed                # type: ignore[attr-defined]
    h.eof = eof                  # type: ignore[attr-defined]


async def _feed(h: 'RoutineHandle', body: Any) -> None:
    """把 body 按协议递完并收流.

    字符串一段; 序列/同步生成器逐段递; **异步生成器边产边递**(真流式);
    不可迭代的单个值当一段. 收尾一律 ``eof``.
    """
    if isinstance(body, str):
        await h.feed(body)
    elif isinstance(body, (bytes, bytearray)):
        await h.feed(body.decode())
    elif hasattr(body, '__aiter__'):
        async for chunk in body:
            await h.feed(chunk)
    else:
        try:
            it = iter(body)
        except TypeError:
            await h.feed(body)
        else:
            for chunk in it:
                await h.feed(chunk)
    await h.eof()


def _compile(src: str) -> Any:
    """编译 cell: 末表达式取值为结果 + 顶层 await 放行.

    末表达式改成 ``__result__ = <expr>`` 是为了复刻 REPL 直觉(最后一行
    写个表达式就能拿到结果), 不额外给模型一套返回语法.
    """
    tree = ast.parse(src, _FILE, 'exec')
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body.pop()
        node = ast.Assign(
            targets=[ast.Name(id=_OUT, ctx=ast.Store())], value=last.value)
        ast.copy_location(node, last)
        tree.body.append(node)
    ast.fix_missing_locations(tree)
    return compile(tree, _FILE, 'exec',
                   flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


def _clip(text: str, cap: int = _STDOUT_CAP) -> str:
    if len(text) <= cap:
        return text
    return f'{text[:cap]}... (+{len(text) - cap} chars)'


def _jsonable(value: Any) -> Any:
    """obs 帧走 pubsub(JSON): 能序列化就原样, 否则 repr 截断."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        text = repr(value)
        return text if len(text) <= _REPR_CAP else f'{text[:_REPR_CAP]}...'


def _echo_line(text: str, col: int) -> tuple:
    """出错那一行 + 指针列. 超长行只留出错点附近(带省略号, 列号跟着挪)."""
    line = str(text or '').rstrip('\n').rstrip('\r')
    if len(line) <= _ECHO_CAP:
        return line, col
    start = max(0, min(col - 1 - _ECHO_CAP // 2, len(line) - _ECHO_CAP))
    seg = line[start:start + _ECHO_CAP]
    head = '…' if start else ''
    tail = '…' if start + _ECHO_CAP < len(line) else ''
    return f'{head}{seg}{tail}', col - start + len(head)


def _syntax_error(exc: SyntaxError) -> str:
    """语法错回执: CPython 的原话 + **出错那一行与指针**.

    多回这一行不为好看: 模型看不见自己发出去的 body(body 不进回执), 只给它
    行号, 它就只能凭记忆重写自己刚写的东西 —— 于是"我是不是打了个全角
    逗号"变成猜谜, 而猜错的方向永远是怀疑宿主改过它的代码. 把原始行原样
    贴回去, 这种争论当场结束: 有全角就有, 没有就没有, 谁都不用信谁.
    """
    head = f'SyntaxError: {exc.msg or "invalid syntax"} (line {exc.lineno})'
    if not exc.text:
        return head
    try:
        col = max(1, int(exc.offset or 1))
    except (TypeError, ValueError):
        col = 1
    line, col = _echo_line(exc.text, col)
    return f'{head}\n  {line}\n  {" " * (col - 1)}^'


class CodeKernel(Routine):
    """代码内核: 常驻, 持有跨格命名空间, 顺序执行每格源码."""

    name = 'code_kernel'      # 不入词表: 模型看不见执行宿主

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._lock = asyncio.Lock()                      # 一格一格跑
        self._ns: Dict[str, Any] = {'__name__': '__code__'}   # 跨格保留
        # "这条孩子是谁要的"—— 父管"跟着谁死",发起者管"取消谁时一起收".
        # 框架只给一种父(建它的实例), 所以"谁要的"这份账只有内核自己记:
        # 任务里 call 出来的实例随任务收, 格里的随格收(见 _reap).
        self._owner_of: contextvars.ContextVar[str] = contextvars.ContextVar(
            'moss_owner_of', default='')
        self._kids: Dict[str, List[Any]] = {}            # 发起者 → 建过的句柄
        self._task_owner: Dict[asyncio.Task, str] = {}   # 任务 → 它的发起者令牌
        self._owner_seq = 0
        self._buf = io.StringIO()                        # 正在跑的格的 stdout
        self._cells: Dict[str, asyncio.Task] = {}        # cell → task
        self._cancelled: Set[str] = set()                # 已被取消的格
        self._bg: Dict[asyncio.Task, str] = {}           # 后台任务 → 在等什么
        self._bg_fails: List[Dict[str, Any]] = []        # 死讯(下一格报)
        self._done = asyncio.Event()                     # 内核退出
        self._task: Optional[asyncio.Task] = None        # 当前格的 task
        # 谁声明了 body(meta['body']): 代码面据此判断 body 传对没有
        self._no_body: Set[str] = await self._scan_no_body()
        # 参数白名单: 与 CTML 面同一道闸(见 shell._check_args) —— 代码里
        # 写错关键字同样是"以为叫了、其实静默失效", 而 Python 面还能当场抛.
        self._params: Dict[str, Set[str]] = await self._scan_params()

    async def _scan_no_body(self) -> Set[str]:
        """词表里"注册了、但没声明 body"的名字 —— `call` 喂 body 给它们就是错.

        只收 catalog 里确实有的名字: 运行期热挂的动词扫不到, 于是对它不加
        闸(不知道就不拦). catalog 未就绪则空集, 闸门整个失效, 不挡主链路.
        """
        try:
            catalog = await self.ctx.get_routines()
        except Exception:
            _log.debug('body scan failed: body check disabled', exc_info=True)
            return set()
        return {str(r.get('name') or '') for r in catalog
                if not (r.get('meta') or {}).get('body')}

    async def _scan_params(self) -> Dict[str, Set[str]]:
        """名字 → 它能收的参数名(来自 input_schema.properties).

        catalog 未就绪时返回空表 —— ``_check_kwargs`` 对查不到的名字一律放行,
        闸门失效也不挡主链路(同 ``_scan_no_body``).
        """
        try:
            catalog = await self.ctx.get_routines()
        except Exception:
            _log.debug('param scan failed: arg check disabled', exc_info=True)
            return {}
        out: Dict[str, Set[str]] = {}
        for r in catalog:
            name = str(r.get('name') or '')
            meta = r.get('meta') or {}
            props = (meta.get('input_schema') or {}).get('properties') or {}
            out[name] = set(props)
        return out

    async def run(self, kwargs: Dict[str, Any]) -> None:
        await self._done.wait()          # 常驻: run 退出 = 内核终止

    async def stop(self) -> None:
        """收摊: 当前格 + 所有后台任务一起取消.

        后台任务归**模型自己**管(``t.cancel()``), 内核只在"这个宿主正在
        消失"时兜底 —— 否则它会活过自己的内核, 在某个随机时刻再投一次
        notify, 而那时没有任何人知道它属于谁.
        """
        self._done.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        for task in list(self._bg):
            task.cancel()

    # --- 对外两个入口 ---

    @request('exec')
    async def _exec_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        cell = str(data.get('cell') or '')
        src = str(data.get('src') or '')
        async with self._lock:
            if cell in self._cancelled:
                # 取消先到(它的 exec 还在路上): 放弃执行
                self._cancelled.discard(cell)
                return {'ok': True, 'stopped': True, 'stdout': ''}
            if self._done.is_set():
                return {'ok': False, 'error': 'code kernel stopped',
                        'stdout': '', 'observe': True}
            try:
                code = _compile(src)
            except SyntaxError as exc:
                # 语法错: 不执行, 但必须让模型看见(它自己写的代码) —— 连
                # 出错那一行一起还给它, 别让它凭记忆猜自己写了什么.
                return {'ok': False, 'error': _syntax_error(exc),
                        'stdout': '', 'observe': True}
            # 格内: 账记这一格(格收尾时按账收, 见 finally).
            o_token = self._owner_of.set(cell)
            try:
                self._buf = io.StringIO()
                self._ns.pop(_OUT, None)   # 上一格的结果不许冒充这一格的
                self._ns.update(self._host_names())
                task = asyncio.create_task(self._exec(code, self._ns))
                self._cells[cell] = task
                self._task = task
                await task
            except asyncio.CancelledError:
                if cell in self._cancelled or self._done.is_set():
                    return {'ok': True, 'stopped': True, 'stdout': self._out()}
                raise                # 外层取消(框架收摊), 不由本实例吞
            except (SystemExit, KeyboardInterrupt) as exc:
                # 不冒到 runner: cell 的退出只是这一格的事
                return self._fail(exc)
            except Exception as exc:
                return self._fail(exc)
            finally:
                self._cells.pop(cell, None)
                self._cancelled.discard(cell)
                self._owner_of.reset(o_token)
                # 子都挂内核名下(框架只给这一种父), 框架不会替格收 ——
                # 格的账得自己清. 少了这一步, 每跑一格就留一批没人管的实例.
                if self._kids.get(cell):
                    try:
                        asyncio.get_running_loop().create_task(self._reap(cell))
                    except RuntimeError:   # loop 已关: 内核收摊时框架级联收
                        self._kids.pop(cell, None)
            return self._ok(self._ns)

    @request('cancel')
    async def _cancel_entry(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """取消一格: 在跑的打断执行体, 还没进锁的记下来让 exec 自行放弃.

        不能拿锁 —— 正跑的那格握着锁, 等锁就是等它自然结束.
        """
        cell = str(data.get('cell') or '')
        task = self._cells.get(cell)
        self._cancelled.add(cell)      # 覆盖"cancel 先到、exec 后到"
        if task is not None and not task.done():
            task.cancel()
        return {'cancelled': task is not None}

    # --- 代码面(注入进 eval 的名字空间) ---

    async def _exec(self, code: Any, ns: Dict[str, Any]) -> None:
        r = eval(code, ns)
        if inspect.isawaitable(r):
            await r

    def _host_names(self) -> Dict[str, Any]:
        """每格刷新: 宿主函数绑定的是**当前**这一格(父节点也跟着对)."""
        return {'__name__': '__code__',
                'call': self._call, 'create': self._create,
                'spawn': self._spawn,
                'publish': self._publish, 'asyncio': asyncio,
                'print': self._print}

    def _spawn(self, coro: Any, *, name: str = '',
               desc: str = '') -> asyncio.Task:
        """``t = spawn(等某件外界的协程, name='watch-config',
        desc='等 config.json 改动')``: 起一条**跨格存活**的后台任务.

        ``name`` 是对号用的短标识(缺省取协程名), ``desc`` 是"在等什么" ——
        两个都出现在回执里, 因为下一拍的你只看得到那一行: 只给名字, 它得
        翻回这格源码才知道 ``watcher`` 是什么; 只给描述, 它没法在两条任务
        之间对号. 名字重复自动加 ``#2``(循环里起一批同名任务很自然, 但对不
        了号就报不了账).

        和 ``asyncio.create_task`` 的区别只有三条, 都不改语义:
        1. 内核记一笔 —— 下一格的回执会报"运行中 N 条", 于是你记得自己有
           什么在等; 内核收摊时兜底取消, 不留幽灵;
        2. 已经是 Task/Future 的直接认领(重复 spawn 同一个不会起两份);
        3. 死了有死讯(见 ``_bg_done``) —— 见下一格, 不是见 stderr.

        **停不停由你决定**: 返回的就是标准 Task, ``t.cancel()`` 随时可用,
        变量跨格保留所以下一格还找得到它.

        **它的父亲是本类, 不是那一格**: 这条任务的整个存在意义就是活过发起
        它的 ``exec``, 而格一收尾节点就摘了 —— 挂格名下等于让子比父长寿, 它
        醒来 ``call`` 时只会拿到 ``parent routine N not found``. 所以这里在
        **启动前把上下文里的父改成本类**: 它自己派出的子实例一律挂内核名下,
        与它同生共死(内核停 → 一起收). 已经是 Task/Future 的直接认领那种改
        不了上下文, 只能是它原本的父 —— 这是唯一的例外, 别当成默认行为.

        **它要的东西跟着它走**: 这条任务 ``call``/``create`` 出来的实例记在
        它自己的账上, 任务收场(取消 / 抛错 / 自然结束)一律收掉 —— 父是内核
        意味着框架不会替它收, 不记这笔账就留下一堆"没人要却还在跑"的实例.
        """
        owner = self._new_owner()
        if isinstance(coro, asyncio.Future):
            task = coro
        else:
            # copy 一份再改: 直接 set 会污染格的上下文(set 与 reset 之间格
            # 自己 call 记错账), 复制后格那份原封不动.
            ctx = contextvars.copy_context()
            ctx.run(self._owner_of.set, owner)
            task = asyncio.create_task(
                coro, name=name or getattr(coro, '__qualname__', '') or 'bg',
                context=ctx)
        base = task.get_name()
        taken = {t.get_name() for t in self._bg}
        if base in taken:
            n = 2
            while f'{base}#{n}' in taken:
                n += 1
            task.set_name(f'{base}#{n}')
        self._bg[task] = str(desc or '').strip()
        self._task_owner[task] = owner
        task.add_done_callback(self._bg_done)
        return task

    def _new_owner(self) -> str:
        """一条任务的令牌: 与格 id 不撞(格是数字串), 也不能用 task 自身
        —— 起协程之前它还不存在."""
        self._owner_seq += 1
        return f'bg:{self._owner_seq}'

    async def _reap(self, owner: str) -> None:
        """收掉发起者名下的子实例: 任务都不要了, 它启动的事也不该留.

        用 ``stop`` 不用 ``destroy``: destroy 只收 **created 态(还没 start)**
        的子实例, 已经跑起来的会被 kernel 拒(rejected op=destroy) —— 而任务
        里 ``call`` 出来的实例基本都已经 start 了. stop 让它走完自己的停路径
        (say 截声 / hold 放行), body 结束再照常摘节点.
        """
        for h in self._kids.pop(owner, []):
            try:
                await h.stop()
            except Exception:
                _log.warning('reap %s failed', h, exc_info=True)

    def _bg_done(self, task: asyncio.Task) -> None:
        """后台任务收场: 出队, 收掉它名下的子实例; 抛错的话把死讯留给下一格.

        异常不在这里抛(抛给谁看?)也不许静默 —— asyncio 的
        "Task exception was never retrieved" 只进 stderr, 模型永远看不到.
        死讯也带 desc: 只有名字时它得翻回起任务那一格才知道是什么失败了.

        ``_reap`` 是协程而这里是同步回调(done callback 不能 await), 所以
        起一条收尸任务 —— 收尸本身失败不该影响报账, 已在 ``_reap`` 里吞掉.
        """
        desc = self._bg.pop(task, '')
        owner = self._task_owner.pop(task, '')
        if owner and owner in self._kids:
            try:
                asyncio.get_running_loop().create_task(self._reap(owner))
            except RuntimeError:      # loop 已关(内核收摊末端): 框架会级联收
                self._kids.pop(owner, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.warning('code bg task failed: %s', exc, exc_info=exc)
            row: Dict[str, Any] = {'name': task.get_name(),
                                   'error': f'{type(exc).__name__}: {exc}'}
            if desc:
                row['desc'] = desc
            self._bg_fails.append(row)

    async def _create(self, name: str, /, **kwargs: Any) -> RoutineHandle:
        """``h = await create('say')``: 建子实例(父是本内核), 拿句柄自己驱动.

        和 ``call`` 的关系: call = create → start → 递 body → 等结果.
        要自己在中间插手(边说边改、说到一半收口)就直接用这个, 句子由
        句柄的 start/feed/eof/stop 说.
        """
        verb = str(name)
        if verb in CONTAINERS:
            # 容器是轨上的构造(子要投给 route, 锚在 lane 上), 而代码里
            # 没有轨道 —— 在这里它一定跑不起来, 不如当场说清.
            raise CallError(
                verb, 'container needs a lane; use asyncio.gather in code')
        if verb in _SAME_KERNEL:
            # 同一个内核 = 同一把锁 + 同一份命名空间: 外层格握着锁等内层,
            # 内层等锁 —— 环等, 一直等到外层超时, 而回执只说"exec timeout",
            # 看不出是自己等自己(600s 的哑巴故障). 源码直接写在格子里,
            # 与嵌套一格完全同义, 所以这里拒掉并指向写法.
            raise CallError(
                verb, 'code cannot nest (one cell at a time, one namespace); '
                      'write the source inline')
        self._check_kwargs(verb, kwargs)
        try:
            h = await self.create(verb, dict(kwargs))
        except asyncio.CancelledError:
            raise                # 打断要往上冒, 不能被包成动作失败
        except Exception as exc:
            raise CallError(verb, str(exc)) from exc
        _attach_body(h, verb, verb not in self._no_body)
        owner = self._owner_of.get()
        if owner:            # 记账: 发起者取消时按这份账收(见 _reap)
            self._kids.setdefault(owner, []).append(h)
        return h

    def _check_kwargs(self, verb: str, kwargs: Dict[str, Any]) -> None:
        """关键字必须在那条 routine 的签名里 —— 写错当场抛给这一格.

        不判的代价: pydantic 丢掉多余字段, 调用"成功"、效果为零, 而代码里
        下一行还在用它的结果. 查不到名字(热挂 / catalog 未就绪)就放行.
        """
        allowed = self._params.get(verb)
        if allowed is None:
            return
        extra = sorted(k for k in kwargs if k not in allowed)
        if not extra:
            return
        ok = ', '.join(sorted(allowed)) if allowed else '不接受任何参数'
        raise CallError(
            verb, f'no such param {", ".join(extra)} (takes: {ok})')

    async def _call(self, name: str, /, *, body: Any = None,
                    **kwargs: Any) -> Any:
        """``await call('x', duration=1)``: 建子实例并等它返回.

        ``body`` 与标签中间那段同义(字符串一段 / 序列多段 / 异步生成器边产边递):
        给了就按 body 协议递过去再收流. 于是有 body 的 routine 不需要自己的
        调用函数 —— ``await call('say', body='你好')`` 就是
        ``<main:say>你好</main:say>``.

        只有声明了 ``meta['body']`` 的 routine 收 body; 给纯参数 routine 传
        body 当场报错(静默丢掉内容是最坏的失败).
        """
        if body is not None and str(name) in self._no_body:
            raise CallError(str(name), 'takes no body (pass it as params)')
        h = await self._create(name, **kwargs)
        try:
            await h.start()
            if body is not None:
                await _feed(h, body)
            return await h
        except CallError:
            raise                # 建实例阶段已经分好类, 不再包一层
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise CallError(str(name), str(exc)) from exc

    async def _publish(self, topic: str, data: Any = None, *,
                       namespace: str = '') -> None:
        """旁路通知(渲染面/观测面): 不进账, 不占道, 失败不拦代码."""
        try:
            await self.publish(str(topic), data, namespace=namespace)
        except Exception:
            _log.debug('code publish failed: %s', topic, exc_info=True)

    def _print(self, *args: Any, **kw: Any) -> None:
        """本格的 stdout: 收进缓冲, 随回执一并回灌(不动全局 stdout)."""
        kw['file'] = self._buf
        print(*args, **kw)

    # --- 结果 ---

    def _out(self) -> str:
        return _clip(self._buf.getvalue())

    def _bg_report(self, res: Dict[str, Any]) -> Dict[str, Any]:
        """把后台任务的两个事实挂上回执: 运行中几条 / 失败几条.

        死的只报一次(取走): 否则每一格都在复述同一件旧事, 而模型早该
        处置过了.
        """
        rows = []
        for task, desc in self._bg.items():
            if task.done():
                continue
            row: Dict[str, Any] = {'name': task.get_name()}
            if desc:
                row['desc'] = desc
            rows.append(row)
        if rows:
            rows.sort(key=lambda r: r['name'])
            res['bg'] = rows
        if self._bg_fails:
            res['bg_failed'] = self._bg_fails
            self._bg_fails = []
        return res

    def _ok(self, ns: Dict[str, Any]) -> Dict[str, Any]:
        res: Dict[str, Any] = {'ok': True, 'stdout': self._out()}
        if _OUT in ns and ns[_OUT] is not None:
            res['result'] = _jsonable(ns[_OUT])
        return self._bg_report(res)

    def _fail(self, exc: BaseException) -> Dict[str, Any]:
        """失败 = 模型该看见的事实: 自带注意债(与错误帧同构)."""
        where = ''
        try:
            frames = [f for f in traceback.extract_tb(exc.__traceback__)
                      if f.filename == _FILE]
            if frames:
                where = f' (line {frames[-1].lineno})'
        except Exception:
            pass
        _log.warning('code cell failed: %s%s', exc, where)
        return self._bg_report(
            {'ok': False, 'error': f'{type(exc).__name__}: {exc}{where}',
             'stdout': self._out(), 'observe': True})
