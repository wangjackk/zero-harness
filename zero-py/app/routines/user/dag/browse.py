"""DAG 浏览 routines ---- 前端经 run_routine 查询清单与 spec.

两个只读 routine:

- ``dag/list``: 递归列出 ``RunDag.dag_dir`` 下的 YAML
- ``dag/spec``: 按文件名读取并解析单个 DAG spec

filename 是相对 DAG_DIR、去掉 ``.yaml`` 的 POSIX 路径；它可直接作为
``run_dag`` 的 ``dag`` 参数（嵌套目录同理）。
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from .loader import load_dag_yaml_file
from .run_dag import RunDag


def _dag_path(dag: str) -> Path:
    """DAG 名称/相对文件名 → YAML 路径, 与 RunDag._load_spec 同规则."""
    p = Path(dag)
    if p.suffix == '.yaml' or p.is_absolute():
        return p
    return RunDag.dag_dir / f'{dag}.yaml'


def list_dags() -> list[dict[str, str]]:
    """列出 DAG_DIR 下全部 YAML；单个文件解析失败也保留条目并附错误."""
    root = RunDag.dag_dir
    dags: list[dict[str, str]] = []
    for path in sorted(root.rglob('*.yaml')):
        rel = path.relative_to(root).with_suffix('').as_posix()
        try:
            spec = load_dag_yaml_file(path)
            dags.append({
                'name': spec.name,
                'description': spec.description,
                'filename': rel,
            })
        except Exception as exc:
            dags.append({
                'name': path.stem,
                'description': f'[解析失败] {exc}',
                'filename': rel,
            })
    return dags


def load_spec(dag: str) -> dict[str, Any]:
    """读取单个 DAG spec；解析/文件错误直接上抛给 run_routine."""
    return dataclasses.asdict(load_dag_yaml_file(_dag_path(dag)))


class DagListInput(BaseModel):
    pass


class DagListOutput(BaseModel):
    dags: list[dict] = Field(default_factory=list,
                             description='name / description / filename')


class DagList(Routine):
    name = 'dag/list'

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '列出 DAG 目录下的全部工作流.',
        'input_schema': DagListInput.model_json_schema(),
        'output_schema': DagListOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict[str, Any]:
        return {'dags': list_dags()}


class DagSpecInput(BaseModel):
    dag: str = Field(description='DAG 文件名/相对路径, 不含 .yaml')


class DagSpecOutput(BaseModel):
    spec: dict = Field(description='解析后的 DAG spec')


class DagSpecView(Routine):
    name = 'dag/spec'

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '读取指定 DAG 的静态 spec.',
        'input_schema': DagSpecInput.model_json_schema(),
        'output_schema': DagSpecOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict[str, Any]:
        dag = str(kwargs.get('dag') or '').strip()
        if not dag:
            raise ValueError('dag is required')
        return {'spec': load_spec(dag)}
