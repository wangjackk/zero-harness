"""when 表达式求值器.

支持语法(对齐 Archon condition-evaluator,并扩展 $WI 命名空间):

    字符串相等/不等:
        $nodeId.output == 'VALUE'
        $nodeId.output != 'VALUE'

    字段访问(两种等价写法):
        $nodeId.output.field == 'VALUE'
        $nodeId.field == 'VALUE'          # 简写,等价于上一行

    数值比较(两侧均可解析为有限浮点数时生效):
        $nodeId.output > '80'
        $nodeId.output.score >= 0.9
        (RHS 数值/布尔字面量可不加引号)

    工作流输入(WI 命名空间):
        $WI.city == '南阳'

    复合条件(AND 优先级高于 OR,无括号):
        $a.output == 'ok' && $b.output != 'error'
        $a.output == 'A' || $a.output == 'B'

求值失败时 fail-closed(返回 False,节点被 skip).
"""
from __future__ import annotations

import json
import re
from typing import Any

from .spec import NodeOutput

# ---------------------------------------------------------------------------
# 正则:单个原子表达式
# 捕获组:
#   1 nodeId        -- $xxx 后的标识符
#   2 segment1      -- 第一段路径(output 或 shorthand 字段名)
#   3 segment2      -- 可选第二段(字段名,当 segment1=='output' 时)
#   4 operator      -- == != <= >= < >
#   5 quotedValue   -- 单引号包裹的 RHS
#   6 unquotedValue -- 裸数字或 true/false
# ---------------------------------------------------------------------------
_ATOM_RE = re.compile(
    r'^\$([a-zA-Z_][a-zA-Z0-9_-]*)'          # $nodeId  或  $WI
    r'\.([a-zA-Z_][a-zA-Z0-9_]*)'            # .segment1
    r'(?:\.([a-zA-Z_][a-zA-Z0-9_]*))?'       # .segment2(可选)
    r'\s*(==|!=|<=|>=|<|>)\s*'               # operator
    r"(?:'([^']*)'|(-?\d+(?:\.\d+)?|true|false))"  # 'quoted' 或 unquoted
    r'$'
)


def _resolve_output_ref(
    node_id: str,
    field: str | None,
    outputs: dict[str, NodeOutput],
) -> str:
    """把 $nodeId.output[.field] 解析成字符串.找不到时返回空串."""
    node_out = outputs.get(node_id)
    if node_out is None:
        return ''
    value: Any = node_out.output
    if value is None:
        return ''

    if field is None:
        return str(value)

    # 如果 output 本身是 dict,直接取字段
    if isinstance(value, dict):
        v = value.get(field)
    else:
        # 尝试 JSON 解析字符串
        text = str(value)
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return ''
        if not isinstance(parsed, dict):
            return ''
        v = parsed.get(field)

    if v is None:
        return ''
    if isinstance(v, (str,)):
        return v
    return str(v)


def _resolve_wi_ref(
    input_name: str,
    dag_inputs: dict[str, Any],
) -> str:
    """把 $WI.inputName 解析成字符串.找不到时返回空串."""
    v = dag_inputs.get(input_name)
    if v is None:
        return ''
    return str(v)


def _split_outside_quotes(expr: str, sep: str) -> list[str]:
    """按 sep 分割字符串,忽略单引号内的分隔符."""
    parts: list[str] = []
    current: list[str] = []
    in_quote = False
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch == "'":
            in_quote = not in_quote
            current.append(ch)
            i += 1
        elif not in_quote and expr[i:i + len(sep)] == sep:
            parts.append(''.join(current).strip())
            current = []
            i += len(sep)
        else:
            current.append(ch)
            i += 1
    parts.append(''.join(current).strip())
    return parts


def _evaluate_atom(
    expr: str,
    outputs: dict[str, NodeOutput],
    dag_inputs: dict[str, Any],
) -> tuple[bool, bool]:
    """
    求值单个原子条件.

    Returns:
        (result, parsed) -- parsed=False 表示解析失败,result 固定为 False(fail-closed)
    """
    trimmed = expr.strip()
    m = _ATOM_RE.match(trimmed)
    if not m:
        return False, False

    node_id, segment1, segment2, operator, quoted_val, unquoted_val = m.groups()
    rhs = quoted_val if quoted_val is not None else unquoted_val
    if rhs is None:
        return False, False

    # 解析 LHS
    is_wi = (node_id == 'WI')
    if is_wi:
        # $WI.inputName -- segment1 是字段名,不支持 segment2
        if segment2 is not None:
            return False, False
        actual = _resolve_wi_ref(segment1, dag_inputs)
    else:
        # $nodeId.output[.field] 或 $nodeId.field
        if segment1 == 'output':
            field = segment2          # 可能为 None(纯 .output 引用)
        else:
            if segment2 is not None:  # $nodeId.field.sub ---- 不支持
                return False, False
            field = segment1
        actual = _resolve_output_ref(node_id, field, outputs)

    # 求值
    if operator in ('==', '!='):
        result = (actual == rhs) if operator == '==' else (actual != rhs)
    else:
        # 数值比较
        try:
            actual_f = float(actual)
            rhs_f = float(rhs)
        except (ValueError, TypeError):
            return False, False
        if not (actual_f == actual_f and rhs_f == rhs_f):  # NaN 检测
            return False, False
        if operator == '<':
            result = actual_f < rhs_f
        elif operator == '>':
            result = actual_f > rhs_f
        elif operator == '<=':
            result = actual_f <= rhs_f
        else:  # '>='
            result = actual_f >= rhs_f

    return result, True


def evaluate_condition(
    expr: str,
    outputs: dict[str, NodeOutput],
    dag_inputs: dict[str, Any] | None = None,
) -> tuple[bool, bool]:
    """
    求值 when 表达式(可能含 && / ||).

    Args:
        expr:       when 字段的字符串,如 ``"$a.output == 'ok' && $b.score > 0.9"``
        outputs:    已完成节点的输出 map
        dag_inputs: DAG 级别的输入参数($WI 命名空间)

    Returns:
        (result, parsed)
        - parsed=False → 解析失败,result=False(fail-closed)
        - parsed=True  → 求值成功,result 为真/假
    """
    _dag_inputs = dag_inputs or {}
    trimmed = expr.strip()

    # OR 优先级最低,先拆 ||
    or_clauses = _split_outside_quotes(trimmed, '||')

    for or_clause in or_clauses:
        # 再拆 && (AND 优先级高于 OR)
        and_atoms = _split_outside_quotes(or_clause, '&&')
        clause_ok = True

        for atom in and_atoms:
            result, parsed = _evaluate_atom(atom, outputs, _dag_inputs)
            if not parsed:
                return False, False   # fail-closed
            if not result:
                clause_ok = False
                break                 # 短路 AND

        if clause_ok:
            return True, True         # 短路 OR

    return False, True
