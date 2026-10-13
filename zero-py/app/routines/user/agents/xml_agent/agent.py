"""XmlAgent -- reactive agent(ContextProvider 记忆 + 内置 LLM,每轮直推 act 子).

- 直接继承 ``Routine``(被动常驻编排器,不收 XML body,不派生工具子)--
  XmlRoutine 的 body_shell/parser/on_body_chunk 一套它都不用,继承是历史残留.
- **记忆用 XmlContextProvider**(见 ``provider.py``):封装 Memory(sqlite 持久化)
  + OVMemory(OpenViking 长期记忆),默认启用 OV.
- **每轮 react 直推一个 act 子**(create+start+stop 直管,act 才是 XmlRoutine,
  工具子由 act 的 body_shell 派生).
- **打断逻辑 inline**(epoch + cancel).
- **LLM 调用内置**(``llm.py``,Responses API 流式),不依赖外部模块.

XML 流式 body:LLM 输出 XML -> agent ``ctx.send`` 喂给 ``act`` 子 -> act 解析 ->
body_shell 派发工具子(output / music / query_weather / ...).

生命周期:``is_passive=True`` -> kernel 连上 auto-start;``on_started`` 订阅
user_input/interrupt/session,``run`` 永久驻留(等 ``_stop``).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from routine import Routine, RoutineHandle, request

from .llm import LLMClient, TextDelta, ReasoningDelta, Completed
from .provider import XmlContextProvider
from .condenser import project_with_summary
from .memory import get_memory

# wire 契约常量: act 注入调用方 agent_id 到 tool routine kwargs, tool 侧按同名键读取.
AGENT_ID_KEY = 'from_agent_id'

# legacy shared constants removed: per-instance agent_id (from manager create
# kwargs) replaces the old shared 'react' id/namespace.
# WS bridge 的 routine name(HttpServer.name 蛇形,集成原 AgentWSBridge)->注册时经
# ctx.get_running_routines 向 kernel 查 running 列表按 name 找 HttpServer 的 id.本
# agent 不持有 HttpServer 的 handle(独立 passive 兄弟,可能跑在另一个进程),故按 name
# 找--kernel 有全局 nodes 视图.
_BRIDGE_NAME = 'web_server'
_SYSTEM_PROMPT_FILE = Path(__file__).parent / 'system_prompt.md'


class ChatMessageReq(BaseModel):
    """chat_message req payload (send_message routine 发来)."""
    model_config = {'populate_by_name': True}
    content: list[dict] = Field(
        description='content 数组 [{type: text|image, ...}] (统一消息协议)',
    )
    from_: str | None = Field(
        default=None,
        alias='from',
        description='发送方 agent_id; 非空表示来自其他 agent, 接收方据此包装格式.',
    )


def _is_stale_response_id_error(exc: BaseException) -> bool:
    """LLM 报 previous_response_id 过期(服务端缓存 TTL 到了)吗?

    doubao/ark 返 ``Error code: 400 - {'error': {'code': 'InvalidParameter.PreviousResponseNotFound', ...}}``;
    openai 官方返 ``BadRequestError`` 带 ``previous_response_id`` not found 文案.
    匹配 str(exc) 兜两种 + 未来同类.命中即清过期 id 重试(全量发,prompt caching 重新起链).
    """
    s = str(exc)
    return 'PreviousResponseNotFound' in s or 'previous_response_id' in s.lower()


class XmlAgentInput(BaseModel):
    agent_id: Optional[str] = Field(
        None, description='Instance id; auto-generated when omitted.',
    )
    session_id: Optional[str] = Field(
        None, description='Session id; resumed from db on re-spawn, else auto.',
    )
    model: Optional[str] = Field(
        None, description='LLM model key (provider/model_name, e.g. seed/glm-5.2). Defaults to models.yaml default.',
    )
    protocol: Optional[str] = Field(
        None, description='LLM protocol override (agent policy): openai-responses '
                          '| openai-completions. Defaults to the provider api. '
                          'Must be declared in the provider apis matrix.',
    )
    reasoning: Optional[dict[str, Any]] = Field(
        None, description='Reasoning override at creation (agent policy): '
                          '{"effort": "high"|"medium"|"low"|"none"}. "none" = '
                          'disable thinking (model must declare can_disable). '
                          'Defaults to the model config effort.',
    )
    ov_config: Optional[dict[str, Any]] = Field(
        None, description='OpenViking config. Keys: url, api_key (or env '
        'OPENVIKING_API_KEY), user (default "zero"), push_every_n_turns '
        '(default 5). None = OV disabled (local sqlite memory only).',
    )
    condense_config: Optional[dict[str, Any]] = Field(
        None, description='Condenser config. Keys: strategy (basic/agentic/'
        'hybrid, default hybrid), trigger_ratio, target_ratio, '
        'preserve_recent_tokens, summary_max_tokens. Empty/None = disabled.',
    )
    preload_skills: Optional[list[str]] = Field(
        None, description='Skills to preload (full body injected into prompt).',
    )
    level1_skills: Optional[list[str]] = Field(
        None, description='Level-1 skill names (name+desc injected into prompt).',
    )
    prompt_routines: Optional[list[str]] = Field(
        None, description='Routine white-list rendered into system prompt '
                          '(XML interface tables). None = no tool table '
                          '(bare conversational agent); scenario tools are '
                          'injected explicitly by the caller per instance '
                          '(supports "ns/*" wildcards).',
    )
    routine_aliases: Optional[dict[str, str]] = Field(
        None, description='Explicit routine tag aliases {full name: alias}. '
                          'The only alias source: names containing "/" '
                          'require an explicit alias to be rendered into '
                          'the prompt interface table. Set via '
                          'set_routine_alias and persisted in the '
                          'agents-table init_kwargs snapshot (restored on '
                          'resume).',
    )


class XmlAgentOutput(BaseModel):
    pass


class XmlAgent(Routine):
    """reactive agent:sqlite 记忆 + 每轮直推 act 子 + 内置 LLM.被动常驻."""

    # routine 命名空间 xml/agent/*: entry routines 见 manager.py
    # (xml/agent/create|list|stop|resume|delete|set_alias), 本类是子 agent
    # 本体 (manager 按 name create).
    name = 'xml/agent'

    # is_passive removed: dynamic instance spawned by XmlAgents manager.
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '精简版 reactive agent: sqlite 记忆 + 每轮 push act, 常驻对话.',
        'input_schema': XmlAgentInput.model_json_schema(),
        'output_schema': XmlAgentOutput.model_json_schema(),
    }
    # prompt routine 白名单不是类常量: 通用 agent 零业务默认, 工具集由
    # 调用方按实例显式注入 (input prompt_routines, 支持 'ns/*' 通配).
    # output 说话类是**被动**派发(裸文本自动走 output),不在此列--LLM 只输出自然语言即可.



    # LLM model key (None → models.yaml default)
    LLM_MODEL: str | None = None

    def __init__(self) -> None:
        super().__init__()
        self._agent_id = 'main'
        self._session_id: Optional[str] = None
        self._model: str | None = self.LLM_MODEL
        self._llm: Optional[LLMClient] = None
        self._epoch: int = 0
        self._react_task: Optional[asyncio.Task] = None
        self._event_lock = asyncio.Lock()
        # agent 消息投递锁: 串行化多条 agent 消息, 逐条等 idle 后投递
        self._agent_msg_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._cur_act: Optional[RoutineHandle] = None
        self._static_prompt = _SYSTEM_PROMPT_FILE.read_text(encoding='utf-8')
        # per-session workspace (runtime/xml_agent/sessions/<session_id>/).
        # skill / ov_cursor 都落这里, 切 session 时整目录切换.
        self._workspace: Optional[Path] = None
        # 上下文 provider: Memory 持久化 + OV 长期记忆 (on_started 里构造)
        self._ctx: Optional[XmlContextProvider] = None
        # condenser 配置 (trigger_ratio 等; TODO 暂未接入)
        self._condense_config: dict[str, Any] = {}
        # 上一轮 LLM 原始 usage (纯运行时遥测不落库; get_history/session_changed
        # 回填前端圆环用, resume 时从 messages.usage 列恢复)
        self._last_usage: dict[str, Any] | None = None
        # 一级 skill 摘要 [(name, desc)], 由 on_started 扫描 workspace skills 生成,
        # 在 _build_messages 里拼到 system prompt 后面 (对标 prime).
        self._skill_summaries: list[tuple[str, str]] = []
        # 预加载 skill 全文 (旁路: 强制全量塞进 prompt, 绕过两级)
        self._preloaded_skills_text: str = ''
        # prompt routine 白名单 (on_started 从 init_kwargs 覆盖; 默认空
        # ---- 通用 agent 零业务默认, 工具集由调用方按实例显式注入)
        self._prompt_routines: list[str] = []
        # 别名表 {全名: 别名}: 只含显式条目 (set_routine_alias 设置 /
        # resume 快照恢复), 渲染与分发共用, 经 init_kwargs 快照持久化.
        self._routine_aliases: dict[str, str] = {}
        # --- reflex 层: act 期用户消息先过链出决策(见 reflex.py) ---
        from .jev import JevClient
        from .reflex import ReflexChain, barge_in_reflex, state_gate
        self._jev = JevClient()  # 长期持有(keep-alive), stop 时 aclose
        self._reflex = ReflexChain(state_gate, barge_in_reflex(self._jev))
        # 判定在途 task(act 期收到消息时 spawn; 锁外跑 Jev 不堵 event_lock)
        self._reflex_task: Optional[asyncio.Task] = None
        # act 期间涌入的待判消息(Jev 判定回来发现新增 -> 合并重判, 自然防抖)
        self._reflex_pending: list = []
        # DEFER 决策的缓存消息(暂不落库, drain 时刻落库并开新轮, 见 _drain_deferred)
        self._deferred: list = []
        # agent 当前正在生成/播报的文本(react 流式循环维护; Jev state 的 speaking)
        self._speaking_now: str = ''
        # --- 情绪层: Jev 评估出增量, 代码积分持状态(见 emotion.py) ---
        from .emotion import Temperament
        self._temperament = Temperament()
        self._mood: tuple[float, float] = (self._temperament.base_v,
                                           self._temperament.base_a)
        # 情绪评估在途 task(react 收尾后 spawn, fire-and-forget)
        self._emotion_task: Optional[asyncio.Task] = None

    def _endpoint_key(self) -> str:
        """返回标识 LLM endpoint 的字符串(model key).

        response_id 是 endpoint 绑定的--不同厂家 / 不同 model 的 response_id
        不能复用(API 报错 / 缓存失效).存入 messages.model 列,
        get_last_response_id 校验匹配后才返回.
        """
        return self._llm.model_key if self._llm else (self._model or '')

    # ==================================================================
    # lifecycle
    # ==================================================================

    async def on_started(self) -> None:
        """被动启动后:向 bridge 注册自己.

        注册走 req(1:1) + 重试(见 _register_with_bridge),不靠 conversation_open
        pubsub--后者无订阅者时静默丢,依赖注册顺序太脆弱.bridge 收到注册后建
        agent_id->namespace 路由 + 广播 conversation_open 给前端建 tab.

        额外初始化
          - per-session workspace (runtime/xml_agent/sessions/<session_id>/)
          - skill registry 多目录直读 (workspace + 用户共享库)
          - 扫描 level1_skills 生成摘要, 注入 system prompt
          - 预加载 preload_skills 全文, 注入 system prompt
          - XmlContextProvider (Memory + OV, peer_id='xml'), init_session
        """
        kwargs = self.init_kwargs or {}
        agent_id = kwargs.get('agent_id') or uuid4().hex
        session_id = kwargs.get('session_id') or uuid4().hex
        model = kwargs.get('model') or self.LLM_MODEL
        self._agent_id = agent_id
        self._session_id = session_id
        self._model = model
        self._llm = LLMClient(model, protocol=kwargs.get('protocol'))
        # reasoning 创建时覆盖 (agent 策略): 未覆盖用模型配置默认档.
        # 'none' 关 thinking; set_reasoning_effort 校验档位 + can_disable
        # 事实, 违规抛错让 spawn fail fast.
        effort_override = (kwargs.get('reasoning') or {}).get('effort')
        if effort_override:
            self._llm.set_reasoning_effort(
                None if effort_override == 'none' else effort_override)
        # 新增配置 (从 init_kwargs 读, manager 透传; 缺省 None/空)
        ov_config = kwargs.get('ov_config')
        self._condense_config = dict(kwargs.get('condense_config') or {})
        preload_skills = kwargs.get('preload_skills') or []
        level1_skills = kwargs.get('level1_skills') or []
        # prompt routine 白名单: 未传 = 无工具表 (通用 agent 零业务默认),
        # 传了整表覆盖. 别名派生不在此处 (含 '/' 的白名单项多数不在
        # catalog, 如 'xx/*' 通配), 统一由 _render_routines 按 catalog
        # 全量覆盖.
        self._prompt_routines = kwargs.get('prompt_routines') or []
        # 别名恢复 (init_kwargs 快照, set_routine_alias 落库): 只含显式条目,
        # 渲染/分发全用这一张表 ---- 含 '/' 的白名单 routine 无显式别名
        # 不进接口表.
        self._routine_aliases.update(kwargs.get('routine_aliases') or {})
        # 别名是创建输入: 注入配置消息的时机在 _register_with_bridge 的
        # on_success (此刻 _ctx 已就绪; on_started 里 _ctx 尚未创建), 只在
        # 创建时刻(会话历史为空)注入, resume(历史非空)不重复注入 ---- 历史
        # 里已有创建记录与运行中 set_routine_alias 的真实事件, 标签来源
        # 全程可追溯.

        # 输入统一走 req: chat_message(user/agent/voice) + interrupt.
        # 注册到 WS bridge:req(1:1,失败重试)替代 conversation_open pubsub.
        # pubsub 无订阅者时静默丢--靠注册顺序保证太脆弱;req 失败 caller 知道,
        # 轮询直到 bridge 起来.fire-and-forget:on_started 不阻塞,run() 照常 park.
        asyncio.create_task(self._register_with_bridge())

        # --- per-session workspace + skill registry (多目录: workspace + skill 库) ---
        # workspace 跟 session_id 1:1 (agent=session 模型, per-agent = per-session).
        # 锚定到项目根 (与 memory.py 的 _DB_PATH 同一基准), 不依赖 cwd.
        self._workspace = (
            Path(__file__).resolve().parents[5]
            / 'runtime' / 'xml_agent' / 'sessions' / self._session_id
        )
        self._workspace.mkdir(parents=True, exist_ok=True)
        try:
            from app.routines.user.skills.registry import build_registry
            # 一级 skill 摘要: 只注入 level1_skills 里指定的 name+desc,
            # LLM 看到匹配的 skill 后自己调 skill/load 加载完整内容 (两级渐进).
            if level1_skills:
                level1_set = set(level1_skills)
                reg = build_registry(self._workspace / 'skills')
                reg.rescan()
                self._skill_summaries = [
                    (s.name, s.description)
                    for s in reg.list_skills()
                    if s.name in level1_set
                ]
                self._logger.info(
                    f'level1 skills: {len(self._skill_summaries)} matched '
                    f'(requested {len(level1_set)})'
                )
            # 预加载 skill 全文 (旁路: 强制全量塞进 prompt, 绕过两级)
            if preload_skills:
                reg = build_registry(self._workspace / 'skills')
                reg.rescan()
                parts: list[str] = []
                for skill_name in preload_skills:
                    try:
                        parts.append(reg.invoke_for_preload(skill_name))
                    except KeyError:
                        self._logger.warning(f'preload skill not found: {skill_name}')
                if parts:
                    self._preloaded_skills_text = '\n\n'.join(parts)
                    self._logger.info(
                        f'preloaded {len(parts)} skill(s) into system prompt'
                    )
        except Exception as exc:
            self._logger.warning(f'skill seed/scan failed: {exc} (skills disabled)')

        # --- XmlContextProvider: Memory 持久化 + OV 长期记忆 (peer_id='xml') ---
        # OV init 失败只 log warning, provider.enabled=False, 后续全跳过.
        self._ctx = XmlContextProvider(
            ov_config=ov_config,
            workspace=self._workspace,
            peer_id='xml',
            agent_id=self._agent_id,
        )
        await self._ctx.init_session(self._session_id)
        # resume 恢复上一轮 usage (持久化在 messages.usage 列), 供前端圆环回填
        self._last_usage = self._ctx.get_last_usage(self._session_id)

        self._logger.info(f'{self.name} started (agent_id={self._agent_id} session={self._session_id}), registering with bridge')

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """永久驻留:等 stop 放行.react 由 chat_message req 驱动(_on_input spawn)."""
        try:
            await self._stop.wait()
        finally:
            # provider 收尾: OV 推完剩余消息 + commit 归档 + 关闭 client.
            # (agent=session 模型: agent 关闭即 session 结束即 commit, 重启后开新 agent)
            if self._ctx is not None:
                try:
                    await self._ctx.finalize_session()
                except Exception as exc:
                    self._logger.warning(f'ctx finalize failed: {exc}')

    async def stop(self) -> None:
        # cascade: cancel in-flight react task + stop act child before parking.
        await self._cancel_react('stop')
        await self._interrupt_act_if_needed()
        # reflex 判定在途也一并掐(决策只是提议, stop 后一律失效)
        task, self._reflex_task = self._reflex_task, None
        self._reflex_pending.clear()
        self._deferred.clear()
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        # 情绪评估在途也一并掐(mood 不更新, 回落靠下轮)
        etask, self._emotion_task = self._emotion_task, None
        if etask is not None and not etask.done():
            etask.cancel()
            try:
                await etask
            except asyncio.CancelledError:
                pass
        # Jev 连接池收尾(依赖方已 cancel, 掐在途请求无副作用)
        await self._jev.aclose()
        self._stop.set()

    async def _register_with_bridge(self) -> None:
        """向 WS bridge req 注册自己 (委托共享实现)."""
        from ._bridge import register_with_bridge

        async def _on_success() -> None:
            # emit session_changed so frontend backfills panel history
            # (same link prime uses; is_new = no prior messages).
            is_new = not self._ctx.load_history(self._session_id)
            # 创建时刻注入别名配置消息 (真实事件, 标签来源可追溯):
            # 别名非空且会话历史为空 = 创建而非 resume ---- on_started 里
            # _ctx 尚未创建, 注入只能在 ctx 就绪的这里做.
            if is_new and self._routine_aliases:
                self._ctx.add_message(
                    'user',
                    '[routine_alias] tags configured: ' + ', '.join(
                        f'{a} = {n}' for n, a in self._routine_aliases.items()),
                    agent_id=self._agent_id,
                    message_id=uuid4().hex, session_id=self._session_id,
                )
            await self._emit_session_changed(self._session_id, is_new=is_new)

        await register_with_bridge(
            agent_id=self._agent_id,
            routine_id=self.id,
            name_prefix='Xml-',
            bridge_name=_BRIDGE_NAME,
            ctx=self.ctx,
            stop_event=self._stop,
            logger=self._logger,
            on_success=_on_success,
        )

    # ==================================================================
    # reactive / interrupt(inline,照 reactive_agent.py 模式)
    # ==================================================================

    def _store_incoming(self, msg) -> str:
        """IncomingMessage -> 落库一条 user 消息, 返回落库 content.

        输入通道的统一落库路径 (_on_input), 保证消息格式 (wrap_from /
        [图片] 占位) 单点维护.
        """
        from ._messaging import wrap_from
        content = wrap_from(msg.text, msg.from_)
        if msg.images and not content:
            content = '[图片]'
        self._ctx.add_message(
            'user', content,
            agent_id=self._agent_id,
            message_id=uuid4().hex, session_id=self._session_id,
            images=msg.images,
        )
        return content

    async def _on_input(self, source, data) -> None:
        async with self._event_lock:
            from ._messaging import IncomingMessage, USER_SOURCE
            msg = IncomingMessage.from_payload(data)
            if not msg.text and not msg.images:
                return
            # act 期的用户消息先过 reflex 链(不直接打断); agent 来源消息
            # 语义明确 + idle 消息直通(现状路径)
            if msg.from_ == USER_SOURCE and self._react_in_flight():
                self._reflex_pending.append(msg)
                if self._reflex_task is None or self._reflex_task.done():
                    task = asyncio.create_task(self._run_reflex())
                    task.add_done_callback(self._log_reflex_error)
                    self._reflex_task = task
                return
            self._epoch += 1
            await self._cancel_react('user_input')
            await self._interrupt_act_if_needed()
            content = self._store_incoming(msg)
            self._logger.info(f'epoch -> {self._epoch} (user_input): {content!r}')
            if msg.from_ != USER_SOURCE:
                await self._emit_incoming(content, msg.from_)
            self._spawn_emotion(content)
            self._start_react(self._epoch)

    def _react_in_flight(self) -> bool:
        task = self._react_task
        return task is not None and not task.done()

    def _log_reflex_error(self, task: asyncio.Task) -> None:
        """reflex 执行器崩溃兜底日志(pending 已被其取走, 崩了就是丢)."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error(f'reflex executor crashed: {exc}', exc_info=exc)

    async def _run_reflex(self) -> None:
        """reflex 决策执行器: 锁外跑 Jev(不堵 event_lock), 执行时重验现场.

        循环体: 拿锁清 pending + 快照现场 -> 锁外判定 -> 拿锁执行.
        重验规则(epoch/在途快照, 决策只是提议, 过期作废):
          - epoch 变了(中途有人打断/新输入): pending 落库不反应, 结束
          - react 已自然结束: 决策降级 PASS(说完就接上, 不打断空气)
          - pending 长了(判定期间用户又说话): 丢弃本次结果, 合并重判
        Jev 失败降级 INTERRUPT(= 引入 reflex 层前的无条件打断, 宁可打断
        不可丢消息).
        """
        from .reflex import Decision, ReflexState
        while True:
            async with self._event_lock:
                if not self._reflex_pending:
                    self._reflex_task = None
                    return  # 消息被执行路径消费干净了
                pending = self._reflex_pending
                self._reflex_pending = []
                epoch = self._epoch
                in_flight = self._react_in_flight()
                recent = self._recent_turns()
            state = ReflexState(
                in_flight=in_flight, speaking=self._speaking_now,
                recent_turns=recent,
            )
            merged = '\n'.join(m.text for m in pending if m.text)
            try:
                decision = await self._reflex.run(merged, state)
            except Exception as exc:
                self._logger.warning(f'reflex failed, fallback interrupt: {exc}')
                decision = Decision.INTERRUPT
            async with self._event_lock:
                # pending 又长了: 用户在判定期间继续说话 -> 合并重判(防抖)
                if self._reflex_pending:
                    self._reflex_pending = pending + self._reflex_pending
                    continue
                # epoch 变了: 打断/新输入已接管现场, 落库保消息, 不反应
                if self._epoch != epoch:
                    for m in pending:
                        content = self._store_incoming(m)
                        self._logger.info(f'reflex stale (epoch), stored: {content!r}')
                    return
                # react 自然结束: act 期决策失效, 降级 PASS(消息接上新轮)
                if in_flight and not self._react_in_flight():
                    decision = Decision.PASS
                await self._execute_reflex(decision, pending, merged)
                if self._reflex_pending:
                    continue  # 执行瞬间又来消息 -> 再判
                self._reflex_task = None
                return

    async def _execute_reflex(self, decision, pending, merged: str) -> None:
        """决策 -> 动作(持锁调用, 可 await react/act 收尾). 消费规则见
        reflex.py 模块注释."""
        from .reflex import Decision
        if decision is Decision.DROP:
            self._logger.info(f'reflex drop: {merged!r}')
            return
        self._logger.info(f'reflex {decision.value}: {merged!r}')
        if decision is Decision.DEFER:
            # 只缓存不落库: 落库会排在本轮 assistant 输出之前, drain 开新轮时
            # 尾序成 assistant -> LLM 400(无 prefill). 落库挪到 drain 时刻,
            # 顺序变成 assistant(本轮) -> user(插话), 新轮从这里接上.
            self._deferred.extend(pending)
            return
        # INTERRUPT 先打断收尾(部分输出先落库)再落新消息: history 尾序
        # assistant(部分) -> user(新), 无 prefill 的模型也能续上新轮.
        # (先落 user 后 cancel 会留下 user -> assistant 尾序, LLM 400)
        if decision is Decision.INTERRUPT:
            self._epoch += 1
            await self._cancel_react('reflex_interrupt')
            await self._interrupt_act_if_needed()
        else:
            self._epoch += 1
        for m in pending:
            self._store_incoming(m)
        self._spawn_emotion(merged)
        self._start_react(self._epoch)

    def _recent_turns(self) -> list[tuple[str, str]]:
        """尾部 1 轮对话 [(user, assistant)], Jev state 的上下文."""
        if self._ctx is None:
            return []
        last_user = last_assistant = ''
        for m in reversed(self._ctx.load_history(self._session_id)):
            role, content = m.get('role'), m.get('content') or ''
            if role == 'assistant' and not last_assistant:
                last_assistant = content
            elif role == 'user' and not last_user:
                last_user = content
            if last_user and last_assistant:
                break
        return [(last_user, last_assistant)] if (last_user or last_assistant) else []

    def _spawn_emotion(self, current: str) -> None:
        """入口情绪评估: Jev Scorex2 出增量 -> 积分 -> 更新 mood (本轮生效).

        在消息落库后启动, 与 react/LLM 完全并行 ---- 落地即 publish(前端
        徽标), TTS 指令不随 act create 快照, 由 speak 起 run 时经
        agent/state 现取(speak 的 session 建立晚于 speak run, 读到的一定
        是本值). Jev 失败跳过本轮 (mood 保持, 回落靠 decay), 不阻断
        react ---- 情绪是背景染色, 不是闸门 (对比 reflex).
        上一轮评估在途时直接覆盖 (新一轮更相关; cancel 后旧任务不会写 mood).
        """
        from .emotion import (emotion_questions, fold_delta, integrate,
                              mood_state, tts_instruction)

        async def _run() -> None:
            try:
                # 只喂本轮输入: 历史轮次会锚定 Jev 判断(见 mood_state 实测注)
                state = mood_state(current, *self._mood)
                answers = await self._jev.decide(state, emotion_questions())
                dv = fold_delta(answers['valence_shift']['score'])
                da = fold_delta(answers['arousal_shift']['score'])
                v, a = integrate(self._mood[0], self._mood[1], dv, da,
                                 self._temperament)
                self._mood = (v, a)
                self._logger.info(
                    f'emotion: dv={dv:+.2f} da={da:+.2f} -> '
                    f'v={v:+.2f} a={a:+.2f}')
                await self.publish('emotion', {
                    'agent_id': self._agent_id,
                    'instruction': tts_instruction(v, a),
                    'valence': round(v, 2),
                    'arousal': round(a, 2),
                })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(f'emotion update skipped: {exc}')

        task = self._emotion_task
        if task is not None and not task.done():
            task.cancel()
        self._emotion_task = asyncio.create_task(_run())

    @request('interrupt')
    async def on_interrupt_req(self, source, data) -> dict:
        """打断在途 react (req 入口, 前端 voice 等程序侧调用).

        fire-and-forget: 立即返回, 打断在后台完成 (等 _event_lock 排队).
        """
        asyncio.create_task(self._on_interrupt(source, data or {}))
        return {'ok': True, 'epoch': self._epoch}

    async def _on_interrupt(self, source, data) -> None:
        async with self._event_lock:
            self._epoch += 1
            self._logger.info(f'epoch -> {self._epoch} (interrupt)')
            await self._cancel_react('interrupt')
            # 关键:必须 interrupt shell -- act 的 body_shell 里 output/TTS 还在播.
            # 只 cancel react task 不会停 act(act 是子 routine,靠 shell interrupt cascade 停).
            # 漏这步 = 说话继续播完(见 _on_input 同款调用).
            await self._interrupt_act_if_needed()

    async def _interrupt_act_if_needed(self) -> None:
        """打断本轮 react 的 act.无 react 在途时 _cur_act 为 None 跳过.

        _cancel_react 已 await react_task 结束,故此时无在途 start ack--act.stop()
        可直接走(等 stopped ack 确认 cascade 停完).对标老 shell.interrupt(wait_done=True).
        """
        act = self._cur_act
        if act is None:
            return
        try:
            await act.stop()
        except Exception as exc:
            self._logger.warning(f'interrupt act stop failed: {exc}')
        self._cur_act = None

    async def _cancel_react(self, reason: str) -> None:
        task = self._react_task
        self._react_task = None
        if task is None:
            return
        if task.done():
            self._logger.info(f'react already done ({reason}), no cancel needed')
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            self._logger.info(f'react cancelled ({reason})')
        except Exception as exc:
            self._logger.warning(f'react error on cancel ({reason}): {exc}')

    def _start_react(self, epoch: int) -> None:
        task = asyncio.create_task(self._run_react(epoch))
        task.add_done_callback(self._on_react_done)
        self._react_task = task

    def _on_react_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error(f'react crashed (epoch={self._epoch}): {exc}', exc_info=exc)
        # reflex DEFER drain: react 自然收尾后自动接上被缓存的消息
        # (spawn 异步抢锁, 不在 task 回调里 await)
        if self._deferred:
            asyncio.create_task(self._drain_deferred())

    async def _drain_deferred(self) -> None:
        async with self._event_lock:
            if not self._deferred or self._react_in_flight():
                return  # 有新输入/判定在途接管了现场, deferred 留给下次收尾
            deferred, self._deferred = self._deferred, []
            # drain 时刻落库: 排在本轮 assistant 输出之后, 尾序 user 才能开新轮
            for m in deferred:
                self._store_incoming(m)
            merged = '\n'.join(m.text for m in deferred if m.text)
            self._epoch += 1
            self._logger.info(f'reflex drain: {merged!r}')
            self._spawn_emotion(merged)
            self._start_react(self._epoch)

    async def _run_react(self, epoch: int) -> None:
        """epoch 内多轮迭代:react 返回 True 表示有 feedback 要再来一轮.

        取消靠 ``_cancel_react`` 的 ``task.cancel() + await task``--CancelledError
        在 react 的下一个 await 点抛出,except 存半截内容后 re-raise,task 自然死.
        epoch 在这不再做取消判断(那是 cancel 的职责),只作 emit 给前端的输出序号.
        """
        # agent_status 广播(reacting/idle): 前端状态灯 + 具身伴随(如 car/gaze
        # 注视会话)的起停信号源. 外层包全方法 ---- 正常/打断/异常全路径收口.
        # ns 'xml_agent': 实现方域(不占全局 topic), 订阅方需 namespace 限定.
        await self._emit_agent_status('reacting')
        try:
            while True:
                more = await self.react(epoch)
                if not more:
                    return
        except asyncio.CancelledError:
            raise
        finally:
            # cancel 已在路上,shield 让广播在事件循环上跑完(丢了也只影响
            # 伴随方,且其超时兜底自愈)
            try:
                await asyncio.shield(self._emit_agent_status('idle'))
            except Exception:
                pass

    async def _emit_agent_status(self, status: str) -> None:
        await self.publish('agent_status', {
            'agent_id': self._agent_id,
            'status': status,
        }, namespace='xml_agent')

    # ==================================================================
    # react -- 一轮 inference + act(照 agent.py:react 流程,记忆换 sqlite)
    # ==================================================================

    async def react(self, epoch: int) -> bool:
        import time as _time
        _t0 = _time.perf_counter()
        def _ms(since: float) -> int:
            return int((_time.perf_counter() - since) * 1000)
        message_id = uuid4().hex
        text_output = ''
        # 两层数据收集:
        #   feedback_items  -- 给 LLM 的(content 过滤后,说话类被抑制)
        #   results_raw     -- 原始所有子 routine 的 {name, result, error}(不过滤,含 output frames)
        feedback_items: list[dict] = []
        results_raw: list[dict] = []
        response_id: str | None = None  # 本轮 LLM response id(成功才有,prompt caching 用)
        round_usage: dict | None = None  # 本轮 LLM token 用量(遥测, 入库供 resume 回填)
        final_sent = False  # 本轮是否已发过 final 帧(防打断补发重复)

        try:
            # 上下文压缩检查 (TODO: condense 当前未接入, 见 _maybe_condense)
            _t = _time.perf_counter()
            await self._maybe_condense(epoch)
            _ms_condense = _ms(_t)
            # OV 增量推送 (provider 内部按 N 轮计数 add_message, 不 commit; 失败不阻断)
            _t = _time.perf_counter()
            if self._ctx is not None and self._ctx.enabled:
                try:
                    self._ctx.tick()
                except Exception as exc:
                    self._logger.warning(f'ov tick failed: {exc}')
            _ms_ov = _ms(_t)
            _t = _time.perf_counter()
            instructions, input_msgs, previous_response_id, full_history = await self._build_messages()
            _ms_build = _ms(_t)
            # sys_prompt 给前端可视化:展示完整 LLM 上下文(system + 全量 history).
            # input_msgs 在 prompt caching 时仅为增量,不能直接用于可视化.
            sys_view = ([{'role': 'system', 'content': instructions}] if instructions else []) + [
                {'role': m['role'], 'content': m['content']} for m in full_history
            ]
            await self._emit_sys_prompt(epoch, message_id, sys_view)
            self._logger.info(
                f'react timing: condense={_ms_condense}ms ov={_ms_ov}ms '
                f'build_messages={_ms_build}ms total_to_sys_prompt={_ms(_t0)}ms'
            )

            # 每轮 react 直推一个 act 子(本轮唯一子--工具子由 act 的 body_shell
            # 派生,不经 agent).create + start + stop 直管,无需 Shell(Shell 为多兄弟
            # 排序/barrier 而设,单子纯开销).对标 self.push('act')(走 routine 的 shell).
            # agent_id: act 在 push 工具子前注入, tool routine 通过 agent/rid
            # 查 rid 后 ctx.req(rid, 'agent_state') 反向获取 skill_dir 等信息.
            _t = _time.perf_counter()
            # 情绪评估在入口已并行启动(_spawn_emotion), 与 LLM 竞速; speak
            # 起 run 时经 agent/state 现取 tts_instruction, create 不等不快照
            act = await self.create('act', {
                AGENT_ID_KEY: self._agent_id,
                # 标签映射表 {全名: 别名}: act 分发时把别名标签映射回全名 (本轮快照)
                'routine_aliases': dict(self._routine_aliases),
            })
            self._cur_act = act
            await act.start()
            _ms_act_create = _ms(_t)

            # LLM 流式 XML -> ctx.send 喂给 act 的 body(走 act.on_message
            # -> parser -> body_shell 派发工具子).mailbox 保发送序.
            #
            # previous_response_id 过期兜底:服务端缓存 TTL 到了(doubao ~30min)会报
            # PreviousResponseNotFound -> 清过期 id,全量重发重试一次.400 在首个 delta
            # 前抛,此时 text_output 仍空,act 没收到 body,重试安全不重复.
            _t = _time.perf_counter()
            _first_token_logged = False
            for _attempt in (0, 1):
                try:
                    async for kind, payload, usage in self._stream_llm(
                        input_msgs, instructions, previous_response_id,
                    ):
                        if kind == 'text' and payload:
                            if not _first_token_logged:
                                self._logger.info(
                                    f'react timing: act_create={_ms_act_create}ms '
                                    f'llm_first_token={_ms(_t)}ms'
                                )
                                _first_token_logged = True
                            await self.ctx.send(act.id, {'text': payload})
                            text_output += payload
                            # reflex state 的 speaking: 实时维护本轮输出
                            self._speaking_now = text_output
                            await self._emit_output(payload, is_final=False, epoch=epoch, message_id=message_id)
                        elif kind == 'thinking' and payload:
                            await self._emit_output(payload, is_final=False, epoch=epoch, message_id=message_id, is_thinking=True)
                        elif kind == 'done':
                            response_id = payload  # Completed.response_id
                            round_usage = usage or None
                            self._logger.info(
                                f'react llm done event: response_id={response_id[:24]}... '
                                f't={_ms(_t)}ms'
                            )
                            await self._emit_usage(usage, epoch=epoch, message_id=message_id)
                    self._logger.info(
                        f'react llm stream loop exited: '
                        f'text_len={len(text_output)} t={_ms(_t)}ms'
                    )
                    self._logger.info(f'react llm output: {text_output!r}')
                    break  # 流完没抛 -> 出 retry
                except Exception as exc:
                    if _attempt == 0 and previous_response_id is not None \
                            and _is_stale_response_id_error(exc):
                        self._logger.warning(
                            f'stale previous_response_id ({previous_response_id[:24]}...) '
                            f'cleared, retry with full context'
                        )
                        self._ctx.clear_last_response_id(
                            self._session_id, previous_response_id,
                        )
                        previous_response_id = None
                        input_msgs = [
                            self._to_input_msg(m)
                            for m in full_history
                            if m['content'] or m.get('images')
                        ]
                        text_output = ''  # 重置(400 前应本就为空,防御性)
                        continue
                    raise  # 其它错 / 第二次仍失败 -> 抛上去 react crashed
            _ms_llm = _ms(_t)
            self._logger.info(
                f'react timing: llm_stream_done={_ms_llm}ms text_len={len(text_output)} '
                f'has_response_id={response_id is not None}'
            )
            # body 流终结:发 _eof 让 act 的 parser 走 STREAM_CLOSED -> body_shell complete
            await self.ctx.send(act.id, {'_eof': True})
            await self._emit_output(text_output, is_final=True, epoch=epoch, message_id=message_id)
            final_sent = True

            _t = _time.perf_counter()
            async for res in act:
                self._logger.info(f'routine result: {res}')
                # 原始记账:所有子 routine(含说话类 output),不过滤.
                results_raw.append({
                    'name': res.get('name') or '',
                    'result': res.get('result'),
                    'error': res.get('error'),
                    'input': res.get('input'),
                })
                # 给 LLM 的:content 过滤后(说话类被 content:null 抑制).
                item = self._extract_feedback_item(res)
                if item is not None:
                    feedback_items.append(item)
            _ms_act_collect = _ms(_t)

        except asyncio.CancelledError:
            # 被新输入/中断打断(_cancel_react 的 task.cancel).保住已输出的部分内容
            # (连续而非 0/1):标 interrupted=True 入库.act 不在这停--event handler 的
            # _interrupt_act_if_needed 会停(那时 task 已 done,无在途 ack 冲突).
            #
            # 补发 final 收尾帧(shield 防 publish 被二次 cancel 掐掉): 正常路径在
            # 流结束后发, 打断路径 cancel 抢先 ---- 不补的话前端收不到 is_final,
            # 闪烁光标挂着不走. text 可能为空(纯思考被打断), 前端照样关流.
            try:
                await asyncio.shield(self._emit_output(
                    text_output, is_final=True, epoch=epoch,
                    message_id=message_id, interrupted=True,
                ))
            except Exception as exc:
                self._logger.warning(f'emit final on interrupt failed: {exc}')
            #
            # response_id 透传:打断可能发生在 LLM 流完后(act 还在跑工具)----此时
            # response_id 已拿到,必须存.否则下轮 get_last_response_id 回退到更早的
            # assistant,增量 cut 会把本条 interrupted assistant 也算进 input_msgs,
            # 用 previous_response_id 模式发一条 assistant 消息给 Responses API 会
            # 报 400 MissingParameter input.content(服务端已记录上一轮完整输出,
            # 客户端再续 assistant 冲突).LLM 流式中被打断时 response_id 仍为 None,
            # 不存 -> 下轮自动回退到更早的,行为不变.
            self._ctx.add_message(
                'assistant', text_output,
                agent_id=self._agent_id,
                message_id=message_id, session_id=self._session_id,
                interrupted=True,
                feedback=feedback_items or None,
                results_raw=results_raw or None,
                response_id=response_id,
                model=self._endpoint_key(),
                usage=round_usage,
            )
            raise
        finally:
            # react 结束(正常/打断/异常): speaking 现场失效
            self._speaking_now = ''

        # 正常完成 -- 存 response_id(供下一轮 previous_response_id 做 prompt caching).
        self._ctx.add_message(
            'assistant', text_output,
            agent_id=self._agent_id,
            message_id=message_id, session_id=self._session_id,
            interrupted=False,
            feedback=feedback_items or None,
            results_raw=results_raw or None,
            response_id=response_id,
            model=self._endpoint_key(),
            usage=round_usage,
        )
        # async for 已耗尽 = act done(_BODY_DONE 由 notify_done 投入).wait() 即时返回,
        # 保留作安全网 + 对标老 act.wait_done().
        await act.wait()
        self._cur_act = None
        self._logger.info(
            f'react timing: llm_stream={_ms_llm}ms act_collect={_ms_act_collect}ms '
            f'total={_ms(_t0)}ms'
        )

        if not feedback_items:
            return False

        feedback_msg = self._build_feedback_message(feedback_items)
        # read_image 的图引用合并进 [feedback] 一条消息 (文本 + 图多模态):
        # 位置在 assistant 之后, 天然在增量窗口内.
        feedback_images = [
            p for it in feedback_items for p in it.get('images') or []
        ]
        self._ctx.add_message(
            'user', feedback_msg,
            agent_id=self._agent_id,
            message_id=uuid4().hex, session_id=self._session_id,
            images=feedback_images or None,
        )
        await self._emit_feedback(
            content=feedback_msg,
            results=self._to_event_results(feedback_items),
            epoch=epoch, message_id=message_id,
        )
        return True

    # ==================================================================
    # 上下文压缩 (委托 provider.compact, 走本地 xml_condenser_agent)
    # ==================================================================

    async def _maybe_condense(self, epoch: int) -> None:
        """每轮 LLM 调用前检查是否需要压缩上下文.

        委托给 XmlContextProvider.compact:
          走 xml_condenser_agent (写 summary 到 messages 表, kind='summary').

        condense_config 为空时跳过 (trigger_ratio 等配置在此).
        """
        if not self._condense_config:
            return
        max_context = self._llm.max_context if self._llm else 0
        try:
            result = await self._ctx.compact(
                agent_id=self._agent_id,
                session_id=self._session_id,
                model_key=self._model,
                max_context=max_context,
                plan_mode=False,
                condense_config=self._condense_config,
                project_root=None,
                cwd=None,
                call=self.call,
            )
        except Exception as exc:
            self._logger.warning(f'compact failed: {exc} (skipping)')
            return
        if result:
            self._logger.info('context condensed via xml_condenser_agent')

    # ==================================================================
    # LLM 调用(Responses API 直调,包内 llm.py 的 LLMClient)
    # ==================================================================

    async def _stream_llm(
        self, input_msgs: list[dict], instructions: str | None,
        previous_response_id: str | None = None,
    ):
        """直调 LLM(Responses API),流式 yield (kind, text|response_id|None).

        复用包内 ``llm.py`` 的 ``LLMClient``(配置从 models.yaml 加载).

        - previous_response_id 非空 -> 只发增量 input + 传它复用服务端缓存 prefix
          (prompt caching).为空 -> 全量发 input.
        - 流式 yield ``('text', str)`` 文本片;末尾 yield ``('done', response_id)``
          表示成功完成,带本次 response id(供入库做下一次的 previous).
        """
        client = self._llm
        async for ev in client.stream(
            input_msgs, instructions=instructions,
            previous_response_id=previous_response_id,
        ):
            if isinstance(ev, TextDelta) and ev.text:
                yield 'text', ev.text, None
            elif isinstance(ev, ReasoningDelta) and ev.text:
                yield 'thinking', ev.text, None
            elif isinstance(ev, Completed):
                yield 'done', ev.response_id, ev.usage

    # ==================================================================
    # messages 组装
    # ==================================================================

    async def _build_messages(self) -> tuple[str | None, list[dict], str | None, list[dict]]:
        """组装 LLM 输入,返回 (instructions, input_msgs, previous_response_id, full_history).

        prompt caching (永远用最近的可用 response_id 做增量):
        - previous_response_id = 最近的带 response_id 的 assistant 消息的 id
          (跳过中间被打断的 assistant--它们 response_id=None).
        - 有它 -> 增量: 发它之后的所有消息(含被打断的半截 assistant, LLM 不失忆).
        - 无它 (首启 / 全被打断 / summary 之后无新对话) -> 全量发.
        - 任何情况都不全量重发(只要有一个可用的 response_id).

        过滤 content='' 的消息: ARK Responses API 对空 content 报 400
        MissingParameter input.content. 触发场景: react 被打断时 LLM 还没开始输出
        (text_output=''), 落库的 assistant content='' -> 下轮 400. content='半截'
        的被打断 assistant 保留(LLM 不失忆), content='' 跳过(空消息对 LLM 无信息量).

        上下文压缩投影: load_history 返回的消息含 kind='summary' 行. summary 替代
        它之前的所有消息, 找 response_id 时只在 new_dialogue 里找(tail 里的
        response_id 是压缩前的, 缓存里没有 summary, 不能用).
        """
        system = self._static_prompt.replace('{AGENT_ID}', self._agent_id)
        routines = await self._render_routines()
        if routines:
            system = system + '\n\n## 可用 routine\n\n' + routines
        # 一级 skill 摘要注入 (对标 prime build_system_prompt 的 skill_summaries):
        # 只列 name+desc, LLM 看到匹配的 skill 后自己调 skill/load 加载完整内容.
        if self._skill_summaries:
            skill_lines = [
                f'- {n}: {d}' for n, d in self._skill_summaries if d
            ]
            if skill_lines:
                system = system + '\n\n## 可用 skill\n\n' + '\n'.join(skill_lines)
        # 预加载 skill 全文 (旁路: 强制全量塞进 prompt, 绕过两级渐进)
        if self._preloaded_skills_text:
            system = system + '\n\n' + self._preloaded_skills_text
        history = self._ctx.load_history(self._session_id)

        # 上下文压缩投影: 用 covered_to 定位 tail, 正确保留 [summary, tail, new_dialogue].
        history, has_summary, new_dialogue_start = project_with_summary(history)

        # provider 声明不支持 previous_response_id (deepseek 实测静默忽略:
        # 接受参数不报错但无状态, 增量链恒断, LLM 每轮只见孤立消息, 工具
        # feedback 后答非所问) -> 放弃增量, 永远全量发历史.
        if self._llm is not None and not self._llm.supports_previous_response_id:
            input_msgs = [
                self._to_input_msg(m)
                for m in history if m['content'] or m.get('images')
            ]
            return system or None, input_msgs, None, history

        # previous_response_id: 永远找最近的带 response_id 的 assistant.
        # 有 summary 时只在 new_dialogue 里找 (tail 里的 response_id 是压缩前的, 不能用).
        # 有 summary 但 new_dialogue 里无 response_id -> None 全量发.
        search_start = new_dialogue_start if has_summary >= 0 else 0
        previous_response_id = None
        cut = len(history)
        for i in range(len(history) - 1, search_start - 1, -1):
            m = history[i]
            if m.get('response_id'):
                previous_response_id = m['response_id']
                cut = i + 1
                break
        # 增量: R 之后的所有新消息 (含带图的 [feedback] -- read_image 的图随
        # feedback 落库, 位置天然在 R 之后). 窗口空则全量.
        if previous_response_id is not None:
            input_msgs = [
                self._to_input_msg(m)
                for m in history[cut:] if m['content'] or m.get('images')
            ]
            if input_msgs:
                return system or None, input_msgs, previous_response_id, history
        # 全量: 首启 / summary 之后无新对话 / 窗口空.
        input_msgs = [
            self._to_input_msg(m)
            for m in history if m['content'] or m.get('images')
        ]
        return system or None, input_msgs, None, history

    def _to_input_msg(self, m: dict) -> dict:
        """history 消息 → LLM input 消息.

        user 消息带 images 时组装多模态 content blocks:
        [input_text, input_image...] -- 图片路径按需转 data URL
        (Responses API 只认 http(s) URL 或 data URL).
        底层模型不支持读图 (supports_images=False) 时剥掉图, 只发文本.
        """
        role, content = m.get('role'), m.get('content') or ''
        images = m.get('images')
        if role == 'user' and images and self._llm is not None \
                and self._llm.supports_images:
            from .._core.uploads import resolve_data_url
            blocks: list[dict] = [{'type': 'input_text', 'text': content}]
            for p in images:
                data_url = resolve_data_url(str(p))
                if data_url:
                    blocks.append({'type': 'input_image', 'image_url': data_url})
            return {'role': role, 'content': blocks}
        return {'role': role, 'content': content}

    async def _render_routines(self) -> str:
        """渲染白名单 ``self._prompt_routines`` 内 routine 的接口表.

        只列 LLM 该**主动**调用的工具.output 说话类是被动派发(裸文本自动走
        output),不列入--LLM 只管输出自然语言,框架自动让它说话.

        走 ``self.get_routines()`` 拿跨 hub 注册清单(kernel 全局 catalog,对标
        routine/list kind=registered 的数据源),条目是 ``{name, hub_id,
        is_passive, meta}``.不能用 ``ctx.hub.runtime.routines``--那是本 hub
        (py 进程)注册表,看不到 rs/ts hub 的 routine(白名单里的 music/play 等),
        曾因单 hub 视野导致 system prompt 缺工具:LLM 面对"放音乐"不知道有
        music/play,只会反复调 routine/list 盘点能力(act 派发走 kernel 全局
        路由却能调成功,形成"能调但不知有"的割裂).
        """
        try:
            routines = await self.get_routines()
        except NotImplementedError:
            self._logger.warning('get_routines not implemented; cannot render routines')
            return ''
        except Exception as exc:
            self._logger.warning(f'get routines failed: {exc}')
            return ''
        by_name: dict[str, dict] = {}
        for r in routines:
            by_name.setdefault(str(r.get('name') or ''), r)
        # 别名表只含显式条目 (set_routine_alias 设置 / resume 快照恢复),
        # 无派生兜底: 含 '/' 的白名单 routine 必须显式设置别名才渲染进
        # 接口表 ---- 别名是调用方的数据, 不是框架的隐式规则; 隐式派生会
        # 让标签来源不可追溯 (prompt 有但没人设过).
        # 本名被占作其他 routine 的别名时不渲染原块
        # (该标签在 act 分发时映射去了别处, 再教 LLM 用就是误导).
        alias_of = {alias: full for full, alias in self._routine_aliases.items()}
        # 展开通配项 ('car/*' -> catalog 中该前缀全部 routine, 按名排序稳定):
        # 逐名快照式白名单会漂移 ---- 新增 routine 永不进表, 通配一次写对.
        # 展开名的别名已在上面全 catalog 覆盖, 无需再补.
        effective: list[str] = []
        for name in self._prompt_routines:
            if name.endswith('/*'):
                prefix = name[:-1]  # 保留尾部 '/'
                effective.extend(sorted(
                    n for n in by_name if n.startswith(prefix)))
            else:
                effective.append(name)
        blocks: list[str] = []
        for name in effective:
            if name in alias_of:
                self._logger.warning(
                    f'skip prompt routine {name!r}: name hijacked as alias '
                    f'for {alias_of[name]!r}'
                )
                continue
            r = by_name.get(name)
            if r is None:
                self._logger.warning(f'prompt routine white-list routine not found: {name!r}')
                continue
            meta_dict = dict(r.get('meta') or {})
            input_schema = meta_dict.get('input_schema')
            if input_schema is None:
                self._logger.warning(f'routine {name!r} has no input_schema, skip')
                continue
            tag = self._routine_aliases.get(name)
            if tag is None:
                if '/' not in name:
                    tag = name  # 无 '/' 的全名天然是合法标签
                else:
                    self._logger.warning(
                        f'skip prompt routine {name!r}: no explicit alias '
                        '(creation param routine_aliases or '
                        'set_routine_alias; names containing "/" require '
                        'an explicit tag)'
                    )
                    continue
            # 块标题恒为原始名, XML 示例用别名标签 ---- 映射由两者并列自明,
            # 不伪造调用记录 (标签来源是创建配置/真实 set_routine_alias,
            # 都已在对话历史留下真实事件).
            xml_example, prop_infos = self._xml_from_schema(tag, input_schema)
            desc = meta_dict.get('description') or ''
            # 块标题恒为原始名; 别名标签只出现在 XML 示例里, 映射由两者
            # 并列自明, 不在块中间嵌入.
            block_lines = [f'### `{name}`', '', f'```xml\n{xml_example}\n```', '']
            if desc:
                block_lines.append(desc)
                block_lines.append('')
            if prop_infos:
                block_lines.append('参数:')
                for p in prop_infos:
                    parts = [f'- `{p["name"]}`']
                    if p['type']:
                        parts.append(f'({p["type"]})')
                    if p['required']:
                        parts.append('必填')
                    elif p['default'] is not None:
                        parts.append(f'默认 {p["default"]}')
                    if p['desc']:
                        parts.append(f'- {p["desc"]}')
                    block_lines.append(' '.join(parts))
                block_lines.append('')
            blocks.append('\n'.join(block_lines).rstrip())
        return '\n\n---\n\n'.join(blocks)

    @staticmethod
    def _xml_from_schema(name: str, schema: dict) -> tuple[str, list[dict]]:
        """从 JSON Schema 渲染 XML 调用示例 + 每参数详情.

        返回 (xml_example, prop_infos),prop_infos 是 [{name, type, desc, default, required}].
        渲染 XML 时:有默认值用默认值,无默认值用类型占位.
        """
        props = schema.get('properties', {}) or {}
        required = set(schema.get('required', []) or [])
        _TYPE_DEFAULT = {'string': 'x', 'integer': '0', 'number': '0', 'boolean': 'true'}
        if not props:
            return f'<{name}/>', []

        def _resolve_type(ps: dict) -> str:
            """anyOf/oneOf 取第一个非 null 类型."""
            t = ps.get('type')
            if t:
                return t
            for combo in ('anyOf', 'oneOf'):
                opts = ps.get(combo) or []
                for opt in opts:
                    if isinstance(opt, dict) and opt.get('type') != 'null':
                        return opt.get('type', 'string')
            return 'string'

        def _fmt_default(v) -> str:
            """int-like float 去尾零(pydantic 把 10 标准化成 10.0 时还原)."""
            if isinstance(v, float) and v.is_integer():
                return str(int(v))
            if v is None:
                return 'null'
            return str(v)

        attrs: list[str] = []
        prop_infos: list[dict] = []
        for pname, pschema in props.items():
            pschema = pschema if isinstance(pschema, dict) else {}
            ptype = _resolve_type(pschema)
            pdesc = (pschema.get('description') or '').strip()
            has_default = 'default' in pschema
            if has_default:
                val = _fmt_default(pschema['default'])
            else:
                val = _TYPE_DEFAULT.get(ptype, 'x')
            attrs.append(f'{pname}="{val}"')
            prop_infos.append({
                'name': pname, 'type': ptype, 'desc': pdesc,
                'default': _fmt_default(pschema['default']) if has_default else None,
                'required': pname in required,
            })
        xml = f'<{name} {" ".join(attrs)}/>'
        return xml, prop_infos

    # ==================================================================
    # emit(对外发布,自动带 agent_id + namespaced 双发)
    # ==================================================================

    async def _emit_output(self, text: str, *, is_final: bool, epoch: int, message_id: str, is_thinking: bool = False, interrupted: bool = False) -> None:
        # 空 text 只在 final 时放行: 打断收尾帧 text 可为空(纯思考被打断),
        # 前端靠 is_final 关流收光标
        if not text and not is_final:
            return
        data = {
            'text': text, 'is_final': is_final,
            'epoch': epoch, 'message_id': message_id,
            'agent_id': self._agent_id,
            'is_thinking': is_thinking,
            'interrupted': interrupted,
        }
        # self._logger.info(f'>>> [{text}]')
        await self.publish('assistant_output', data)
        await self.publish('assistant_output', data, namespace=self._agent_id)

    async def _emit_incoming(self, text: str, from_agent: str) -> None:
        """推一条入站 agent 消息到前端渲染 (用户消息前端已本地 push, 不走这)."""
        data = {'text': text, 'from': from_agent, 'agent_id': self._agent_id}
        await self.publish('incoming_message', data)
        await self.publish('incoming_message', data, namespace=self._agent_id)

    @request('set_effort')
    async def on_set_effort(self, source, data) -> dict:
        """运行时改 reasoning effort. None 关掉 reasoning (模型不支持关时报错)."""
        effort = (data or {}).get('effort')
        if effort == 'none' or effort == '':
            effort = None
        if self._llm:
            try:
                self._llm.set_reasoning_effort(effort)
            except ValueError as exc:
                return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'effort': effort}

    @request('chat_message')
    async def on_chat_message(self, source, data) -> dict:
        """单向异步: 给本 agent 发一条消息, 立即返回, 不等回复.

        按 payload.priority 决定投递: 高 (>=PRIORITY_HIGH) 立即 _on_input 打断
        当前 react; 低 排队等 idle. send_message 写死低; 无 priority 视为高.
        """
        try:
            req = ChatMessageReq.model_validate(data or {})
        except Exception as exc:
            return {'ok': False, 'error': f'invalid payload: {exc}'}
        from .._core.messaging import split_content
        try:
            message, images = split_content(req.content)
        except ValueError as exc:
            return {'ok': False, 'error': f'invalid payload: {exc}'}
        if not message and not images:
            return {'ok': False, 'error': 'message is required'}
        from ._messaging import PRIORITY_HIGH
        raw_priority = (data or {}).get('priority')
        priority = int(raw_priority) if raw_priority is not None else PRIORITY_HIGH
        payload: dict = {'text': message, 'from': req.from_}
        if images:
            payload['images'] = images
        if priority >= PRIORITY_HIGH:
            task = asyncio.create_task(self._on_input(source, payload))
        else:
            task = asyncio.create_task(self._deliver_agent_message(source, payload))
        task.add_done_callback(self._log_deliver_error)
        return {'ok': True, 'epoch': self._epoch}

    async def _deliver_agent_message(self, source, data) -> None:
        """等当前 react 完成 (idle) 后再投递, 不打断在途 react.

        多条 agent 消息通过 _agent_msg_lock 串行: 第一条等 idle 投递后启动
        新 react, 第二条等新 react 完成再投递, 依此类推.
        """
        async with self._agent_msg_lock:
            while True:
                t = self._react_task
                if t is None or t.done():
                    break
                try:
                    await t
                except Exception:
                    pass
            await self._on_input(source, data)

    def _log_deliver_error(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._logger.error(f'deliver_agent_message crashed: {exc}', exc_info=exc)

    async def _emit_feedback(self, *, content: str, results: list, epoch: int, message_id: str) -> None:
        data = {
            'content': content, 'results': results,
            'epoch': epoch, 'message_id': message_id,
            'agent_id': self._agent_id,
        }
        await self.publish('feedback', data)
        await self.publish('feedback', data, namespace=self._agent_id)

    async def _emit_usage(self, usage: dict | None, *, epoch: int, message_id: str) -> None:
        """推 token 用量 + 上下文百分比"""
        if usage:
            self._last_usage = dict(usage)
        llm = self._llm
        data = {
            **self._usage_data(usage),
            'epoch': epoch,
            'message_id': message_id,
            'agent_id': self._agent_id,
            'model_key': llm.model_key if llm else (self._model or ''),
            'model_name': llm.model_name if llm else '',
            'reasoning_effort': llm.reasoning_effort if llm else None,
        }
        await self.publish('usage', data)
        await self.publish('usage', data, namespace=self._agent_id)

    def _usage_data(self, usage: dict | None) -> dict:
        """usage 载荷 (token 数 + 按 condense 触发比率缩放的 percent).

        _emit_usage 实时推送与 get_history/session_changed 回填共用, 保证两路口径一致.
        """
        llm = self._llm
        max_context = llm.max_context if llm else 0
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        if usage:
            input_tokens = int(usage.get('input_tokens') or usage.get('prompt_tokens') or 0)
            output_tokens = int(usage.get('output_tokens') or usage.get('completion_tokens') or 0)
            total_tokens = int(usage.get('total_tokens') or (input_tokens + output_tokens))
        try:
            ratio = float(self._condense_config.get('trigger_ratio', 0.5))
        except (TypeError, ValueError):
            ratio = 0.5
        trigger_tokens = max_context * ratio if max_context > 0 else 0
        percent = round(input_tokens / trigger_tokens * 100, 1) if trigger_tokens > 0 else 0.0
        return {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': total_tokens,
            'max_context': max_context,
            'percent': percent,
        }

    async def _emit_sys_prompt(self, epoch: int, message_id: str, messages: list) -> None:
        await self.publish('sys_prompt', {
            'epoch': epoch, 'message_id': message_id,
            'messages': messages, 'agent_id': self._agent_id,
        })

    async def _emit_session_changed(self, session_id: str, *, is_new: bool) -> None:
        """notify that the session switched, with the message history attached."""
        messages = [] if is_new else self._ctx.load_messages(session_id)
        # resume/启动回填: 上一轮 usage, 圆环立即显示
        usage = None
        if not is_new and self._last_usage and self._llm:
            usage = {
                **self._usage_data(self._last_usage),
                'agent_id': self._agent_id,
                'model_key': self._llm.model_key,
                'model_name': self._llm.model_name,
                'reasoning_effort': self._llm.reasoning_effort,
            }
        await self.publish('session_changed', {
            'agent_id': self._agent_id,
            'session_id': session_id,
            'is_new': is_new,
            'messages': messages,
            'usage': usage,
        })

    @request('get_history')
    async def on_get_history(self, source, data) -> dict:
        """Return this agent's session message history (read fresh from db).

        Returns {ok, session_id, messages, usage}. messages is the general chat
        message list (empty list for a session with no messages). Used by the
        bridge on reconnect to refill the client's view.
        usage: 上一轮的 token 用量 ---- usage 是纯运行时遥测不落库,
        前端刷新后 agentUsage 清空, 这里回填让圆环立即恢复 (无轮次时 None).
        """
        messages = self._ctx.load_messages(self._session_id)
        usage = None
        if self._last_usage and self._llm:
            usage = {
                **self._usage_data(self._last_usage),
                'agent_id': self._agent_id,
                'model_key': self._llm.model_key,
                'model_name': self._llm.model_name,
                'reasoning_effort': self._llm.reasoning_effort,
            }
        return {
            'ok': True,
            'session_id': self._session_id,
            'messages': messages,
            'usage': usage,
        }

    @request('agent_state')
    async def on_agent_state(self, source, data) -> dict:
        """返回 agent 运行时状态, 供 tool routine 通过 ctx.req 反向获取."""
        from ._agent_state import AgentState
        from .emotion import tts_instruction
        return AgentState(
            agent_id=self._agent_id,
            skill_dir=str(self._workspace / 'skills') if self._workspace else None,
            skill_index_cache_dir=str(self._workspace / '.cache') if self._workspace else None,
            project_root=None,
            session_id=self._session_id,
            tts_instruction=tts_instruction(*self._mood),
        ).model_dump()

    @request('set_routine_alias')
    async def on_set_routine_alias(self, source, data) -> dict:
        """设置 routine 标签 {全名: 别名}, upsert 合并.

        效果: prompt 接口表按别名渲染 (下一轮 react 生效), act 分发时把
        别名标签映射回全名调用. 别名 == 全名 的条目等效于清除该标签.
        校验: 全名必须存在于 kernel 注册表 (typo fail fast); 白名单外的
        全名只记 warning 不拒 (渲染不进 prompt 即死配置, 由调用方自觉).
        """
        aliases = (data or {}).get('aliases')
        if not isinstance(aliases, dict) or not aliases:
            return {'ok': False,
                    'error': 'aliases must be a non-empty {name: alias} dict'}
        registered = {str(r.get('name') or '')
                      for r in await self.get_routines()}
        bad = {str(n): a for n, a in aliases.items()
               if not isinstance(n, str) or not n.strip()
               or not isinstance(a, str) or n not in registered}
        if bad:
            return {'ok': False, 'error': f'unknown target routine(s): {bad}'}
        for target in aliases:
            listed = target in self._prompt_routines or any(
                target.startswith(w[:-1]) for w in self._prompt_routines
                if w.endswith('/*'))
            if not listed:
                self._logger.warning(
                    f'alias target {target!r} not in prompt white-list '
                    f'(dead config; consider adding it to prompt_routines)'
                )
        # upsert: 别名 == 全名 清除该条目, 否则覆盖. 别名表渲染/分发共用,
        # 回写 init_kwargs 快照落盘 ---- 重启 resume 恢复, 不白设.
        set_items: list[str] = []
        cleared: list[str] = []
        for name, alias in aliases.items():
            if alias == name:
                self._routine_aliases.pop(name, None)
                cleared.append(name)
            else:
                self._routine_aliases[name] = alias
                set_items.append(f'{alias} = {name}')
        try:
            get_memory().merge_init_kwargs(
                self._agent_id,
                {'routine_aliases': dict(self._routine_aliases)},
            )
        except Exception as exc:
            self._logger.warning(f'persist routine aliases failed: {exc}')
        # 运维事件可见性: 别名表变化以消息形式进入对话历史 (user role,
        # 同 feedback 惯例), 下轮 LLM 自然可见 ---- 而非只静默重渲染
        # system prompt, LLM 对自己工具标签集的变化无感知.
        notice = '[routine_alias]'
        if set_items:
            notice += ' tags updated: ' + ', '.join(sorted(set_items))
        if cleared:
            notice += ('; ' if set_items else ' ') + 'tags cleared: ' \
                + ', '.join(sorted(cleared))
        self._ctx.add_message(
            'user', notice,
            agent_id=self._agent_id,
            message_id=uuid4().hex, session_id=self._session_id,
        )
        self._logger.info('routine aliases updated: %s', self._routine_aliases)
        return {'ok': True, 'aliases': dict(self._routine_aliases)}

    @request('run')
    async def on_run(self, source, data) -> dict:
        """HTTP 转发的 routine 调用. 注入 from_agent_id 后 call 目标 routine."""
        target = str((data or {}).get('target') or '')
        kwargs = (data or {}).get('kwargs') or {}
        if not target:
            return {'ok': False, 'error': 'missing target routine name'}
        if not isinstance(kwargs, dict):
            return {'ok': False, 'error': 'kwargs must be a dict'}
        kwargs[AGENT_ID_KEY] = self._agent_id
        try:
            result = await self.call(target, kwargs)
            return {'ok': True, 'result': result}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}

    # ==================================================================
    # feedback helpers
    # ==================================================================

    @staticmethod
    def _extract_feedback_item(res: dict) -> dict | None:
        """从 routine result dict 提取 feedback 条目,返回 None 表示忽略.

        带 ``content`` 键的 dict 只把 ``content`` 的值反馈给 LLM(details
        留给程序侧);``content: null`` 表示这条不反馈(如 output 说话类
        routine, 结果只供诊断).

        read_image 特判: result 里的 ``images`` (本地路径列表) 提到 item 上,
        随 [feedback] 合并成一条多模态消息 (文本 + 图) 落库. 模型不支持读图时
        组装侧 (_to_input_msg) 剥掉.
        """
        name = str(res.get('name') or '').strip()
        result = res.get('result')
        is_error = False
        if result is None:
            result = res.get('error')
            is_error = result is not None
        images: list[str] = []
        if name == 'read_image' and isinstance(result, dict):
            images = [str(p) for p in result.get('images') or [] if p]
        if isinstance(result, dict) and 'content' in result:
            result = result['content']
        if not name or result is None:
            return None
        item = {
            'name': name,
            'result': json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result),
            'is_error': is_error,
            'input': res.get('input'),
        }
        if images:
            item['images'] = images
        return item

    @staticmethod
    def _build_feedback_message(items: list[dict]) -> str:
        lines = ['[feedback]']
        for item in items:
            prefix = 'ERROR ' if item.get('is_error') else ''
            lines.append(f'- {item["name"]}: {prefix}{item["result"]}')
        return '\n'.join(lines)

    @staticmethod
    def _to_event_results(items: list[dict]) -> list[dict]:
        results: list[dict] = []
        for item in items:
            name = str(item.get('name') or '').strip()
            value = str(item.get('result') or '').strip()
            if not name or not value:
                continue
            out: dict = {'name': name}
            if item.get('input') is not None:
                out['input'] = item['input']
            if item.get('is_error'):
                out['error'] = {'msg': value}
            else:
                out['result'] = value
            results.append(out)
        return results
