"""FlowAgent ---- turn/step 循环的编码 agent (dsh ReactLoopAgent 移植).

与 prime ReactorAgent 的核心差异:
- 输入不打断在途轮次: followup 进 next-turn FIFO, 当前轮完成后处理
  (prime 是 bump epoch 打断重启)
- 轮次/步骤有显式边界, pre_step 可拒绝, turn_stopping 可观察 ----
  循环的每个环节是进程内扩展点 (HookRegistry)
- 工具串并行交给 Shell 编排: modules cone 交集冲突→串行, 不冲突→并行
  (与 kernel TryAcquire 同一真理源, 不靠 meta 标志位)
- 中断走 Abort 协作信号, cancel 可保留 inbox (排队消息不丢)

零项目基础设施依赖: 存储 (EventLog) / LLM (flow.llm) / token 度量
(flow.context) / 协议常量 (flow.wire) / prompt / schema 发现 /
bridge 注册全部自有. 仅依赖 routine 框架本身与 Shell 编排原语.
spawn 与前端走 FlowHub (flow.hub) ---- 独立前门, 不经 prime
manager / preset / sqlite Store.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from routine import Routine, request
from routine.errors import ReqError, ReqTimeout
from routine.logger import setup_logger

from .eventlog import EventLog
from .hooks import HookRegistry
from .inbox import Inbox, InboxMessage
from .llm import Llm
from .loop import FlowLoop, _is_context_overflow
from .phase import Aborted, Running
from .prompt import build_flow_system_prompt
from .scheduler import ToolScheduler
from .skills import invoked_skill_names, render_skill_content, skills_req
from .stats import derive_stats
from .wire import AGENT_ID_KEY, PRIORITY_HIGH, PRIORITY_LOW, \
    USER_SOURCE, wrap_from, workspace_for

_log = setup_logger('flow.agent')


class FlowAgentInput(BaseModel):
    """FlowAgent 的输入契约: manager 按 preset 组装投递的 spawn kwargs.

    未知 kwargs (如 skills 相关) 由 pydantic 忽略 ---- flow 不实现
    skill 体系.
    """
    model_config = {'extra': 'ignore'}

    model: str | None = Field(None, description='LLM model name.')
    plan_mode: bool = Field(False, description='Readonly tools only.')
    extra_instructions: str | None = Field(None, description='Extra system instructions.')
    max_turns: int | None = Field(None, description='Max steps per driver run.')
    session_id: str | None = Field(None, description='Session id. Auto when omitted.')
    project_dir_root_path: str | None = Field(None, description='Project root path.')
    agent_id: str | None = Field(None, description='Agent id. Auto when omitted.')
    agent_name: str | None = Field(None, description='Display name (from preset).')
    enabled_tools: list[str] | None = Field(None, description='Tool whitelist.')
    disabled_tools: list[str] | None = Field(None, description='Tool blacklist.')
    condense_config: dict[str, Any] | None = Field(None, description='Condenser config.')


class FlowAgentOutput(BaseModel):
    pass


class FlowAgent(Routine):
    """turn/step 循环 agent: inbox 双队列 + 轮次事件日志 + waterfall 扩展点."""

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': FlowAgentInput.model_json_schema(),
        'output_schema': FlowAgentOutput.model_json_schema(),
        'description': (
            'Turn/step loop coding agent (dsh ReactLoopAgent style). '
            'Non-interrupting inbox, turn/step event log, waterfall '
            'extension points, Shell-orchestrated tool execution.'
        ),
    }

    _BRIDGE_NAME = 'flow_manager'

    def __init__(self) -> None:
        super().__init__()
        self._agent_id: str = 'main'
        self._project_root: Optional[str] = None
        self._model: Optional[str] = None
        self._plan_mode: bool = False
        self._max_turns: Optional[int] = None
        self._instructions: str = ''
        self._llm: Optional[Llm] = None
        self._events: Optional[EventLog] = None
        self._enabled_tools: Optional[set[str]] = None
        self._disabled_tools: Optional[set[str]] = None
        self._condense_config: dict[str, Any] = {}
        self._tool_map: dict[str, Any] = {}
        self._schemas: list[dict] = []

        self._workspace: Optional[Path] = None
        self._last_usage: dict[str, Any] = {}
        self._catalog_digest: str = ''

        # 循环运行时
        self.inbox = Inbox()
        self.hooks = HookRegistry()
        self.scheduler = ToolScheduler(self)
        self._register_default_hooks()
        self._running: Optional[Running] = None
        self._maintenance_task: Optional[asyncio.Task] = None
        self._driver_task: Optional[asyncio.Task] = None
        self._epoch: int = 0
        self._stop = asyncio.Event()
        self._event_lock = asyncio.Lock()

    # ── 生命周期 ──

    async def run(self, kwargs: Dict[str, Any]) -> None:
        params = FlowAgentInput.model_validate(kwargs)
        self._init(params)
        await self._subscribe()
        if self._llm is not None:
            await self.emit_usage(
                usage=None, max_context=0, epoch=0, message_id='',
                model_key=self._llm.key,
                model_name=self._llm.model_id,
                reasoning_effort=self._llm.reasoning_effort,
            )
        await self.emit_stats()
        asyncio.create_task(self._register_with_bridge())
        try:
            await self._stop.wait()
        finally:
            await self._cancel(keep_inbox=False)
            if self._events is not None:
                self._events.close()

    async def stop(self) -> None:
        self._stop.set()

    # ── 初始化 ──

    def _init(self, params: FlowAgentInput) -> None:
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

        self._workspace = workspace_for(
            self._agent_id, params.project_dir_root_path)
        self._workspace.mkdir(parents=True, exist_ok=True)

        self._instructions = _build_instructions(
            params, agent_id=self._agent_id)
        self._llm = Llm(model=params.model)

        # EventLog: 一个 session 一个 JSONL (resume = 重读事件流)
        sid = params.session_id or self._agent_id
        self._session_id = sid
        self._events = EventLog(self._workspace / 'sessions' / f'{sid}.jsonl')
        if self._events.length == 0:
            self._events.append(
                'agent/start', model=params.model or '',
                plan_mode=params.plan_mode, agent_id=self._agent_id)
        # resume 后增量链保守作废: 首次请求全量重发
        self._events.invalidate_rid()
        # resume 目录 digest: 从事件流恢复 (下步仅真实变化才追加)
        for ev in reversed(self._events.events()):
            if ev.get('type') == 'skill/catalog':
                self._catalog_digest = str(ev.get('digest') or '')
                break
        # resume 恢复上一轮 usage (持久化在 assistant/message 事件), 供前端圆环回填
        for ev in reversed(self._events.events()):
            if ev.get('type') == 'assistant/message':
                u = ev.get('usage')
                if isinstance(u, dict) and u:
                    self._last_usage = dict(u)
                break

    def build_tools(self) -> list[dict]:
        """构建 tools schema + tool_map (driver 首次唤醒时调用一次).

        工具生态约定: meta['tool']=True 的 routine 是工具, plan 模式
        只放行 meta['readonly']=True. schema 从 meta['input_schema']
        (JSON Schema dict) 提取, x-hidden 字段对模型隐藏.
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

        schemas: list[dict] = []
        tool_map: dict[str, Any] = {}
        for name in tool_names:
            cls = all_by_name.get(name)
            if cls is None:
                continue
            if self._plan_mode and not getattr(cls, 'meta', {}).get('readonly'):
                continue
            tool_map[name] = cls
            schemas.append(_tool_schema(cls))
        self._tool_map = tool_map
        return schemas

    # ── 默认链环 (压缩等内置行为以 hook 形态挂链, 可被第三方组合/抢先) ──

    def _register_default_hooks(self) -> None:
        """pre_step 首环 = 压力压缩; request_error 首环 = 溢出恢复.

        对齐 dsh compaction-basic 的挂接方式: 循环零感知压缩, 压缩
        监听循环事件. 第三方 on_waterfall 默认排在这些环之后,
        prepend=True 可抢先 (如免模型的工具结果裁剪).
        """

        async def condense_hook(payload: dict, next_):
            try:
                # 两段式减压 (dsh pruner → compaction): 免模型裁剪先跑,
                # 剪完仍超才 LLM 摘要 ---- 便宜的先来
                over = await self._maybe_prune()
                if over:
                    await self.maybe_condense()
            except Exception as exc:
                # dsh 语义: 压缩失败只 warn, 不打断轮次
                _log.warning('condense failed: %r; continuing the turn', exc)
            return await next_()

        async def overflow_hook(payload: dict, next_):
            exc = payload.get('exc')
            if not _is_context_overflow(exc):
                return await next_()
            try:
                result = await self.maybe_condense(force=True)
            except Exception as recover_exc:
                _log.warning('overflow compaction failed: %r', recover_exc)
                return await next_()
            if result:
                _log.info('context overflow recovered via compaction; retrying')
                return {'kind': 'retry'}
            return await next_()

        self.hooks.on_waterfall('pre_step', condense_hook)
        self.hooks.on_waterfall('request_error', overflow_hook)

    # ── skill 注入 (dsh tool-skill 两个 pre-step 监听器) ──
    # 目录与手势注入都发生在领取的用户消息落日志之后 ---- 事件顺序
    # user → catalog → invoke, 注入物离模型回答最近 (dsh 注册顺序
    # 决定的同款位置语义; EventLog 是追加式, 注入点必须由 loop 驱动).

    async def inject_context(self, messages: list) -> None:
        """每步注入点: 目录 digest 检查 + 用户 /name 手势加载.

        失败只 warn, 不打断步骤 (对齐 dsh 监听器容错).
        """
        try:
            await self._sync_skill_catalog()
        except Exception as exc:
            _log.warning('skill catalog sync failed: %r', exc)
        try:
            await self._inject_invoked_skills(messages)
        except Exception as exc:
            _log.warning('skill invocation scan failed: %r', exc)

    async def _sync_skill_catalog(self) -> None:
        """快照 flow_skills 目录; digest 变化 → append skill/catalog 事件.

        skill 工具不可见时不出目录 (dsh toolVisible 检查).
        digest 只覆盖条目 ---- 正文编辑不触发重发 (KV cache 友好).
        append-only 不作废增量链: 新目录是新增 input item.
        """
        if 'skill' not in (self._tool_map or {}):
            return
        snap = await skills_req(self, 'catalog',
                                {'cwd': self._project_root or ''})
        if not isinstance(snap, dict):
            return
        digest = str(snap.get('digest') or '')
        if digest and digest != self._catalog_digest:
            self.events.append('skill/catalog',
                               entries=snap.get('entries') or [],
                               digest=digest)
            self._catalog_digest = digest
            _log.info('skill catalog updated: %d entries',
                      len(snap.get('entries') or []))

    async def _inject_invoked_skills(self, messages: list) -> None:
        """用户手势通道: /<name> 记号 → 内联 <skill_content> 注入.

        只扫 user 来源消息 (外部文本不能伪造手势). 未知名 /
        禁用户调用的名字留在原文当普通文本 (skip 信号, 不注入
        也不报错). 这是 disable-model-invocation skill 的唯一入口.
        """
        user_texts = [m.text for m in messages
                      if getattr(m, 'source', None) == USER_SOURCE]
        names = invoked_skill_names(user_texts)
        for name in names:
            result = await skills_req(self, 'load',
                                      {'name': name, 'channel': 'user',
                                       'cwd': self._project_root or ''})
            if not isinstance(result, dict) or not result.get('ok'):
                continue    # 手势不是本边界认可的声明: 保持沉默
            content = render_skill_content(
                str(result['name']), str(result['resource_base']),
                str(result['content']))
            self.events.append('skill/invoke', name=name, content=content)
            _log.info('skill invoked by user gesture: %s', name)

    # ── 订阅 + 输入路由 (followup 语义: 排队不打断) ──

    async def _subscribe(self) -> None:
        ns = self.namespace(self._agent_id)
        await ns.subscribe('user_input', self._on_input)
        await ns.subscribe('interrupt', self._on_interrupt)

    async def _deliver(self, text: str, from_agent: str,
                       target: str = 'next-turn') -> None:
        """统一投递入口: wrap_from 后进 inbox, 唤醒.

        followup/steer 唤醒 (steer 在下一个步骤边界生效, 不打断在途
        请求); inject 不唤醒.
        """
        content = wrap_from(text, from_agent)
        async with self._event_lock:
            await self.inbox.send(
                InboxMessage(text=content, source=from_agent), target)
            if from_agent != USER_SOURCE:
                await self.emit_incoming_message(content, from_agent)
            self._wake()

    async def _on_input(self, source, data: dict) -> None:
        text = str((data or {}).get('text') or '')
        if not text:
            return
        from_agent = str((data or {}).get('from')
                         or 'user')
        await self._deliver(text, from_agent, 'next-turn')

    async def _on_interrupt(self, source, data: dict) -> None:
        await self._cancel(keep_inbox=False)

    # ── 驱动相位管理 ──

    @property
    def events(self) -> EventLog:
        assert self._events is not None, 'agent not initialized'
        return self._events

    def _wake(self) -> None:
        """driver 活着则 noop (轮次/步骤边界自然领取 inbox); idle 则起驱动.

        maintenance 相位独占期间 (对齐 dsh runMaintenance): 唤醒锁存
        ---- inbox 留存, 维护任务完成后由 _wake 重放.
        """
        if self._running is not None or self._maintenance_task is not None:
            return
        self._epoch += 1
        running = Running(turn=self.events.last_turn())
        self._running = running
        self._driver_task = asyncio.create_task(self._drive(running))

    async def run_maintenance(self, name: str, fn) -> Any:
        """空闲期独占执行维护任务 (对齐 dsh runMaintenance).

        - running 中: 等轮次收敛 (dsh 语义: maintenance 不打断在途轮次)
        - 独占: 期间的唤醒锁存在 inbox, 完成后 _wake 重放
        - 互斥: 同刻仅一个 maintenance (后到者直接报错)
        fn: () -> Awaitable[dict] (结果回给 request 调用方)
        """
        # 等待在途轮次收敛 (driver 收尾可能级联起新驱动, 循环等到 idle)
        while ((task := self._driver_task) is not None
               and not task.done()):
            await asyncio.wait({task})
        if self._maintenance_task is not None:
            raise RuntimeError(f'maintenance already running: {self._maintenance_task.get_name()}')
        self._maintenance_task = asyncio.current_task()
        self._maintenance_task.set_name(name)
        try:
            return await fn()
        finally:
            self._maintenance_task = None
            # 维护期间锁存的唤醒重放 (表面可能已变, 驱动重新派生)
            if self.inbox.has_pending:
                self._wake()

    @request('compact')
    async def on_compact_req(self, source, data) -> dict:
        """手动压缩: 空闲期独占 (dsh compactNow)."""
        try:
            result = await self.run_maintenance(
                'manual-compact', lambda: self.maybe_condense(force=True))
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'result': result}

    async def _drive(self, running: Running) -> None:
        try:
            if not self._schemas:
                self._schemas = self.build_tools()
            await FlowLoop(self).run(running)
        except Aborted:
            # abort 机制(loop.py Aborted 分支)已发 final 收尾, 无需补
            pass
        except asyncio.CancelledError:
            # task.cancel 直杀(打断/停止, 未走 abort 机制): 补发 final 收尾帧,
            # 前端收不到 is_final 光标挂着不走. running.message_id 是当轮 id
            # (loop._turn 开轮时挂上), 同 id 才能关对流的 streaming 状态.
            if running.message_id:
                try:
                    await asyncio.shield(self.emit_text(
                        '', epoch=self._epoch, message_id=running.message_id,
                        is_final=True))
                except Exception:
                    pass
        except Exception as exc:
            _log.error('flow driver crashed: %s', exc, exc_info=True)
            await self.emit_text(f'[执行出错: {exc}]', epoch=self._epoch,
                                 message_id=uuid4().hex, is_final=True)
        finally:
            self._running = None
            # abort 后到达的 pending (cancel keep_inbox / 打断期间的新输入):
            # 续驱动消化; 显式 cancel 已清 inbox, 自然不续
            if running.abort.aborted and self.inbox.has_pending:
                self._wake()

    async def _cancel(self, *, keep_inbox: bool = False) -> None:
        async with self._event_lock:
            if not keep_inbox:
                await self.inbox.clear()
            running = self._running
            if running is not None:
                running.abort.abort('cancel')
        if self._running is not None:
            # top-down 排水在跑的工具 (join 不挂死, 随后检查点抛 Aborted)
            try:
                await self.scheduler.interrupt()
            except Exception as exc:
                _log.warning('scheduler interrupt failed: %r', exc)
        task = self._driver_task
        if task is not None and not task.done():
            try:
                await asyncio.wait({task}, timeout=5.0)
            except Exception:
                pass
        task = self._maintenance_task
        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait({task}, timeout=5.0)
            except Exception:
                pass

    # ── inbox 委托 (loop 经由 agent 访问, 保持单入口) ──

    @property
    def inbox_pending(self) -> bool:
        return self.inbox.has_pending

    @property
    def inbox_next_step_pending(self) -> bool:
        return self.inbox.next_step_pending

    async def inbox_claim(self, target: str) -> list[InboxMessage]:
        return await self.inbox.claim(target)

    # ── emit (前端事件, 对齐 ReactorAgent 契约) ──

    async def emit_text(self, text: str, *, epoch: int, message_id: str,
                        is_final: bool = False, is_thinking: bool = False) -> None:
        if not text and not is_final:
            return
        data = {
            'text': text, 'is_final': is_final, 'is_thinking': is_thinking,
            'epoch': epoch, 'message_id': message_id, 'agent_id': self._agent_id,
        }
        await self.publish('assistant_output', data)
        await self.publish('assistant_output', data, namespace=self._agent_id)

    async def emit_feedback(self, *, content: str, results: list,
                            epoch: int, message_id: str) -> None:
        data = {
            'content': content, 'results': results,
            'epoch': epoch, 'message_id': message_id, 'agent_id': self._agent_id,
        }
        await self.publish('feedback', data)
        await self.publish('feedback', data, namespace=self._agent_id)

    async def emit_usage(self, *, usage: dict[str, Any] | None, max_context: int,
                         epoch: int, message_id: str,
                         model_key: str = '', model_name: str = '',
                         reasoning_effort: str | None = None) -> None:
        if usage:
            self._last_usage = dict(usage)
        data = {
            **self._usage_data(usage, max_context),
            'epoch': epoch, 'message_id': message_id,
            'agent_id': self._agent_id, 'model_key': model_key,
            'model_name': model_name, 'reasoning_effort': reasoning_effort,
        }
        await self.publish('usage', data)
        await self.publish('usage', data, namespace=self._agent_id)

    def _usage_data(self, usage: dict[str, Any] | None,
                    max_context: int) -> dict[str, Any]:
        """usage 载荷 (token 数 + 按 condense 触发比率缩放的 percent).

        emit_usage 实时推送与 get_history 回填共用, 保证两路口径一致.
        """
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0
        if usage:
            input_tokens = int(usage.get('input_tokens') or usage.get('prompt_tokens') or 0)
            output_tokens = int(usage.get('output_tokens') or usage.get('completion_tokens') or 0)
            total_tokens = int(usage.get('total_tokens') or (input_tokens + output_tokens))
        trigger_tokens = max_context * self.trigger_ratio if max_context > 0 else 0
        percent = round(input_tokens / trigger_tokens * 100, 1) if trigger_tokens > 0 else 0.0
        return {
            'input_tokens': input_tokens, 'output_tokens': output_tokens,
            'total_tokens': total_tokens, 'max_context': max_context,
            'percent': percent,
        }

    async def emit_incoming_message(self, text: str, from_agent: str) -> None:
        data = {'text': text, 'from': from_agent, 'agent_id': self._agent_id}
        await self.publish('incoming_message', data)
        await self.publish('incoming_message', data, namespace=self._agent_id)

    async def emit_turn_end(self, reason: dict, *, turn: int, message_id: str) -> None:
        """轮次边界事件 (前端可选消费; EventLog 亦留痕)."""
        data = {
            'turn': turn, 'reason': reason.get('kind'),
            'detail': reason, 'message_id': message_id, 'agent_id': self._agent_id,
        }
        await self.publish('turn_event', data)
        await self.publish('turn_event', data, namespace=self._agent_id)

    async def emit_stats(self) -> None:
        """统计快照 (dsh session/projection): 事件流全量派生, live 与
        resume 同路."""
        if self._events is None:
            return
        data = {'agent_id': self._agent_id,
                **derive_stats(self._events.events())}
        await self.publish('stats', data)
        await self.publish('stats', data, namespace=self._agent_id)

    def make_started_handler(self, name: str, args: dict, call_id: str,
                             rid: str | None, *, epoch: int, message_id: str):
        async def on_started(_handle=None) -> None:
            await self.emit_feedback(
                content='',
                results=[{
                    'name': name, 'input': args, 'result': None,
                    'rid': rid, 'call_id': call_id, 'status': 'running',
                }],
                epoch=epoch, message_id=message_id,
            )
        return on_started

    # ── request handlers (对齐 ReactorAgent 前端契约) ──

    @request('agent_state')
    async def on_agent_state(self, source, data) -> dict:
        return {
            'agent_id': self._agent_id,
            'session_id': self._session_id,
            'project_root': self._project_root,
            'plan_mode': self._plan_mode,
            'model': self._model,
            'status': 'running' if self._running is not None else 'idle',
        }

    @request('run')
    async def on_run(self, source, data) -> dict:
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
            try:
                self._llm.set_reasoning_effort(effort)
            except ValueError as exc:
                return {'ok': False, 'error': str(exc)}
        self.events.append('effort/set', effort=effort)
        return {'ok': True, 'effort': effort}

    @request('chat_message')
    async def on_chat_message(self, source, data) -> dict:
        """三档路由 (对齐 dsh inbox):
        high (>=PRIORITY_HIGH) → followup: next-turn FIFO, 当前轮后处理
        mid  (0 < p < HIGH)    → steer: next-step, 下一步边界生效 (唤醒)
        low  (<=PRIORITY_LOW)  → inject: next-step, 躺队列等唤醒
        """
        from .._core.messaging import split_content
        try:
            message, images = split_content((data or {}).get('content') or [])
        except ValueError as exc:
            return {'ok': False, 'error': f'invalid payload: {exc}'}
        if images:
            _log.warning('flow agent is text-only, ignoring %d image(s)', len(images))
        message = message.strip()
        if not message:
            return {'ok': False, 'error': 'message is required'}
        from_agent = (data or {}).get('from')
        raw_priority = (data or {}).get('priority')
        priority = int(raw_priority) if raw_priority is not None else PRIORITY_HIGH
        if priority >= PRIORITY_HIGH:
            task = asyncio.create_task(self._deliver(message, from_agent or 'user',
                                                     'next-turn'))
        elif priority > PRIORITY_LOW:
            task = asyncio.create_task(self._deliver(message, from_agent or 'user',
                                                     'next-step'))
        else:
            task = asyncio.create_task(self._inject(message, from_agent))
        task.add_done_callback(self._log_deliver_error)
        return {'ok': True, 'epoch': self._epoch}

    async def _inject(self, message: str, from_agent) -> None:
        """低优先级注入: 进 next-step 不唤醒 (对齐 dsh inject 语义)."""
        from_agent = from_agent or USER_SOURCE
        content = wrap_from(message, from_agent)
        async with self._event_lock:
            await self.inbox.send(
                InboxMessage(text=content, source=from_agent), 'next-step')
            if from_agent != USER_SOURCE:
                await self.emit_incoming_message(content, from_agent)

    def _log_deliver_error(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.error('deliver message crashed: %s', exc, exc_info=exc)

    @request('get_history')
    async def on_get_history(self, source, data) -> dict:
        """返回本 agent 的 session 消息历史 (从事件流实时派生).

        usage: 上一轮的 token 用量 (resume 从事件流恢复), 前端刷新后
        agentUsage 清空, 这里回填让圆环立即恢复 (无轮次时 None).
        """
        usage = None
        if self._last_usage and self._llm:
            usage = {
                **self._usage_data(self._last_usage, self._llm.max_context),
                'agent_id': self._agent_id,
                'model_key': self._llm.key,
                'model_name': self._llm.model_id,
                'reasoning_effort': self._llm.reasoning_effort,
            }
        return {
            'ok': True,
            'session_id': self._session_id,
            'messages': self.events.derive_history(),
            'stats': derive_stats(self.events.events()),
            'usage': usage,
        }

    @request('interrupt')
    async def on_interrupt_req(self, source, data) -> dict:
        asyncio.create_task(self._on_interrupt(source, data or {}))
        return {'ok': True}

    # ── 辅助 ──

    def _mark_response(self, response_id: str) -> None:
        """一次成功请求落定: 增量链 (prev_rid) 恢复可用."""
        self.events.mark_response(response_id)

    async def _maybe_prune(self, *, force: bool = False) -> bool:
        """调 flow_pruner 免模型裁剪旧工具结果 (dsh pruner 挂 pre-step).

        返回剪完后是否仍超阈值 (truthy → 调用方接着走 LLM 摘要).
        """
        if self._events is None or not self._llm:
            return False
        if self._plan_mode:
            return False
        try:
            result = await self.call('flow_pruner', {
                'items': self._events.items(),
                'max_context': self._llm.max_context,
                'force': force,
                'condense_config': self._condense_config,
            })
        except Exception as exc:
            _log.warning('flow_pruner call failed: %r (skipping)', exc)
            return True   # pruner 不可用不阻塞: 让摘要链路接管
        if not isinstance(result, dict) or not result.get('pruned'):
            return bool(result.get('over_threshold_after')) \
                if isinstance(result, dict) else True
        self._events.apply_prune(result.get('edits') or {})
        return bool(result.get('over_threshold_after'))

    async def maybe_condense(self, *, force: bool = False) -> dict | None:
        """调 flow_compactor routine 压缩, 落定为 replacement 事件.

        由 pre_step 链默认首环调用 (步间压力) / request_error 链溢出
        恢复环调用. 返回压缩结果 (truthy = 已压缩).
        """
        if self._events is None or not self._llm:
            return None
        if self._plan_mode:
            return None
        try:
            result = await self.call('flow_compactor', {
                'items': self._events.items(),
                'max_context': self._llm.max_context,
                'model_key': self._llm.key,
                'force': force,
                'fork_response_id': (self._events.last_response_id()
                                     if self._events.rid_available() else None),
                'condense_config': self._condense_config,
            })
        except Exception as exc:
            _log.warning('flow_compactor call failed: %r (skipping)', exc)
            return None
        if not isinstance(result, dict) or not result.get('condensed'):
            return None
        # 表面替换落定: 事件流是唯一真相, 派生自动生效
        self._events.apply_replacement(
            result.get('summary', ''),
            int(result.get('cut_index') or 0),
            tokens_before=int(result.get('tokens_before') or 0),
            tokens_after=int(result.get('tokens_after') or 0),
        )
        return result

    @property
    def trigger_ratio(self) -> float:
        try:
            r = float(self._condense_config.get('trigger_ratio', 0.5))
        except (TypeError, ValueError):
            return 0.5
        return r if r == r and abs(r) != float('inf') else 0.5

    # ── bridge 注册 (向 WS 前门登记自己, 轮询直到成功) ──

    async def _register_with_bridge(self) -> None:
        payload = {
            'agent_id': self._agent_id,
            'namespace': self._agent_id,
            'name': f'Flow-{self._agent_id[:8]}',
            'routine_id': self.id,
        }
        try:
            while not self._stop.is_set():
                bridge_id = None
                try:
                    routines = await self.ctx.get_running_routines()
                    for r in routines:
                        if str(r.get('name') or '') == self._BRIDGE_NAME:
                            bridge_id = str(r.get('id') or '') or None
                            break
                except Exception as exc:
                    _log.warning('get_running failed (%r), retry', exc)
                if bridge_id is not None:
                    try:
                        await self.ctx.req(
                            bridge_id, 'register_agent', payload, timeout=2.0)
                        _log.info('registered with bridge (bridge_id=%s)',
                                  bridge_id)
                        await self._emit_session_history()
                        return
                    except (ReqTimeout, ReqError) as exc:
                        _log.warning('register req failed (%r), retry', exc)
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            return

    async def _emit_session_history(self) -> None:
        is_new = self._events is None or self._events.length == 0
        # resume/启动回填: 上一轮 usage, 圆环立即显示
        usage = None
        if not is_new and self._last_usage and self._llm:
            usage = {
                **self._usage_data(self._last_usage, self._llm.max_context),
                'agent_id': self._agent_id,
                'model_key': self._llm.key,
                'model_name': self._llm.model_id,
                'reasoning_effort': self._llm.reasoning_effort,
            }
        await self.publish('session_changed', {
            'agent_id': self._agent_id,
            'session_id': self._session_id,
            'is_new': is_new,
            'messages': [] if is_new else self._events.derive_history(),
            'stats': {} if is_new else derive_stats(self._events.events()),
            'usage': usage,
        })


# ── 自有 prompt / schema (模块级纯函数) ──

def _build_instructions(params: FlowAgentInput, *, agent_id: str) -> str:
    """flow 系统提示: 委托 prompt 模块 (文本与 prime 对齐, 零项目依赖)."""
    return build_flow_system_prompt(
        project_root=params.project_dir_root_path,
        extra=params.extra_instructions,
        agent_id=agent_id,
        plan_mode=params.plan_mode,
    )


def _tool_schema(cls: Any) -> dict[str, Any]:
    """routine 类 → Responses API function-calling schema."""
    meta = getattr(cls, 'meta', {}) or {}
    parameters = dict(meta.get('input_schema') or {})
    if not parameters:
        parameters = {'type': 'object', 'properties': {}, 'required': []}
    parameters.pop('title', None)
    parameters.pop('description', None)
    props = parameters.get('properties')
    if isinstance(props, dict):
        hidden = [n for n, spec in props.items()
                  if isinstance(spec, dict) and spec.get('x-hidden')]
        for n in hidden:
            props.pop(n, None)
        required = parameters.get('required')
        if isinstance(required, list):
            parameters['required'] = [r for r in required if r not in hidden]
    return {
        'type': 'function',
        'name': cls.name,
        'description': meta.get('description') or cls.name,
        'parameters': parameters,
    }
