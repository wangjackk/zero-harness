"""tool_schema -- 把 routine 类转成 LLM function-calling schema.

用 meta['input_schema'](Pydantic 模型 -> 精确 JSON Schema).新框架 routine 都有
input_schema,无需老版 signature 反射降级.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from routine import Routine
    from typing import Type


def tool_schema(cls: 'Type[Routine]') -> dict[str, Any]:
    """生成单个工具的 function-calling schema.

    返回值可直接放进 Responses API 的 tools=[...] 列表.
    """
    description = _description(cls)
    input_schema = getattr(cls, 'meta', {}).get('input_schema')

    if input_schema is not None:
        if isinstance(input_schema, dict):
            # 已是预计算好的 JSON Schema dict(meta 里存 model_json_schema() 结果)
            parameters = dict(input_schema)
        else:
            # 兼容旧式:Pydantic 模型类直接放在 meta 里
            parameters = input_schema.model_json_schema()
        # 顶层 title/description 是模型自带的,工具描述单独放外层,避免重复
        parameters.pop('title', None)
        parameters.pop('description', None)
        _strip_hidden_fields(parameters)
    else:
        # 无 input_schema:退化成空 object(新框架 routine 应都有 input_schema)
        parameters = {'type': 'object', 'properties': {}, 'required': []}

    return {
        'type': 'function',
        'name': cls.name,
        'description': description,
        'parameters': parameters,
    }


def tool_schemas(
    routines: 'list[Type[Routine]]',
    *,
    only_tools: bool = True,
    readonly_only: bool = False,
    whitelist: 'set[str] | None' = None,
) -> list[dict[str, Any]]:
    """批量生成 schema 列表,可按 meta 过滤.

    only_tools:    只包含 meta.tool=True 的 routine(默认 True)
    readonly_only: 只包含 meta.readonly=True 的 routine(plan 模式用)
    whitelist:     只包含名字在此集合中的 routine(None = 不过滤,全部可用)
    """
    result = []
    for cls in routines:
        meta = getattr(cls, 'meta', {}) or {}
        if only_tools and not meta.get('tool'):
            continue
        if readonly_only and not meta.get('readonly'):
            continue
        if whitelist is not None and cls.name not in whitelist:
            continue
        result.append(tool_schema(cls))
    return result


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _description(cls: 'Type[Routine]') -> str:
    """优先 meta['description'],再 fallback 名称."""
    meta = getattr(cls, 'meta', {}) or {}
    return meta.get('description') or cls.name


def _strip_hidden_fields(parameters: dict[str, Any]) -> None:
    """从 JSON Schema properties 移除标记 x-hidden 的字段.

    框架注入字段 (如发送方身份) 用 ``Field(json_schema_extra={'x-hidden': True})``
    声明: 模型里有定义 (类型安全 + IDE 补全), 但不出现在给 LLM 的 tool schema 里,
    避免 LLM 误填. 同时从 required 里移除.
    """
    props = parameters.get('properties')
    if not isinstance(props, dict):
        return
    hidden = [
        name for name, spec in props.items()
        if isinstance(spec, dict) and spec.get('x-hidden')
    ]
    if not hidden:
        return
    for name in hidden:
        props.pop(name, None)
    required = parameters.get('required')
    if isinstance(required, list):
        parameters['required'] = [r for r in required if r not in hidden]
