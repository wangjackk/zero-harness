"""Code —— 一个 body 是源码的 routine: 收完整段 Python, 交给代码内核执行.

和其它 routine 一样投递、排队、记账、回执, 唯一不同的是 body 是源码. **执行不在
本实例里** —— 源码送到常驻的 ``CodeKernel``(见 ``code_kernel.py``), 由它持有
一份跨格保留的命名空间. 本类只做三件事: 收 body、转发、按回执报账.

    await call('wave', hand='left')     # 建 wave 子实例(父是本格), 等结果
    await call('say', body='你好')       # 同 <say>你好</say>(body 就是标签中间那段)
    h = await create('say')             # 要自己插手就直取句柄:
    await h.start()                     #   start → feed → 自己决定何时 stop
    await h.feed('说到一半')
    await h.stop()
    await asyncio.sleep(0.5)            # 等就自己 asyncio.sleep(代码里没有轨道)
    await publish('moss/text', {})      # 旁路通知
    print(x)                            # stdout 收进回执

body 由 ``CtmlRoutine`` 统一收(``{'text'}`` 增量 / ``{'eof'}`` 收尾);
本类两个钩子都不覆盖 —— 不解释, 就是**收完才开工**(``run`` 等
``_body_ready``). 变量跨格保留(IPython 语义), 所以上一格定义的 ``df``
这一格还在; 命名空间活到 moss 停为止.

回执一条: 载荷 ``{content, details}`` —— content 是 stdout 与末表达式
(模型读这个), details 是 ``{stdout, result, ok}`` 原文(UI/排障). 代码
跑挂不算门失败(执行就是它的活), 但会带 ``observe`` 点名; 内核没起来
才是门失败 —— 那会抛成错误帧.

``meta['example']`` 的正文在 ``example.md``(见包的 docstring): 这里只读一次,
meta 拿到的是普通字符串 —— 渲染层不必知道文案住在哪个文件.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine.logger import setup_logger
from routine.routine import Modules

from ...ctml import CtmlRoutine

__all__ = ['Code']

_log = setup_logger('moss.code')

_EXEC_TIMEOUT = 600.0     # 一格默认最长执行时间(秒); timeout 参数覆盖

#: 回执里给模型的正文上限(头尾各留一半) —— 报错和结果常在 stdout 末尾,
#: 只截头会正好丢掉模型最想要的那几行.
_CONTENT_MAX = 3000


def _clip_mid(text: str, cap: int = _CONTENT_MAX) -> str:
    """超长输出留头留尾(中间折掉), 并说明折了多少 —— 不给假象."""
    if len(text) <= cap:
        return text
    half = cap // 2
    return (f'{text[:half]}…(中间省略 {len(text) - cap} 字)…'
            f'{text[-half:]}')


def _receipt(r: Dict[str, Any]) -> Dict[str, Any]:
    """内核回执 → 回执载荷.

    内核那条是**执行的事实**(``{ok, stdout, result}``), 门这条是**给模型
    看的话**. 两者分开: 代码跑挂了是"执行的结果"(门照样完成了它的事),
    所以照常返回、写进 content, 不是抛异常.
    """
    stdout = str(r.get('stdout') or '')
    result = r.get('result')
    details = {'stdout': stdout, 'result': result, 'ok': bool(r.get('ok'))}
    bg = list(r.get('bg') or [])
    bg_failed = list(r.get('bg_failed') or [])
    if bg:
        # 后台任务的名字必须进 content: 模型看不见 details, 而"还有哪些在
        # 跑"正是它决定要不要 cancel / 还要等多久的唯一依据.
        details['bg'] = bg
    if bg_failed:
        details['bg_failed'] = bg_failed
    if r.get('stopped'):
        details['stopped'] = True
        tail = f'末尾「{stdout[-20:]}」' if stdout.strip() else ''
        # 打断的是这一格, 不代表后台任务也停了 —— 报出来, 别让它以为清场了
        return {'content': (f'被打断, 已输出 {len(stdout)} 字 {tail}'
                            + _bg_note(bg, bg_failed)).strip(),
                'details': details}
    if not r.get('ok'):
        details['error'] = r.get('error')
        content = f'执行出错: {r.get("error")}'
        if stdout.strip():
            content += '\nstdout:\n' + stdout.rstrip()
        content += _bg_note(bg, bg_failed)
        return {'content': _clip_mid(content), 'details': details,
                'observe': True}
    parts = []
    if stdout.strip():
        parts.append('stdout:\n' + stdout.rstrip())
    if result is not None:
        parts.append(f'结果: {result!r}')
    parts.append(_bg_note(bg, bg_failed).strip('\n') or '')
    return {'content': _clip_mid('\n'.join(p for p in parts if p)),
            'details': details}


def _bg_note(bg: list, bg_failed: list) -> str:
    """后台任务的账(只报事实, 不劝模型做什么).

    两行同构: ``<状态> N 条: 名字(在等什么)`` —— 名字用来对号(``t.cancel()``
    对着变量做), 括号里说清它在等什么. 状态词是账目用语(运行中/失败), 不
    用人话("还挂着"/"挂了"): 回执是**记账**, 不是转述.
    """
    out = ''
    if bg:
        out += (f'\n后台任务运行中 {len(bg)} 条: '
                f'{", ".join(_bg_item(r) for r in bg)}'
                ' —— t.cancel() 可停')
    if bg_failed:
        out += (f'\n后台任务失败 {len(bg_failed)} 条: '
                + '; '.join(f'{_bg_item(r)} → {r.get("error", "")}'
                            for r in bg_failed))
    return out


def _bg_item(row: dict) -> str:
    name = str(row.get('name') or '?')
    desc = str(row.get('desc') or '')
    return f'{name}({desc})' if desc else name


class CodeInput(BaseModel):
    """code 的入参: 源码走 body, 参数只有 timeout."""

    timeout: float = Field(default=_EXEC_TIMEOUT,
                           description='这一格最多跑多少秒')


# 多行文案放 md: 改话术不碰 py, 改行为不碰 md. 读一次(import 期), 文件缺失
# 当场炸在加载处 —— 比运行期静默少一段说明好查.
_EXAMPLE = (Path(__file__).resolve().parent / 'example.md').read_text(
    encoding='utf-8').strip('\n')


class Code(CtmlRoutine):
    """写代码: 收完整段源码 → 交内核执行 → 一条结果账."""

    name = 'code'

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '写代码: body = Python 源码(收完才执行), 可用 call/create/'
            'spawn/publish/asyncio; 变量跨格保留; 末表达式即结果.'),
        # 收 body(默认是不收, 见 ctml.CtmlRoutine): 源码写在标签中间.
        'body': True,
        'input_schema': CodeInput.model_json_schema(),
        # 一格代码里"能写什么"的全部上下文(走法/句柄/跨格变量/叫不动的名字),
        # 正文在 example.md —— 只在 code 进词表时随行出现.
        'example': _EXAMPLE,
        # 查询类同一个属性: 结果当场回灌. body 里的 call 不进根账, 于是 stdout
        # 与末表达式只从这条回执出去 —— 不声明就等于算完只落账, 没人看见.
        'observe': True,
    }

    @classmethod
    def modules(cls, kwargs: Dict[str, Any] = None) -> Modules:
        """占 ``code``: 一台解释器一份命名空间, 一次一格(见 ``modules.py``).

        声明成模块 = 第二个 code 在 **start** 时被 kernel 拒, 错误帧当场
        回灌; 不声明则两格在 kernel 那把锁上静默排队 —— 看起来像并行, 出
        了事只有一句 exec timeout, 看不出是谁在等谁.
        """
        return Modules(['code'])

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        await super().on_created(rid, kwargs)
        self._kernel: str = ''     # 执行宿主 id(见 _kernel_rid, 首次执行时解析)
        self._timeout: float = float(CodeInput.model_validate(
            kwargs or {}).timeout)

    async def _kernel_rid(self) -> str:
        """执行宿主 id: 常驻内核由 shell 拉起, 这里按名字自解析(找到即记住).

        不自解析就得让 ``route`` 认识"谁要内核" —— 一份出厂声明加一次编排
        参数注入. 而"按名字找依赖"这套 ``chan_create`` / ``capability`` 都
        在用(``_rid('shell')``), 多一条声明只是多一条要维护的边: 声明的动作
        名与注入的键必须成对, 漏一处就是"code 拿不到内核"这种哑巴故障.
        缓存为空(内核晚于首格启动)时再找一次, 不把启动竞态钉成永久失败.
        """
        if self._kernel:
            return self._kernel
        for r in await self.get_running_routines():
            if r.get('name') == 'code_kernel':
                self._kernel = str(r.get('id') or '')
                break
        return self._kernel

    # --- 执行: 转给内核(body 由基类收, 收完才执行) ---

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        await self._body_ready.wait()      # 源码必须完整
        if self._aborted:
            return {'content': '被打断: 代码没跑完',
                    'details': {'stopped': True}}
        src = self.body_text
        if not src.strip():
            return {'content': '源码是空的, 什么都没执行', 'details': {}}
        kernel = await self._kernel_rid()
        if not kernel:
            # 门做不到它声称要做的事(执行) → 抛, 信封改成 error. 回执里
            # 说"没起来"是把故障伪装成事实, 模型只会以为自己写错了代码.
            raise RuntimeError(
                'code kernel not started —— 装配没把 code_kernel 拉起来')
        try:
            r = await self.req(kernel, 'exec',
                               {'cell': self.id, 'src': src},
                               timeout=self._timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 超时/内核故障: 让内核别继续跑这一格(幂等, 失败就算了)
            await self._cancel()
            raise RuntimeError(f'exec failed: {exc}') from None
        return _receipt(r)

    async def stop(self) -> None:
        """打断: 取消内核里这一格, 让它立刻返回(账照报, 带 stopped 尾).

        格子里打出去的动作父是本 code 实例, 由框架级联收 —— 这里只负责
        叫停 task, 不碰子实例(那是 _stop_node 的事).
        """
        self.abort()                  # 不再收 body; 等 body 的 run 立刻放行
        await self._cancel()

    async def _cancel(self) -> None:
        if not self._kernel:
            return
        try:
            await self.req(self._kernel, 'cancel', {'cell': self.id},
                           timeout=10.0)
        except Exception:
            # 内核也在停 / 已死: 它自己会收摊, 不必把它算成打断失败
            _log.debug('cancel cell %s failed', self.id, exc_info=True)
