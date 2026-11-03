"""Moss —— 一体: 一个灵魂(Ghost) + 一具身体(Shell), 一起带起来.
原版:github: https://github.com/GhostInShells/MOSShell

Moss 负责组装这一个灵魂 + 这一具身体, 造完就放手 —— 灵魂只管
字进字出, 身体只管解析分发, 谁也不越界. 动作词表由装配注入
(``verbs``), 身体照单守门; prompt 片段(``prompts``)由 Moss 装配时
拼**一次**注入灵魂 —— 前缀缓存友好, 不每轮重拼.

    Moss(创建) → Ghost(LLM I/O) ⇄ Shell(解析+分发+词表) → Channel(串行轨)
"""
from __future__ import annotations
import asyncio
from typing import Any, ClassVar, Dict, List

from pydantic import BaseModel, Field

from routine.logger import setup_logger
from routine.routine import Routine

from .container import CONTAINERS
from .ghost import Ghost
from .prompt.capability import canon

__all__ = ['Moss', 'BUILTIN_VERBS']

_log = setup_logger('mossv2.moss')

# 出厂词表: 身体的结构性 routine —— 装配自动注入, 不靠调用方记得.
# 容器名从类名算(唯一出处), 其余各自在自己的模块里. 全部是普通 routine
# (统一形态), 差别只在各自的类怎么解释 body、以及调度属性(call_soon).
# **名字必须与注册成对**: 留一个没有实例的名字, 清单里就多一行裸名字
# (不报错, 只是哑巴) —— 曾经的 ``clear`` 就是这样: 归族成 chan_clear 后
# 忘了从这里撤掉.
BUILTIN_VERBS = ['say', 'chan_create', 'chan_status', 'chan_clear', 'chan_delete',
                 'sleep', 'noop', 'interrupt', 'notify', 'routine/doc',
                 'code', 'asr_mode',
                 # 'bb_write', 'bb_line', 'bb_arrow', 'bb_rect', 'bb_poly',
                 # 'bb_circle', 'bb_ellipse', 'bb_ring', 'bb_underline',
                 # 'bb_point', 'bb_erase', 'bb_pixel', 'bb_objects',
                 *sorted(CONTAINERS)]


class MossInput(BaseModel):
    """moss 入参: 装配声明."""

    verbs: List[str] = Field(
        default_factory=list,
        description='初始动作词表(CTML 可调的 routine 名, 注入 Shell)')

    detail: List[str] = Field(
        default_factory=list,
        description='能力清单里详列的名字(印签名/参数/示例); 其余只印名字, '
                    '模型用 routine/doc 按需取. 空 = 全详列(前缀最长, 不会丢信息)')

    observe: List[str] = Field(
        default_factory=list,
        description='出厂回执名单(装配声明): 这些词每次调用都要回执. 写在这里'
                    '而不是 routine 的 meta 里 —— 跨身体共用的 routine 不该带'
                    '某一具身体的调用语法')

    prompts: List[str] = Field(
        default_factory=list,
        description='prompt 片段名单(按序拼接: 稳定在前易变在后, 注入 Ghost)')

    max_moments: int = Field(
        default=100,
        description='记忆滑窗上限(帧, 透传 Ghost; 原版 max_moments_size'
                    '=100 同参)')


class Moss(Routine):
    """一体组装: 带起 Ghost + Shell, 把两者接上, 然后常驻."""

    name = 'moss'
    is_passive = True

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '一体: passive 常驻, 组装并带起 Ghost(灵魂) + Shell(身体), '
            '接通即放手. 入参 verbs = 初始动作词表, prompts = prompt '
            '片段名单(Moss 拼好一次注入 Ghost 系统前缀).'
        ),
        'input_schema': MossInput.model_json_schema(),
    }

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = MossInput.model_validate(kwargs or {})
        self._verbs: List[str] = inp.verbs
        self._detail: List[str] = inp.detail
        self._observe: List[str] = inp.observe
        self._prompts: List[str] = inp.prompts
        self._max_moments: int = inp.max_moments
        self._stopped = asyncio.Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        verbs = [*BUILTIN_VERBS, *self._verbs]
        # 详列名单里写错一个字 = 那个能力静默退回裸名(用法得靠 routine/doc 才拿到).
        # 不当场炸: 拉起失败 = 灵魂不在, 一个笔误不该让整具身体起不来 ——
        # 但错误要看得见(日志里点名), 不能悄悄少印一块.
        unknown = [d for d in self._detail if canon(d) not in set(verbs)]
        if unknown:
            _log.error('detail 里有词表外的名字(已忽略, 它们会退回裸名): %s',
                       ', '.join(unknown))
        detail = [canon(d) for d in self._detail if canon(d) in set(verbs)]
        # 同 detail: 写错一个字就是那个词静默不点灯(查询类白调), 当场点名.
        bad_obs = [o for o in self._observe if canon(o) not in set(verbs)]
        if bad_obs:
            _log.error('observe 里有词表外的名字(已忽略, 不会出厂点灯): %s',
                       ', '.join(bad_obs))
        observe = [canon(o) for o in self._observe if canon(o) in set(verbs)]
        shell = await self.create(
            'shell', {'verbs': verbs, 'detail': detail, 'observe': observe})
        await shell.start()
        # 默认轨: 装配预建 main 一条(灵魂到来说话前就绪). 开新轨是
        # 灵魂的显式决策(chan_create 动作), 装配不代劳
        await self.req(shell.id, 'chan', {'op': 'create', 'name': 'main'})
        # prompt 片段并发取(片段互不依赖, call 各自独立), gather 保序
        # —— 按名单序拼一次注入(前缀缓存: 不每轮重拼; 片段失败 =
        # 装配错误, 当场炸)
        frags = await asyncio.gather(*[self.call(p) for p in self._prompts])
        system = '\n\n'.join(frags)
        # 脑子是灵魂的零件: 谁组装灵魂谁负责插上. 装配缺省 = 真脑
        # (一个 Ghost, 脑子是外设); 外部/测试已经注入过就不动 ---- None
        # 才表示"还没插", 所以清单起和代码起拿到的是同一颗脑子.
        if Ghost.brain_factory is None:
            from .brain import Brain
            Ghost.brain_factory = Brain
        ghost = await self.create(
            'ghost', {'shell_id': shell.id, 'system': system,
                      'max_moments': self._max_moments})
        await ghost.start()
        # 桥梁: 何时想/想几拍归 mindflow(拍循环 + 对话屏障), 接通即放手
        mindflow = await self.create('mindflow', {'ghost_id': ghost.id})
        await mindflow.start()
        _log.info('moss up: ghost=%s shell=%s mindflow=%s',
                  ghost.id, shell.id, mindflow.id)
        # 常驻: run 退出 = routine 终止. 等的是 stop() 点的灯, 不是框架
        # 3s 后的兜底 cancel ---- 后者会让每次退出都白等三秒(父还要再叠一层).
        await self._stopped.wait()

    async def stop(self) -> None:
        self._stopped.set()
