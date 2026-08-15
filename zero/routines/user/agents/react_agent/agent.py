"""ReactAgent -- reactive agent(ContextProvider 记忆 + 内置 LLM,每轮直推 act 子).

- 直接继承 ``Routine``(被动常驻编排器,不收 XML body,不派生工具子)--
  XmlRoutine 的 body_shell/parser/on_body_chunk 一套它都不用,继承是历史残留.
- **记忆用 ReactContextProvider**(见 ``provider.py``):封装 Memory(sqlite 持久化)
  + OVMemory(OpenViking 长期记忆),默认启用 OV.
- **每轮 react 直推一个 act 子**(submit+start+stop 直管,act 才是 XmlRoutine,
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

from zero.routines._shared._paths import AGENT_ID_KEY

from .._core.llm import LLMClient, TextDelta, ReasoningDelta, Completed
from .provider import ReactContextProvider
from .condenser import project_with_summary

# legacy shared constants removed: per-instance agent_id (from manager submit
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
    message: str = Field(description='消息文本')
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


class ReactAgentInput(BaseModel):
    agent_id: Optional[str] = Field(
        None, description='Instance id; auto-generated when omitted.',
    )
    session_id: Optional[str] = Field(
        None, description='Session id; resumed from db on re-spawn, else auto.',
    )
    model: Optional[str] = Field(
        None, description='LLM model key (provider/model_name, e.g. seed/glm-5.2). Defaults to models.yaml default.',
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


class ReactAgentOutput(BaseModel):
    pass


class ReactAgent(Routine):
    """reactive agent:sqlite 记忆 + 每轮直推 act 子 + 内置 LLM.被动常驻."""

    # is_passive removed: dynamic instance spawned by ReactAgents manager.
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '精简版 reactive agent: sqlite 记忆 + 每轮 push act, 常驻对话.',
        'input_schema': ReactAgentInput.model_json_schema(),
        'output_schema': ReactAgentOutput.model_json_schema(),
    }
    # LLM 可主动调用的 routine 白名单:进 system prompt(XML 示例形式).
    # output 说话类是**被动**派发(裸文本自动走 output),不在此列--LLM 只输出自然语言即可.
    PROMPT_ROUTINES: list[str] = ['play_music', 'list_music', 'query_weather', 'print_heart',
                                  'WAIT', 'list_routines', 'routine_doc',
                                  'list_running_agents', 'send_message']



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
        # per-session workspace (runtime/react_agent/sessions/<session_id>/).
        # skill / ov_cursor 都落这里, 切 session 时整目录切换.
        self._workspace: Optional[Path] = None
        # 上下文 provider: Memory 持久化 + OV 长期记忆 (on_started 里构造)
        self._ctx: Optional[ReactContextProvider] = None
        # condenser 配置 (trigger_ratio 等; TODO 暂未接入)
        self._condense_config: dict[str, Any] = {}
        # 一级 skill 摘要 [(name, desc)], 由 on_started 扫描 workspace skills 生成,
        # 在 _build_messages 里拼到 system prompt 后面 (对标 prime).
        self._skill_summaries: list[tuple[str, str]] = []
        # 预加载 skill 全文 (旁路: 强制全量塞进 prompt, 绕过两级)
        self._preloaded_skills_text: str = ''

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
        """被动启动后:订阅 user_input/interrupt/session + 向 bridge 注册自己.

        注册走 req(1:1) + 重试(见 _register_with_bridge),不靠 conversation_open
        pubsub--后者无订阅者时静默丢,依赖注册顺序太脆弱.bridge 收到注册后建
        agent_id->namespace 路由 + 广播 conversation_open 给前端建 tab.

        额外初始化
          - per-session workspace (runtime/react_agent/sessions/<session_id>/)
          - seed builtin skills 到 workspace/skills/
          - 扫描 level1_skills 生成摘要, 注入 system prompt
          - 预加载 preload_skills 全文, 注入 system prompt
          - ReactContextProvider (Memory + OV, peer_id='react'), init_session
        """
        kwargs = self.init_kwargs or {}
        agent_id = kwargs.get('agent_id') or uuid4().hex
        session_id = kwargs.get('session_id') or uuid4().hex
        model = kwargs.get('model') or self.LLM_MODEL
        self._agent_id = agent_id
        self._session_id = session_id
        self._model = model
        self._llm = LLMClient(model)
        # 新增配置 (从 init_kwargs 读, manager 透传; 缺省 None/空)
        ov_config = kwargs.get('ov_config')
        self._condense_config = dict(kwargs.get('condense_config') or {})
        preload_skills = kwargs.get('preload_skills') or []
        level1_skills = kwargs.get('level1_skills') or []

        ns = self.namespace(self._agent_id)
        await ns.subscribe('user_input', self._on_input)
        await ns.subscribe('interrupt', self._on_interrupt)
        # 注册到 WS bridge:req(1:1,失败重试)替代 conversation_open pubsub.
        # pubsub 无订阅者时静默丢--靠注册顺序保证太脆弱;req 失败 caller 知道,
        # 轮询直到 bridge 起来.fire-and-forget:on_started 不阻塞,run() 照常 park.
        asyncio.create_task(self._register_with_bridge())

        # --- per-session workspace + skill seed (复用 skills 的 registry) ---
        # workspace 跟 session_id 1:1 (agent=session 模型, per-agent = per-session).
        self._workspace = Path('runtime/react_agent/sessions') / self._session_id
        self._workspace.mkdir(parents=True, exist_ok=True)
        try:
            from zero.routines.user.skills.registry import (
                build_registry, seed_workspace_skills,
            )
            seeded = seed_workspace_skills(self._workspace)
            if seeded:
                self._logger.info(
                    f'seeded {seeded} skill(s) into workspace {self._workspace}'
                )
            # 一级 skill 摘要: 只注入 level1_skills 里指定的 name+desc,
            # LLM 看到匹配的 skill 后自己调 load_skill 加载完整内容 (两级渐进).
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

        # --- ReactContextProvider: Memory 持久化 + OV 长期记忆 (peer_id='react') ---
        # OV init 失败只 log warning, provider.enabled=False, 后续全跳过.
        self._ctx = ReactContextProvider(
            ov_config=ov_config,
            workspace=self._workspace,
            peer_id='react',
            agent_id=self._agent_id,
        )
        await self._ctx.init_session(self._session_id)

        self._logger.info(f'{self.name} started (agent_id={self._agent_id} session={self._session_id}), registering with bridge')

    async def run(self, kwargs: Dict[str, Any]) -> None:
        """永久驻留:等 stop 放行.react 由 user_input 事件驱动(_on_input spawn)."""
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
        self._stop.set()

    async def _register_with_bridge(self) -> None:
        """向 WS bridge req 注册自己 (委托共享实现)."""
        from .._core.bridge import register_with_bridge

        async def _on_success() -> None:
            # emit session_changed so frontend backfills panel history
            # (same link prime uses; is_new = no prior messages).
            is_new = not self._ctx.load_history(self._session_id)
            await self._emit_session_changed(self._session_id, is_new=is_new)

        await register_with_bridge(
            agent_id=self._agent_id,
            routine_id=self.id,
            name_prefix='React-',
            bridge_name=_BRIDGE_NAME,
            ctx=self.ctx,
            stop_event=self._stop,
            logger=self._logger,
            on_success=_on_success,
        )

    # ==================================================================
    # reactive / interrupt(inline,照 reactive_agent.py 模式)
    # ==================================================================

    async def _on_input(self, source, data) -> None:
        async with self._event_lock:
            from zero.routines._shared._agent_messaging import IncomingMessage, USER_SOURCE, wrap_from
            msg = IncomingMessage.from_payload(data)
            if not msg.text:
                return
            content = wrap_from(msg.text, msg.from_)
            self._epoch += 1
            self._logger.info(f'epoch -> {self._epoch} (user_input): {content!r}')
            await self._cancel_react('user_input')
            await self._interrupt_act_if_needed()
            self._ctx.add_message(
                'user', content,
                agent_id=self._agent_id,
                message_id=uuid4().hex, session_id=self._session_id,
            )
            if msg.from_ != USER_SOURCE:
                await self._emit_incoming(content, msg.from_)
            self._start_react(self._epoch)

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

    async def _run_react(self, epoch: int) -> None:
        """epoch 内多轮迭代:react 返回 True 表示有 feedback 要再来一轮.

        取消靠 ``_cancel_react`` 的 ``task.cancel() + await task``--CancelledError
        在 react 的下一个 await 点抛出,except 存半截内容后 re-raise,task 自然死.
        epoch 在这不再做取消判断(那是 cancel 的职责),只作 emit 给前端的输出序号.
        """
        try:
            while True:
                more = await self.react(epoch)
                if not more:
                    return
        except asyncio.CancelledError:
            raise

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
        #   feedback_items  -- 给 LLM 的(for_llm 过滤后,说话类被抑制)
        #   results_raw     -- 原始所有子 routine 的 {name, result, error}(不过滤,含 output frames)
        feedback_items: list[dict] = []
        results_raw: list[dict] = []
        response_id: str | None = None  # 本轮 LLM response id(成功才有,prompt caching 用)

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
            # 派生,不经 agent).submit + start + stop 直管,无需 Shell(Shell 为多兄弟
            # 排序/barrier 而设,单子纯开销).对标 self.push('act')(走 routine 的 shell).
            # agent_id: act 在 push 工具子前注入, tool routine 通过 get_agent_rid
            # 查 rid 后 ctx.req(rid, 'agent_state') 反向获取 skill_dir 等信息.
            _t = _time.perf_counter()
            act = await self.submit('act', {
                AGENT_ID_KEY: self._agent_id,
            })
            self._cur_act = act
            await act.start()
            _ms_act_submit = _ms(_t)

            # LLM 流式 XML -> ctx.send 喂给 act 的 body(走 act.on_message reorder
            # -> parser -> body_shell 派发工具子).seq 递增保 reorder 顺序.
            #
            # previous_response_id 过期兜底:服务端缓存 TTL 到了(doubao ~30min)会报
            # PreviousResponseNotFound -> 清过期 id,全量重发重试一次.400 在首个 delta
            # 前抛,此时 text_output 仍空,act 没收到 body,重试安全不重复.
            _t = _time.perf_counter()
            _first_token_logged = False
            for _attempt in (0, 1):
                try:
                    seq = 0
                    async for kind, payload, usage in self._stream_llm(
                        input_msgs, instructions, previous_response_id,
                    ):
                        if kind == 'text' and payload:
                            if not _first_token_logged:
                                self._logger.info(
                                    f'react timing: act_submit={_ms_act_submit}ms '
                                    f'llm_first_token={_ms(_t)}ms'
                                )
                                _first_token_logged = True
                            await self.ctx.send(act.id, {'text': payload, 'id': seq})
                            seq += 1
                            text_output += payload
                            await self._emit_output(payload, is_final=False, epoch=epoch, message_id=message_id)
                        elif kind == 'thinking' and payload:
                            await self._emit_output(payload, is_final=False, epoch=epoch, message_id=message_id, is_thinking=True)
                        elif kind == 'done':
                            response_id = payload  # Completed.response_id
                            self._logger.info(
                                f'react llm done event: response_id={response_id[:24]}... '
                                f't={_ms(_t)}ms'
                            )
                            await self._emit_usage(usage, epoch=epoch, message_id=message_id)
                    self._logger.info(
                        f'react llm stream loop exited: seq={seq} '
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
                            {'role': m['role'], 'content': m['content']}
                            for m in full_history
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
            await self.ctx.send(act.id, {'_eof': True, 'id': seq})
            await self._emit_output(text_output, is_final=True, epoch=epoch, message_id=message_id)

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
                # 给 LLM 的:for_llm 过滤后(说话类被 for_llm:null 抑制).
                item = self._extract_feedback_item(res)
                if item is not None:
                    feedback_items.append(item)
            _ms_act_collect = _ms(_t)

        except asyncio.CancelledError:
            # 被新输入/中断打断(_cancel_react 的 task.cancel).保住已输出的部分内容
            # (连续而非 0/1):标 interrupted=True 入库.act 不在这停--event handler 的
            # _interrupt_act_if_needed 会停(那时 task 已 done,无在途 ack 冲突).
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
            )
            raise

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
        self._ctx.add_message(
            'user', feedback_msg,
            agent_id=self._agent_id,
            message_id=uuid4().hex, session_id=self._session_id,
        )
        await self._emit_feedback(
            content=feedback_msg,
            results=self._to_event_results(feedback_items),
            epoch=epoch, message_id=message_id,
        )
        return True

    # ==================================================================
    # 上下文压缩 (委托 provider.compact, 走本地 react_condenser_agent)
    # ==================================================================

    async def _maybe_condense(self, epoch: int) -> None:
        """每轮 LLM 调用前检查是否需要压缩上下文.

        委托给 ReactContextProvider.compact:
          走 react_condenser_agent (写 summary 到 messages 表, kind='summary').

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
            self._logger.info('context condensed via react_condenser_agent')

    # ==================================================================
    # LLM 调用(Responses API 直调,复用 _core/llm.py 的 LLMClient)
    # ==================================================================

    async def _stream_llm(
        self, input_msgs: list[dict], instructions: str | None,
        previous_response_id: str | None = None,
    ):
        """直调 LLM(Responses API),流式 yield (kind, text|response_id|None).

        复用 ``_core/llm.py`` 的 ``LLMClient``(配置从 models.yaml 加载).

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
        # 只列 name+desc, LLM 看到匹配的 skill 后自己调 load_skill 加载完整内容.
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

        if previous_response_id is not None:
            # 增量: 发 R 之后的所有消息(含被打断的半截 assistant), 过滤空 content.
            input_msgs = [
                {'role': m['role'], 'content': m['content']}
                for m in history[cut:] if m['content']
            ]
            # 增量为空(过滤后无消息) -> 降级全量(否则 API 报 input.content 缺失).
            if not input_msgs:
                previous_response_id = None
                input_msgs = [
                    {'role': m['role'], 'content': m['content']}
                    for m in history if m['content']
                ]
        else:
            # 全量: 首启 / 全被打断 / summary 之后无新对话.
            input_msgs = [
                {'role': m['role'], 'content': m['content']}
                for m in history if m['content']
            ]
        return system or None, input_msgs, previous_response_id, history

    async def _render_routines(self) -> str:
        """渲染白名单 ``PROMPT_ROUTINES`` 内 routine 的接口表.

        只列 LLM 该**主动**调用的工具.output 说话类是被动派发(裸文本自动走
        output),不列入--LLM 只管输出自然语言,框架自动让它说话.

        走 ``ctx.hub.runtime.routines.get_routines()`` 拿已注册 routine 类列表
        (对标 HttpServer._list_routines 的既定路径),读 ``meta['input_schema']``
        渲染.新框架 routine 都有 input_schema,无需 prompt_providers 的签名
        反射降级.
        """
        try:
            hub = self.ctx.hub
            if hub is None:
                self._logger.warning('no RoutineHub on ctx.hub; cannot render routines')
                return ''
            routines = hub.runtime.routines.get_routines()
        except Exception as exc:
            self._logger.warning(f'get routines failed: {exc}')
            return ''
        by_name = {cls.name: cls for cls in routines}
        blocks: list[str] = []
        for name in self.PROMPT_ROUTINES:
            cls = by_name.get(name)
            if cls is None:
                self._logger.warning(f'PROMPT_ROUTINES white-list routine not found: {name!r}')
                continue
            meta_dict = dict(getattr(cls, 'meta', {}) or {})
            input_schema = meta_dict.get('input_schema')
            if input_schema is None:
                self._logger.warning(f'routine {name!r} has no input_schema, skip')
                continue
            xml_example, prop_infos = self._xml_from_schema(name, input_schema)
            desc = meta_dict.get('description') or ''
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

    async def _emit_output(self, text: str, *, is_final: bool, epoch: int, message_id: str, is_thinking: bool = False) -> None:
        if not text:
            return
        data = {
            'text': text, 'is_final': is_final,
            'epoch': epoch, 'message_id': message_id,
            'agent_id': self._agent_id,
            'is_thinking': is_thinking,
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
        """运行时改 reasoning effort. None 关掉 reasoning."""
        effort = (data or {}).get('effort')
        if effort == 'none' or effort == '':
            effort = None
        if self._llm:
            self._llm.set_reasoning_effort(effort)
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
        if not req.message.strip():
            return {'ok': False, 'error': 'message is required'}
        from zero.routines._shared._agent_messaging import PRIORITY_HIGH
        raw_priority = (data or {}).get('priority')
        priority = int(raw_priority) if raw_priority is not None else PRIORITY_HIGH
        payload: dict = {'text': req.message, 'from': req.from_}
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
        llm = self._llm
        max_context = llm.max_context if llm else 0
        reasoning_effort = llm.reasoning_effort if llm else None
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        if usage:
            input_tokens = int(usage.get('input_tokens') or usage.get('prompt_tokens') or 0)
            output_tokens = int(usage.get('output_tokens') or usage.get('completion_tokens') or 0)
            total_tokens = int(usage.get('total_tokens') or (input_tokens + output_tokens))
        trigger_tokens = max_context * 0.8 if max_context > 0 else 0
        percent = round(input_tokens / trigger_tokens * 100, 1) if trigger_tokens > 0 else 0.0
        data = {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': total_tokens,
            'max_context': max_context,
            'percent': percent,
            'epoch': epoch,
            'message_id': message_id,
            'agent_id': self._agent_id,
            'model_key': llm.model_key if llm else (self._model or ''),
            'model_name': llm.model_name if llm else '',
            'reasoning_effort': reasoning_effort,
        }
        await self.publish('usage', data)
        await self.publish('usage', data, namespace=self._agent_id)

    async def _emit_sys_prompt(self, epoch: int, message_id: str, messages: list) -> None:
        await self.publish('sys_prompt', {
            'epoch': epoch, 'message_id': message_id,
            'messages': messages, 'agent_id': self._agent_id,
        })

    async def _emit_session_changed(self, session_id: str, *, is_new: bool) -> None:
        """notify that the session switched, with the message history attached."""
        messages = [] if is_new else self._ctx.load_messages(session_id)
        await self.publish('session_changed', {
            'agent_id': self._agent_id,
            'session_id': session_id,
            'is_new': is_new,
            'messages': messages,
        })

    @request('get_history')
    async def on_get_history(self, source, data) -> dict:
        """Return this agent's session message history (read fresh from db).

        Returns {ok, session_id, messages}. messages is the general chat
        message list (empty list for a session with no messages). Used by the
        bridge on reconnect to refill the client's view.
        """
        messages = self._ctx.load_messages(self._session_id)
        return {
            'ok': True,
            'session_id': self._session_id,
            'messages': messages,
        }

    @request('agent_state')
    async def on_agent_state(self, source, data) -> dict:
        """返回 agent 运行时状态, 供 tool routine 通过 ctx.req 反向获取."""
        from zero.routines._shared._agent_state import AgentState
        return AgentState(
            agent_id=self._agent_id,
            skill_dir=str(self._workspace / 'skills') if self._workspace else None,
            skill_index_cache_dir=str(self._workspace / '.cache') if self._workspace else None,
            project_root=None,
            session_id=self._session_id,
        ).model_dump()

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

        带 ``for_llm`` 键的 dict 只把 ``for_llm`` 的值反馈给 LLM;``for_llm: null``
        表示这条不反馈(如 output 说话类 routine,结果只供诊断).沿用本会话
        建的约定(见老 ``reactive_agent._extract_feedback_item``).
        """
        name = str(res.get('name') or '').strip()
        result = res.get('result')
        is_error = False
        if result is None:
            result = res.get('error')
            is_error = result is not None
        if isinstance(result, dict) and 'for_llm' in result:
            result = result['for_llm']
        if not name or result is None:
            return None
        return {
            'name': name,
            'result': json.dumps(result, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result),
            'is_error': is_error,
            'input': res.get('input'),
        }

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
