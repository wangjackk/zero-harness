"""Moment —— 感知关键帧: 原版 ``blueprint/moment.py`` 同构(塌缩版).

原版 docstring 第一句即定义: "Moment — a keyframe of the agent's
context-aware perception". 为什么需要它(原版 Echoes docstring 原话):
"no current model supports full-duplex interaction, this adapter layer
is still needed to stitch the pieces back into one exchange" —— 世界
是流式/碎片/可中断的, 模型只认"一次完整交换", 帧就是缝合层.

结构(原版 Moment 四要素, 塌缩到 dict 级):
- ``previous: Echoes`` —— **缝合缝**: 上一拍系统实际执行的痕迹
  (动作回执 + 停止原因). 本拍的底: 脑子每拍先看见上拍干出了什么;
- ``percepts`` —— 本拍外界输入, **按源 keyed**(同源聚合不跨帧累积,
  原版 percepts 同构);
- ``dynamic_context`` —— 瞬态快照(原版 dynamic_context: 每帧重取
  最新, **永不入史** —— for_saving/history_turns 都丢);
- ``logos`` —— 本拍模型的输出(中断拍提交已执行的部分 —— 反幻觉:
  原版"无 logos 但有 executed_logos 的轮也必须缝进 history,
  otherwise they become pure hallucination").

五级防膨胀闸门(考据自原版源码, 全部就位):
① 帧内同源聚合(percepts 按源 keyed) ② 瞬态分离(dynamic_context
不入史) ③ 滑窗(deque 按**帧**计, max_frames 默认 100 = 原版
max_moments_size) ④ Epoch 压缩(new_epoch: 清窗转生 recap, 置顶
渲染) ⑤ to_history_turns(history_turns: 帧链重建回合制条目, 空
logos 不产 assistant 条目).

与原版的分界: 原版 MomentsObserver 挂在 mindflow(单体架构, 同进程
函数调用); 这里 Moments 是 ghost 的内部组件(非 routine) —— 帧的
产生处恰是 ghost.think, 记忆是灵魂的实体. 排空屏障(shell flush)
保证回执到达时拍的差事已尘埃落定 —— 缝合的另一半由拍边界控制流
承担(原版靠 Moment 沉淀碎片, 这里碎片到达时已在拍边界澄清).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = ['Echoes', 'Moment', 'Moments']


@dataclass
class Echoes:
    """回声(缝合缝的内容): 系统实际执行的痕迹.

    - ``frames``: 动作回执(obs 帧, 只报执行事实不复读内容);
    - ``stop_reason``: 上一拍怎么断的(空 = 正常跑完; 'interrupted'/
      超时/脑挂 —— 原版 stop_reason 织进下一帧 moment.previous 同构);
    - ``need_observe``: 有未观察的新回声(原版同名标志, add_echoes
      置位, new_moment 缝合时清 —— 回声帧循环的火种).
    """

    frames: List[Dict[str, Any]] = field(default_factory=list)
    stop_reason: str = ''
    need_observe: bool = False

    def render(self) -> List[Dict[str, Any]]:
        """回声 → 回合制条目: obs 帧 + interrupted 留痕(原版
        to_history_turns 的 echoes 半边; 原版 add_echoes 只 append
        非空消息同构 —— 空回声不渲染)."""
        out: List[Dict[str, Any]] = []
        if self.frames:
            out.append({'role': 'obs', 'frames': list(self.frames)})
        if self.stop_reason:
            out.append({'role': 'interrupted', 'reason': self.stop_reason})
        return out


@dataclass
class Moment:
    """关键帧: previous(缝合缝) + percepts(本拍输入, 按源) +
    dynamic_context(瞬态, 不入史) + logos(本拍输出)."""

    index: int = 0
    previous: Echoes = field(default_factory=Echoes)
    percepts: Dict[str, str] = field(default_factory=dict)
    #: 各源输入的**元信息**(``{源: {low_conf/db/paused}}``): 正文之外,
    #: 关于这段输入本身的事实 —— 哪几个字可能听错、多响、是不是说完了.
    #: 与 percepts 同 key(按源), 原版是挂在每条 Message 的 attributes 上;
    #: 我们按源聚合正文, 属性也只能按源, 否则一拍里多包会对不上.
    #: 存的是全部读数, 印进 prompt 的只有 paused(见 ``brain._listen``).
    percept_attrs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    dynamic_context: Dict[str, str] = field(default_factory=dict)
    logos: str = ''
    #: 开帧时刻(墙钟). 原版每条消息自带 ``created``(信号**到达**时间, 不是
    #: 渲染时间); 我们的 percepts 是拍级拼装(挂账还会并入更早的话), 所以
    #: 时间精度只到拍 —— 不假装比实际更准.
    created: float = field(default_factory=time.time)

    def for_saving(self) -> Dict[str, Any]:
        """持久视图: 丢 dynamic_context(原版 for_saving/to_json/
        as_history_messages 三处同丢瞬态)."""
        return {
            'index': self.index,
            'created': self.created,
            'previous': {'frames': list(self.previous.frames),
                         'stop_reason': self.previous.stop_reason},
            'percepts': dict(self.percepts),
            'percept_attrs': {k: dict(v)
                              for k, v in self.percept_attrs.items()},
            'logos': self.logos,
        }

    def render(self) -> List[Dict[str, Any]]:
        """帧 → 回合制条目(to_history_turns 的帧内半边): 缝合缝在前
        (先看见上拍干了什么), percepts 各源一条 user(带 ``created`` ——
        原版 ``<listen created=...>`` 的原料, 渲染在 brain 入口),
        logos 非空一条 assistant(空 logos 不产条目 —— 无话可说不是说了
        空话)."""
        out: List[Dict[str, Any]] = list(self.previous.render())
        for src, text in self.percepts.items():
            if text:
                item = {'role': 'user', 'text': text, 'source': src,
                        'created': self.created}
                if attrs := self.percept_attrs.get(src):
                    item['attrs'] = dict(attrs)
                out.append(item)
        if self.logos:
            out.append({'role': 'assistant', 'text': self.logos})
        return out


class Moments:
    """观察器(原版 BaseMomentsObserver 同构): 帧链 + 待缝回声 +
    瞬态槽 + Epoch.

    - ``new_moment(percepts)``: 开帧 —— 把 pending 回声缝成
      previous(need_observe 随之清), 动态槽快照进帧;
    - ``commit_logos(moment, text)``: 拍收尾落 logos(调用方保证一次;
      中断拍传已执行的部分 = 反幻觉缝合);
    - ``history_turns()``: 帧链重建回合制条目(recap 置顶 + 各帧 +
      pending 尾缀 —— pending 尾缀保证链刚收完/中断时回声立即可见,
      缝进下一帧后自然归位, 不双份);
    - ``feed()``: 脑子视图 = history_turns + 瞬态尾拼([源] 文本).
    """

    def __init__(self, max_frames: int = 100):
        self._max = max(1, int(max_frames))
        self._frames: deque = deque()
        self._pending = Echoes()          # 待缝进下一帧的回声
        self._dynamic: Dict[str, str] = {}   # 瞬态槽(同源覆盖)
        self._recap = ''
        self._index = 0
        self.dropped = 0   # 出窗帧数(观测面: 忘掉了多少拍)
        self.epochs = 0    # epoch 压缩次数(观测面: 窗内历史转生 recap)

    # --- 写 ---

    def add_echoes(self, frames: List[Dict[str, Any]]) -> None:
        """回声到达(身体回灌): 攒进 pending, 置 need_observe
        (原版 add_echoes + need_observe=True 同构)."""
        if frames:
            self._pending.frames.extend(frames)
            self._pending.need_observe = True

    def set_stop_reason(self, reason: str) -> None:
        """拍链中断留痕(原版 abort reason 织进下一帧 previous
        同构): 攒进 pending, 下一帧的缝合缝可见."""
        if reason:
            self._pending.stop_reason = reason
            self._pending.need_observe = True

    def new_moment(self, percepts: Optional[Dict[str, str]] = None,
                   percept_attrs: Optional[Dict[str, Dict[str, Any]]] = None,
                   ) -> Moment:
        """开帧: pending 回声缝成 previous(原版 observe() 产生 Moment
        同构), 动态槽快照, 滑窗按帧计(超限最旧帧出窗)."""
        moment = Moment(index=self._index, previous=self._pending,
                        percepts=dict(percepts or {}),
                        percept_attrs={k: dict(v) for k, v in
                                       (percept_attrs or {}).items()},
                        dynamic_context=dict(self._dynamic))
        self._index += 1
        self._pending = Echoes()   # 已缝走; need_observe 随之清
        self._frames.append(moment)
        while len(self._frames) > self._max:
            self._frames.popleft()
            self.dropped += 1
        return moment

    def commit_logos(self, moment: Moment, logos: str) -> None:
        """拍收尾: logos 落帧. 中断拍传已执行的部分(反幻觉: 执行了
        但模型没"说"完的话也必须入史, 原版 executed_logos 缝合同构)."""
        moment.logos = logos

    def set_dynamic(self, updates: Dict[str, str]) -> None:
        """瞬态槽刷新(原版 dynamic_context 每帧重取最新同构): 同源
        覆盖 —— 状态类输入永远只留一份"现在", 历史零膨胀."""
        for src, text in updates.items():
            if text:
                self._dynamic[str(src)] = str(text)

    def new_epoch(self, recap: str) -> None:
        """Epoch 压缩(原版 new_epoch: compact + recap + baseline):
        帧链出清转生 recap(不是忘 —— dropped 不计), recap 置顶渲染,
        epochs 计数. 空 recap / 无帧不动作(无事可压)."""
        if not recap or not self._frames:
            return
        self._frames.clear()
        self._pending = Echoes()
        self._recap = recap
        self.epochs += 1

    # --- 读 ---

    def need_observe(self) -> bool:
        """有未观察的新回声(原版同名: 回声帧循环的火种)."""
        return self._pending.need_observe

    def history_turns(self) -> List[Dict[str, Any]]:
        """帧链 → 回合制条目(to_history_turns): recap 置顶 + 各帧
        顺序渲染 + pending 尾缀. 脑子每拍吃的与 recall 读的同一份."""
        out: List[Dict[str, Any]] = []
        if self._recap:
            out.append({'role': 'recap', 'text': self._recap})
        for f in self._frames:
            out.extend(f.render())
        out.extend(self._pending.render())
        return out

    def feed(self) -> List[Dict[str, Any]]:
        """脑子视图: 回合制条目 + 瞬态尾拼(``role='transient'`` —— 模型
        每拍看到各源最新的"现在", 历史里从没有旧的).

        瞬态**不在这里拼文本**: 渲染是脑子入口的事(原版 Message 只在
        ``as_contents`` 时才变成 XML, 数据层始终结构化), 所以这里只给
        ``{role, source, text}``, 由 ``input_items`` 包成
        ``<dynamic_context>``.
        """
        turns = self.history_turns()
        if self._dynamic:
            tail = [{'role': 'transient', 'text': t, 'source': src}
                    for src, t in self._dynamic.items()]
            return turns + tail
        return turns

    def transients(self) -> Dict[str, str]:
        """瞬态槽视图(源 → 最新文本, 观测面)."""
        return dict(self._dynamic)

    def frames_view(self) -> List[Dict[str, Any]]:
        """帧视图(for_saving: 丢瞬态; 测试/观测面)."""
        return [f.for_saving() for f in self._frames]

    def pending_view(self) -> Dict[str, Any]:
        """待缝回声视图(观测面): 还没归进任何帧的漂到回执 + 火种旗
        (need_observe: 有未观察的新回声 —— UI 据此提示 /go 可消化)."""
        return {'frames': list(self._pending.frames),
                'stop_reason': self._pending.stop_reason,
                'need_observe': self._pending.need_observe}

    @property
    def recap(self) -> str:
        """Epoch 前情提要(观测面, 空 = 无压缩)."""
        return self._recap

    def __len__(self) -> int:
        return len(self._frames)
