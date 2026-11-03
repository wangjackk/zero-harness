"""mcp/tools ---- 列 MCP 工具清单 (渐进披露的第一步).

``server`` 不填 = 全部已配置 server. 单个 server 连不上不连累整份清单:
该条目带 ``error`` 字段, 其余照常返回 ---- "哪些能用"和"哪个坏了"要能在
一次查询里同时看到.

只回名字与一句话描述, 不回 inputSchema: 参数留给 ``mcp/doc``. 一个 server
常有二十个工具, 全量 schema 一次塞进上下文是纯开销.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field
from routine import Routine
from routine.errors import ReqError, ReqTimeout

from .client import McpError
from .lookup import connect_hint, mcp_req

__all__ = ['McpTools']

_LIST_TIMEOUT = 120.0

# 故障时的唯一披露点. 运维面 (mcp/connect, mcp/disconnect) 没有 meta.tool,
# 永不进 tools 列表 ---- 它只在"某个 server 连不上"这一刻才有意义, 所以
# 提示也只在这一刻出现: 清单正常时一个字都不占.


class McpToolsInput(BaseModel):
    server: str = Field(
        '',
        description=('Only list tools of this MCP server. '
                     'Leave empty to list every configured server.'))


class McpToolsOutput(BaseModel):
    servers: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='[{"server": name, "tools": [{name, description}], "error"?}]')
    content: str = Field('', description='One-line summary')
    error: Optional[str] = Field(None, description='Transport error')


class McpTools(Routine):
    """List MCP tools (names + one-line description), optionally per server."""

    name = 'mcp/tools'

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': McpToolsInput.model_json_schema(),
        'output_schema': McpToolsOutput.model_json_schema(),
        'description': (
            'List available MCP tools. Call this first, before mcp/doc and '
            'mcp/call. Leave server empty to list all configured servers; a '
            'single unreachable server is reported in its own entry and does '
            'not break the rest. Returns tool names + one-line descriptions '
            'only; use mcp/doc for a tool\'s inputSchema.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            inp = McpToolsInput.model_validate(kwargs or {})
        except Exception as exc:
            return {'error': f'invalid arguments: {exc}'}

        server = (inp.server or '').strip()
        try:
            resp = await mcp_req(self, 'tools/list',
                                 {'server': server, 'brief': True},
                                 timeout=_LIST_TIMEOUT)
        except McpError as exc:
            return {'error': str(exc)}
        except ReqTimeout:
            return {'error': f'MCP 工具查询超时 ({_LIST_TIMEOUT:.0f}s)'}
        except ReqError as exc:
            return {'error': f'MCP 工具查询失败: {exc}'}

        listing: List[Dict[str, Any]] = []
        total = 0
        for item in resp.get('servers') or []:
            tools = item.get('tools') or []
            total += len(tools)
            sname = str(item.get('server') or '')
            entry: Dict[str, Any] = {'server': sname, 'tools': tools}
            if item.get('error'):
                entry['error'] = str(item['error'])
                # 名字在这一行、用法在 routine/doc 里: 不给 schema, 只给出口.
                entry['hint'] = connect_hint(server=sname)
            listing.append(entry)

        cfg_err = str(resp.get('config_error') or '')
        if not listing:
            if cfg_err:
                return {'servers': [], 'error': cfg_err,
                        'content': f'app/mcp.yaml 解析失败: {cfg_err}'}
            return {'servers': [], 'content': '未配置任何 MCP server (app/mcp.yaml)'}
        broken = [e['server'] for e in listing if e.get('error')]
        hint = (f'{len(listing)} 个 server, {total} 个工具. '
                '用 mcp/doc {"name": "<server>/<tool>"} 看参数, 再 mcp/call 调用.')
        if broken:
            # 末句不进 f-string: ``{name: "mcp/connect"}`` 要原样给模型抄.
            hint += (f' 连不上: {", ".join(broken)}. '
                     '连接问题可 run_routine mcp/connect 单独处理, '
                     '用法用 routine/doc {name: "mcp/connect"} 查.')
        return {'servers': listing, 'content': hint}
