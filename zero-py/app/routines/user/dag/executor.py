"""节点就绪判断 + kwargs 模板替换.

内部工具函数,被 RunDag 主循环调用.
"""
from __future__ import annotations

import json
import re
from typing import Any


def _try_parse_structured(text: str) -> dict | list | None:
    """尝试从文本中提取 JSON 对象,处理三种常见模型输出:
    1. 纯 JSON
    2. ```json ... ``` 代码块包裹
    3. 前缀散文 + 末尾 JSON(思考模型常见)
    """
    cleaned = text.strip()
    # 去掉 markdown 代码块
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?\s*```\s*$', '', cleaned).strip()

    def _try(s: str) -> dict | list | None:
        try:
            parsed = json.loads(s)
            return parsed if isinstance(parsed, (dict, list)) else None
        except (json.JSONDecodeError, ValueError):
            return None

    result = _try(cleaned)
    if result is not None:
        return result

    # 从第一个 { 开始尝试
    first = cleaned.find('{')
    if first > 0:
        return _try(cleaned[first:])
    return None

from .condition_evaluator import evaluate_condition
from .spec import DagNodeSpec, NodeOutput

# $node_id.output 或 $node_id.output.field
_NODEREF_RE = re.compile(r'\$([a-zA-Z_][a-zA-Z0-9_-]*)\.output(?:\.([a-zA-Z_][a-zA-Z0-9_]*))?')
# $WI.input_name  (WI = Workflow Inputs,显式命名空间)
_INPUTREF_RE = re.compile(r'\$WI\.([a-zA-Z_][a-zA-Z0-9_]*)')


def deps_all_terminal(node: DagNodeSpec, outputs: dict[str, NodeOutput]) -> bool:
    """所有 depends_on 已进入终态(不管成功/失败/跳过)."""
    return all(dep in outputs for dep in node.depends_on)


def _check_trigger_rule(node: DagNodeSpec, outputs: dict[str, NodeOutput]) -> bool:
    deps = node.depends_on
    if not deps:
        return True

    states = [outputs[d].state for d in deps]
    rule = node.trigger_rule

    if rule == 'all_success':
        return all(s == 'completed' for s in states)
    if rule == 'one_success':
        return any(s == 'completed' for s in states)
    if rule == 'none_failed_min_one_success':
        return (not any(s == 'failed' for s in states)) and any(s == 'completed' for s in states)
    if rule == 'all_done':
        return True  # deps_all_terminal 已保证
    return False


def should_run(
    node: DagNodeSpec,
    outputs: dict[str, NodeOutput],
    dag_inputs: dict[str, Any] | None = None,
) -> bool:
    """综合 trigger_rule + when 判断节点是否应该运行(False → 标记为 skipped)."""
    if not _check_trigger_rule(node, outputs):
        return False
    if not node.when:
        return True
    result, _ = evaluate_condition(node.when, outputs, dag_inputs)
    return result


def resolve_kwargs(
    node: DagNodeSpec,
    outputs: dict[str, NodeOutput],
    dag_inputs: dict[str, Any],
) -> dict[str, Any]:
    """把 inputs 里的模板变量替换为实际值.

    两种语法:
    - ``$nodeId.output``       -- 引用某节点的 return 值
    - ``$nodeId.output.field`` -- 引用节点 return dict 的某个 key
    - ``$WI.inputName``        -- 引用 DAG 级别的输入参数(WI = Workflow Inputs)

    找不到时抛 :class:`KeyError`,由调用方记 failed 状态并跳过.
    """
    def _sub_noderef(m: re.Match) -> str:
        ref_id = m.group(1)
        field = m.group(2)          # $node_id.output.field 的 field 部分,无则 None
        if ref_id not in outputs:
            raise KeyError(f'${ref_id}.output 未找到 (节点尚未完成或不存在)')
        value = outputs[ref_id].output
        if field is not None:
            node_out = outputs[ref_id]
            if isinstance(value, str) and node_out.output_format == 'json':
                value = _try_parse_structured(value) or value
            if isinstance(value, dict):
                if field not in value:
                    raise KeyError(f'${ref_id}.output.{field} 不存在 (output 中无此 key)')
                return str(value[field])
            raise KeyError(f'${ref_id}.output.{field} 无法提取:output 不是 dict (type={type(value).__name__})')
        return str(value)

    def _sub_inputref(m: re.Match) -> str:
        ref_name = m.group(1)
        if ref_name in dag_inputs:
            return str(dag_inputs[ref_name])
        raise KeyError(f'$WI.{ref_name} 未找到 (未传入此 DAG 输入)')

    resolved: dict[str, Any] = {}
    for key, val in node.inputs.items():
        if not isinstance(val, str):
            resolved[key] = val
            continue
        # 先替换 $nodeId.output(含 .output 后缀,优先级高)
        val = _NODEREF_RE.sub(_sub_noderef, val)
        # 再替换 $inputName(plain 变量)
        val = _INPUTREF_RE.sub(_sub_inputref, val)
        resolved[key] = val
    return resolved
