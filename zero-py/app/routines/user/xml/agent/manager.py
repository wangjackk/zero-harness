"""XmlAgents -- resident passive routine that spawns xml_agent children.

is_passive=True -> kernel auto-starts on connect (single resident instance).
Driven via req (bridge -> entry routine -> manager):

  - create_agent  : create+start a XmlAgent child, return agent_id
  - list_agents   : enumerate agents (live + historical, from the sqlite Memory)
  - stop_agent    : cascade-stop a child

Mirrors prime/manager.py. Agent records persist in the sqlite Memory
(xml_agent/memory.py agents table): create writes a row, list reads from
the db. agent_id 是身份, session_id 是 UUID 会话身份 (agents 表持久化). 无线性链. status/routine_id
是运行时态, 由 manager 内存管 (self._agents dict), 不持久化. live 状态跨重启
全丢失, list_agents 通过内存判断 live/stopped.

Each child is an independent XmlAgent instance, isolated by agent_id (own
pubsub namespace). 一个 agent 一个 session (session_id 是 UUID), resume = 重启
某个 agent_id (历史消息按 session_id 自动加载).
"""
from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from routine import Routine, request, subscribe
from routine.logger import setup_logger

from .agent import XmlAgent
from .memory import get_memory
from .llm import LLMClient, _DEFAULT_MODEL

_log = setup_logger('xml_agent.manager')

# child routine name -- what manager creates.
_AGENT_ROUTINE_NAME = 'xml/agent'
# resident manager routine name -- what XmlAgentCreate locates by name to
# req into.
_MANAGER_NAME = 'xml/agent/manager'

# OpenViking 不默认启用: 前端未传 ov_config 时为 None, agent 只用本地
# sqlite memory (provider 对 None 有完整降级路径). 要长期记忆显式传配置.

# agent 类型 manifest: manager 启动时主动 register 到 agent_hub
# (hub 纯被动, 不扫描 catalog), 前端经 agent_hub/types 查类型清单动态渲染创建表单.
_TYPES_HUB_NAME = 'agent_hub'
_MANIFEST: Dict[str, Any] = {
    'kind': 'xml',
    'label': 'XML Agent',
    'description': 'XML 协议 agent: 工具调用以 XML 指令流驱动, 事件/记忆/巩固体系自管理',
    'can_resume': True,
    'uses_model': True,
    'uses_routines': True,
    'uses_aliases': True,
    'routines': {
        'create': 'xml/agent/create',
        'list': 'xml/agent/list',
        'stop': 'xml/agent/stop',
        'resume': 'xml/agent/resume',
        'delete': 'xml/agent/delete',
    },
    'create_fields': [
        {'name': 'agent_id', 'label': 'Agent ID', 'type': 'text',
         'required': False, 'placeholder': 'auto-generated'},
    ],
}


async def _find_rid_by_name(routine: 'Routine', name: str) -> Optional[str]:
    """按 name 查 running routine 的 rid (单次, 竞态重试在调用方)."""
    for r in await routine.get_running_routines():
        if str(r.get('name') or '') == name:
            rid = str(r.get('id') or '').strip()
            if rid:
                return rid
    return None


def _clean_protocol(value: Any) -> Optional[str]:
    """protocol 入参清洗: 空串/空白 -> None (未指定 = 矩阵推导)."""
    v = str(value or '').strip()
    return v or None


# spawn 可选配置键: create 时快照持久化 (agents 表 init_kwargs 列),
# resume 时合并恢复 ---- 重启不丢配置 (prompt_routines 白名单 / 显式
# routine 别名等). 运行时漂移经 memory.merge_init_kwargs 回写同一列.
_SPAWN_OPTIONAL_KEYS = ('ov_config', 'reasoning', 'condense_config',
                        'preload_skills', 'level1_skills', 'prompt_routines',
                        'routine_aliases')


def _snapshot_init_kwargs(data: dict) -> dict:
    """spawn 可选配置快照: data 里非 None 的可选字段子集 (持久化用)."""
    return {k: v for k, v in (data or {}).items()
            if k in _SPAWN_OPTIONAL_KEYS and v is not None}


async def _find_manager_rid(routine: 'Routine') -> Optional[str]:
    """按 name 定位常驻 manager routine id (running 列表里找, 5s 上限)."""
    for _ in range(50):
        try:
            routines = await routine.get_running_routines()
        except Exception as exc:
            _log.warning('get_running failed (%r), retry', exc)
            routines = []
        for r in routines:
            if str(r.get('name') or '') == _MANAGER_NAME:
                rid = str(r.get('id') or '').strip()
                if rid:
                    return rid
        await asyncio.sleep(0.1)
    return None


class XmlAgentsInput(BaseModel):
    pass


class XmlAgentsOutput(BaseModel):
    pass


class XmlAgents(Routine):
    """resident passive manager: dynamically create/list/stop xml agents.

    agent records persist in the sqlite Memory so the roster survives restart;
    live handles held in-memory only for stop + status annotation.
    """

    is_passive = True
    name = 'xml/agent/manager'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': XmlAgentsInput.model_json_schema(),
        'output_schema': XmlAgentsOutput.model_json_schema(),
        'description': (
            'Resident manager for xml agents. Spawns/lists/stops XmlAgent '
            'child instances on request. Agent records persist to sqlite; each '
            'child runs concurrently, isolated by agent_id.'
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop = asyncio.Event()
        # agent_id -> {'handle': RoutineHandle, 'session_id': str, 'model': str|None}
        # only live agents; historical ones live in the db.
        self._agents: Dict[str, dict] = {}
        # focus 模型: 语音等广播输入只路由给 main agent (route once,
        # 不让所有 agent 各自抢话). agent_id; None = 无 focus, 语音丢弃.
        self._main_id: Optional[str] = None
        # agent_ids with an in-flight stop (handle.stop() not yet acked).
        # guards against concurrent stop reqs (tab close + Stop button, or a
        # double-click): the second sees the agent already stopping and returns
        # ok idempotently instead of hitting handle's "another start/stop in
        # flight" RuntimeError.
        self._stopping: set[str] = set()
        # wake_track 直持 handle: 唤醒即 stop 旧的重新武装(窗口重新计时
        # + 实例唯一); Lock 串行并发 wake 帧的 re-arm.
        self._wake_track = None
        self._wake_track_lock = asyncio.Lock()
        self._mem = get_memory()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """resident: wait for stop. children outlive individual req handlers."""
        _log.info('xml agents manager started')
        asyncio.create_task(self._manifest_loop())
        asyncio.create_task(self._resume_last_chat())
        await self._stop.wait()

    async def _resume_last_chat(self) -> None:
        """kernel 重启后恢复最近聊的 agent 并接管语音 (main).

        updated_at 每次写消息都刷新, list_agents 首位即最近聊的.
        重启后无 live agent, 语音路由没有 focus 全丢 ---- 默认拉回
        最近那个. 空库 / resume 失败仅记日志, 不影响 manager 启动;
        之后 UI 打开别的 agent 时 set_main 显式覆盖.
        """
        try:
            agents = self._mem.list_agents()
            if not agents:
                return
            latest = str(agents[0].get('agent_id') or '')
            if not latest:
                return
            result = await self.on_resume(None, {'agent_id': latest})
            if result.get('ok'):
                _log.info('main agent restored to last chatted: %s', latest)
            else:
                _log.warning('resume last chatted agent %s failed: %s',
                             latest, result.get('error'))
        except Exception as exc:
            _log.warning('restore last chatted agent failed: %r', exc)

    async def _manifest_loop(self) -> None:
        """周期 ping agent_hub (10s), 不通就重新 find + register.

        hub 纯被动, manager 主动注册 ---- 但 agent_hub 实例重建
        (kernel 重启 / watcher 热载) 会换 rid 且内存注册表清零,
        一次性注册 (成功即止) 对此不感知. 常态一次轻 ping 探活,
        失联 (hub 挂了 / 换 rid) 即回到 find+register 快速重试.
        """
        last_rid: Optional[str] = None
        while not self._stop.is_set():
            interval = 10.0
            try:
                if last_rid:
                    await self.req(last_rid, 'ping', {}, timeout=2.0)
                else:
                    rid = await _find_rid_by_name(self, _TYPES_HUB_NAME)
                    if rid:
                        await self.req(rid, 'register', _MANIFEST)
                        last_rid = rid
                        _log.info('agent manifest registered (kind: %s)',
                                  _MANIFEST['kind'])
            except Exception as exc:
                _log.warning('agent_hub ping/register retry: %r', exc)
                last_rid = None
            if not last_rid:
                interval = 0.5
            await asyncio.sleep(interval)

    async def stop(self) -> None:
        # 注销 (尽力而为; hub 已停则静默放弃)
        try:
            rid = await _find_rid_by_name(self, _TYPES_HUB_NAME)
            if rid:
                await self.req(rid, 'unregister', {'kind': _MANIFEST['kind']})
        except Exception as exc:
            _log.warning('agent_types unregister on stop: %r', exc)
        # cascade-stop all live children. status 不持久化, 内存清空即可.
        for agent_id in list(self._agents):
            info = self._agents.get(agent_id)
            if info is None:
                continue
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                try:
                    await handle.stop()
                except Exception as exc:
                    _log.warning('stop child %s on manager stop: %r', agent_id, exc)
        self._agents.clear()
        # wake_track 也是本 manager create 的 child, 一并收口
        handle = self._wake_track
        self._wake_track = None
        if handle is not None and not handle.is_done():
            try:
                await handle.stop()
            except Exception as exc:
                _log.warning('stop wake_track on manager stop: %r', exc)
        self._stop.set()

    @subscribe('voice')
    async def _handle_voice(self, source, data):
        """voice 总线事件按 focus 路由 (route once).

        - result: 识别文本 → main agent chat_message (高优先级, 打断在途
          react ---- 语音是新输入).
        - wake: 唤醒即打断 main agent 在途 react (用户开口, 旧回合作废;
          文本随后由 result 到达). 只打断, 不进上下文 ---- 纯 interrupt,
          唤醒词不是对话内容.
        - aborted/state: 系统事件, 不进 agent.
        无 main 或 main 已死: 丢弃.
        """
        data = data or {}
        kind = data.get('kind')
        if kind not in ('result', 'wake'):
            return
        target = self._main_live()
        if target is None:
            _log.warning('voice %s dropped: no live main agent', kind)
            return
        agent_id, handle = target
        if kind == 'wake':
            # 唤醒即具身响应: 打断在途对话 + 武装 wake_track(声源定向粗调 +
            # 视觉精调). fire-and-forget ---- 车离线等失败不阻塞语音链.
            # 带上本次唤醒角: arm 建链异步已错过该广播, 由 wake_track
            # 首轮立即执行(wakes=1 起算), 无需等下一次唤醒.
            asyncio.create_task(self._arm_wake_track(data.get('cal_angle')))
            try:
                await self.req(handle.id, 'interrupt', {})
            except Exception as exc:
                _log.warning('route wake to main %s failed: %r', agent_id, exc)
            return
        from ...agents._core.messaging import build_content
        try:
            await self.req(handle.id, 'chat_message', {
                'content': build_content(str(data.get('text') or '')),
                'from': 'asr',
            })
        except Exception as exc:
            _log.warning('route voice to main %s failed: %r', agent_id, exc)

    async def _arm_wake_track(self, cal_angle=None) -> None:
        """唤醒即重新武装 car/wake_track: stop 旧实例(打断在跑 align/
        track, finally 零速)再带 first_cal_angle create 新实例 ---- 每次
        唤醒窗口重新计时, 实例恒唯一不堆积. 车离线等 CreateError 静默
        降级 ---- 语音链不受影响. Lock 串行化并发 wake 帧的 re-arm."""
        async with self._wake_track_lock:
            handle = self._wake_track
            self._wake_track = None
            if handle is not None and not handle.is_done():
                try:
                    await handle.stop()
                except Exception as exc:
                    _log.warning('stop old wake_track: %r', exc)
            try:
                handle = await self.create(
                    'car/wake_track', {'first_cal_angle': cal_angle})
                await handle.start()
                self._wake_track = handle
            except Exception as exc:
                _log.info('wake_track arm failed (car offline?): %r', exc)

    def _main_live(self) -> Optional[tuple]:
        """(agent_id, handle) if main agent is live, else None.

        main 不在 live 表 (stopped/removed) 时自动清 focus ---- 持有过期
        focus 只会让语音静默丢进死信箱.
        """
        mid = self._main_id
        if mid is None:
            return None
        info = self._agents.get(mid)
        handle = info.get('handle') if info else None
        if handle is None or handle.is_done():
            self._main_id = None
            return None
        return mid, handle

    @request('set_main')
    async def on_set_main(self, source, data: dict) -> dict:
        """指派 main agent (focus). agent_id 必须当前 live."""
        agent_id = str((data or {}).get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        info = self._agents.get(agent_id)
        handle = info.get('handle') if info else None
        if handle is None or handle.is_done():
            return {'ok': False, 'error': f'agent {agent_id} is not live'}
        old = self._main_id
        self._main_id = agent_id
        _log.info('main agent: %s -> %s', old, agent_id)
        # focus 变更广播: 订阅方(如 car/gaze 的 main 过滤)免逐次 req 查询,
        # 本地缓存对照. ns 'xml_agent'(实现方域, 同 agent_status).
        await self.publish('agent_focus', {'agent_id': agent_id},
                           namespace='xml_agent')
        return {'ok': True, 'agent_id': agent_id}

    @request('get_main')
    async def on_get_main(self, source, data: dict) -> dict:
        """查询当前 main agent (focus). 挂了返回 None."""
        target = self._main_live()
        if target is None:
            return {'agent_id': None}
        return {'agent_id': target[0], 'routine_id': target[1].id}

    # ------------------------------------------------------------------
    # req handlers (bridge -> entry routine -> manager)
    # ------------------------------------------------------------------

    @request('create_agent')
    async def on_create(self, source, data: dict) -> dict:
        """创建新 XmlAgent. agent_id 不传则自动生成 (xml_<N> 自增);
        传了且 DB 已存在则拒绝 (那是 resume 的语义).
        """
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if agent_id:
            if self._mem.get_agent(agent_id) is not None:
                return {'ok': False, 'error': f'agent_id {agent_id} already exists; use resume instead'}
        else:
            agent_id = self._mem.next_agent_id()
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        session_id = uuid4().hex
        model = data.get('model') or _DEFAULT_MODEL
        protocol = _clean_protocol(data.get('protocol'))
        result = await self._spawn_child(
            agent_id, data, is_resume=False, session_id=session_id,
            model=model, protocol=protocol)
        # spawn 成功后才持久化: probe 失败 (未知 model / 未声明协议 / 非法
        # 档位) 不留脏记录. child 侧只 UPDATE 不 INSERT, 无竞态.
        if result.get('ok'):
            try:
                self._mem.register_agent(
                    agent_id, session_id=session_id, model=model,
                    protocol=protocol,
                    init_kwargs=_snapshot_init_kwargs(data))
            except Exception as exc:
                _log.warning('persist agent %s failed: %r', agent_id, exc)
        return result

    @request('resume_agent')
    async def on_resume(self, source, data: dict) -> dict:
        """恢复已停止的 xml agent. agent_id 必传, 必须已存在 DB, 当前不能 live.
        child 从 messages 表按 session_id (agents 表持久化的 UUID) replay 历史.
        """
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} already live'}
        agent_rec = self._mem.get_agent(agent_id)
        if agent_rec is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not found'}
        session_id = str(agent_rec.get('session_id') or '') or uuid4().hex
        # model / protocol 优先级: 显式传参 > agent 记录持久化值 > 全局默认.
        # resume 不换模型 ---- 持久化值即会话创建时实际生效的 model.
        model = data.get('model') or agent_rec.get('model') or _DEFAULT_MODEL
        protocol = (_clean_protocol(data.get('protocol'))
                    or agent_rec.get('protocol') or None)
        # spawn 可选配置同优先级链: 创建时的 init_kwargs 快照打底,
        # 本次显式传参(非 None)覆盖 ---- 重启/resume 不丢白名单等配置.
        saved = agent_rec.get('init_kwargs') or {}
        data = {**saved, **{k: v for k, v in (data or {}).items()
                            if v is not None}}
        return await self._spawn_child(
            agent_id, data, is_resume=True, session_id=session_id,
            model=model, protocol=protocol)

    async def _spawn_child(
        self, agent_id: str, data: dict, *, is_resume: bool, session_id: str,
        model: str, protocol: str | None = None,
    ) -> dict:
        """公共 spawn 逻辑: 解析参数 → create+start child → 记录 live.

        create / resume 共用, 区别仅在前置校验 (agent_id 来源 + DB 检查) 和
        register_agent (由调用方各自处理). model / protocol 由调用方解析完毕
        传入 (create/resume 各有自己的优先级链).

        可选配置 (_SPAWN_OPTIONAL_KEYS): data 里没传的字段不写入 child_kwargs,
        XmlAgent.on_started 用 .get() 取时得 None -> 走默认 (ov_config=None
        只用本地 sqlite memory, prompt_routines=None 无工具表, 其余禁用).
        """
        child_kwargs: Dict[str, Any] = {
            'agent_id': agent_id,
            'session_id': session_id,
            'model': model,
            'protocol': protocol,
        }
        for opt_key in _SPAWN_OPTIONAL_KEYS:
            if data.get(opt_key) is not None:
                child_kwargs[opt_key] = data[opt_key]
        try:
            # 预检 LLM 配置 (model/protocol 组合 + reasoning 覆盖档位):
            # 在 create 前拦截未知 model / 未声明协议 / 非法档位, spawn
            # 直接返回错误而不是起一个坏 child.
            probe = LLMClient(model, protocol=protocol)
            effort = (data.get('reasoning') or {}).get('effort')
            if effort:
                probe.set_reasoning_effort(
                    None if effort == 'none' else effort)
            reasoning_effort = probe.reasoning_effort
            handle = await self.create(_AGENT_ROUTINE_NAME, child_kwargs)
            await handle.start()
        except Exception as exc:
            action = 'resume' if is_resume else 'create'
            _log.error('%s xml agent %s failed: %r', action, agent_id, exc)
            return {'ok': False, 'error': str(exc)}

        self._agents[agent_id] = {
            'handle': handle,
            'model': model,
            'protocol': protocol,
            'reasoning_effort': reasoning_effort,
        }
        # 无 focus 时首个拉起的 agent 默认接管语音; 之后仍需 set_main 显式切换.
        # _main_live 而非 _main_id 判空: 死 main(热重载/stop 残留 id)视同无
        # main, 顺带清 focus, 否则新 agent 永远接不了管.
        if self._main_live() is None:
            self._main_id = agent_id
            # 默认接管同样是 focus 变更(None -> agent): 广播对齐 set_main,
            # 否则冷启竞态的订阅方(car/gaze 的 main 缓存)永锁在 None.
            await self.publish('agent_focus', {'agent_id': agent_id},
                               namespace='xml_agent')
        action = 'resumed' if is_resume else 'created'
        _log.info(
            '%s xml agent: agent_id=%s routine_id=%s model=%s protocol=%s main=%s',
            action, agent_id, handle.id, model, protocol or '(derived)',
            'yes' if self._main_id == agent_id else 'no',
        )
        return {
            'ok': True,
            'agent_id': agent_id,
            'routine_id': handle.id,
            'session_id': session_id,
        }

    @request('live_agents')
    async def on_live_agents(self, source, data: dict) -> dict:
        """纯内存 live agent 快照 [{agent_id, routine_id}] ---- 热路径
        (rid 解析) 专用, 不碰 SQLite. 全量元数据走 list_agents."""
        out = []
        for aid, info in self._agents.items():
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                out.append({'agent_id': aid, 'routine_id': handle.id})
        return {'agents': out}

    @request('list_agents')
    async def on_list(self, source, data: dict) -> dict:
        """enumerate all agents (live + historical), annotated with running state."""
        rows = self._mem.list_agents()
        items = []
        for row in rows:
            agent_id = row.get('agent_id')
            info = self._agents.get(agent_id) if agent_id else None
            handle = info.get('handle') if info else None
            live = info is not None and handle is not None and not handle.is_done()
            items.append({
                'agent_id': agent_id,
                'session_id': agent_id,  # agent = session
                'model': row.get('model'),
                'protocol': row.get('protocol'),
                'reasoning_effort': (info or {}).get('reasoning_effort'),
                'status': 'live' if live else 'stopped',
                'routine_id': handle.id if live else None,
                'title': row.get('title'),
                'created_at': row.get('created_at'),
                'updated_at': row.get('updated_at'),
                'live': live,
                'started': bool(handle and handle.is_started()) if live else False,
                'done': bool(handle and handle.is_done()) if info else True,
            })
        return {'agents': items}

    @request('stop_agent')
    async def on_stop_agent(self, source, data: dict) -> dict:
        """cascade-stop a live child. status 由 manager 内存管 (不持久化)."""
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        info = self._agents.get(agent_id)
        if info is not None:
            if agent_id in self._stopping:
                # already stopping (concurrent req): idempotent ok, don't
                # re-issue handle.stop() -> would hit "another start/stop in
                # flight". the in-flight stop will pop.
                _log.info('stop %s already in flight, idempotent ok', agent_id)
                return {'ok': True, 'agent_id': agent_id}
            handle = info.get('handle')
            if handle is not None and not handle.is_done():
                self._stopping.add(agent_id)
                try:
                    await handle.stop()
                except Exception as exc:
                    _log.warning('stop child %s failed: %r', agent_id, exc)
                    return {'ok': False, 'error': str(exc)}
                finally:
                    self._stopping.discard(agent_id)
            self._agents.pop(agent_id, None)
            # 停的是 main: 清 focus 后若仅剩一个 live agent 则自动补位 ----
            # 前端会话切换是"先建新后停旧", 建新时旧 main 未死不接管, 停旧后
            # 无人补位, voice 全 drop. 多于一个 live 不猜, 仍需 set_main.
            if self._main_id == agent_id:
                self._main_id = None
                survivors = [aid for aid, inf in self._agents.items()
                             if inf.get('handle') is not None
                             and not inf['handle'].is_done()]
                if len(survivors) == 1:
                    self._main_id = survivors[0]
                    _log.info('main agent: auto-fallback -> %s', survivors[0])
                    await self.publish(
                        'agent_focus', {'agent_id': survivors[0]},
                        namespace='xml_agent')
        else:
            # not live here: idempotent ok if it's a known historical agent,
            # else signal not-found so bridge can try the other manager.
            row = self._mem.get_agent(agent_id)
            if row is None:
                return {'ok': False, 'error': 'agent not found'}
        _log.info('stopped xml agent: agent_id=%s', agent_id)
        return {'ok': True, 'agent_id': agent_id}

    @request('delete_agent')
    async def on_delete_agent(self, source, data: dict) -> dict:
        """删除 agent: 拒绝 live, 调 memory.delete_agent 删 agents + messages.

        删除前如果是 live 先拒 (前端应先 stop 再 delete). 删完不可恢复.
        """
        data = data or {}
        agent_id = str(data.get('agent_id') or '').strip()
        if not agent_id:
            return {'ok': False, 'error': 'agent_id is required'}
        if agent_id in self._agents:
            return {'ok': False, 'error': f'agent_id {agent_id} is live; stop it first'}
        if self._mem.get_agent(agent_id) is None:
            return {'ok': False, 'error': f'agent_id {agent_id} not found'}
        try:
            self._mem.delete_agent(agent_id)
        except Exception as exc:
            _log.warning('delete agent %s failed: %r', agent_id, exc)
            return {'ok': False, 'error': str(exc)}
        _log.info('deleted xml agent: agent_id=%s', agent_id)
        return {'ok': True, 'agent_id': agent_id}


# ======================================================================
# entry routine -- bridge calls this to spawn an agent
# ======================================================================

class CreateXmlAgentInput(BaseModel):
    agent_id: Optional[str] = Field(
        None, description='Instance id; auto-generated when omitted. Reuses a stopped agent if it exists.',
    )
    model: Optional[str] = Field(
        None, description='LLM model key (provider/model_name, e.g. seed/glm-5.2). Defaults to models.yaml default when omitted.',
    )
    protocol: Optional[str] = Field(
        None, description='LLM protocol override (agent policy): openai-responses '
                          '| openai-completions. Defaults to the provider apis '
                          'matrix derivation.',
    )
    reasoning: Optional[dict] = Field(
        None, description='Reasoning override at creation: {"effort": '
                           '"high"|"medium"|"low"|"none"}.',
    )


class CreateXmlAgentOutput(BaseModel):
    pass


class CreateXmlAgent(Routine):
    """entry routine: spawn a xml agent via the resident manager.

    bridge creates this routine (ctx.call) -> run locates the resident
    XmlAgents manager by name and reqs it to create+start a XmlAgent
    child. returns the new agent_id.
    """

    name = 'xml/agent/create'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': CreateXmlAgentInput.model_json_schema(),
        'output_schema': CreateXmlAgentOutput.model_json_schema(),
        'description': (
            'Entry routine that spawns a xml agent via the resident '
            'XmlAgents manager. Returns the new agent_id. The spawned agent '
            'runs concurrently, isolated by agent_id.'
        ),
    }

    # req timeout: manager create+start; normally < 1s. 10s generous ceiling.
    _REQ_TIMEOUT = 10.0

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        manager_id = await self._find_manager()
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot spawn agent'
            )
        return await self.req(
            manager_id, 'create_agent', kwargs or {},
            timeout=self._REQ_TIMEOUT,
        )

    async def _find_manager(self) -> Optional[str]:
        """locate the resident manager routine id by name."""
        for _ in range(50):
            try:
                routines = await self.get_running_routines()
            except Exception as exc:
                _log.warning('get_running failed (%r), retry', exc)
                routines = []
            for r in routines:
                if str(r.get('name') or '') == _MANAGER_NAME:
                    rid = str(r.get('id') or '').strip()
                    if rid:
                        return rid
            await asyncio.sleep(0.1)
        return None


class StopXmlAgentInput(BaseModel):
    agent_id: str = Field(description='Agent id to stop (e.g. xml_1).')


class StopXmlAgent(Routine):
    """entry routine: stop a live XmlAgent via the resident manager."""

    name = 'xml/agent/stop'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': StopXmlAgentInput.model_json_schema(),
        'description': (
            'Entry routine that stops a live XmlAgent via the resident '
            'XmlAgents manager. The agent record persists; resume to restart.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        agent_id = str((kwargs or {}).get('agent_id') or '').strip()
        if not agent_id:
            raise ValueError('agent_id is required')
        manager_id = await _find_manager_rid(self)
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot stop agent')
        return await self.req(
            manager_id, 'stop_agent', {'agent_id': agent_id},
            timeout=10.0,
        )


class ResumeXmlAgentInput(BaseModel):
    agent_id: str = Field(description='Agent id to resume (e.g. xml_1).')


class ResumeXmlAgent(Routine):
    """entry routine: resume a stopped XmlAgent via the resident manager."""

    name = 'xml/agent/resume'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': ResumeXmlAgentInput.model_json_schema(),
        'description': (
            'Entry routine that resumes a stopped XmlAgent via the resident '
            'XmlAgents manager. Rebuilds state from persisted history.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        agent_id = str((kwargs or {}).get('agent_id') or '').strip()
        if not agent_id:
            raise ValueError('agent_id is required')
        manager_id = await _find_manager_rid(self)
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot resume agent')
        # 全量 kwargs 转发 (agent_id 必填, model 等可选覆盖)
        return await self.req(
            manager_id, 'resume_agent', kwargs or {}, timeout=10.0)


class ListXmlAgentsInput(BaseModel):
    pass


class ListXmlAgentsOutput(BaseModel):
    agents: Optional[list] = Field(
        None, description='Agent records with live status.',
    )


class ListXmlAgents(Routine):
    """entry routine: list all XmlAgents (live + stopped) via the manager."""

    name = 'xml/agent/list'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': ListXmlAgentsInput.model_json_schema(),
        'output_schema': ListXmlAgentsOutput.model_json_schema(),
        'description': (
            'Entry routine that lists all XmlAgents (live + stopped) via '
            'the resident XmlAgents manager.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        manager_id = await _find_manager_rid(self)
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot list agents')
        return await self.req(manager_id, 'list_agents', {}, timeout=10.0)


class DeleteXmlAgentInput(BaseModel):
    agent_id: str = Field(description='Agent id to delete (must be stopped).')


class DeleteXmlAgent(Routine):
    """entry routine: delete a stopped XmlAgent record via the manager."""

    name = 'xml/agent/delete'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': DeleteXmlAgentInput.model_json_schema(),
        'description': (
            'Entry routine that deletes a stopped XmlAgent record (store '
            'row + messages) via the resident XmlAgents manager. '
            'Irreversible; live agents are rejected (stop first).'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        agent_id = str((kwargs or {}).get('agent_id') or '').strip()
        if not agent_id:
            raise ValueError('agent_id is required')
        manager_id = await _find_manager_rid(self)
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot delete agent')
        return await self.req(
            manager_id, 'delete_agent', {'agent_id': agent_id},
            timeout=10.0,
        )


class SetRoutineAliasInput(BaseModel):
    agent_id: str = Field(description='Target agent id (must be live).')
    aliases: Dict[str, str] = Field(
        description='Routine tag map {routine_name: alias} to upsert. '
                    'alias == routine_name effectively clears the tag.',
    )


class SetRoutineAlias(Routine):
    """entry routine: set routine tags on a live xml agent.

    定位链: manager (by name) -> req live_agents 拿 agent rid -> req 目标
    agent ``set_routine_alias``. 别名由 agent 持有 (prompt 渲染 + act 分发
    映射), 本 routine 只是薄代理, 不存状态.
    """

    name = 'xml/agent/set_routine_alias'
    meta: ClassVar[Dict[str, Any]] = {
        'input_schema': SetRoutineAliasInput.model_json_schema(),
        'description': (
            'Set routine tags (routine name -> alias) on a live xml '
            'agent. The agent renders its prompt interface tables with the '
            'aliases and maps them back to full names when dispatching.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        agent_id = str((kwargs or {}).get('agent_id') or '').strip()
        if not agent_id:
            raise ValueError('agent_id is required')
        manager_id = await _find_manager_rid(self)
        if manager_id is None:
            raise RuntimeError(
                f'{_MANAGER_NAME} manager not running; cannot resolve agent')
        live = await self.req(manager_id, 'live_agents', {}, timeout=10.0)
        target_rid = next(
            (str(it.get('routine_id'))
             for it in (live or {}).get('agents') or []
             if it.get('agent_id') == agent_id and it.get('routine_id')),
            None,
        )
        if not target_rid:
            raise ValueError(f'agent {agent_id!r} not live')
        return await self.req(
            target_rid, 'set_routine_alias',
            {'aliases': (kwargs or {}).get('aliases') or {}},
            timeout=10.0,
        )
