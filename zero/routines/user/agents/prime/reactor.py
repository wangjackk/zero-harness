"""ReactorAgent ---- 从 Routine 重新开始的编码 agent.

自包含 epoch 中断 / emit 事件 / 工具调度, 不依赖 ReactiveAgent.
react 循环委托给 ReactLoop, 工具执行委托给 ToolExecutor.
对话状态用 ContextProvider(消息)+ ResponseTracker(response_id).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from routine import Routine, request
from zero.routines.user.agents._core.paths import AGENT_ID_KEY
from routine.logger import setup_logger

from zero.routines.user.skills.registry import build_registry, seed_workspace_skills

from .._core.llm import LLMClient
from .._core.memory import build_context_provider
from .._core.session import SessionStore
from .._core.system_prompt import build_system_prompt
from ...tool_schema import tool_schema
from .loop import ReactLoop, ReactInterrupted

_log = setup_logger('reactor.agent')


class ReactorAgentInput(BaseModel):
    model: str | None = Field(None, description='LLM model name.')
    plan_mode: bool = Field(False, description='Readonly tools only.')
    extra_instructions: str | None = Field(None, description='Extra system instructions.')
    max_turns: int | None = Field(None, description='Max conversation turns.')
    session_id: str | None = Field(None, description='Session id. Auto when omitted.')
    project_dir_root_path: str | None = Field(None, description='Project root path.')
    agent_id: str | None = Field(None, description='Agent id. Auto when omitted.')
    enabled_tools: list[str] | None = Field(None, description='Tool whitelist.')
    disabled_tools: list[str] | None = Field(None, description='Tool blacklist.')
    condense_config: dict[str, Any] | None = Field(None, description='Condenser config.')
    preload_skills: List[str] | None = Field(
        None, description='Skill names to preload (full content appended to system prompt).',
    )
    level1_skills: List[str] | None = Field(
        None, description='Skill names whose name+description are injected into the system '
                          'prompt (level-1 discovery; agent calls load_skill for full content). '
                          'Mutually exclusive with preload_skills per skill.',
    )


class ReactorAgentOutput(BaseModel):
    pass


class ReactorAgent(Routine):
    """Coding agent with simplified dialog state and modular react loop."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': ReactorAgentInput.model_json_schema(),
        'output_schema': ReactorAgentOutput.model_json_schema(),
        'description': (
            'Reactive coding agent with ContextProvider-based memory. '
            'Drives an LLM conversation loop with tool calling.'
        ),
    }

    _BRIDGE_NAME = 'web_server'

    def __init__(self) -> None:
        super().__init__()
        self._agent_id: str = 'main'
        self._project_root: Optional[str] = None
        self._model: Optional[str] = None
        self._plan_mode: bool = False
        self._max_turns: Optional[int] = None
        self._instructions: str = ''
        self._llm: Optional[LLMClient] = None
        self._ctx: Any = None
        self._enabled_tools: Optional[set[str]] = None
        self._disabled_tools: Optional[set[str]] = None
        self._condense_config: dict[str, Any] = {}
        self._tool_map: dict[str, Any] = {}
        self._effective_whitelist: Optional[set[str]] = None

        # 持久化 + skill 体系
        self._session: Any = None
        self._writer: Any = None
        self._workspace: Optional[Path] = None
        self._last_usage: dict[str, Any] = {}

        # epoch 中断
        self._epoch: int = 0
        self._stop: asyncio.Event = asyncio.Event()
        self._event_lock: asyncio.Lock = asyncio.Lock()
        self._tracker_task: Optional[asyncio.Task] = None

    # ── 生命周期 ──

    async def run(self, kwargs: Dict[str, Any]) -> None:
        params = ReactorAgentInput.model_validate(kwargs)
        self._init(params)
        self._preload_skills(params.preload_skills or [])
        if self._ctx is not None:
            await self._ctx.init_session(self._session.session_id)
        await self._subscribe()
        # 推初始 usage 事件: 让前端首屏立即显示模型名 + reasoning effort
        if self._llm is not None:
            await self.emit_usage(
                usage=None,
                max_context=0,
                epoch=0,
                message_id='',
                model_key=self._llm.model_key,
                model_name=self._llm.model_name,
                reasoning_effort=self._llm.reasoning_effort,
            )
        asyncio.create_task(self._register_with_bridge())
        try:
            await self._stop.wait()
        finally:
            if self._ctx is not None and self._ctx.enabled:
                try:
                    await self._ctx.finalize_session()
                except Exception as exc:
                    _log.warning('ctx finalize failed: %r', exc)
            if self._writer is not None:
                self._writer.close(self._last_usage)
            if self._session is not None:
                session_id = self._session.session_id
                SessionStore.close(session_id)
                try:
                    from ..tools._shared._cwd_state import clear_session as _clear_cwd
                    from ..tools._shared._file_state import clear_session as _clear_file
                    from ..tools.shell.BackgroundShellTool.runtime import BgRegistry as _BgRegistry
                    from ..tools.remote.SshTool.runtime import SshRegistry as _SshRegistry
                    _clear_cwd(session_id)
                    _clear_file(session_id)
                    _BgRegistry.clear_session(session_id)
                    _SshRegistry.clear_session(session_id)
                except Exception as exc:
                    _log.warning('session state cleanup failed for %s: %r', session_id, exc)

    async def stop(self) -> None:
        self._stop.set()
        await self._cancel_tracker()

    # ── 初始化 ──

    def _init(self, params: ReactorAgentInput) -> None:
        self._agent_id = params.agent_id or uuid4().hex
        self._model = params.model
        self._plan_mode = params.plan_mode
        self._max_turns = params.max_turns
        self._project_root = params.project_dir_root_path

        if params.enabled_tools and params.disabled_tools:
            raise ValueError('enabled_tools and disabled_tools are mutually exclusive')
        self._enabled_tools = set(params.enabled_tools) if params.enabled_tools else None
        self._disabled_tools = set(params.disabled_tools) if params.disabled_tools else None
        self._condense_config = dict(params.condense_config or {})

        # workspace + skill seed: <project>/.agents/<agent_id>/
        self._workspace = None
        if params.project_dir_root_path:
            self._workspace = Path(params.project_dir_root_path) / '.agents' / self._agent_id
            self._workspace.mkdir(parents=True, exist_ok=True)
            seeded = seed_workspace_skills(self._workspace)
            # 子类 hook: 额外 seed 自带 skills 到 workspace (如 prime/skills/).
            seeded += self._seed_extra_skills(self._workspace)
            if seeded:
                _log.info('seeded %d skill(s) into workspace (agent_id=%s)',
                          seeded, self._agent_id)

        # level1 skill summaries: name + description 注入 system prompt
        skill_summaries: list[tuple[str, str]] = []
        level1_set = set(params.level1_skills or [])
        if self._workspace and level1_set:
            try:
                _reg = build_registry(self._workspace / 'skills')
                _reg.rescan()
                skill_summaries = [
                    (s.name, s.description)
                    for s in _reg.list_skills()
                    if s.name in level1_set
                ]
            except Exception as exc:
                _log.warning('scan skill summaries failed: %r', exc)

        self._instructions = self._build_system_prompt(
            params=params,
            skill_summaries=skill_summaries,
        )
        self._llm = LLMClient(model=params.model)

        # session + writer + dialog (agent_id 是身份, session_id 是 UUID 会话身份)
        sid = params.session_id or self._agent_id
        self._session = SessionStore.open(
            session_id=sid,
            agent_id=self._agent_id,
            cwd=params.project_dir_root_path,
            project_root=params.project_dir_root_path,
            model=params.model,
            reasoning_effort=self._llm.reasoning_effort,
            plan_mode=params.plan_mode,
            max_items=80,
        )
        self._writer = self._session.writer
        # 构造 ContextProvider (Local), 挂到 session.ctx.
        # replay items 暂存在 session._replay_items, response_id 状态在 tracker.
        self._ctx = build_context_provider(
            writer=self._writer,
            workspace=self._workspace,
            max_items=80,
        )
        self._session.ctx = self._ctx
        replay_items = getattr(self._session, '_replay_items', None) or []
        if replay_items:
            self._ctx.load_items(replay_items)

    def _seed_extra_skills(self, workspace: Path) -> int:
        """子类 hook: 额外 seed 自带 skills 到 workspace. 默认不做事."""
        return 0

    def _build_system_prompt(
        self, *, params: ReactorAgentInput,
        skill_summaries: list[tuple[str, str]],
    ) -> str:
        """子类 hook: 构建 system prompt. 默认用 reactor 风格."""
        return build_system_prompt(
            plan_mode=params.plan_mode,
            extra=params.extra_instructions,
            model=params.model,
            project_root=params.project_dir_root_path,
            skill_summaries=skill_summaries,
            agent_id=self._agent_id,
        )

    def _preload_skills(self, skills: list[str]) -> None:
        """预加载 skill: 全量正文拼到 system prompt 末尾 (prefix caching 友好)."""
        if not skills or self._workspace is None:
            return
        reg = build_registry(self._workspace / 'skills')
        parts: list[str] = []
        for skill_name in skills:
            try:
                content = reg.invoke_for_preload(skill_name)
                parts.append(content)
                _log.info('preloaded skill: %s (agent_id=%s)', skill_name, self._agent_id)
            except KeyError:
                _log.warning('preload skill not found: %s', skill_name)
        if parts:
            self._instructions = (self._instructions or '') + '\n\n' + '\n\n'.join(parts)

    # ── 工具构建 ──

    def build_tools(self) -> list[dict]:
        """构建 tools schema + tool_map, 在 react 首轮调用.

        显式 enabled_tools 直接成为最终白名单, 不需要 meta.tool 二次过滤:
        tool 是否暴露给 LLM 是业务侧的显式选择, 不该让 routine 自带标志位来管.
        隐式发现 (无 enabled/disabled) 时才用 meta.tool 兜底识别工具.
        """
        hub = self.ctx.hub
        all_routines = hub.runtime.routines.get_routines()
        all_by_name = {cls.name: cls for cls in all_routines}

        if self._enabled_tools:
            tool_names: set[str] = set(self._enabled_tools)
        elif self._disabled_tools is not None:
            tool_names = {
                cls.name for cls in all_routines
                if getattr(cls, 'meta', {}).get('tool')
                and cls.name not in self._disabled_tools
            }
        else:
            tool_names = {
                cls.name for cls in all_routines
                if getattr(cls, 'meta', {}).get('tool')
            }

        self._effective_whitelist = tool_names

        schemas: list[dict] = []
        tool_map: dict[str, Any] = {}
        for name in tool_names:
            cls = all_by_name.get(name)
            if cls is None:
                continue
            if self._plan_mode and not getattr(cls, 'meta', {}).get('readonly'):
                continue
            tool_map[name] = cls
            schemas.append(tool_schema(cls))
        self._tool_map = tool_map
        return schemas

    # ── 订阅 ──

    async def _subscribe(self) -> None:
        ns = self.namespace(self._agent_id)
        await ns.subscribe('user_input', self._on_input)
        await ns.subscribe('interrupt', self._on_interrupt)

    # ── 事件处理 ──

    async def _on_input(self, source, data: dict) -> None:
        async with self._event_lock:
            from zero.routines.user.agents._core.messaging import USER_SOURCE, wrap_from
            text = str((data or {}).get('text') or '')
            if not text:
                return
            from_agent = str((data or {}).get('from') or USER_SOURCE)
            content = wrap_from(text, from_agent)
            self._epoch += 1
            await self._cancel_tracker()
            self._ctx.append_user(content)
            if from_agent != USER_SOURCE:
                await self.emit_incoming_message(content, from_agent)
            self._tracker_task = asyncio.create_task(self._run_react(self._epoch))

    async def _on_interrupt(self, source, data: dict) -> None:
        async with self._event_lock:
            self._epoch += 1
            await self._cancel_tracker()

    async def _cancel_tracker(self) -> None:
        if self._tracker_task and not self._tracker_task.done():
            self._tracker_task.cancel()
            try:
                await self._tracker_task
            except (asyncio.CancelledError, ReactInterrupted, Exception):
                pass

    async def _run_react(self, epoch: int) -> None:
        message_id = uuid4().hex
        loop = ReactLoop(self)
        try:
            await loop.run(epoch, message_id)
        except asyncio.CancelledError:
            pass

    # ── epoch 中断 ──

    def check_epoch(self, epoch: int) -> None:
        if epoch != self._epoch:
            raise ReactInterrupted()

    # ── emit (前端事件发布) ──

    async def emit_text(
        self, text: str, *,
        epoch: int, message_id: str,
        is_final: bool = False, is_thinking: bool = False,
    ) -> None:
        # 空 text + is_final: 收尾信号, 仍要发 (清除前端 streaming 状态).
        # 空 text + 非 final: 无意义, 跳过.
        if not text and not is_final:
            return
        data = {
            'text': text, 'is_final': is_final, 'is_thinking': is_thinking,
            'epoch': epoch, 'message_id': message_id, 'agent_id': self._agent_id,
        }
        await self.publish('assistant_output', data)
        await self.publish('assistant_output', data, namespace=self._agent_id)

    async def emit_feedback(self, *, content: str, results: list, epoch: int, message_id: str) -> None:
        data = {
            'content': content, 'results': results,
            'epoch': epoch, 'message_id': message_id, 'agent_id': self._agent_id,
        }
        await self.publish('feedback', data)
        await self.publish('feedback', data, namespace=self._agent_id)

    async def emit_sys_prompt(self, *, epoch: int, message_id: str, messages: list) -> None:
        data = {
            'epoch': epoch, 'message_id': message_id,
            'messages': messages, 'agent_id': self._agent_id,
        }
        await self.publish('sys_prompt', data)
        await self.publish('sys_prompt', data, namespace=self._agent_id)

    async def emit_usage(
        self, *, usage: dict[str, Any] | None, max_context: int,
        epoch: int, message_id: str, trigger_ratio: float = 0.8,
        model_key: str = '', model_name: str = '',
        reasoning_effort: str | None = None,
    ) -> None:
        if usage:
            self._last_usage = dict(usage)
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        if usage:
            input_tokens = int(usage.get('input_tokens') or usage.get('prompt_tokens') or 0)
            output_tokens = int(usage.get('output_tokens') or usage.get('completion_tokens') or 0)
            total_tokens = int(usage.get('total_tokens') or (input_tokens + output_tokens))
        trigger_tokens = max_context * trigger_ratio if max_context > 0 else 0
        percent = round(input_tokens / trigger_tokens * 100, 1) if trigger_tokens > 0 else 0.0
        data = {
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'total_tokens': total_tokens, 'max_context': max_context,
            'percent': percent, 'epoch': epoch, 'message_id': message_id,
            'agent_id': self._agent_id, 'model_key': model_key,
            'model_name': model_name, 'reasoning_effort': reasoning_effort,
        }
        await self.publish('usage', data)
        await self.publish('usage', data, namespace=self._agent_id)

    async def emit_incoming_message(self, text: str, from_agent: str) -> None:
        data = {'text': text, 'from': from_agent, 'agent_id': self._agent_id}
        await self.publish('incoming_message', data)
        await self.publish('incoming_message', data, namespace=self._agent_id)

    # ── request handlers ──

    @request('agent_state')
    async def on_agent_state(self, source, data) -> dict:
        skill_dir = str(self._workspace / 'skills') if self._workspace else None
        session_id = self._session.session_id if self._session else self._agent_id
        return {
            'agent_id': self._agent_id,
            'session_id': session_id,
            'project_root': self._project_root,
            'skill_dir': skill_dir,
            'plan_mode': self._plan_mode,
            'model': self._model,
        }

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

    @request('set_effort')
    async def on_set_effort(self, source, data) -> dict:
        effort = (data or {}).get('effort')
        if effort == 'none' or effort == '':
            effort = None
        if self._llm:
            self._llm.set_reasoning_effort(effort)
        if self._session:
            self._session.state.reasoning_effort = effort
            self._writer.write_state_snapshot(self._session.state.to_dict())
        return {'ok': True, 'effort': effort}

    @request('chat_message')
    async def on_chat_message(self, source, data) -> dict:
        """单向异步: 给本 agent 发一条消息, 立即返回, 不等回复.

        按 payload.priority 决定投递: 高 (>=PRIORITY_HIGH) 立即 _on_input
        打断当前 react; 低 排队等 idle. 无 priority 视为高.
        """
        message = str((data or {}).get('message', '')).strip()
        if not message:
            return {'ok': False, 'error': 'message is required'}
        from_agent = (data or {}).get('from')
        from zero.routines.user.agents._core.messaging import PRIORITY_HIGH
        raw_priority = (data or {}).get('priority')
        priority = int(raw_priority) if raw_priority is not None else PRIORITY_HIGH
        payload = {'text': message, 'from': from_agent}
        if priority >= PRIORITY_HIGH:
            task = asyncio.create_task(self._on_input(source, payload))
        else:
            task = asyncio.create_task(self._deliver_agent_message(source, payload))
        task.add_done_callback(self._log_deliver_error)
        return {'ok': True, 'epoch': self._epoch}

    async def _deliver_agent_message(self, source, data) -> None:
        """等当前 react 完成 (idle) 后再投递, 不打断在途 react.

        多条 agent 消息通过 _event_lock 串行: 第一条等 idle 投递后启动
        新 react, 第二条等新 react 完成再投递, 依此类推.
        """
        while True:
            t = self._tracker_task
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
            _log.error('deliver_agent_message crashed: %s', exc, exc_info=exc)

    @request('get_history')
    async def on_get_history(self, source, data) -> dict:
        """返回本 agent 的 session 消息历史 (从 store 实时读取).

        返回 {ok, session_id, messages}. 无消息时 messages 为空列表.
        bridge 在重连时调此接口回填前端视图.
        """
        from .._core.messages import entries_to_messages
        from .._core.store import get_store

        session_id = self._session.session_id if self._session else None
        messages = entries_to_messages(
            get_store().iter_session_messages(self._agent_id, session_id),
        ) if session_id else []
        return {
            'ok': True,
            'session_id': session_id,
            'messages': messages,
        }

    @request('interrupt')
    async def on_interrupt_req(self, source, data) -> dict:
        asyncio.create_task(self._on_interrupt(source, data or {}))
        return {'ok': True}

    # ── 辅助 ──

    def _mark_response(
        self, response_id: str, text: str = '',
        usage: dict[str, Any] | None = None,
    ) -> None:
        """记录一次 response 完成: tracker 更新 cursor + writer 持久化 checkpoint."""
        self._session.tracker.mark_response(
            response_id, text, usage, len(self._ctx.items()),
        )
        if self._writer:
            self._writer.write_response_checkpoint(response_id, text, usage)
        self._last_usage = usage or {}

    def conv_to_prompt_messages(self) -> list[dict]:
        msgs: list[dict] = []
        if self._instructions:
            msgs.append({'role': 'system', 'content': self._instructions})
        if not self._ctx:
            return msgs
        for item in self._ctx.items():
            role = item.get('role')
            if role in ('user', 'assistant'):
                msgs.append({'role': role, 'content': item.get('content', '')})
            elif item.get('type') == 'function_call':
                msgs.append({'role': 'tool',
                             'content': f"call: {item.get('name')}({item.get('arguments', '')})"})
            elif item.get('type') == 'function_call_output':
                msgs.append({'role': 'tool', 'content': f"result: {item.get('output', '')}"})
        return msgs

    async def maybe_condense(self, epoch: int) -> None:
        """上下文压缩: 委托给 ContextProvider.compact."""
        if not self._ctx or not self._llm:
            return
        result = await self._ctx.compact(
            agent_id=self._agent_id,
            session_id=self._session.session_id,
            model_key=self._llm.model_key,
            max_context=self._llm.max_context,
            plan_mode=self._plan_mode,
            condense_config=self._condense_config,
            project_root=self._project_root,
            cwd=self._project_root,
            call=self.call,
        )
        if result:
            # 压缩后 items 已投影替换 (summary + retained), 服务端 response_id
            # 缓存对不上, 失效. 清 tracker, 下次全量重发 (to_request 返回全量 + None).
            self._session.tracker.reset()

    @property
    def trigger_ratio(self) -> float:
        try:
            return float(self._condense_config.get('trigger_ratio', 0.8))
        except (TypeError, ValueError):
            return 0.8

    # ── bridge 注册 ──

    async def _register_with_bridge(self) -> None:
        from .._core.bridge import register_with_bridge
        await register_with_bridge(
            agent_id=self._agent_id,
            routine_id=self.id,
            name_prefix='Reactor-',
            bridge_name=self._BRIDGE_NAME,
            ctx=self.ctx,
            stop_event=self._stop,
            logger=_log,
            on_success=self._emit_session_history,
        )

    async def _emit_session_history(self) -> None:
        from .._core.messages import entries_to_messages
        from .._core.store import get_store

        is_new = not bool(self._ctx.items()) if self._ctx else True
        messages = [] if is_new else entries_to_messages(
            get_store().iter_session_messages(self._agent_id, self._session.session_id),
        )
        await self.publish('session_changed', {
            'agent_id': self._agent_id,
            'session_id': self._session.session_id if self._session else self._agent_id,
            'is_new': is_new,
            'messages': messages,
        })
        await self.emit_sys_prompt(
            epoch=self._epoch, message_id=uuid4().hex,
            messages=self.conv_to_prompt_messages(),
        )
