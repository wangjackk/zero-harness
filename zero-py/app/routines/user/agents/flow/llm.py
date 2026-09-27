"""flow 自有 LLM client ---- OpenAI Responses API 流式调用.

零项目依赖: 配置读 zero/models.yaml (数据文件, 与生态共享同一份模型
配置), 代码全部归 flow. 无 provider 类层次 ---- 展平为一张表:

- model 级 reasoning 声明存在 → 支持 reasoning: {effort} 参数, 默认 effort
  从 reasoning.effort 读; effort 由 apply 阶段动态写入, 支持运行时改
- 事件解析: output_text.delta / reasoning(*_summary)_text.delta /
  response.completed (usage + function_call 输出项)

models.yaml 形状:
  models:
    <provider>:
      api_key: $ENV
      base_url: ...
      <model_name>: {model, max_context, reasoning: {...}, extra: {...}, tools: [...]}
  default: <provider>/<model_name>
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import yaml
from httpx import AsyncClient, ConnectError, ReadError, RemoteProtocolError, StreamClosed
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

from routine.logger import setup_logger

_log = setup_logger('flow.llm')

_CONFIG_PATH = Path(__file__).resolve().parents[4] / 'models.yaml'

# stream(reasoning_effort=...) 的 "未指定" 哨兵 (None 是合法值 = 显式关)
UNSET = object()

_PROVIDER_SCALARS = frozenset({'api_key', 'base_url'})

_REASONING_DELTAS = (
    'response.reasoning_summary_text.delta',
    'response.reasoning_text.delta',
)


class LlmConnError(RuntimeError):
    """上游 LLM 端点不可达."""


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


# ── 配置加载 + 展平 ──

def _expand_env(value: str) -> str:
    if not isinstance(value, str) or not value.startswith('$'):
        return value
    name = value[2:-1] if value.startswith('${') else value[1:]
    if not name.isidentifier():
        return value
    env = os.environ.get(name)
    if env is None:
        _log.warning('env %s not set (api_key empty)', name)
        return ''
    return env


def _load_models() -> tuple[dict[str, dict[str, Any]], str]:
    """models.yaml → (模型展平表, 默认 model key)."""
    if not _CONFIG_PATH.is_file():
        raise FileNotFoundError(f'models.yaml not found: {_CONFIG_PATH}')
    with _CONFIG_PATH.open('r', encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}
    providers = data.get('models') or {}
    if not providers:
        raise ValueError('models.yaml missing "models" section')

    table: dict[str, dict[str, Any]] = {}
    for prov, prov_cfg in providers.items():
        api_key = _expand_env(prov_cfg.get('api_key', ''))
        base_url = prov_cfg.get('base_url', '')
        for m_name, m_cfg in prov_cfg.items():
            if m_name in _PROVIDER_SCALARS or not isinstance(m_cfg, dict):
                continue
            extra = dict(m_cfg.get('extra') or {})
            # reasoning: model 级本地声明(不透传 API), 展平直读
            r = m_cfg.get('reasoning')
            default_effort = r.get('effort') if isinstance(r, dict) else None
            # off 映射: can_disable 声明; true = off 时发 effort=none
            can_disable = bool(r.get('can_disable')) if isinstance(r, dict) else False
            table[f'{prov}/{m_name}'] = {
                'api_key': api_key,
                'base_url': base_url,
                'model': m_cfg.get('model', m_name),
                'max_context': int(m_cfg.get('max_context', 0)),
                'extra': extra,
                'tools': list(m_cfg.get('tools') or []),
                'default_effort': default_effort,
                # reasoning 声明存在 = 支持 reasoning 参数(不支持不发,避免未知参数)
                'has_reasoning': isinstance(r, dict),
                'can_disable': can_disable,
            }
    return table, data.get('default', '') or next(iter(table), '')


_MODELS, DEFAULT_MODEL = _load_models()
_clients: dict[str, AsyncOpenAI] = {}


def _client_for(model_key: str) -> AsyncOpenAI:
    """按 provider 前缀共享 AsyncOpenAI client (懒构造)."""
    prov = model_key.split('/', 1)[0]
    if prov not in _clients:
        cfg = _MODELS[model_key]
        kwargs: dict[str, Any] = {
            'api_key': cfg['api_key'], 'base_url': cfg['base_url'],
        }
        host = (urlparse(cfg['base_url']).hostname or '').lower()
        if host in {'127.0.0.1', 'localhost', '::1'}:
            kwargs['http_client'] = AsyncClient(trust_env=False)
        _clients[prov] = AsyncOpenAI(**kwargs)
    return _clients[prov]


class Llm:
    """单模型实例: 持 effort 状态, 产类型化事件流."""

    def __init__(self, model: str | None = None) -> None:
        key = model or DEFAULT_MODEL
        cfg = _MODELS.get(key)
        if cfg is None:
            raise ValueError(
                f'unknown model {key!r}, available: {sorted(_MODELS)}')
        self.key = key
        self.model_id = cfg['model']
        self.max_context = cfg['max_context']
        self._effort: str | None = cfg['default_effort']
        self._has_reasoning: bool = cfg['has_reasoning']
        self._can_disable: bool = cfg['can_disable']
        self._extra = cfg['extra']
        self._native_tools = cfg['tools']

    @property
    def reasoning_effort(self) -> str | None:
        return self._effort

    def set_reasoning_effort(self, effort: str | None) -> None:
        """off 能力模型级: 支持 reasoning 但未声明 can_disable 时直接报错,
        不静默假装关掉 (无 reasoning 参数的模型 off 天然有效)."""
        if effort is None and self._has_reasoning and not self._can_disable:
            raise ValueError(
                f'model {self.key}: thinking cannot be disabled')
        self._effort = effort

    async def stream(
        self,
        input: Any,
        *,
        instructions: str | None = None,
        tools: list | None = None,
        previous_response_id: str | None = None,
        disable_reasoning: bool = False,
        reasoning_effort: Any = UNSET,
        **extra_kw: Any,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | Completed]:
        """流式调用. reasoning_effort 单次覆盖, 不落实例态."""
        effort = self._effort if reasoning_effort is UNSET else reasoning_effort

        kw: dict[str, Any] = {
            'model': self.model_id,
            'input': input,
            'stream': True,
            **self._extra,
            **extra_kw,
        }
        if self._has_reasoning:
            # off 发 effort=none (Responses API 实测: qwen/deepseek 均认).
            # 模型不支持关(can_disable 未声明)时只能省略参数 ---- 服务端
            # thinking 照旧; set_reasoning_effort 入口已拦截, 这里是
            # disable(condenser) 等内部调用的降级路径: 请求仍成功.
            if disable_reasoning or not effort:
                if self._can_disable:
                    kw['reasoning'] = {'effort': 'none'}
            else:
                kw['reasoning'] = {'effort': effort}
        if instructions:
            kw['instructions'] = instructions
        request_tools = [*(tools or []), *self._native_tools]
        if request_tools:
            kw['tools'] = request_tools
        if previous_response_id:
            kw['previous_response_id'] = previous_response_id

        client = _client_for(self.key)
        started = time()
        ttfb_ms: int | None = None
        response = None
        try:
            try:
                response = await client.responses.create(**kw)
            except (ConnectError, APIConnectionError, APITimeoutError) as exc:
                raise LlmConnError(f'{self.key}: {exc!r}') from exc

            async for event in response:
                ev_type = getattr(event, 'type', '') or ''
                if ev_type == 'response.output_text.delta':
                    delta = getattr(event, 'delta', '') or ''
                    if delta:
                        if ttfb_ms is None:
                            ttfb_ms = int((time() - started) * 1000)
                        yield TextDelta(text=delta)
                elif ev_type in _REASONING_DELTAS:
                    delta = getattr(event, 'delta', '') or ''
                    if delta:
                        yield ReasoningDelta(text=delta)
                elif ev_type == 'response.completed':
                    resp = getattr(event, 'response', None)
                    if resp is None:
                        continue
                    fc_items = [
                        FunctionCallItem(
                            name=getattr(it, 'name', ''),
                            arguments=getattr(it, 'arguments', '{}'),
                            call_id=getattr(it, 'call_id', ''),
                        )
                        for it in (getattr(resp, 'output', None) or [])
                        if getattr(it, 'type', None) == 'function_call'
                    ]
                    _log.info('stream done: %s ttfb=%sms total=%dms fc=%d',
                              self.key,
                              ttfb_ms if ttfb_ms is not None else -1,
                              int((time() - started) * 1000), len(fc_items))
                    usage = getattr(resp, 'usage', None)
                    try:
                        usage = usage.model_dump() if usage else None
                    except Exception:
                        usage = None
                    yield Completed(
                        response_id=getattr(resp, 'id', '') or '',
                        text=getattr(resp, 'output_text', '') or '',
                        usage=usage,
                        function_calls=fc_items,
                    )
        except (RemoteProtocolError, ReadError, StreamClosed) as exc:
            _log.warning('stream ended early: %s (%r)', self.key, exc)
        finally:
            if response is not None:
                try:
                    await response.close()
                except (RemoteProtocolError, ReadError, StreamClosed):
                    pass
