"""LLM provider profiles ---- 声明式请求构建策略.

参考 hermes-agent providers/base.py 的设计:
- profile 只描述 "怎么发请求", 不持有 client / 不负责流式
- 子类按需覆盖 apply_reasoning(), 把统一的 effort 翻译成 provider 特定参数
- client 构造和 stream 共享在 LLMClient 层

当前所有 provider 都走 OpenAI Responses API. 实测阿里云 qwen 系列 / deepseek /
seed(glm-5.2) 都支持标准 reasoning.effort 参数 (high/medium/low 开 thinking,
none 关 thinking), 因此统一用 OpenAIReasoningProvider 即可, 无需 provider 特化.
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
    extra: dict = field(default_factory=dict)
    builtin_tools: list = field(default_factory=list)

    def apply_reasoning(self, kw: dict[str, Any], effort: str | None, disable: bool) -> None:
        """把 reasoning effort 翻译成 provider 特定参数, 直接写入 kw.

        - effort: 'high' / 'medium' / 'low' / None (off)
        - disable: True 时强制关 reasoning (如 condenser 调用)
        默认实现: 不支持 reasoning, 什么都不做.
        """
        pass

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

    配置识别: extra.reasoning 字段存在.
    """
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 默认 effort 从配置读, 没有就是 None
        r = self.extra.get('reasoning')
        self._default_effort: str | None = (
            r.get('effort') if isinstance(r, dict) else None
        )
        # extra 里移除 reasoning, 避免请求时被 **extra 重复写入
        # (reasoning 由 apply_reasoning 动态控制, 支持 set_reasoning_effort)
        self.extra.pop('reasoning', None)

    @property
    def default_effort(self) -> str | None:
        return self._default_effort

    def apply_reasoning(self, kw: dict[str, Any], effort: str | None, disable: bool) -> None:
        if effort and not disable:
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


def make_provider(key: str, cfg: dict) -> LlmProvider:
    """工厂: 根据 extra 配置自动识别 provider 类型.

    识别规则:
    - extra.reasoning 存在 -> OpenAIReasoningProvider
    - 否则               -> LlmProvider (不支持 reasoning)
    """
    common = dict(
        name=key,
        model_id=cfg.get('model', ''),
        base_url=cfg['base_url'],
        api_key=cfg['api_key'],
        max_context=int(cfg.get('max_context', 0)),
        extra=dict(cfg.get('extra') or {}),
        builtin_tools=list(cfg.get('tools') or []),
    )
    extra = common['extra']
    if isinstance(extra.get('reasoning'), dict):
        return OpenAIReasoningProvider(**common)
    return LlmProvider(**common)
