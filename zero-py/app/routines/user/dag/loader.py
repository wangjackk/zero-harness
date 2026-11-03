"""YAML 解析 + 加载时校验.

parse_dag_yaml(text) → DagSpec
load_dag_yaml_file(path) → DagSpec

校验内容:
  1. 必填字段 / 合法标识符
  2. depends_on / inputs $ref 引用的 id 必须存在
  3. output 指向的 id 必须存在
  4. Kahn 算法检测有环
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .spec import DagNodeSpec, DagSpec, InputSpec, RetrySpec

_ID_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_-]*$')
_NODEREF_RE = re.compile(r'\$([a-zA-Z_][a-zA-Z0-9_-]*)\.output(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?')
_INPUTREF_RE = re.compile(r'\$WI\.([a-zA-Z_][a-zA-Z0-9_]*)')  # $var (不带 .output)
_TRIGGER_RULES = {'all_success', 'one_success', 'all_done', 'none_failed_min_one_success'}
_ALLOWED_INPUT_TYPES = {'str', 'int', 'float', 'bool'}


class DagLoadError(ValueError):
    """DAG YAML 解析 / 校验失败."""


def parse_dag_yaml(text: str) -> DagSpec:
    """解析 YAML 文本为 :class:`DagSpec`,解析失败抛 :class:`DagLoadError`."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise DagLoadError(f'invalid YAML: {e}') from e

    if not isinstance(data, dict):
        raise DagLoadError('DAG YAML 根节点必须是 mapping')

    name = str(data.get('name') or '').strip()
    if not name:
        raise DagLoadError('name 字段必填')

    description = str(data.get('description') or '').strip()
    output_refs: dict[str, str] = _parse_outputs(data.get('outputs'))

    # --- inputs ---
    inputs = _parse_inputs(data.get('inputs') or {})

    raw_nodes = data.get('nodes')
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise DagLoadError('nodes 必须是非空列表')

    nodes: list[DagNodeSpec] = []
    node_ids: set[str] = set()

    for i, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            raise DagLoadError(f'nodes[{i}] 必须是 mapping')

        node_id = str(raw.get('id') or '').strip()
        if not node_id:
            raise DagLoadError(f'nodes[{i}]: id 必填')
        if not _ID_RE.match(node_id):
            raise DagLoadError(f'node {node_id!r}: id 只允许字母/数字/下划线/连字符且以字母或下划线开头')
        if node_id in node_ids:
            raise DagLoadError(f'node id 重复: {node_id!r}')
        node_ids.add(node_id)

        routine = str(raw.get('routine') or '').strip()
        if not routine:
            raise DagLoadError(f'node {node_id!r}: routine 必填')

        raw_deps = raw.get('depends_on') or []
        depends_on = [raw_deps] if isinstance(raw_deps, str) else [str(d).strip() for d in raw_deps]

        node_inputs: dict[str, Any] = dict(raw.get('inputs') or {})

        trigger_rule = str(raw.get('trigger_rule') or 'all_success').strip()
        if trigger_rule not in _TRIGGER_RULES:
            raise DagLoadError(
                f'node {node_id!r}: trigger_rule {trigger_rule!r} 不合法, '
                f'可选: {sorted(_TRIGGER_RULES)}'
            )

        when: str | None = raw.get('when')
        if when is not None:
            when = str(when).strip() or None

        retry: RetrySpec | None = None
        raw_retry = raw.get('retry')
        if raw_retry is not None:
            if not isinstance(raw_retry, dict):
                raise DagLoadError(f'node {node_id!r}: retry 必须是 mapping')
            max_attempts = int(raw_retry.get('max_attempts', 1))
            if not (1 <= max_attempts <= 5):
                raise DagLoadError(f'node {node_id!r}: retry.max_attempts 必须在 1-5 之间')
            delay_ms = int(raw_retry.get('delay_ms', 2000))
            if not (1000 <= delay_ms <= 60000):
                raise DagLoadError(f'node {node_id!r}: retry.delay_ms 必须在 1000-60000 之间')
            on_error = str(raw_retry.get('on_error', 'all'))
            if on_error not in ('all', 'transient'):
                raise DagLoadError(f'node {node_id!r}: retry.on_error 只能是 all 或 transient')
            retry = RetrySpec(max_attempts=max_attempts, delay_ms=delay_ms, on_error=on_error)

        output_format: dict | None = raw.get('output_format')
        if output_format is not None and not isinstance(output_format, dict):
            raise DagLoadError(f'node {node_id!r}: output_format 必须是 dict (JSON schema)')

        nodes.append(DagNodeSpec(
            id=node_id,
            routine=routine,
            inputs=node_inputs,
            depends_on=depends_on,
            trigger_rule=trigger_rule,
            when=when,
            retry=retry,
            output_format=output_format,
        ))

    input_names = {inp.name for inp in inputs}

    # input 名称不允许与 node id 重名(避免混淆)
    conflict = input_names & node_ids
    if conflict:
        raise DagLoadError(f'inputs 与 node id 重名: {sorted(conflict)}')

    # --- 引用校验 ---
    for node in nodes:
        for dep in node.depends_on:
            if dep not in node_ids:
                raise DagLoadError(f'node {node.id!r}: depends_on 引用了未知 id {dep!r}')

        for key, val in node.inputs.items():  # noqa: SIM118
            if not isinstance(val, str):
                continue
            # $nodeId.output
            for m in _NODEREF_RE.finditer(val):
                ref_id = m.group(1)
                if ref_id not in node_ids:
                    raise DagLoadError(
                        f'node {node.id!r}: inputs.{key} 引用了未知节点 {ref_id!r}'
                    )
            # $WI.inputName -- 直接匹配,无歧义
            for m in _INPUTREF_RE.finditer(val):
                ref_name = m.group(1)
                if ref_name not in input_names:
                    raise DagLoadError(
                        f'node {node.id!r}: inputs.{key} 引用了未声明的 $WI.{ref_name}'
                    )

        if node.when:
            for m in _NODEREF_RE.finditer(node.when):
                ref_id = m.group(1)
                if ref_id not in node_ids:
                    raise DagLoadError(
                        f'node {node.id!r}: when 引用了未知节点 {ref_id!r}'
                    )

    for alias, node_id in output_refs.items():
        if node_id not in node_ids:
            raise DagLoadError(f'outputs.{alias} 引用了未知节点 id {node_id!r}')

    # --- 环检测 (Kahn) ---
    in_degree = {n.id: len(n.depends_on) for n in nodes}
    adj: dict[str, list[str]] = {n.id: [] for n in nodes}
    for node in nodes:
        for dep in node.depends_on:
            adj[dep].append(node.id)

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    visited = 0
    while queue:
        nid = queue.pop()
        visited += 1
        for downstream in adj[nid]:
            in_degree[downstream] -= 1
            if in_degree[downstream] == 0:
                queue.append(downstream)

    if visited < len(nodes):
        cycle_nodes = [nid for nid, deg in in_degree.items() if deg > 0]
        raise DagLoadError(f'DAG 存在环路,涉及节点: {cycle_nodes}')

    return DagSpec(
        name=name,
        description=description,
        inputs=inputs,
        nodes=nodes,
        outputs=output_refs,   # {alias: node_id}
    )


def _parse_outputs(raw: Any) -> dict[str, str]:
    """解析 outputs 字段,支持三种写法:

    kv mapping(推荐,alias → node_id)::

        outputs:
          result: summary
          raw: merge

    list(alias == node_id)::

        outputs: [merge, summary]

    单字符串(alias == node_id)::

        outputs: summary
    """
    if not raw:
        return {}
    if isinstance(raw, str):
        nid = raw.strip()
        return {nid: nid}
    if isinstance(raw, list):
        return {str(x).strip(): str(x).strip() for x in raw}
    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items()}
    raise DagLoadError(f'outputs 格式不合法: {type(raw).__name__}')


def _parse_inputs(raw: Any) -> list[InputSpec]:
    """解析 inputs 字段,支持两种写法:

    列表形式(有序,推荐)::

        inputs:
          - name: city_a
            type: str
            desc: 第一个城市
            default: 北京

    mapping 形式(简洁)::

        inputs:
          city_a:
            type: str
            default: 北京
    """
    if not raw:
        return []

    items: list[tuple[str, dict]] = []
    if isinstance(raw, list):
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                raise DagLoadError(f'inputs[{i}] 必须是 mapping')
            iname = str(item.get('name') or '').strip()
            if not iname:
                raise DagLoadError(f'inputs[{i}]: name 必填')
            items.append((iname, item))
    elif isinstance(raw, dict):
        for iname, item in raw.items():
            items.append((str(iname), item if isinstance(item, dict) else {}))
    else:
        raise DagLoadError('inputs 必须是 list 或 mapping')

    seen: set[str] = set()
    result: list[InputSpec] = []
    for iname, item in items:
        if not _ID_RE.match(iname):
            raise DagLoadError(f'input 名称不合法: {iname!r}')
        if iname in seen:
            raise DagLoadError(f'input 名称重复: {iname!r}')
        seen.add(iname)

        itype = str(item.get('type') or 'str').strip()
        if itype not in _ALLOWED_INPUT_TYPES:
            raise DagLoadError(f'input {iname!r}: type 必须是 {sorted(_ALLOWED_INPUT_TYPES)} 之一')

        default = item.get('default')
        result.append(InputSpec(
            name=iname,
            type=itype,
            desc=str(item.get('desc') or '').strip(),
            default=default,
            required=(default is None and not item.get('optional', False)),
        ))
    return result


def load_dag_yaml_file(path: Path | str) -> DagSpec:
    """从文件路径读取并解析 DAG YAML."""
    p = Path(path)
    return parse_dag_yaml(p.read_text(encoding='utf-8'))
