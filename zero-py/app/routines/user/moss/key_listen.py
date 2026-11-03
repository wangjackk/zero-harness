"""KeyListen —— 键盘按钮源 → 耳朵的 press/release(PTT 的触发者).

被动常驻: 监听一个键的按下/松开**边沿**, ``req(asr, 'press'|'release')``.
键名不在这里配 —— 每 ``refresh`` 秒向耳朵问一次当前 mode/button, 于是
``<asr_mode mode="ptt" button="f9"/>`` 是唯一真理源, 换键只改那一处.

**只有 pynput 一个后端, 没有兜底**: 它事件式(按下/松开立刻到)、跨
Windows / macOS / Linux(X11). 回调在它自己的工作线程, 转回事件循环后
只做一件事: 发 press/release.

拿不到就**报错结束** —— 导入失败、平台没有可用的钩子(如 Wayland)、
键名不认识(pynput 里没有这个 Key), 一律让本实例失败退出, 不降级成
轮询、也不"记一行日志然后待命". 理由: 一个静悄悄躺着、看起来在跑却
永远收不到按键的监听器, 比一个明确报错的退出难查得多. 耳朵的 always
模式不依赖本实例; ptt 模式则需要它在, 起不来就该看见.

为什么按键不自己"说话": 按键是输入事件源, 说话的是耳朵. 本 routine 只把
"按下/松开"翻译成耳朵听得懂的两种事件, 模式判断、开麦、提交全在耳朵那侧.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine

__all__ = ['KeyListen', 'KeyListenInput']

_log = setup_logger('moss.key')

# pynput 的名字差异(其余按键名与 pynput.keyboard.Key 的枚举名一致)
_PYNPUT_ALIAS = {'return': 'enter', 'escape': 'esc', 'control': 'ctrl',
                 'pageup': 'page_up', 'pagedown': 'page_down'}


class KeyListenInput(BaseModel):
    """key_listen 入参(装配声明)."""

    refresh: float = Field(
        default=1.0, gt=0,
        description='向耳朵问 mode/button 的间隔(秒): 换键/切模式后最多'
                    '这么久生效')


class KeyListen(Routine):
    """键盘 PTT 按钮源: 按下 → asr.press, 松开 → asr.release."""

    name = 'key_listen'
    is_passive = True

    # 监听后端工厂: (button, on_edge) -> watcher(start/stop).
    # 真身是 pynput 全局钩子; 测试注入替身.
    watcher_factory = staticmethod(lambda button, on_edge: _PynputWatcher(
        button, on_edge))

    # 后端可用性检查: 真身要求 pynput 导得进来(缺了就抛, 实例结束).
    # 与 watcher_factory 同性质的注入点 —— 测试换替身后端时一并换掉.
    backend_check = staticmethod(lambda: _require_pynput())

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '键盘 PTT 按钮源: 被动常驻, 监听耳朵当前 button 指定的键, '
            '按下发 asr.press、松开发 asr.release(模式/键名都从耳朵读; '
            'pynput 事件式, 起不来就报错结束, 无兜底)'),
    }

    def __init__(self) -> None:
        super().__init__()
        self._halt = asyncio.Event()
        self._err: Optional[BaseException] = None   # 致命错(收尾后抛给框架)
        self._rid: Optional[str] = None     # 耳朵地址缓存
        self._mode = ''                     # 耳朵当前模式(刷新任务写)
        self._button = ''                   # 耳朵当前按钮名
        self._watched = ''                  # 正在监听的键(空 = 没听)
        self._watcher: Any = None           # 当前监听器(start/stop)
        self._bg: set[asyncio.Task] = set()  # 在途发送(防 GC 回收)

    async def run(self, kwargs: Dict[str, Any]) -> None:
        inp = KeyListenInput.model_validate(kwargs or {})
        type(self).backend_check()      # 没有 pynput → 直接抛, 实例结束
        self._halt.clear()
        self._err = None
        self._rid, self._mode, self._button = None, '', ''
        self._watched, self._watcher = '', None
        _log.info('key listen up: refresh=%.1fs backend=pynput', inp.refresh)
        refresher = asyncio.create_task(self._refresh_loop(inp.refresh))
        try:
            await self._halt.wait()
        finally:
            refresher.cancel()
            await asyncio.gather(refresher, return_exceptions=True)
            for t in list(self._bg):
                t.cancel()
            self._bg.clear()
            self._stop_watch()
        if self._err is not None:
            raise self._err           # 用不了就报错结束, 不装成正常退出

    async def stop(self) -> None:
        self._halt.set()

    # --- 配置同步(只有 ptt 才监听) ---

    def _sync(self) -> None:
        """按耳朵的当前模式起/停监听. 配置没变就什么都不做.

        建不起来(键名不认识 / 平台没钩子)就让异常冒出去 —— 由刷新任务
        收成致命错, run 收尾后抛出, 实例结束.
        """
        want = self._button if self._mode == 'ptt' else ''
        if want == self._watched:
            return
        self._stop_watch()
        if not want:
            return
        watcher = type(self).watcher_factory(want, self._edge)
        watcher.start()
        self._watcher = watcher
        self._watched = want
        _log.info('key listen: 监听 %r (mode=ptt)', want)

    def _stop_watch(self) -> None:
        w, self._watcher = self._watcher, None
        if w is not None:
            try:
                w.stop()
            except Exception:
                _log.debug('watcher stop failed', exc_info=True)
        self._watched = ''

    def _edge(self, down: bool) -> None:
        """后端回调(已转回事件循环): 一个边沿 = 一次发送."""
        if not self._watched:
            return
        task = asyncio.ensure_future(self._send('press' if down else 'release'))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _refresh_loop(self, every: float) -> None:
        """定期问耳朵当前 mode/button(唯一真理源).

        问不到(耳朵还没起/超时)保持旧值 —— 那是暂时状态, 下一轮再试;
        但**建监听失败是致命的**: 记下错、放开 run, 不再重试.
        """
        while not self._halt.is_set():
            rid = await self._asr_rid()
            if rid is not None:
                reply: Dict[str, Any] = {}
                try:
                    reply = await self.req(rid, 'mode', {}, timeout=5)
                except Exception:
                    _log.debug('mode refresh failed', exc_info=True)
                if reply.get('ok'):
                    self._mode = str(reply.get('mode') or 'always')
                    self._button = str(reply.get('button') or '')
                    try:
                        self._sync()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        # 用不了就结束: 不猜键名、不降级后端
                        _log.error('key listen 无法监听 %r: %s',
                                   self._button, exc)
                        self._err = exc
                        self._halt.set()
                        return
            await asyncio.sleep(every)

    async def _send(self, event: str) -> None:
        """把边沿发给耳朵. 名字就是耳朵当前的 button, 不会对不上."""
        rid = await self._asr_rid()
        if rid is None:
            return
        try:
            await self.req(rid, event, {'button': self._watched}, timeout=5)
        except Exception:
            _log.debug('%s failed', event, exc_info=True)

    async def _asr_rid(self) -> Optional[str]:
        if self._rid:
            return self._rid
        try:
            for r in await self.get_running_routines():
                if r.get('name') == 'asr':
                    self._rid = str(r['id'])
                    return self._rid
        except Exception:
            _log.debug('asr lookup failed', exc_info=True)
        return None


# --- 唯一后端: pynput(事件式) ---

def _require_pynput() -> None:
    """没有 pynput 就没有本 routine: 直接抛, 不留待命形态."""
    try:
        import pynput  # noqa: F401
        from pynput import keyboard  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            f'key_listen 需要 pynput, 但导入失败({exc}); '
            f'装不上就别注册 key_listen(asr 仍可用 always 模式)') from exc


def _pynput_key(name: str):
    """按钮名 → pynput 的 key 对象(不认识就抛 KeyError)."""
    from pynput import keyboard
    n = str(name or '').strip().lower()
    key = getattr(keyboard.Key, _PYNPUT_ALIAS.get(n, n), None)
    if key is not None:
        return key
    if len(n) == 1 and n.isalnum():
        return keyboard.KeyCode.from_char(n)
    raise KeyError(f'unknown key {name!r}')


def _same_key(ev: Any, target: Any) -> bool:
    """pynput 的按键对象比较(字符键忽略大小写: Shift+A 也是 a)."""
    from pynput import keyboard
    if ev == target:
        return True
    if isinstance(ev, keyboard.KeyCode) and isinstance(target,
                                                       keyboard.KeyCode):
        return (ev.char or '').lower() == (target.char or '').lower()
    return False


class _PynputWatcher:
    """pynput 全局键盘钩子: 边沿直接回调(经 loop 转线程安全)."""

    def __init__(self, button: str, on_edge: Callable[[bool], None]) -> None:
        from pynput import keyboard
        self._key = _pynput_key(button)     # 不认识 → KeyError, 由调用方报
        self._on_edge = on_edge
        self._loop = asyncio.get_running_loop()
        self._listener = keyboard.Listener(
            on_press=lambda k: self._fire(k, True),
            on_release=lambda k: self._fire(k, False))

    def _fire(self, ev: Any, down: bool) -> None:
        if _same_key(ev, self._key):
            self._loop.call_soon_threadsafe(self._on_edge, down)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()
