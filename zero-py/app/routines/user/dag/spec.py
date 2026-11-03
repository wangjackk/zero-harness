"""DAG 数据结构定义.

DagNodeSpec  -- 单个节点的静态描述
DagSpec      -- 整个 DAG 的静态描述
NodeOutput   -- 节点运行完成后的动态结果
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# cancel 哨兵:dag_cancel routine 在 result 里放这个 key,父 RunDag 在 done
# 循环里识别它来终止整棵 DAG.result 走 wire 序列化,所以用 JSON-friendly 的
# 魔法 key 而非自定义类实例.这是"子→父发消息"的载体----result 通道本身.
DAG_CANCEL_KEY = '__dag_cancel__'


@dataclass
class InputSpec:
    name: str
    type: str = 'str'           # str | int | float | bool
    desc: str = ''
    default: Any = None
    required: bool = True


@dataclass
class RetrySpec:
    max_attempts: int = 1           # 最多重试次数(不含首次)
    delay_ms: int = 2000            # 首次重试延迟(毫秒),每次翻倍
    on_error: str = 'all'           # 'all' | 'transient'(暂无区分,预留)


@dataclass
class DagNodeSpec:
    id: str
    routine: str                        # 调用的 routine 名称(含控制流,如 dag_cancel)
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    trigger_rule: str = 'all_success'   # all_success | one_success | all_done | none_failed_min_one_success
    when: str | None = None             # 简单表达式, 如 "$a.output == 'ok'"; None=无条件
    retry: RetrySpec | None = None      # 失败时重试配置
    output_format: dict[str, Any] | None = None  # JSON schema;设置后 agent 输出 JSON,支持 $node.output.field


@dataclass
class DagSpec:
    name: str
    description: str
    nodes: list[DagNodeSpec]
    inputs: list[InputSpec] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    # kv 映射:{alias → node_id}
    #   {}                    → 返回全量状态表 {node_id: {state, output, error}}
    #   {'result': 'summary'} → {'result': value}
    #   {'a': 'n1', 'b': 'n2'} → {'a': v1, 'b': v2}


@dataclass
class NodeOutput:
    state: str                  # 'completed' | 'failed' | 'skipped'
    output: Any = None
    error: str | None = None
    output_format: str = 'text' # text | json | md | yaml
