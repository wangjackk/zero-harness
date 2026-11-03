"""mcp/call ---- 调用任意 MCP 工具 (唯一的外部能力入口).

渐进披露, 与 skill/list → skill/load 同一套路:
  不确定有哪些工具 / 参数怎么填  →  ``mcp/doc``
  已知 server 与工具名           →  ``mcp/call`` {name: '<server>/<tool>', kwargs: {...}}

本 routine 不维护 server 清单、不碰协议: 会话由常驻 ``mcp`` 持有, 这里只做
"按名字找实例 → req 一跳" (同 skills_req 的做法). 失败一律收成 ``error``
字段返回, 不抛异常打断 agent ---- 对齐 run_routine 的约定.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field, ValidationError
from routine import Routine
from routine.errors import ReqError, ReqTimeout

from .client import McpError
from .lookup import connect_hint, mcp_req

__all__ = ['McpCall']

# req 层超时只在**调用方显式给了 timeout** 时才有意义 (那时能算准: 调用超时
# + 余量). 没给的时候不能猜一个数 ---- 真正的超时由底座按该 server 的
# spec.timeout 判定, 调用方这里只是一个防死锁的天花板: 猜小了会"req 先死而
# 对端还在跑", 猜大了只是把僵死场景的等待拖长 (底座与本单元同进程, 真僵死
# 的话等 180s 和等 1h 没有区别).
_CEILING_TIMEOUT = 3600.0
_REQ_MARGIN = 15.0

_NEED_NAME = ('name is required: pass "<server>/<tool>"; '
              'call mcp/tools first if you do not know the tool name')



class McpCallInput(BaseModel):
    name: str = Field(
        description=(
            'Tool to call, as "<server>/<tool>" (e.g. "fs/read_file"). '
            'Call mcp/doc first to learn which tools exist.'))
    kwargs: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            'Tool arguments object. Must match the tool inputSchema shown by '
            'mcp/doc; leave empty for no-arg tools.'))
    timeout: Optional[float] = Field(
        default=None,
        description=('Optional call timeout in seconds. '
                     'Defaults to the server\'s configured timeout.'))


class McpCallOutput(BaseModel):
    server: str = Field('', description='MCP server that served the call')
    tool: str = Field('', description='Tool name')
    is_error: bool = Field(False, description='True when the tool reported failure')
    text: str = Field('', description='Text content of the result')
    content: list = Field(default_factory=list,
                          description='Normalized content blocks')
    error: Optional[str] = Field(None, description='Transport/protocol error')
    hint: Optional[str] = Field(
        None,
        description=('Present when the failure was a link/session problem: '
                     'a ready-to-copy mcp/connect invocation to retry it.'))


class McpCall(Routine):
    """Call one tool on one MCP server; the resident ``mcp`` holds the session."""

    name = 'mcp/call'

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': McpCallInput.model_json_schema(),
        'output_schema': McpCallOutput.model_json_schema(),
        'description': (
            'Call a tool provided by an external MCP server. '
            'You do not know these tools in advance: call mcp/tools to list '
            'them, mcp/doc for the exact inputSchema, then call here with '
            'matching kwargs. Images/resources in the result are saved and '
            'returned as /files/<name> paths.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            inp = McpCallInput.model_validate(kwargs or {})
        except ValidationError as exc:
            if any(tuple(e.get('loc') or ()) == ('name',) for e in exc.errors()):
                return {'error': _NEED_NAME}
            return {'error': f'invalid arguments: {exc}'}

        timeout = float(inp.timeout or 0.0)
        req_timeout = (timeout + _REQ_MARGIN) if timeout else _CEILING_TIMEOUT
        try:
            result = await mcp_req(self, 'tools/call', {
                'name': inp.name,
                'arguments': dict(inp.kwargs or {}),
                'timeout': timeout or None,
            }, timeout=req_timeout)
        except McpError as exc:
            return {'error': str(exc), 'name': inp.name}
        except ReqTimeout:
            return {'error': f'MCP 调用超时 ({req_timeout:.0f}s): {inp.name}',
                    'hint': connect_hint(inp.name), 'name': inp.name}
        except ReqError as exc:
            return {'error': f'MCP 调用失败: {exc}', 'name': inp.name}

        if result.get('link_error'):
            # 底座标的链路故障: 工具名/参数没问题, 是会话起不来或断了.
            return {'error': str(result.get('error') or ''), 'name': inp.name,
                    'hint': connect_hint(inp.name)}
        return result
