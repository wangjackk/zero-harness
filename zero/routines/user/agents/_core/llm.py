"""LLMClient ---- 封装 Responses API 流式调用.

模型配置外置到项目根 models.yaml, 支持 provider/model 二级路由.
Provider 差异 (reasoning 参数格式等) 由 LlmProvider 处理, 见 provider.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import yaml
from httpx import AsyncClient, ConnectError, ReadError, RemoteProtocolError, StreamClosed
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from routine.logger import setup_logger
from .provider import LlmProvider, make_provider

_log = setup_logger('prime.llm')

# ──────────────────────────────────────────────────────────────────────────────
# 配置加载: 项目根 models.yaml
#   models:
#     <provider>:
#       api_key: ...
#       base_url: ...
#       <model_name>:
#         model: <real_model_id>
#         max_context: ...
#         extra: {...}
#         tools: [...]          # 原生 API tools 字段项, 如 {type: web_search}
#   default: <provider>/<model_name>
# ──────────────────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parents[4] / 'models.yaml'

# provider 级别的标量字段 (非 model 入口), 展平时跳过.
_PROVIDER_SCALAR_KEYS: frozenset[str] = frozenset({'api_key', 'base_url'})


def _load_config() -> dict[str, Any]:
    """读 models.yaml, 失败抛错."""
    if not _CONFIG_PATH.is_file():
        raise FileNotFoundError(f'models.yaml not found: {_CONFIG_PATH}')
    with _CONFIG_PATH.open('r', encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    if 'models' not in data:
        raise ValueError(f'models.yaml missing "models" section: {_CONFIG_PATH}')
    return data


_CONFIG: dict[str, Any] = _load_config()
_PROVIDERS: dict[str, dict] = _CONFIG.get('models', {})
_DEFAULT_MODEL: str = _CONFIG.get('default', '')

# provider/model -> 展平配置 {api_key, base_url, model, max_context, extra, tools}
_MODELS: dict[str, dict] = {}

for _prov_name, _prov_cfg in _PROVIDERS.items():
    _api_key = _prov_cfg.get('api_key', '')
    _base_url = _prov_cfg.get('base_url', '')
    for _m_name, _m_cfg in _prov_cfg.items():
        if _m_name in _PROVIDER_SCALAR_KEYS:
            continue
        if not isinstance(_m_cfg, dict):
            continue
        _full_key = f'{_prov_name}/{_m_name}'
        _MODELS[_full_key] = {
            'api_key': _api_key,
            'base_url': _base_url,
            'model': _m_cfg.get('model', _m_name),
            'max_context': int(_m_cfg.get('max_context', 0)),
            'extra': dict(_m_cfg.get('extra') or {}),
            'tools': list(_m_cfg.get('tools') or []),
        }

if not _DEFAULT_MODEL and _MODELS:
    _DEFAULT_MODEL = next(iter(_MODELS))

# 懒构造的 client 缓存: 按 provider 缓存 (同 provider 下多 model 共享 client)
_clients: dict[str, AsyncOpenAI] = {}

# 懒构造的 provider 实例缓存: 按 model key 缓存
_providers: dict[str, LlmProvider] = {}


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


def _get_provider(name: str | None) -> LlmProvider:
    """返回 model key 对应的 LlmProvider 实例 (懒构造 + 缓存)."""
    key = name or _DEFAULT_MODEL
    if key not in _providers:
        cfg = _MODELS.get(key)
        if cfg is None:
            raise ValueError(f'unknown model {key!r}, available: {sorted(_MODELS)}')
        _providers[key] = make_provider(key, cfg)
    return _providers[key]


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
    """封装 Responses API,产出类型化事件流.

    model 格式 '<provider>/<model_name>' (如 'seed/glm-5.2'),
    None 时使用 models.yaml 中的 default.
    Provider 差异 (reasoning 参数格式等) 由 self._provider 处理.
    """

    def __init__(self, model: str | None = None) -> None:
        self._provider = _get_provider(model)
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
    def model_name(self) -> str:
        """实际模型 id (如 'glm-5.2' / 'qwen3.7-max'), 供前端显示."""
        return self._provider.model_id

    @property
    def reasoning_effort(self) -> str | None:
        """当前 reasoning effort ('high'/'medium'/'low'/None). None = 不带 reasoning."""
        return self._reasoning_effort

    def set_reasoning_effort(self, effort: str | None) -> None:
        """运行时改 reasoning effort. None 关掉 reasoning."""
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
        **extra_kw: Any,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | Completed]:
        provider = self._provider
        client = _get_client(provider)
        model_id = provider.model_id
        base_url = provider.base_url

        kw: dict[str, Any] = {
            'model': model_id,
            'input': input,
            'stream': True,
            # extra 里不含 reasoning (provider 子类已移除), 保留 store / extra_body 等
            **provider.extra,
            **extra_kw,
        }
        # reasoning 参数格式由 provider 决定
        provider.apply_reasoning(kw, self._reasoning_effort, disable_reasoning)
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
                    '--- full request kw ---\n%s',
                    model_id,
                    [it.get('type') or it.get('role') for it in input]
                    if isinstance(input, list) else type(input).__name__,
                    (previous_response_id[:24] + '…') if previous_response_id else None,
                    len(instructions or ''),
                    len(request_tools),
                    kw.get('store'),
                    sorted(kw.keys()),
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


def _extract_usage(resp: Any) -> dict[str, Any] | None:
    usage = getattr(resp, 'usage', None)
    if usage is None:
        return None
    try:
        return usage.model_dump()
    except Exception:
        return None
