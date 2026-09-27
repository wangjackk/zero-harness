"""LLM provider profiles ---- 声明式请求构建策略.

参考 hermes-agent providers/base.py 的设计:
- profile 只描述 "怎么发请求", 不持有 client / 不负责流式
- 子类按需覆盖 apply_reasoning(), 把统一的 effort 翻译成 provider 特定参数
- client 构造和 stream 共享在 LLMClient 层

与 xml_agent/llm_provider.py 是同构副本 (包内自治, 双端同步维护).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LlmProvider:
    """Provider profile: 描述请求构建策略, 不持有 client.

    子类按需覆盖 apply_reasoning() 把 effort 翻译成 provider 特定参数.
    默认 apply_reasoning 不做任何事 (该模型不支持 reasoning).
    """
    name: str                       # 'seed/glm-5.2' 等 full key
    model_id: str                   # 'glm-5.2' / 'qwen3.7-max' 真实模型 id
    base_url: str
    api_key: str
    max_context: int = 0
    reasoning: dict = field(default_factory=dict)  # 本地能力声明(不透传 API)
    extra: dict = field(default_factory=dict)      # 纯 API 请求体透传
    builtin_tools: list = field(default_factory=list)
    # 协议: 本实例绑定的协议 (配置默认 api 或 agent 层覆盖). 怎么用归 agent
    # 层管, 配置层只提供事实.
    api: str = 'openai-responses'
    # 协议矩阵 {协议: {能力声明}} (本地能力声明, 不透传). 能力是 (provider,
    # 协议) 的客观事实, 如 deepseek 的 responses 静默忽略 previous_response_id:
    # apis: {openai-responses: {previous_response_id: false}}.
    apis: dict = field(default_factory=dict)

    @property
    def previous_response_id(self) -> bool:
        """本协议下 Responses API previous_response_id 是否真实生效.

        按协议查矩阵: 仅 responses 协议有增量链概念 (completions 无状态,
        恒 False); responses 未声明则缺省 True (矩阵只记阉割事实).
        agent 侧据此决定增量/全量.
        """
        if self.api != 'openai-responses':
            return False
        return bool(self.apis.get('openai-responses', {}).get(
            'previous_response_id', True))

    def apply_reasoning(self, kw: dict[str, Any], effort: str | None, disable: bool) -> None:
        """把 reasoning effort 翻译成 provider 特定参数, 直接写入 kw.

        - effort: 'high' / 'medium' / 'low' / None (off)
        - disable: True 时强制关 reasoning (如 condenser 调用)
        默认实现: 不支持 reasoning, 什么都不做.
        """
        pass

    @property
    def can_disable(self) -> bool:
        """是否支持关 thinking.

        base 模型无 reasoning 参数, 本就无 thinking, off 天然成立.
        """
        return True

    @property
    def reasoning_event_types(self) -> tuple[str, ...]:
        """该 provider 流式响应中承载 reasoning delta 的事件类型.

        默认空: 不支持 reasoning 的 provider 无需监听.
        """
        return ()


class OpenAIReasoningProvider(LlmProvider):
    """OpenAI 标准 reasoning: reasoning: {effort: <level>}.

    适用于所有实测支持 reasoning.effort 的 provider:
    - seed/glm-5.2
    - ali-token/qwen3.7-max, qwen3.8-max
    - ali-token/deepseek-v4-flash-0731 (effort=none 可关掉内置 reasoning)
    - openrouter 等

    配置识别: model 级 reasoning 字段存在.
    """
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 默认 effort 从配置读, 没有就是 None
        self._default_effort: str | None = (
            self.reasoning.get('effort') if self.reasoning else None
        )
        # can_disable: 声明该模型支持关 thinking (off 时发 off 档值).
        # 未声明 = 不支持关 (如 glm-5.3 强制思考), set off 会 fail fast.
        self._can_disable: bool = bool(
            self.reasoning.get('can_disable') if self.reasoning else False
        )
        # off 档发给 API 的值 (配置事实, 缺省 'none').
        self._off_effort: str = str(self.reasoning.get('off') or 'none')

    @property
    def default_effort(self) -> str | None:
        return self._default_effort

    @property
    def can_disable(self) -> bool:
        """是否支持关 thinking."""
        return self._can_disable

    def apply_reasoning(self, kw: dict[str, Any], effort: str | None, disable: bool) -> None:
        # off 发配置声明的 off 档值 (缺省 'none'; Responses API 实测:
        # qwen/deepseek 均认 effort=none). 模型不支持关时只能省略参数 ----
        # 服务端 thinking 照旧, 无法关闭; set_reasoning_effort 入口已拦截,
        # 这里是 disable(condenser) 等内部调用的降级路径: 请求仍成功.
        if disable or not effort:
            if self._can_disable:
                kw['reasoning'] = {'effort': self._off_effort}
            return
        kw['reasoning'] = {'effort': effort}

    @property
    def reasoning_event_types(self) -> tuple[str, ...]:
        # OpenAI Responses API 有两种 reasoning delta 事件:
        # - reasoning_summary_text.delta: reasoning 摘要 (seed/glm-5.2)
        # - reasoning_text.delta: 完整 reasoning text (ali-token/qwen3.7-max)
        # 监听两者, 兼容不同 provider 实现.
        return (
            'response.reasoning_summary_text.delta',
            'response.reasoning_text.delta',
        )


def _pick_protocol(apis: dict) -> str:
    """协议推导 (agent 层缺省策略): 从声明矩阵按固定优先级选第一个.

    空矩阵 = 仅隐式声明 openai-responses. 与 TS adapter 同规则.
    """
    if not apis:
        return 'openai-responses'
    for candidate in ('openai-responses', 'openai-completions',
                      'anthropic-messages'):
        if candidate in apis:
            return candidate
    raise ValueError(
        f'apis matrix declares no known protocol: {sorted(apis)}')


def make_provider(key: str, cfg: dict, protocol: str | None = None) -> LlmProvider:
    """工厂: 根据 model 级 reasoning 配置自动识别 provider 类型.

    识别规则:
    - reasoning 声明存在 -> OpenAIReasoningProvider
    - 否则               -> LlmProvider (不支持 reasoning)

    protocol 给定时绑定该协议 (必须在配置协议矩阵里); 缺省从矩阵推导.
    """
    apis = dict(cfg.get('apis') or {})
    if protocol is None:
        protocol = _pick_protocol(apis)
    elif protocol not in apis:
        raise ValueError(
            f'model {key!r}: protocol {protocol!r} not in apis matrix '
            f'(declared: {sorted(apis) or "none"}); how to use a protocol '
            f'is agent policy, but the fact must be declared first')
    # 协议级 base_url 覆盖 (协议特例端点); 缺省沿用 provider 级, 由 SDK 拼协议路径.
    proto_base = str((apis.get(protocol) or {}).get('base_url') or '').strip()
    if proto_base:
        cfg = {**cfg, 'base_url': proto_base}
    common = dict(
        name=key,
        model_id=cfg.get('model', ''),
        base_url=cfg['base_url'],
        api_key=cfg['api_key'],
        max_context=int(cfg.get('max_context', 0)),
        reasoning=dict(cfg.get('reasoning') or {}),
        extra=dict(cfg.get('extra') or {}),
        builtin_tools=list(cfg.get('tools') or []),
        api=protocol,
        apis=apis,
    )
    if common['reasoning']:
        return OpenAIReasoningProvider(**common)
    return LlmProvider(**common)
