"""LLMClient ---- 封装 LLM 流式调用.

支持 openai-responses (默认) 与 openai-completions 双协议, 协议选择归
agent 层 (LLMClient(protocol=...)), 配置层只提供事实 (models.yaml 的
apis 协议矩阵). 与 xml_agent/llm.py 是同构副本 (包内自治, 双端同步维护).
模型配置外置到项目根 models.yaml (读写引擎 models_store), 支持
provider/model 二级路由.
Provider 差异 (reasoning 参数格式等) 由 LlmProvider 处理, 见 provider.py.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from time import perf_counter, time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from httpx import AsyncClient, ConnectError, ReadError, RemoteProtocolError, StreamClosed
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from routine.logger import setup_logger
from .provider import LlmProvider, make_provider

_log = setup_logger('prime.llm')

# ──────────────────────────────────────────────────────────────────────────────
# 配置: 项目根 models.yaml ---- 唯一读写引擎在 models_store (models/* routine
# 系列共用). 本模块只消费: 展平表 + default; 配置写入 (models/save|delete)
# 后 epoch bump, 下方 client/provider 缓存据此失效重建.
# ──────────────────────────────────────────────────────────────────────────────

from .models_store import default_key, epoch as _cfg_epoch, flatten

# default 是 import 时刻快照 (prime/xml_agent manager 的 _DEFAULT_MODEL 引用),
# 改 default 需重启; provider/model 增删与 key/url 修改热生效.
_DEFAULT_MODEL: str = default_key()

# stream(reasoning_effort=...) 的 "未指定" 哨兵 (None 是合法值 = 显式关)
_UNSET = object()

# 懒构造的 client 缓存: 按 provider 缓存 (同 provider 下多 model 共享 client)
_clients: dict[str, AsyncOpenAI] = {}

# 懒构造的 provider 实例缓存: 按 model key 缓存
_providers: dict[str, LlmProvider] = {}

# 建缓存时记录的配置版本 (models_store.epoch), 不一致即全量重建
_cached_epoch: int = _cfg_epoch()


def list_models() -> dict[str, Any]:
    """展平后的模型配置 (含 api_key ---- 内部函数; 对外脱敏走 models/list
    routine 出口)."""
    return {'default': _DEFAULT_MODEL, 'models': flatten()}


class LLMConnectionError(RuntimeError):
    """Raised when the upstream LLM endpoint cannot be reached."""


def _format_exception_chain(exc: BaseException) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f'{type(current).__name__}: {current}')
        current = current.__cause__ or current.__context__
    return ' <- '.join(parts)


def _is_local_base_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or '').lower()
    return host in {'127.0.0.1', 'localhost', '::1'}


def _get_provider(name: str | None, protocol: str | None = None) -> LlmProvider:
    """返回 model key 对应的 LlmProvider 实例 (懒构造 + 缓存).

    同 model 不同协议是不同实例, 缓存键含协议.
    配置版本 (models_store.epoch) 变化即清空两级缓存全量重建 ----
    models/save|delete 后的旧 key/url client 不复用.
    """
    global _cached_epoch
    if _cfg_epoch() != _cached_epoch:
        _providers.clear()
        _clients.clear()
        _cached_epoch = _cfg_epoch()
    key = name or _DEFAULT_MODEL
    cache_key = f'{key}|{protocol or ""}'
    if cache_key not in _providers:
        models = flatten()
        cfg = models.get(key)
        if cfg is None:
            raise ValueError(f'unknown model {key!r}, available: {sorted(models)}')
        _providers[cache_key] = make_provider(key, cfg, protocol)
    return _providers[cache_key]


def _get_client(provider: LlmProvider) -> AsyncOpenAI:
    """返回 provider 对应的 AsyncOpenAI client (按 provider name 缓存, 共享 base_url/api_key)."""
    # provider name = key 中 '/' 前半, 同 provider 下多 model 共享一个 client
    client_key = provider.name.split('/', 1)[0] if '/' in provider.name else provider.name
    if client_key not in _clients:
        client_kwargs: dict[str, Any] = {
            'api_key': provider.api_key,
            'base_url': provider.base_url,
        }
        if _is_local_base_url(provider.base_url):
            client_kwargs['http_client'] = AsyncClient(trust_env=False)
        _clients[client_key] = AsyncOpenAI(**client_kwargs)
    return _clients[client_key]


# ──────────────────────────────────────────────────────────────────────────────
# 事件类型
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class TextDelta:
    text: str


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class FunctionCallItem:
    name: str
    arguments: str   # JSON 字符串
    call_id: str


@dataclass
class Completed:
    response_id: str
    text: str
    usage: dict[str, Any] | None = None
    function_calls: list[FunctionCallItem] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# 客户端
# ──────────────────────────────────────────────────────────────────────────────

class LLMClient:
    """封装 LLM 流式调用 (openai-responses / openai-completions).

    model 格式 '<provider>/<model_name>' (如 'seed/glm-5.2'),
    None 时使用 models.yaml 中的 default.
    protocol: 协议选择 (agent 层策略), None = 从配置协议矩阵推导
    必须已在 models.yaml 该 provider 的 apis 矩阵里声明 (事实先行).
    Provider 差异 (reasoning 参数格式等) 由 self._provider 处理.
    """

    def __init__(self, model: str | None = None, protocol: str | None = None) -> None:
        self._provider = _get_provider(model, protocol)
        self.max_context: int = self._provider.max_context
        # reasoning effort: 从 provider 默认值读, 可运行时改 (前端调 set_effort).
        # None 表示该模型默认不开启 reasoning.
        self._reasoning_effort: str | None = getattr(
            self._provider, 'default_effort', None
        )

    @property
    def model_key(self) -> str:
        """当前模型 key ('seed/glm-5.2' 等), 供 condenser 等外部模块使用."""
        return self._provider.name

    @property
    def protocol(self) -> str:
        """本实例绑定的协议 ('openai-responses' / 'openai-completions')."""
        return self._provider.api

    @property
    def supports_previous_response_id(self) -> bool:
        """本协议下的增量链 (previous_response_id) 是否真实生效.

        按协议查矩阵 (见 LlmProvider.previous_response_id): completions
        无增量链概念恒 False; responses 协议下 deepseek 等实测静默忽略,
        须在 apis 矩阵声明 false, agent 侧据此全量发历史.
        """
        return self._provider.previous_response_id

    @property
    def model_name(self) -> str:
        """实际模型 id (如 'glm-5.2' / 'qwen3.7-max'), 供前端显示."""
        return self._provider.model_id

    @property
    def reasoning_effort(self) -> str | None:
        """当前 reasoning effort ('high'/'medium'/'low'/None). None = 不带 reasoning."""
        return self._reasoning_effort

    def set_reasoning_effort(self, effort: str | None) -> None:
        """运行时改 reasoning effort. None 关掉 reasoning.

        - 档位只认 high/medium/low (其它值直接报错, 不静默透传).
        - off 能力是模型级的: provider 未声明 can_disable (如 glm-5.3 强制
          thinking) 时直接报错, 不静默假装关掉.
        """
        if effort is not None and effort not in ('high', 'medium', 'low'):
            raise ValueError(
                f'invalid reasoning effort {effort!r}: '
                f'expected high | medium | low | none')
        if effort is None and not self._provider.can_disable:
            raise ValueError(
                f'model {self._provider.name}: thinking cannot be disabled')
        self._reasoning_effort = effort

    async def stream(
        self,
        input: Any,
        *,
        instructions: str | None = None,
        tools: list | None = None,
        tool_choice: Any = None,
        previous_response_id: str | None = None,
        disable_reasoning: bool = False,
        reasoning_effort: Any = _UNSET,
        **extra_kw: Any,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | Completed]:
        if self._provider.api == 'openai-completions':
            async for ev in self._stream_completions(
                input, instructions=instructions, tools=tools,
                tool_choice=tool_choice, **extra_kw,
            ):
                yield ev
            return
        provider = self._provider
        client = _get_client(provider)
        model_id = provider.model_id
        base_url = provider.base_url

        # per-call effort 覆盖 (sentinel 区分 "未指定" 与 None=off);
        # 不落实例态 ---- 单次请求生效, dsh "适配器默认值不粘步" 语义
        effort = self._reasoning_effort if reasoning_effort is _UNSET \
            else reasoning_effort

        kw: dict[str, Any] = {
            'model': model_id,
            'input': input,
            'stream': True,
            # extra 里不含 reasoning (provider 子类已移除), 保留 store / extra_body 等
            **provider.extra,
            **extra_kw,
        }
        # reasoning 参数格式由 provider 决定
        provider.apply_reasoning(kw, effort, disable_reasoning)
        if instructions:
            kw['instructions'] = instructions
        # 合并运行时 function tools + 模型原生 tools (如 web_search)
        request_tools = [*(tools or []), *provider.builtin_tools]
        if request_tools:
            kw['tools'] = request_tools
        if tool_choice is not None:
            kw['tool_choice'] = tool_choice
        if previous_response_id:
            kw['previous_response_id'] = previous_response_id

        started = time()
        ttfb_ms: int | None = None

        response = None
        try:
            try:
                response = await client.responses.create(**kw)
            except (ConnectError, APIConnectionError, APITimeoutError) as exc:
                elapsed_ms = int((time() - started) * 1000)
                detail = _format_exception_chain(exc)
                _log.warning(
                    'stream connect failed: model=%s(%s) base_url=%s total=%dms error=%s',
                    provider.name, model_id, base_url, elapsed_ms, detail,
                )
                raise LLMConnectionError(
                    f'{provider.name} @ {base_url}: {detail}'
                ) from exc
            except Exception as exc:
                _log.error(
                    'stream request failed: model=%s input_types=%s prev_rid=%s '
                    'instructions=%d tools=%d store=%s kw_keys=%s\nerror=%s\n'
                    'input=%s\n--- full request kw ---\n%s',
                    model_id,
                    [it.get('type') or it.get('role') for it in input]
                    if isinstance(input, list) else type(input).__name__,
                    (previous_response_id[:24] + '…') if previous_response_id else None,
                    len(instructions or ''),
                    len(request_tools),
                    kw.get('store'),
                    sorted(kw.keys()),
                    str(exc)[:500],
                    str(input)[:500] if isinstance(input, list) else str(input)[:500],
                    json.dumps(kw, ensure_ascii=False, default=str),
                )
                raise

            try:
                async for event in response:
                    ev_type: str = getattr(event, 'type', '') or ''

                    if ev_type == 'response.output_text.delta':
                        delta: str = getattr(event, 'delta', '') or ''
                        if delta:
                            if ttfb_ms is None:
                                ttfb_ms = int((time() - started) * 1000)
                            yield TextDelta(text=delta)

                    elif ev_type in provider.reasoning_event_types:
                        delta = getattr(event, 'delta', '') or ''
                        if delta:
                            yield ReasoningDelta(text=delta)

                    elif ev_type == 'response.completed':
                        resp = getattr(event, 'response', None)
                        if resp is None:
                            continue
                        elapsed_ms = int((time() - started) * 1000)
                        usage = _extract_usage(resp)
                        fc_items: list[FunctionCallItem] = [
                            FunctionCallItem(
                                name=getattr(item, 'name', ''),
                                arguments=getattr(item, 'arguments', '{}'),
                                call_id=getattr(item, 'call_id', ''),
                            )
                            for item in (getattr(resp, 'output', None) or [])
                            if getattr(item, 'type', None) == 'function_call'
                        ]
                        _log.info(
                            'stream done: model=%s(%s) ttfb=%s total=%dms fc=%d',
                            provider.name, model_id,
                            f'+{ttfb_ms}ms' if ttfb_ms is not None else '?',
                            elapsed_ms, len(fc_items),
                        )
                        yield Completed(
                            response_id=resp.id,
                            text=getattr(resp, 'output_text', '') or '',
                            usage=usage,
                            function_calls=fc_items,
                        )
            except (RemoteProtocolError, ReadError, StreamClosed) as exc:
                elapsed_ms = int((time() - started) * 1000)
                _log.warning(
                    'stream ended early: model=%s(%s) total=%dms error=%s',
                    provider.name, model_id, elapsed_ms, exc,
                )
        finally:
            if response is not None:
                try:
                    await response.close()
                except (RemoteProtocolError, ReadError, StreamClosed) as exc:
                    _log.debug('stream close ignored: %s', exc)


    async def _stream_completions(
        self,
        input: Any,
        *,
        instructions: str | None = None,
        tools: list | None = None,
        tool_choice: Any = None,
        **extra_kw: Any,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | Completed]:
        """openai-completions 流式分支 (chat.completions.create).

        - input 是 responses 形态消息数组, 转换成 completions messages
          (instructions -> system, input_text/input_image blocks -> parts).
        - thinking 内置 (如 ds completions 的 reasoning_content delta), 无
          reasoning.effort 参数 ---- 不发 reasoning 字段, disable_reasoning
          无法生效 (无参数可关).
        - 无服务端会话: 无 response_id (Completed.response_id 恒 ''), 增量链
          概念不存在 ---- agent 侧全量发历史.
        - 原生 function calls 不解析: 工具调用走 XML body (协议无关), 若
          未来需要再补 tool_calls delta 聚合.
        """
        if tools or tool_choice is not None:
            raise ValueError(
                f'model {self._provider.name}: native tools not supported '
                f'over openai-completions (tools go through XML body)')
        provider = self._provider
        client = _get_client(provider)
        model_id = provider.model_id
        base_url = provider.base_url

        kw: dict[str, Any] = {
            'model': model_id,
            'messages': _to_chat_messages(input, instructions),
            'stream': True,
            'stream_options': {'include_usage': True},
            **provider.extra,
            **extra_kw,
        }

        started = time()
        ttfb_ms: int | None = None
        text_acc: list[str] = []
        usage: dict[str, Any] | None = None
        finished = False

        response = None
        try:
            try:
                response = await client.chat.completions.create(**kw)
            except (ConnectError, APIConnectionError, APITimeoutError) as exc:
                elapsed_ms = int((time() - started) * 1000)
                detail = _format_exception_chain(exc)
                _log.warning(
                    'stream connect failed: model=%s(%s) base_url=%s total=%dms error=%s',
                    provider.name, model_id, base_url, elapsed_ms, detail,
                )
                raise LLMConnectionError(
                    f'{provider.name} @ {base_url}: {detail}'
                ) from exc
            except Exception as exc:
                _log.error(
                    'stream request failed: model=%s input_types=%s '
                    'kw_keys=%s\nerror=%s\n--- full request kw ---\n%s',
                    model_id,
                    [it.get('type') or it.get('role') for it in input]
                    if isinstance(input, list) else type(input).__name__,
                    sorted(kw.keys()),
                    str(exc)[:500],
                    json.dumps(kw, ensure_ascii=False, default=str),
                )
                raise

            try:
                async for chunk in response:
                    cu = getattr(chunk, 'usage', None)
                    if cu is not None:
                        usage = _extract_usage(chunk)
                    for choice in (getattr(chunk, 'choices', None) or []):
                        delta = getattr(choice, 'delta', None)
                        if delta is not None:
                            rdelta = getattr(delta, 'reasoning_content', None)
                            if rdelta:
                                yield ReasoningDelta(text=rdelta)
                            tdelta = getattr(delta, 'content', None)
                            if tdelta:
                                if ttfb_ms is None:
                                    ttfb_ms = int((time() - started) * 1000)
                                text_acc.append(tdelta)
                                yield TextDelta(text=tdelta)
                        if getattr(choice, 'finish_reason', None):
                            finished = True
                    if finished and usage is not None:
                        break
            except (RemoteProtocolError, ReadError, StreamClosed) as exc:
                elapsed_ms = int((time() - started) * 1000)
                _log.warning(
                    'stream ended early: model=%s(%s) total=%dms error=%s',
                    provider.name, model_id, elapsed_ms, exc,
                )

            elapsed_ms = int((time() - started) * 1000)
            _log.info(
                'stream done: model=%s(%s) api=openai-completions ttfb=%s total=%dms',
                provider.name, model_id,
                f'+{ttfb_ms}ms' if ttfb_ms is not None else '?',
                elapsed_ms,
            )
            yield Completed(
                response_id='',
                text=''.join(text_acc),
                usage=usage,
                function_calls=[],
            )
        finally:
            if response is not None:
                try:
                    await response.close()
                except (RemoteProtocolError, ReadError, StreamClosed) as exc:
                    _log.debug('stream close ignored: %s', exc)


def _to_chat_messages(input: Any, instructions: str | None) -> list[dict]:
    """responses 形态 input -> completions messages.

    - instructions -> 首条 system 消息
    - content 为 str 的消息原样; blocks 数组逐块映射:
      input_text/output_text -> text part, input_image -> image_url part
    - 未知 block 类型立即报错 (协议违规当场暴露, 不静默丢)
    """
    messages: list[dict] = []
    if instructions:
        messages.append({'role': 'system', 'content': instructions})
    for item in input:
        role = item.get('role')
        content = item.get('content')
        if isinstance(content, list):
            parts: list[dict] = []
            for block in content:
                btype = block.get('type')
                if btype in ('input_text', 'output_text'):
                    parts.append({'type': 'text', 'text': block.get('text', '')})
                elif btype == 'input_image':
                    parts.append({'type': 'image_url',
                                  'image_url': {'url': block.get('image_url')}})
                else:
                    raise ValueError(f'unsupported content block type: {btype!r}')
            messages.append({'role': role, 'content': parts})
        else:
            messages.append({'role': role, 'content': content})
    return messages


def _extract_usage(resp: Any) -> dict[str, Any] | None:
    usage = getattr(resp, 'usage', None)
    if usage is None:
        return None
    try:
        return usage.model_dump()
    except Exception:
        return None
