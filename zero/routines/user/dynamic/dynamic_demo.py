"""DynamicDemo ---- dynamic 实验场的活样例: 一个 routine 一个文件 + manifest re-export.

creator agent 写新 routine 就照这个形状: 本文件是 routine 实现,
``dynamic/__init__.py`` 的 ``__all__`` re-export 后 watcher 自动热注册.
文件与 manifest 编辑顺序无关 (watcher 自动重试收敛).

入参出参 schema 用 pydantic BaseModel 声明, ``model_json_schema()`` 生成
input_schema/output_schema ---- 类型安全 + IDE 补全, 不手写 JSON dict.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine


class DynamicDemoInput(BaseModel):
    text: str = Field(default='', description='要回显的文本')


class DynamicDemoOutput(BaseModel):
    echo: str = Field(description='回显的 text 原文')
    received: Dict[str, Any] = Field(description='收到的完整 kwargs')


class DynamicDemo(Routine):
    """原样回显输入, 用于验证 dynamic 热重载链路."""

    name = 'dynamic_demo'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'dynamic 实验场样例: 回显输入 kwargs, 验证热重载链路.',
        'input_schema': DynamicDemoInput.model_json_schema(),
        'output_schema': DynamicDemoOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]):
        inp = DynamicDemoInput.model_validate(kwargs)
        return DynamicDemoOutput(
            echo=inp.text, received=kwargs,
        ).model_dump()
