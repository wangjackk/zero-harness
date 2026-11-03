"""ui —— PySide6 进程外挂交互面(kernel dial-in 模式).

终端交互与业务日志混流曾是宿主病根(interact 时代) —— 本入口把交互面
整体搬出 moss 进程: moss 跑在主进程(kernel 开 grpc dial-in 口), UI 进程
自带 RoutineHub('moss-ui') 经 ``kernel.remote(addr)`` 拨入, 与主
进程平权挂在同一 kernel 上.

可行性依据(2026-10-17 考据):
- pubsub 订阅表在 kernel(``kernel/manager.py``: (namespace, topic) →
  set[rid]), **跨 hub 路由** —— 订阅者在哪个进程都收得到
- UI 与灵魂互不认识: ghost 不持 UI 引用, adapter 不碰记忆 —— 本
  文件只做"订阅渲染 + req 转发"
- IPython hub_routine(子进程)/zero-rs(车端 LAN)同模式现役

线程模型(Windows 最稳姿势): Qt 主线程跑 QApplication; hub 的
asyncio loop 放 QThread —— pubsub 回调 emit Qt Signal(跨线程自动
queued connection), 输入事件 ``run_coroutine_threadsafe`` 投回 loop.
不用 qasync 硬缝合两个事件循环.

运行(两个终端, cwd=zero-py)::

    # 终端 1: moss 主进程(唯一入口 = 通用入口 + 装配清单)
    python -m app.main app/moss.yaml

    # 终端 2: UI 进程
    python -m app.routines.user.moss.ui

地址: ZERO_KERNEL_ADDR 覆盖 > 主进程写的 dial-in 口 > config
network.grpc.addr(0.0.0.0 归一成 127.0.0.1). UI 崩溃不带走 moss
(adapter 掉线而已, 重启重拨); grpc transport 断线自动重连.

冒烟: ``QT_QPA_PLATFORM=offscreen MOSS_UI_AUTOCLOSE_MS=4000
python -m app.routines.user.moss.ui`` 起窗自关, exit 0 即过.

主区 = **模型收发的原文**(2026-10-25 定稿): 进来的那半是 ``model_input``
(脑子自己渲染的, 与喂给模型的是同一份产物), 出去的那半是 logos 字流原文.
**一个字都不改** —— 不解 ``<percepts>`` 那层壳, 不加"你:/Moss:"前缀: 解一层
壳或加一个前缀都是替模型转述, 而转述出来的那份看起来同样正常, 出错也不报.

快照跟拍 = **常开心跳(1s) + 事件点灯(250ms 防抖)**. 拍末不一定有信号:
语音不经过输入框(send 不点灯), ``say`` 又没有回执帧(obs 只在非空时广播)
—— 只靠点灯会停在第一拍, 界面上"我说的话"就少了一半.

三样东西不进主区(它们没有真的进过模型): 我正说着的话(灰字行, 标"未
入史", 下一拍真进了 ``<percepts>`` 就自己消失)、回执与打断(状态栏一行,
下一拍会以 ``<observe>``/``<stop_reason>`` 块出现)、思考流(灰字旁注 ——
严格说它既不是输入也不是输出, 但它是首字那八九秒里唯一还活着的信号).

上下文侧栏(右侧 dock, 可拖动/关闭, 三页签) —— 一份 ghost ``recall``
只读快照供三处: 帧链(recap 前情提要 / pending 漂到未归账回执 +
need_observe 火种旗 / 帧链缝合缝·输入·logos / 瞬态槽)、历史
(``model_input``: 脑子自己报的"这一拍读到的原样", 块形态照抄 · 比主区
多的只是块头标题, 用来翻查)、系统提示词(``system``: 装配拼一次的前缀
—— persona/protocol/能力清单, 每拍随请求发, 看它就是看它始终被告知了
什么). 快照跟拍是常开心跳(1s) + obs/打断/发送/连接四处点灯(250ms 防抖)
—— 心跳那一半不能省: 拍末不一定有信号(见上); 刷新钮另可手动拉. 长文页
只在当前页签可见时重排, 内容没变不重建(保滚动位置). UI 只被 req 读,
不碰记忆.

重连(SDK 自带 + 兜底按钮): **transport 断线自动重连是 SDK 内建**
(grpc_client: stream 断 → 清实例 → backoff 0.2s×1.5 上限 5s → 重连
+ 重发 catalog.push → kernel 重拉 adapter 拿新 rid, 订阅表重建) ——
moss 主进程重启后 UI **零操作自动恢复**(实测). adapter 的
on_created/stop 即连接真值, 状态栏可见"断开→自动恢复". "重连"按钮
只是兜底: transport 卡死(半死流/重连风暴)时强制换拨 —— 停旧 hub
线程(干净收摊, **等退净**再起新, 防旧线程清全局时抹掉新线程的
_loop/_adapter)再起新线程重拨.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import sys
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
# 框架包 routine-py(防御性: venv 已装则重复无害; 同 app.main)
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))
_ROOT = _HERE.parents[3]   # zero-py/(app.* 可导入)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Qt
from PySide6.QtGui import (
    QColor, QFontDatabase, QTextCharFormat, QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from routine import Routines, kernel
from routine.hub import RoutineHub
from routine.routine import Routine, subscribe

from app.config import load_env, network
from app.routines.user.moss.frames import frame_line
__all__ = ['UiAdapter', 'ChatWindow', 'HubThread', 'TextPanel',
           'context_payload', 'history_text', 'transcript_text', 'main']

# --- 模块级单例(UI 进程单实例, 装配简单优于注入花活) ---
_adapter: Optional['UiAdapter'] = None   # hub 侧实例(on_created 填)
_loop: Optional[asyncio.AbstractEventLoop] = None   # hub 线程的 loop


class _Bridge(QObject):
    """Qt 信号桥: hub 线程(asyncio)→Qt 主线程. 桥住在主线程(本模块
    import 期创建), 跨线程 emit 自动 queued connection, 线程安全."""

    text = Signal(str)        # ghost 字流块(CTML 原文)
    think = Signal(str)       # 它的心里话(思考流, 灰字; 不是对话)
    heard = Signal(dict)      # 耳朵听到的我的话(partial 全文 / final 定稿)
    obs = Signal(list)         # 观测帧(回执/错误)
    interrupt = Signal(str)    # 拍链打断
    status = Signal(str)       # 连接状态/错误
    context = Signal(dict)     # 上下文快照(recall: 帧链 + 历史 + 系统提示词)


_bridge = _Bridge()


def context_payload(recall: Dict[str, Any]) -> Dict[str, Any]:
    """ghost ``recall`` 回包 → 侧栏载荷(**键名只在这里写一次**).

    三页吃同一份快照, 缺字段各自降级(空帧链 / 空历史 / 空提示词) ——
    回包少了字段, 页面上就是"那一页是空的", 不会报错. 正因为静默, 键名
    不能在两处各写一遍: 改了一边却忘另一边, 症状只是"页签空白".
    """
    return {
        'frames': recall.get('frames') or [],
        'pending': recall.get('pending') or {},
        'recap': recall.get('recap') or '',
        'transients': recall.get('transients') or {},
        'dropped': recall.get('dropped', 0),
        'epochs': recall.get('epochs', 0),
        'model_input': recall.get('model_input') or [],
        'system': recall.get('system') or '',
    }


def _verbose_frame(f: Dict[str, Any]) -> str:
    """冒烟输出用: 帧行 + details(界面上不显示的那一半).

    回执分两层, 界面只吃 content; details 是给人排障看的结构, 终端冒烟
    时露一眼就够(要真在窗口里展开, 那是另一件事 —— 得给 QTextBrowser
    做可折叠块).
    """
    d = (f.get('result') or {}).get('details') \
        if isinstance(f.get('result'), dict) else None
    line = frame_line(f)
    return f'{line}  <details {json.dumps(d, ensure_ascii=False)}>' if d else line


class UiAdapter(Routine):
    """UI 的 kernel 市民(passive 常驻): 订阅 moss 命名空间转发 Qt,
    接收 UI 线程的 declare/command/interrupt 转发. ghost 不持 UI
    引用, 本类不碰记忆."""

    name = 'ui_adapter'
    is_passive = True

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': (
            'PySide6 UI 进程外挂交互面(kernel dial-in): 订阅 ghost '
            'pubsub(text/think/obs/interrupt + 耳朵 heard, namespace=moss)'
            '桥接 Qt; send_line 处理 declare//stop/do 转发 mindflow.'),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        global _adapter
        _adapter = self
        self._stop = asyncio.Event()
        # 实例拉起 = 连接真值(初连与 transport 自动重连恢复共用此
        # 回调): 状态栏对"断开→自动恢复"可见, moments 随初拉
        _bridge.status.emit('已连接 —— 打字开聊')

    async def stop(self) -> None:
        """实例销毁 = transport 断线(kernel 重启/网络断): SDK 自动
        backoff 重连(0.2s×1.5 上限 5s, 重连后 catalog push 重建) ——
        无需操作; "重连"按钮只是 transport 卡死时的强制换拨兜底."""
        _bridge.status.emit('连接断开 —— SDK 自动重连中(backoff ≤5s), '
                            'moss 起来后自动恢复, 无需操作')

    # ── moss 命名空间订阅(hub 线程内回调, emit 即返) ──

    @subscribe('text', namespace='moss')
    async def _on_text(self, source, data: Any) -> None:
        """ghost 字流段(CTML 原文, 边到边推)."""
        _bridge.text.emit(str(data))

    @subscribe('think', namespace='moss')
    async def _on_think(self, source, data: Any) -> None:
        """它的心里话(思考流): 首字未到那几秒唯一的活信号.

        与 ``text`` 分开走 —— 思考不是它说的话, 不进对话; 渲染上灰字
        斜体单起一行, 正文一到就收.
        """
        _bridge.think.emit(str(data))

    @subscribe('heard', namespace='moss')
    async def _on_heard(self, source, data: Any) -> None:
        """耳朵听到的话(开口就在推全文, 判停后定稿).

        与 moss 的 ``text``(输出) 成对: 一个"我说", 一个"它说"。这里是
        **本段全文**而非增量 —— 订阅方整行替换即可流式长出来。
        """
        _bridge.heard.emit(dict(data or {}))

    @subscribe('obs', namespace='moss')
    async def _on_obs(self, source, data: Any) -> None:
        """观测帧回灌(回执/错误, ghost 广播的同一份)."""
        _bridge.obs.emit(list(data or []))

    @subscribe('interrupt', namespace='moss')
    async def _on_interrupt(self, source, data: Any) -> None:
        """拍链中断(用户在等, 不能默默断)."""
        _bridge.interrupt.emit(str(data))

    # ── 生命周期 ──

    async def run(self, kwargs):
        await self._stop.wait()   # passive 常驻: 窗口关 = conn.close 收摊

    async def stop(self) -> None:
        self._stop.set()

    # ── UI → moss(输入框) ──

    async def send_line(self, line: str) -> None:
        """一行输入 → declare(普通)/command(反射弧)/interrupt(停止)/
        next(手动续拍). declare 即回(申报登记 fire-and-forget): 不锁
        UI, 字流/观测/打断从订阅推过来; 用户随时能再说话(在途拍可被
        新输入抢占)."""
        mindflow = await self._wait('mindflow')
        if line == '/stop':
            # 纯打断(原版 interrupt primitive): 停在途拍留痕不回应
            await self.req(mindflow, 'declare', {'interrupt': True}, 30)
        elif line.startswith('/do '):
            # 反射弧(原版 command nucleus): CTML 直投身体不经思考
            await self.req(mindflow, 'command', {'text': line[4:]}, 30)
        elif line == '/go':
            # 手动续拍(原版 need_observe 回声帧的手动替身): 不带
            # 新输入开一拍 —— 链活 = 插队下一拍; 链死 = 独立开链,
            # 开帧缝合缝缝着漂到的回执(pending), 模型被动读到
            await self.req(mindflow, 'declare', {'mode': 'next'}, 30)
        else:
            await self.req(mindflow, 'declare', {'text': line}, 30)

    async def _wait(self, name: str, timeout: float = 10.0) -> str:
        """按名等运行中的 routine(moss 带起 ghost 需一拍)."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            for r in await self.get_running_routines():
                if r.get('name') == name:
                    return r['id']
            await asyncio.sleep(0.1)
        raise RuntimeError(f'routine not running: {name}')

    async def fetch_context(self) -> None:
        """拉上下文快照(ghost ``recall``) → 侧栏三页.

        一份 recall 供三个页签: 帧链(pending/frames/recap/瞬态)、历史
        (``model_input`` —— 脑子自己报的"这一拍读到的原样")、系统提示词
        (``system`` —— 装配拼好一次的前缀). UI 只显示, 不重拼: 块形态
        的唯一渲染产地是脑子, 这里再拼一遍只会拼出第二套真相."""
        try:
            ghost = await self._wait('ghost', timeout=3.0)
            r = await self.req(ghost, 'recall', {}, 10)
            _bridge.context.emit(context_payload(r))
        except Exception as exc:
            _bridge.status.emit(f'上下文拉取失败: {exc}')


class HubThread(QThread):
    """hub 宿主线程: 独立 asyncio loop 上 RoutineHub 拨入 kernel;
    conn 断线由 grpc transport 自动重连(hub 侧已清/重建本地实例)."""

    def __init__(self, addr: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._addr = addr
        self._halt: Optional[asyncio.Event] = None
        self._halt_wanted = False   # halt 早于 loop 就绪: 记账, 起来就点

    def run(self) -> None:   # QThread 入口(线程体)
        global _loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop = loop
        self._halt = asyncio.Event()
        if self._halt_wanted:   # 窗口在拨入前就关了
            self._halt.set()
        try:
            loop.run_until_complete(self._serve())
        except BaseException:   # noqa: BLE001 —— 线程体: 收摊优先, 不外抛
            _bridge.status.emit('hub 线程异常退出')
        finally:
            _loop = None
            # 兜底收尸: _serve 正常路径已自己 cancel+gather; 走到这里
            # 还有存货(异常退/收摊被打断), 再扫一遍 —— loop.close() 前
            # 不清干净, pending 任务会打成 "Task was destroyed but it
            # is pending!"(Event.wait 这类最容易被抓)
            try:
                pending = [t for t in asyncio.all_tasks(loop)
                           if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()

    def halt(self) -> None:
        """主线程调用: 往 hub loop 里点停机事件(线程安全投递)."""
        h, lp = self._halt, _loop
        if h is not None and lp is not None:
            lp.call_soon_threadsafe(h.set)
        else:
            self._halt_wanted = True   # loop 还没起来, 起来自会关

    async def _serve(self) -> None:
        rs = Routines()
        rs.register(UiAdapter)
        hub = RoutineHub(rs, hub_id='moss-ui')
        _bridge.status.emit(f'拨入 {self._addr} ...')
        conn = None
        while not self._halt.is_set():
            try:
                conn = await hub.connect(kernel.remote(self._addr))
                break
            except Exception as exc:
                _bridge.status.emit(
                    f'连接失败({exc}) —— 先起 moss: '
                    'python -m app.main app/moss.yaml'
                    '; 3s 后重试')
                try:
                    await asyncio.wait_for(self._halt.wait(), timeout=3.0)
                    return
                except asyncio.TimeoutError:
                    pass
        if conn is None:
            return
        _bridge.status.emit(f'已连接 {self._addr} —— 打字开聊')
        try:
            await self._halt.wait()
        finally:
            _bridge.status.emit('已断开')
            # 优雅收摊: 销毁本地实例(adapter 走正常 stop 生命周期)
            # + 停 transport + cancel 收束剩余后台任务(mailbox 泵等),
            # loop 干净退场不留 "Task was destroyed" 警告.
            # 两步都设上界: 收摊拖过主线程的等待窗口就会被 terminate(),
            # 那才是真正会把任务留在 pending 的路径.
            try:
                hub.runtime.peer_down_all('ui window closed')
                await asyncio.wait_for(hub.lifecycle.stop_all_local(),
                                       timeout=2.0)
            except Exception:
                pass
            try:
                await asyncio.wait_for(conn.close(), timeout=3.0)
            except Exception:
                pass
            pending = [t for t in asyncio.all_tasks()
                       if t is not asyncio.current_task()]
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


class MomentsPanel(QWidget):
    """帧链页(只读观测面, 数据全部来自 ghost recall):
    recap(前情提要, 紫) → pending(漂到未归账回执, 珊瑚色 —— 侧栏
    的"进行时") → 帧链(新 → 旧, 每帧: 缝合缝[蓝]·输入[绿]·logos
    [琥珀]) → 瞬态槽(灰, 不入史). 排列即语义: pending 永远压在
    帧链上面 —— 它是还没归账、等下一帧缝走的部分."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.on_refresh = None   # 外接拉取回调(ChatWindow 注入)
        self._stats = QLabel('未连接')
        btn = QPushButton('刷新')
        head = QHBoxLayout()
        head.addWidget(self._stats, 1)
        head.addWidget(btn)
        view = QTextBrowser()
        view.setOpenExternalLinks(False)
        self.view = view
        box = QVBoxLayout(self)
        box.addLayout(head)
        box.addWidget(view, 1)
        btn.clicked.connect(self._manual)

    def _manual(self) -> None:
        if self.on_refresh is not None:
            self.on_refresh()

    # ── 渲染(payload = UiAdapter.fetch_context 的瘦身载荷) ──

    def set_data(self, d: Dict[str, Any]) -> None:
        frames = d.get('frames') or []
        self._stats.setText(
            f"帧 {len(frames)} · 出窗 {d.get('dropped', 0)} "
            f"· epoch {d.get('epochs', 0)}")
        parts: list[str] = []
        recap = d.get('recap') or ''
        if recap:
            parts.append(
                '<div style="color:#a07ad0;margin-bottom:6px">'
                f'<b>recap · 前情提要(epoch {d.get("epochs", 0)})</b><br>'
                f'{html.escape(recap)}</div>')
        p = d.get('pending') or {}
        p_lines = [html.escape(frame_line(f)) for f in p.get('frames') or []]
        if p.get('stop_reason'):
            p_lines.append(
                f"interrupted: {html.escape(p['stop_reason'])}")
        flag = (' · need_observe 亮: /go 可消化' if p.get('need_observe')
                else '')
        if p_lines or flag:
            body = '<br>'.join(p_lines) if p_lines else '(空)'
            parts.append(
                f'<div style="color:#d08770;margin-bottom:6px">'
                f'<b>pending · 未归账回执{html.escape(flag)}</b><br>'
                f'{body}</div>')
        for f in reversed(frames):
            parts.append(self._frame_html(f))
        tr = d.get('transients') or {}
        if tr:
            body = '<br>'.join(
                f'[{html.escape(s)}] {html.escape(t)}'
                for s, t in tr.items())
            parts.append(
                '<div style="color:#8a8a8a;margin-top:6px">'
                f'<b>瞬态槽 · 不入史</b><br>{body}</div>')
        self.view.setHtml(
            '<br>'.join(parts) or '<div>(空 —— 开口说话后这里会长出帧)</div>')

    @staticmethod
    def _frame_html(f: Dict[str, Any]) -> str:
        """一帧一卡: 缝合缝(上拍干了什么) → 输入(按源) → logos."""
        idx = f.get('index')
        prev = f.get('previous') or {}
        prev_lines = [html.escape(frame_line(x))
                      for x in prev.get('frames') or []]
        if prev.get('stop_reason'):
            prev_lines.append(
                f"interrupted: {html.escape(prev['stop_reason'])}")
        prev_html = '<br>'.join(prev_lines) if prev_lines else '(空)'
        per = f.get('percepts') or {}
        per_html = '<br>'.join(
            f'{html.escape(s)}: {html.escape(t)}' for s, t in per.items()
        ) if per else '(空)'
        logos = html.escape(f.get('logos') or '') or '(空)'
        return (
            '<div style="margin-bottom:8px;border-left:3px solid '
            '#4e9af1;padding-left:6px">'
            f'<b>帧#{idx}</b><br>'
            f'<span style="color:#4e9af1">缝合缝: </span>{prev_html}<br>'
            f'<span style="color:#5a9e6f">输入: </span>{per_html}<br>'
            f'<span style="color:#c98a3d">logos: </span>{logos}'
            '</div>')


#: 模型条目的块头标签: 非 user 的按 role; user 块(模型侧 role 全是
#: user)按首行 tag 分 —— 那是脑子包出来的块, 界面照实点明是哪一块.
_ITEM_TAGS = (('<percepts>', '感知'), ('<observe>', '观测'),
              ('<dynamic_context>', '瞬态'), ('<stop_reason>', '中断'),
              ('<recap>', '提要'))


def _content_text(content: Any) -> str:
    """条目 ``content`` → 可显示文本. 图块换成一行 ``[image]``.

    绝大多数条目是 str, 原样返回.**带图的那一条**是多模态 blocks
    (``input_text`` + 若干 ``input_image``): ``image_url`` 是 base64, 一张
    截图几十万字符, 原样贴进界面会卡死 —— 这是唯一必须动手的地方, 不是
    为了好看.

    文本块仍然一个字都不改; 图块换成 ``[image]``: 图本来就没有"文字原版"
    可言, 留一行说明"这里有一张图"就是它能有的全部表示.
    """
    if not isinstance(content, list):
        return str(content or '')
    out: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get('type') in ('input_text', 'output_text', 'text'):
            out.append(str(block.get('text') or ''))
        elif block.get('type') in ('input_image', 'image_url'):
            out.append('[image]')
    return '\n'.join(out)


def _item_label(item: Dict[str, Any]) -> str:
    """一条模型条目 → 人读的块名(感知/观测/瞬态/中断/提要/它/输入)."""
    role = str(item.get('role') or '?')
    if role == 'assistant':
        return '它'
    head = _content_text(item.get('content')).lstrip()
    for tag, name in _ITEM_TAGS:
        if head.startswith(tag):
            return name
    return '输入' if role == 'user' else role


def history_text(items: List[Dict[str, Any]]) -> str:
    """模型条目 → 纯文本(一块一段, 块头点明是哪一块, 正文照抄).

    这里只**分段加标题**, 不解释内容: ``content`` 是脑子喂进去的原文,
    照抄才是它读到的东西 —— 重排一遍就成了第二套真相.
    """
    out: List[str] = []
    for i, it in enumerate(items, 1):
        out.append(f'── [{i}] {_item_label(it)} ' + '─' * 24)
        out.append(_content_text(it.get('content')))
        out.append('')
    return '\n'.join(out).rstrip() + '\n'


#: 主区的块分隔线.
_SEP = '─' * 46


def transcript_text(items: List[Dict[str, Any]]) -> str:
    """``model_input`` → 主区原文(一块一段, 块间一条等宽横线).

    **一个字都不改**: 不折行、不缩进、不加时间戳、不解 ``<percepts>``
    那层壳。主区要回答的问题是"刚才进模型的到底是什么", 唯一可信的答案
    就是原文本身 —— 解一层壳、加一个 ``你:`` 都是替模型转述, 而转述出
    来的那份看起来同样正常, 出错也不报。

    跟历史页的唯一区别是**没有块头标题**(那里要索引, 这里不要)。
    """
    out: List[str] = []
    for it in items:
        body = _content_text(it.get('content'))
        if not body:          # 空条目: 画一条空分隔线只会让人以为丢了内容
            continue
        if out:
            out.append(_SEP)
        out.append(body)
    return '\n'.join(out)


class TextPanel(QWidget):
    """只读长文页签(系统提示词 / 历史共用): 计数行 + 复制 + 弹出 + 文本域.

    两页都是**原文**(系统前缀上万字、历史几十条块), 所以用
    QPlainTextEdit 而不是 QTextBrowser: 纯文本域原生给选择/复制/滚动,
    也不必为好看把原文富文本化一遍 —— 加了颜色和标题, 人就会以为原文
    里本来有那些东西.

    快照每 250ms 一份(**内容没变就不重建**): 重建会吃掉滚动位置, 用户
    正读中间, 一有观测帧就被弹回顶部.
    """

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._title = title
        self._text = ''
        self._dlg: Optional[QDialog] = None
        self._stats = QLabel('未连接')
        self._copy = QPushButton('复制')
        self._copy.setToolTip('这一页原文整篇进剪贴板(不含统计行)')
        self._pop = QPushButton('弹出')
        self._pop.setToolTip('同一份原文开独立窗口 —— 窄侧栏读长文太挤')
        view = QPlainTextEdit()
        view.setReadOnly(True)
        # 等宽: 缩进/标签的对齐是原文的一部分, 比例字体读不出层级
        view.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        self.view = view
        head = QHBoxLayout()
        head.addWidget(self._stats, 1)
        head.addWidget(self._copy)
        head.addWidget(self._pop)
        box = QVBoxLayout(self)
        box.addLayout(head)
        box.addWidget(view, 1)
        self._copy.clicked.connect(self.copy)
        self._pop.clicked.connect(self.popup)

    def set_text(self, text: str, stats: str = '') -> None:
        """换一份原文: 字面相同就不动(保住光标与滚动位置)."""
        if stats:
            self._stats.setText(stats)
        if text == self._text:
            return
        self._text = text
        sb = self.view.verticalScrollBar()
        keep = sb.value()
        self.view.setPlainText(text)
        sb.setValue(min(keep, sb.maximum()))

    def copy(self) -> None:
        QApplication.clipboard().setText(self._text)
        self._copy.setText('已复制')
        QTimer.singleShot(1200, lambda: self._copy.setText('复制'))

    def popup(self) -> None:
        """独立窗口看同一份原文. 引用留住 —— 否则 PySide 回收, 一闪就没."""
        dlg = QDialog(self)
        dlg.setWindowTitle(self._title)
        dlg.resize(1000, 760)
        view = QPlainTextEdit(dlg)
        view.setReadOnly(True)
        view.setFont(self.view.font())
        view.setPlainText(self._text)
        lay = QVBoxLayout(dlg)
        lay.addWidget(view)
        dlg.show()
        self._dlg = dlg


class ChatWindow(QMainWindow):
    """主区 = **LLM 边界流水**, 不是对话框: 一块一块的原文, 块间一条等宽
    横线. 没有"你:/Moss:" —— 加前缀就是把原文重排一遍, 而重排出来的那
    份看起来同样正常, 却不是模型读到/写出的东西.

    三样东西不进主区(它们**没有真的进过模型**):
    - 我正在说的话(耳朵 partial、刚要发出的字) → 输入框上面一行灰字,
      标"未入史"; 下一拍真进了 ``<percepts>`` 就自己消失;
    - 回执 / 打断 → 状态栏一行摘要(它们下一拍会以 ``<observe>`` /
      ``<stop_reason>`` 块出现, 那个才是模型真正看到的);
    - 思考流 → 主区里的灰字旁注. 它既不是输入也不是输出, 但它是"它还
      活着"的唯一证据(首字要等八九秒).

    文字全部照抄, 一块一段 —— 主区要回答的是"刚才进模型的到底是什么",
    唯一可信的答案就是原文本身.
    """

    #: "想:" 前缀(思考流; 灰字单起一行, 与流水明确分开)
    _THINK_TAG = '想: '

    def __init__(self, addr: str):
        super().__init__()
        self._addr = addr   # 重连换拨用
        self.setWindowTitle(f'Moss — {addr}')
        self.resize(1180, 640)
        self._headed = False    # 流水里"它正在说"的那一块是否开着
        self._thinking = False  # '想: ' 灰字行是否打开中(正文一到就收)
        self._think_fmt = QTextCharFormat()
        self._think_fmt.setForeground(QColor('#8a8f9a'))
        self._think_fmt.setFontItalic(True)
        # 空格式 = 文档默认: 每处插正文前显式设一次, 免得接在思考块
        # 后面时把灰斜体串到正文上(insertText 用 cursor 当前格式)
        self._body_fmt = QTextCharFormat()
        # 上次重排之后新增的输出字母(**不是**"这一拍的全部"): 结账只有
        # ``_rebuild._settle_live`` 一处, 判据是"快照里有没有它" —— 回执帧
        # 不可靠(``say`` 没有回执), 拿它当拍末就会重影。
        self._live = ''
        self._draft = ''
        self._items: Optional[List[Dict[str, Any]]] = None   # 上次快照

        view = QPlainTextEdit()   # 纯文本域: 原文不需要排版, 也没有哪种
        view.setReadOnly(True)    # 富文本能表达而纯文本表达不了的边界
        view.setUndoRedoEnabled(False)
        # 等宽: 标签缩进/对齐是原文的一部分, 比例字体读不出层级
        view.setFont(QFontDatabase.systemFont(
            QFontDatabase.SystemFont.FixedFont))
        self.view = view
        hint = QLabel('')
        hint.setStyleSheet('color:#8a8f9a;font-style:italic;')
        hint.setToolTip('还没有进模型的东西: 耳朵在听的字、按下回车待入史'
                        '的一条. 下一拍进了 <percepts> 就从这里消失.')
        self.hint = hint
        inp = QLineEdit()
        inp.setPlaceholderText(
            '说点什么(Enter 发送; /go 手动续一拍; /stop 停止; '
            '/do <ctml> 反射弧直投)')
        self.inp = inp
        btn_send = QPushButton('发送')
        btn_go = QPushButton('激活')
        btn_go.setToolTip('手动续一拍(不带新输入): 让模型看见刚漂到的'
                          '动作回执, 如天气结果')
        btn_stop = QPushButton('停止')
        btn_re = QPushButton('重连')
        btn_re.setToolTip('兜底: 断线后 SDK 会自动重连(moss 重启后也'
                          '自动恢复), 仅 transport 卡死时用它强制换拨')
        row = QHBoxLayout()
        row.addWidget(inp, 1)
        row.addWidget(btn_send)
        row.addWidget(btn_go)
        row.addWidget(btn_stop)
        row.addWidget(btn_re)
        box = QVBoxLayout()
        box.addWidget(view, 1)
        box.addWidget(hint)
        box.addLayout(row)
        w = QWidget()
        w.setLayout(box)
        self.setCentralWidget(w)

        inp.returnPressed.connect(self._send)
        btn_send.clicked.connect(self._send)
        btn_go.clicked.connect(self._go_clicked)
        btn_stop.clicked.connect(lambda: self._dispatch('/stop'))
        btn_re.clicked.connect(self._reconnect)

        # ── 上下文侧栏(右侧 dock, 三页签): 帧链 / 历史 / 系统提示词 ──
        # 三层各答一个问题: 帧链 = 它这一拍干了什么; 历史 = 它这一拍读到
        # 了什么(脑子自己的渲染, 与喂进去的同一份); 系统提示词 = 它始终
        # 被告知了什么(装配拼一次的前缀, 每拍都随请求发).
        self.panel = MomentsPanel()
        self.panel.on_refresh = self._request_context
        self.history_panel = TextPanel('历史 —— 模型每拍读到的原样')
        self.system_panel = TextPanel('系统提示词 —— 装配拼好的前缀')
        self.tabs = QTabWidget()
        # 长文页(历史/提示词)要有横向地方放: 侧栏窄到三百字宽, 那两页
        # 就只剩"能滚", 读不出结构
        self.tabs.setMinimumWidth(340)
        self.tabs.addTab(self.panel, '帧链')
        self.tabs.addTab(self.history_panel, '历史')
        self.tabs.addTab(self.system_panel, '系统提示词')
        dock = QDockWidget('上下文', self)
        dock.setWidget(self.tabs)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self._ctx: Dict[str, Any] = {}   # 最近一份快照(切页签时重放)
        self._ctx_sig: Any = None        # 快照指纹: 没变就整份不发
        # 两盏灯: **心跳**保证跟得上(拍末不一定有信号 —— say 这类动作
        # 没有回执帧, 语音输入又不经过输入框, 只靠事件点灯会停在第一拍),
        # **点灯**是有事时的快拉(obs/打断/发送/连接四处)。
        self._ctx_timer = QTimer(self)
        self._ctx_timer.setInterval(1000)
        self._ctx_timer.timeout.connect(self._fetch_context)
        self._ctx_timer.start()
        self._ctx_kick = QTimer(self)
        self._ctx_kick.setSingleShot(True)
        self._ctx_kick.setInterval(250)
        self._ctx_kick.timeout.connect(self._fetch_context)
        self.tabs.currentChanged.connect(lambda _: self._render_tab())

        _bridge.text.connect(self._on_text)
        _bridge.think.connect(self._on_think)
        _bridge.heard.connect(self._on_heard)
        _bridge.obs.connect(self._on_obs)
        _bridge.interrupt.connect(self._on_interrupt)
        _bridge.status.connect(self._on_status)
        _bridge.context.connect(self._on_context)
        if os.environ.get('MOSS_UI_VERBOSE'):   # 冒烟: 状态/字流同步打 stdout
            _bridge.status.connect(lambda s: print(f'[status] {s}', flush=True))
            _bridge.text.connect(lambda s: print(f'[text] {s}', flush=True))
            _bridge.think.connect(lambda s: print(f'[think] {s}', flush=True))
            _bridge.heard.connect(lambda d: print(
                f"[heard] {d.get('kind')} {d.get('text')}", flush=True))
            # 回执的 details: 界面上只显示 content(模型读的那一行), 原始
            # 数据在这里露一眼 —— 排障/看数据结构用.
            _bridge.obs.connect(lambda fs: print(
                '[obs] ' + ' | '.join(_verbose_frame(f) for f in fs),
                flush=True))
            _bridge.context.connect(lambda d: print(
                f"[context] 帧{len(d.get('frames') or [])} "
                f"pending{len((d.get('pending') or {}).get('frames') or [])} "
                f"obs-旗={bool((d.get('pending') or {}).get('need_observe'))} "
                f"历史{len(d.get('model_input') or [])}条 "
                f"提示词{len(d.get('system') or '')}字",
                flush=True))

        self.thread: Optional[HubThread] = None

    def _on_status(self, msg: str) -> None:
        """状态栏 + 连接成功即拉一次上下文快照(初始三页)."""
        self.statusBar().showMessage(msg)
        if msg.startswith('已连接'):
            self._request_context()

    def _request_context(self) -> None:
        """点灯(防抖): 250ms 内多次触发合一次快拉(心跳照旧跑)."""
        self._ctx_kick.start()

    def _fetch_context(self) -> None:
        """UI 线程 → hub loop: run_coroutine_threadsafe 投递 recall."""
        if _adapter is None or _loop is None:
            return
        asyncio.run_coroutine_threadsafe(_adapter.fetch_context(), _loop)

    def _on_context(self, d: Dict[str, Any]) -> None:
        """快照分发: 主区流水对一遍, 帧链每次重建(短), 长文页只在**当前
        可见**时重排 —— 历史几十条、提示词六千字, 藏在别的页签后面时
        不必重排.

        心跳每秒一份, **内容没变就整份不分发**: 帧链面板是重建式的,
        每秒重放一次会把人正在读的位置弹走。
        """
        sig = (repr(d.get('model_input')), repr(d.get('frames')),
               repr(d.get('pending')), repr(d.get('transients')),
               d.get('recap'), d.get('system'))
        if sig == self._ctx_sig:
            return
        self._ctx_sig = sig
        self._ctx = d
        self._apply_items(d.get('model_input') or [])
        self.panel.set_data(d)
        self._render_tab()

    # ── 主区流水 ──

    def _apply_items(self, items: List[Dict[str, Any]]) -> None:
        """``model_input`` 变了才重排主区.

        250ms 一份快照, 内容没变就什么都不做 —— 重排会吃掉滚动位置; 变了
        就是"新的一拍进去了", 草稿到此转正(它已经躺在 ``<percepts>`` 里,
        再留在灰字行上就成了两句).
        """
        if items == self._items:
            return
        self._items = items
        self._draft = ''
        self._rebuild()

    def _settle_live(self) -> str:
        """取走这一拍的流式增量; **已进快照的不再贴**(否则同一句印两遍).

        判据只能来自快照本身 —— ``say`` 没有回执帧(``ghost.on_message``
        只在 ``obs`` 非空时广播), 所以根本没有"拍末"可以清 ``_live``。
        靠 ``_on_obs`` 清的那一版里, **纯说话的拍永远清不掉**: 下一拍重排
        把快照里那一句画一遍, 再把 ``_live`` 里同一句贴一遍, 屏幕上就是
        同一句话两遍(而 ``model_input`` 里只有一遍) —— 重影, 不报错.

        语义就此定为"上次重排之后新增的 logos": 出口只有这一个, 重排即
        结账, 不可能跨拍累积.
        """
        live, self._live = self._live, ''
        if not live.strip():
            return ''
        for it in reversed(self._items or []):
            if it.get('role') != 'assistant':
                continue
            if live.strip() in str(it.get('content') or ''):
                return ''      # 快照已经包含它: 贴上去就是重影
            break              # 最后一条 assistant 还没有它 = 真没进史
        return live

    def _rebuild(self) -> None:
        """整段重排: 快照原文 + 正在流的那一拍(还没 commit 进记忆的)."""
        at_bottom = self._at_bottom()
        text = transcript_text(self._items or [])
        pending = self._settle_live()
        if pending:
            text = f'{text}\n\n{_SEP}\n{pending}' if text else pending
        self.view.setPlainText(text)
        self._headed = bool(pending)   # 流接得上: 下一块 chunk 直接续
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.view.setTextCursor(c)
        self._follow(at_bottom)
        self._update_hint()

    def _update_hint(self) -> None:
        """灰字行 = "还没进模型的东西". 只有它消失才说明真的进去了."""
        self.hint.setText(f'未入史 · {self._draft}' if self._draft else '')

    def _render_tab(self) -> None:
        """渲染当前页签(长文页各有自己的快照源, 切过去才排)."""
        cur = self.tabs.currentWidget()
        if cur is self.history_panel:
            self._render_history()
        elif cur is self.system_panel:
            self._render_system()

    def _render_history(self) -> None:
        """历史页 = 模型每拍读到的原样(model_input: 脑子自己渲染的)."""
        items = self._ctx.get('model_input') or []
        if not items:
            self.history_panel.set_text(
                '(空 —— 还没开过拍; 说句话, 这里长出模型读到的东西)',
                '0 条')
            return
        chars = sum(len(str(it.get('content') or '')) for it in items)
        self.history_panel.set_text(history_text(items),
                                    f'{len(items)} 条 · {chars} 字')

    def _render_system(self) -> None:
        """系统提示词页 = 装配拼一次的 system 前缀(每拍随请求发)."""
        s = str(self._ctx.get('system') or '')
        if not s:
            self.system_panel.set_text(
                '(空 —— 未连上, 或装配的 prompts 名单是空的)', '0 字')
            return
        self.system_panel.set_text(s, f'{len(s)} 字 · {s.count(chr(10)) + 1} 行')

    def attach_hub(self, thread: HubThread) -> None:
        self.thread = thread

    # ── 渲染(全部在 Qt 主线程, 信号 queued 投递) ──

    def _close_head(self) -> None:
        """关掉"它正在说"的那一块(流结束/观测到/打断)."""
        if self._headed:
            self.view.appendPlainText('')
            self._headed = False

    def _close_think(self) -> None:
        """收思考行: 正文/观测/我的话一来, 灰字到此为止.

        只落标记不断行 —— 后面每个开块处都有自己的断行判定
        (``atBlockStart`` / ``appendPlainText``), 这里补一次换段反而多
        出空行."""
        self._thinking = False

    def _on_think(self, chunk: str) -> None:
        """思考流 → 灰字一行(流式追加).

        它正在想的时候还没说话 —— 这一行是"它还活着、正在算"的可见
        证据(首字延迟常有八九秒, 没有它就只剩一片空白). 正文一到就收;
        灰色内容只在这个进程里给人看, 不进身体、不进记忆.
        """
        if not chunk:
            return
        self._close_head()   # 正文块开着: 思考另起一块, 不接在它话里
        at_bottom = self._at_bottom()
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        if not self._thinking:
            if not c.atBlockStart():
                c.insertBlock()
            c.setCharFormat(self._think_fmt)
            c.insertText(self._THINK_TAG)
            self._thinking = True
        c.setCharFormat(self._think_fmt)
        c.insertText(chunk)
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self._follow(at_bottom)

    def _on_text(self, chunk: str) -> None:
        """logos 原文 → 主区新的一块(流式追加, 一个字都不加).

        不加前缀、不缩进、不改行: 主区要回答的是"它刚才到底写了什么",
        加一个 ``Moss:`` 就变成了"我替它转述".
        """
        if not chunk:
            return
        self._close_think()   # 开口了: 心里话到此为止
        self._live += chunk
        at_bottom = self._at_bottom()
        c = self.view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        if not self._headed:
            # 开块: 块边界唯一的装饰是一条等宽横线(谁说的由内容自证)
            if self.view.document().characterCount() > 1:
                c.insertText(f'\n\n{_SEP}\n')
            self._headed = True
        c.setCharFormat(self._body_fmt)   # 防串思考行的灰斜体
        c.insertText(chunk)   # CTML 原文(与终端同视)
        self.view.moveCursor(QTextCursor.MoveOperation.End)
        self._follow(at_bottom)

    def _at_bottom(self) -> bool:
        sb = self.view.verticalScrollBar()
        return sb.value() >= sb.maximum() - 8

    def _follow(self, at_bottom: bool) -> None:
        """跟着流走 —— 但只在人本来就贴在底部时.

        判据取在插入**之前**: 插入会让 maximum 变大, 事后再比就永远是
        "没在底部". 往上翻着读历史的人不该因为一条思考流被拽回尾端.
        """
        if at_bottom:
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _on_heard(self, ev: dict) -> None:
        """耳朵听到的话 → **灰字行**, 不进主区.

        ``partial`` 是本段全文(服务端如此) → 直接覆盖, 不做增量累加;
        ``final`` 判停定稿, 但**仍然不入流水** —— 它还没进模型, 下一拍
        才随 ``<percepts>`` 入史, 那时灰字行自己消失(``_apply_items``).
        流水只放真的进去过的东西, 这条界线就是主区的全部意义.
        """
        text = str(ev.get('text') or '').replace('\n', ' ').strip()
        if not text:
            return
        self._close_think()   # 我开口 = 它的心里话到此为止
        self._draft = text
        self._update_hint()

    def _on_obs(self, frames: list) -> None:
        """回执 → 状态栏一行摘要, 不进主区.

        它下一拍会以 ``<observe>`` 块出现(一条回执一行, 模型读的就是那
        个) —— 主区里再印一遍 ``obs>``, 就是同一个事实两套说法.
        """
        self._close_head()      # 这一拍说完了: 输出已经 commit 进记忆
        self._close_think()
        # 注意: 这里**不**清 ``_live`` —— 回执不是可靠的"拍末"(``say``
        # 根本没有回执帧), 清在这里会让"拍内已流的那段"在下一次重排时
        # 被快照补一遍再被 ``_live`` 贴一遍。结账只发生在 ``_rebuild``
        # 的 ``_settle_live``(判据是快照本身)。
        if frames:
            self.statusBar().showMessage(
                ' / '.join(frame_line(f) for f in frames), 8000)
        self._request_context()   # 回执落账 → 帧链/pending/流水都变了

    def _on_interrupt(self, reason: str) -> None:
        self._close_head()
        self._close_think()
        # ``_live`` 同样不清: 被打断的半句确实发出去了(人也听见了), 它该
        # 留在屏幕上; 结账仍旧只有 ``_rebuild`` 一处。
        self.statusBar().showMessage(f'interrupted: {reason}', 8000)
        self._request_context()   # 留痕进 pending → 下一拍是 <stop_reason>

    def _append_sys(self, text: str) -> None:
        """系统提示 → 状态栏(主区只放模型收发的东西)."""
        self.statusBar().showMessage(text, 8000)

    # ── 输入 ──

    def _send(self) -> None:
        line = self.inp.text().strip()
        if not line:
            return
        self.inp.clear()
        self._close_head()
        self._close_think()
        self._draft = line          # 灰字行: 还没入史
        self._update_hint()
        self._dispatch(line)

    def _go_clicked(self) -> None:
        self._append_sys('-- 激活: 手动续一拍(不带新输入) --')
        self._dispatch('/go')

    def _dispatch(self, line: str) -> None:
        """UI 线程 → hub loop: run_coroutine_threadsafe 投递 declare."""
        if _adapter is None or _loop is None:
            self._append_sys(f'[未连接] moss 还没起来: {line}')
            return
        fut = asyncio.run_coroutine_threadsafe(_adapter.send_line(line),
                                               _loop)
        fut.add_done_callback(self._on_send_done)
        self._request_context()   # 开新拍(percepts 入帧) → 侧栏跟拍

    def _on_send_done(self, fut) -> None:
        """发送结果回看(在 hub 线程回调, emit 信号回主线程)."""
        exc = fut.exception()
        if exc is not None:
            _bridge.status.emit(f'发送失败: {exc}')

    def _reconnect(self) -> None:
        """原地换拨: moss 主进程重启(kernel 全新, rid 全换)后无需
        重启 UI —— 停旧 hub 线程(走已验证的干净收摊: 销毁本地
        adapter 实例 + 停 transport + loop 退场), 再起新线程重拨.
        顺序上必须**等旧线程退净**再起新: 旧线程退出时会清模块级
        _loop/_adapter, 抢跑会把新线程刚设的全局抹掉(竞态)."""
        global _adapter
        old = self.thread
        if old is not None:
            old.halt()
            # 收摊有界(上面 2s+3s), 给足窗口再考虑 terminate —— 硬杀
            # 线程会把任务留在 pending(loop 没机会关), 换来 destroyed 警告
            if not old.wait(6000):
                old.terminate()   # 兜底: 拨入卡死别拖住窗口
                old.wait(1000)
        _adapter = None   # 旧实例已销毁: 派活守卫提示未连接
        _bridge.status.emit('重连: 换拨新线程 ...')
        t = HubThread(self._addr)
        self.attach_hub(t)
        t.start()

    def closeEvent(self, event) -> None:   # noqa: N802(Qt 约定)
        if self.thread is not None:
            self.thread.halt()
            if not self.thread.wait(6000):
                self.thread.terminate()   # 兜底: 拨入卡死别拖住窗口
        event.accept()


def _dial_addr() -> str:
    """拨入地址: ZERO_KERNEL_ADDR > config grpc.addr(0.0.0.0 → 127.0.0.1)."""
    addr = os.environ.get('ZERO_KERNEL_ADDR') or network().grpc.addr
    host, _, port = addr.rpartition(':')
    if host in ('0.0.0.0', '::', ''):
        host = '127.0.0.1'
    return f'{host}:{port}'


def main() -> None:
    load_env()
    addr = _dial_addr()
    app = QApplication(sys.argv)
    win = ChatWindow(addr)
    thread = HubThread(addr)
    win.attach_hub(thread)
    win.show()
    thread.start()
    auto = os.environ.get('MOSS_UI_AUTOCLOSE_MS')   # 冒烟: 起窗自关
    if auto:
        QTimer.singleShot(int(auto), app.quit)
    autotext = os.environ.get('MOSS_UI_AUTOTEXT')   # 冒烟: 自动发一句
    if autotext:
        def _fire():
            if _adapter is None:
                QTimer.singleShot(300, _fire)   # 等 adapter 拉起
                return
            win.inp.setText(autotext)
            win._send()
        QTimer.singleShot(500, _fire)
    autogo = os.environ.get('MOSS_UI_AUTOGO_MS')   # 冒烟: 定时自动点激活
    if autogo:
        QTimer.singleShot(int(autogo), win._go_clicked)
    autore = os.environ.get('MOSS_UI_AUTORECONNECT_MS')   # 冒烟: 自动重连
    if autore:
        QTimer.singleShot(int(autore), win._reconnect)
    t2 = os.environ.get('MOSS_UI_AUTOTEXT2')   # 冒烟: 重连后再发一句
    t2at = os.environ.get('MOSS_UI_AUTOTEXT2_MS')
    if t2 and t2at:
        def _fire2():
            win.inp.setText(t2)
            win._send()
        QTimer.singleShot(int(t2at), _fire2)
    app.exec()   # Qt 主线程事件循环; hub 在自己线程
    thread.halt()
    thread.wait(4000)


if __name__ == '__main__':
    main()
