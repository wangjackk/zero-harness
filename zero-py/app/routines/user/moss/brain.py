"""真脑: Ghost 的 LLM 接线(``brain_factory`` 注入 Ghost 的脑子).

灵魂不变, 换的只是脑子: LLMClient 走 openai-responses 流式,
``system``(装配时拼好的前缀) → instructions, 记忆 → input items
(全量, **按原版块形态渲染**: ``<percepts><listen source=.. created=..>``
/ ``<observe>`` / ``<stop_reason>`` / ``<dynamic_context>`` / ``<recap>``
—— 数据层是结构化的, 渲染只发生在这一处, 对应原版
``Message.as_contents(with_meta=True)``). 前缀缓存友好 ——
instructions 稳定, 历史只增尾部. epoch 摘要(ghost epoch 入口)走同一
``stream`` 协议: history=窗口, system=摘要指令.

思考(reasoning delta)不进对话流, 也不进 logos —— 它走一条旁路: 灵魂
若挂上 ``on_reasoning``, 脑子就把"心里话"交给它(灵魂只广播给 UI, 让人
看得见它正在想). 首字延迟可以有八九秒, 那段时间思考流是唯一还活着的
信号. 脑子本身不认识 pubsub/UI, 出口由使用者注入.

Completed 的 response_id/usage(增量链 previous_response_id)暂不用,
实测需要时再接.
"""
from __future__ import annotations

from datetime import datetime
from typing import (
    Any, AsyncIterator, Awaitable, Callable, ClassVar, Dict, List, Optional,
)

from routine.logger import setup_logger

from ..agents._core.llm import LLMClient, ReasoningDelta, TextDelta
from ..agents._core.uploads import resolve_data_url
from .frames import frame_line

__all__ = ['Brain', 'input_items']

_log = setup_logger('moss.brain')


def _stamp(ts: Any) -> str:
    """墙钟 → 原版短时间戳 ``D19 00:01:17+8``（照抄
    ``message.format_timestamp``）。

    只留日 + 时分秒 + 时区小时: 年/月由会话的"今天"承担, 模型没有时钟,
    这个属性是它判断"这话多久之前说的"的唯一依据. 空/坏值 → ``''``
    （属性就整个不写, 原版 ``gen_attributes`` 同构: 没有的维度不占位）.
    """
    if not ts:
        return ''
    try:
        dt = datetime.fromtimestamp(float(ts)).astimezone()
    except (TypeError, ValueError, OSError):
        return ''
    tz = ''
    if (offset := dt.utcoffset()) is not None:
        total = int(offset.total_seconds())
        sign = '+' if total >= 0 else '-'
        hours, rem = divmod(abs(total), 3600)
        minutes = rem // 60
        tz = f'{sign}{hours}' if minutes == 0 else f'{sign}{hours}:{minutes:02d}'
    return f"D{dt.strftime('%d')} {dt.strftime('%H:%M:%S')}{tz}"


def _attr(value: Any) -> str:
    """属性值转义(拼进 ``<listen ...>`` 的单引号/尖括号都得让位).

    识别结果理论上可以含任何字符(引擎把 ``<`` 当成一个字吐出来并非
    不可能). 不转义的后果不是"显示难看", 是**块结构被撑破** —— 模型
    看到的是一段坏 XML, 它会去猜边界, 猜错就整句读歪.
    """
    return (str(value).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _listen(source: str, created: Any, text: str,
            attrs: Optional[Dict[str, Any]] = None) -> str:
    """一条听来的话 → 原版 ``<listen source=".." created="..">``（照抄
    ``listener_nucleus._listen_message``: tag 名固定, 源只走属性）。

    听侧读数里**只印 ``paused``**: 这一段还没说完(**只在 false 时印** ——
    现在进脑子的包都是收尾包, 恒真的值写进每一行只是每拍付一遍字数).

    ``low_conf`` / ``db`` **暂不印**(2026-10-25): 两样由听侧算好、随包送到
    ``attrs``(``mindflow._percept_attrs`` → ``Moment.percept_attrs``), 数据
    一路都在, 闸门关在这一处 —— 哪天要露给模型, 在这里加两个分支就够, 上游
    一层都不用动. 注意 ``attrs`` 里出现这两个键是**正常**的, 不是没接上.

    没有的属性不占位(原版 ``gen_attributes`` 同构: 空字符串印成
    ``low_conf=""`` 只会让模型去猜"空是什么意思")。
    """
    attrs = attrs or {}
    out = f' source="{_attr(source)}"' if source else ''
    if stamp := _stamp(created):
        out += f' created="{stamp}"'
    if attrs.get('paused') is False:
        out += ' paused="false"'
    return f'<listen{out}>\n{text}\n</listen>'


def _frame_images(frames: Any) -> List[str]:
    """obs 帧里的图: 载荷 ``images``(本地路径)按帧序展开.

    图不走 ``content`` 那一行(一张图几十万字符), 它是附在消息上的多模态
    block —— 所以从载荷里取, 顺序天然是"这一拍先看了哪张, 后看了哪张".
    """
    out: List[str] = []
    for f in frames or []:
        r = f.get('result') if isinstance(f, dict) else None
        if isinstance(r, dict):
            out.extend(str(p) for p in (r.get('images') or []) if p)
    return out


def _with_images(text: str, frames: Any, *, inline: bool = True) -> Any:
    """``<observe>`` 文本 + 图 → responses 的多模态 content blocks.

    形状照抄原版/agent 侧(``input_text`` + 若干 ``input_image``): 文本在前
    图在后, 模型先读到"这是什么图"再看见图本身. 读不出图的路径就地丢掉
    —— 文件被删/权限不对时, 只剩那句话, 比发一个坏 URL 让整次请求 400 好.

    ``inline=False``: 只留块的位置(``image_url`` 空串), 不读文件不编码 ——
    给**回读**用. 回读的读者是人/界面, base64 对它是噪音; 而心跳每秒拉一次
    recall, 真编码就是每秒把同一张图读一遍再转 13 万字符. 块的形态一致
    ("这里有一张图"), 内容不必一致(那不是给人看的).

    一条都附不上就退回纯文本: 别给模型一个只有 ``input_text`` 的 blocks
    数组, 那跟字符串没有任何区别, 只是多了一层没人需要的结构.
    """
    blocks: List[Dict[str, Any]] = [{'type': 'input_text', 'text': text}]
    for path in _frame_images(frames):
        url = resolve_data_url(path) if inline else ''
        if url or not inline:
            blocks.append({'type': 'input_image', 'image_url': url})
    return blocks if len(blocks) > 1 else text


def input_items(history: List[Dict[str, Any]], *,
                supports_images: bool = False,
                inline_images: bool = True) -> List[Dict[str, Any]]:
    """记忆 → Responses input items(全量), **按原版形态渲染**.

    数据层始终保持结构化(``role`` / ``source`` / ``created`` /
    ``attrs``), 只有进脑子这一刻才变成文本块 —— 这就是原版 ``Message.
    as_contents(with_meta=True)`` 的位置(``message.py:431-465``), 不是两套
    数据. ``attrs`` 就是原版 Message 的 attributes(听侧读数), 数据层原样
    存 dict, 到 ``_listen`` 才拼成属性串.

    块形态(照抄原版 ``blueprint/moment.py`` 的折叠结果):

    - 输入块 ``<percepts>``(``:343-349``): 这一拍外界说了什么.
      其中 ``source`` 非 ``user`` 的一条包 ``<listen source=".."
      created="..">``(``listener_nucleus.py:361-370``: tag 名固定
      ``listen``, 源只走属性); ``source`` 就是 ``user`` 的(UI 打字)
      直接裸文本 —— 原版只有"听来的话"带 tag, 文本输入没有.
      **听来的话最多带一个听侧专有属性**(``paused="false"``: 还没说完);
      打字没有 —— 模型据此知道什么时候可以插话. 另两个读数
      (``low_conf`` / ``db``)数据照旧送到, 渲染层暂不印, 见 ``_listen``.
    - ``<observe>``(``command.py:799/814``): 上一拍身体实际干了什么,
      一条回执行一行(``frames.frame_line`` 是唯一写法, 只取载荷
      ``content`` —— ``details`` 没有通往 prompt 的路).
      载荷带 ``images`` 时, 这一条消息的 content 变成多模态 blocks
      (``<observe>`` 文本 + 图), 见 ``_with_images``.
    - ``<stop_reason>``(``moment.py:289``): 上一拍怎么断的.
    - ``<dynamic_context>``(``moment.py:341``): 各源最新的"现在",
      永不入史(历史里从没有旧的).
    - ``<recap>``: epoch 压缩的前情提要, 置窗顶. 原版这条通路没接
      (``with_epoch_recap`` 全库无注册方), tag 名是我们定的; 内容换
      全角尖括号 —— 那是模型自己写的摘要, 出现指令标签只会诱发复读.

    连续的输入条目合并成**一条** user 内容(块内换行): 原版一拍的所有
    percepts 本来就折在一个 ``<moment>`` 的 ``<percepts>`` 里, 拍内各源
    是一起的, 不是多条"用户消息".

    **图只挂在最近一次带图的观测上**(``supports_images=True`` 时): 历史全量
    重放, 若每一拍的图都跟着发, 一张截图 base64 几十万字符, 每拍付一遍,
    几拍就把窗口烧穿. 所以图是"我正在看", 不是"我记得" —— 更早的观测只剩
    那行文本(路径/尺寸/字节数都在, 要看就再读一次, 便宜). 判据同
    ``frames``: 模型能拿到的非文本内容只有这一个字段.

    **只此一处渲染**: ``stream`` 用它喂脑子, 灵魂 ``recall`` 经
    ``Brain.render_history`` 用它回读("模型这一拍到底读到什么") —— UI
    显示的是同一份产物, 不是照着规则再拼一遍.
    """
    items: List[Dict[str, Any]] = []
    percepts: List[str] = []
    transients: List[str] = []

    # 图挂在哪一拍: 只有最近一次带图的观测(见 docstring)
    last_images_at = -1
    if supports_images:
        for i, h in enumerate(history):
            if h.get('role') == 'obs' and _frame_images(h.get('frames')):
                last_images_at = i

    def flush_percepts() -> None:
        if percepts:
            items.append({'role': 'user',
                          'content': '<percepts>\n'
                                     + '\n'.join(percepts)
                                     + '\n</percepts>'})
            percepts.clear()

    def flush_transients() -> None:
        if transients:
            items.append({'role': 'user',
                          'content': '<dynamic_context>\n'
                                     + '\n'.join(transients)
                                     + '\n</dynamic_context>'})
            transients.clear()

    for idx, h in enumerate(history):
        role = h.get('role')
        if role == 'transient':
            src = str(h.get('source') or '')
            text = str(h.get('text') or '')
            if text:
                transients.append(f'{src}: {text}' if src else text)
            continue
        flush_transients()
        if role == 'obs':
            flush_percepts()
            frames = h.get('frames') or []
            lines = [frame_line(f) for f in frames]
            if lines:
                text = '<observe>\n' + '\n'.join(lines) + '\n</observe>'
                items.append({
                    'role': 'user',
                    'content': (_with_images(text, frames, inline=inline_images)
                                if idx == last_images_at else text)})
        elif role == 'interrupted':
            flush_percepts()
            reason = str(h.get('reason') or '')
            items.append({'role': 'user',
                          'content': f'<stop_reason>{reason}</stop_reason>'})
        elif role == 'recap':
            flush_percepts()
            text = str(h.get('text', '')).replace(
                '<', '〈').replace('>', '〉')
            items.append({'role': 'user',
                          'content': f'<recap>\n{text}\n</recap>'})
        elif role == 'user':
            text = str(h.get('text') or '')
            if not text:
                continue
            src = str(h.get('source') or 'user')
            if src == 'user':
                percepts.append(text)
            else:
                percepts.append(_listen(src, h.get('created'), text,
                                        h.get('attrs')))
        else:
            flush_percepts()
            items.append({'role': role, 'content': h.get('text', '')})
    flush_percepts()
    flush_transients()
    return items


class Brain:
    """真脑: LLMClient 接线(models.yaml 配置, 流式). 协议一个入口
    ``stream(history, system)`` —— Ghost 不认识 LLM, 只认脑子."""

    # models.yaml 的模型 key;
    model: ClassVar[str | None] = 'deepseek/deepseek-flash'
    # 'openai-responses' | 'openai-completions'; None = 配置矩阵推导.
    # 两协议同吃 [input, instructions] 入口, 分叉在 LLMClient 内部.
    protocol: ClassVar[str | None] = None

    def __init__(self):
        self._llm = self._make_client()
        # 思考出口(灵魂注入; None = 思考只丢不外流). 不是对话: 正文走
        # ``stream`` 的 yield, 心里话走这条回调 —— 一个脑子一个出口.
        self.on_reasoning: Callable[[str], Awaitable[None]] | None = None

    def _make_client(self) -> Any:
        """造 LLM 客户端(测试覆盖点: 换假脑不碰网络)."""
        return LLMClient(model=self.model, protocol=self.protocol)

    #: 回读能力(可选): 灵魂 ``recall`` 问"你这一拍读到的原样是什么"
    #: 时调它 —— 渲染只有这一个产地, UI 不重写一遍块形态.
    def render_history(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """回读 = 与 ``stream`` **同一套块形态**(同一个 ``input_items``,
        同一个"图只挂最近一拍"判据), 图只留位置不内联.

        "同一份" 说的是**形状与取舍规则**, 不是字节: 真发出去的那份里
        ``image_url`` 是 base64, 回读给它空串(``inline_images=False``) ——
        回读的读者是界面, base64 是噪音, 而心跳每秒拉一次 recall, 真编码
        等于每秒把同一张图读一遍. 界面上那一块仍显示 ``[image]``."""
        return input_items(history, supports_images=self.supports_images,
                           inline_images=False)

    @property
    def supports_images(self) -> bool:
        """本模型吃不吃图(配置的 ``image``). 假脑(测试)没有这个属性 →
        ``getattr`` 兜底 False, 也就是"不附图"那一侧."""
        return bool(getattr(getattr(self, '_llm', None), 'supports_images', False))

    async def stream(self, history: List[Dict[str, Any]],
                     system: str) -> AsyncIterator[str]:
        """字流: TextDelta 直通(它要说的话); 思考走 ``on_reasoning``
        旁路(它正在想); Completed 等收尾事件不进对话."""
        async for ev in self._llm.stream(
                input_items(history, supports_images=self.supports_images),
                instructions=system or None):
            if isinstance(ev, ReasoningDelta):
                if ev.text and self.on_reasoning is not None:
                    await self.on_reasoning(ev.text)
                continue
            if isinstance(ev, TextDelta) and ev.text:
                yield ev.text
