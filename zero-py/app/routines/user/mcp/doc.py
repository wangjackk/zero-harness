"""mcp/doc ---- 一个 MCP 工具的参数说明 (渐进披露的最后一步).

``name`` 必填: 只有已经知道要调哪个工具时, 才需要读它的 inputSchema.
不知道有哪些工具请先 ``mcp/tools`` ---- 两个单元各管一件事, doc 不猜
"你是不是其实想列清单".

短名 (不带 ``server/``) 与 ``mcp/call`` 走同一套解析规则: 单 server 时可直接
用; 多 server 下只在已连会话里唯一命中时放行, 否则要求写 ``<server>/<tool>``.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field, ValidationError
from routine import Routine
from routine.errors import ReqError, ReqTimeout

from .client import McpError
from .lookup import connect_hint, mcp_req

__all__ = ['McpDoc']

_DOC_TIMEOUT = 90.0

_NEED_NAME = ('name is required: pass "<server>/<tool>"; '
              'call mcp/tools first if you do not know the tool name')


class McpDocInput(BaseModel):
    name: str = Field(
        description=(
            'Tool to read: "<server>/<tool>" (e.g. "fs/read_file"). '
            'Call mcp/tools first if you do not know the tool name.'))


class McpDocOutput(BaseModel):
    server: str = Field('', description='Owning MCP server')
    tool: str = Field('', description='Tool name')
    description: str = Field('', description='What the tool does')
    input_schema: Dict[str, Any] = Field(
        default_factory=dict, description='JSON Schema of the tool arguments')
    usage: str = Field('', description='Ready-to-use mcp/call example')
    error: Optional[str] = Field(None, description='Lookup error')


def _usage(server: str, tool: str, schema: Dict[str, Any]) -> str:
    """按 inputSchema 的 required 生成一条可直接照抄的 mcp/call 调用."""
    props = schema.get('properties') or {}
    required = [k for k in (schema.get('required') or []) if k in props]
    body = {'name': f'{server}/{tool}',
            'kwargs': {k: f'<{k}>' for k in required}}
    return 'mcp/call ' + json.dumps(body, ensure_ascii=False)


class McpDoc(Routine):
    """Read one MCP tool's description and inputSchema."""

    name = 'mcp/doc'

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': McpDocInput.model_json_schema(),
        'output_schema': McpDocOutput.model_json_schema(),
        'description': (
            'Read one MCP tool\'s exact inputSchema before calling it. '
            'name must be "<server>/<tool>"; use mcp/tools first to discover '
            'tool names. Returns description, input_schema and a ready-to-use '
            'mcp/call example.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            inp = McpDocInput.model_validate(kwargs or {})
        except ValidationError:
            return {'error': _NEED_NAME}

        name = (inp.name or '').strip()
        if not name:
            return {'error': _NEED_NAME}

        try:
            detail = await mcp_req(self, 'tool/doc', {'name': name},
                                   timeout=_DOC_TIMEOUT)
        except McpError as exc:
            return {'error': str(exc), 'name': name}
        except ReqTimeout:
            return {'error': f'MCP 文档查询超时 ({_DOC_TIMEOUT:.0f}s): {name}',
                    'name': name}
        except ReqError as exc:
            return {'error': f'MCP 文档查询失败: {exc}', 'name': name}

        if detail.get('link_error'):
            return {'error': str(detail.get('error') or ''), 'name': name,
                    'hint': connect_hint(name)}

        server = str(detail.get('server') or '')
        tool = str(detail.get('tool') or '')
        schema = detail.get('input_schema')
        if not isinstance(schema, dict):
            schema = {}
        return {
            'server': server,
            'tool': tool,
            'description': str(detail.get('description') or ''),
            'input_schema': schema,
            'usage': _usage(server, tool, schema),
            'content': f'{server}/{tool} 的参数见 input_schema',
        }
