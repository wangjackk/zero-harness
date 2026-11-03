"""会话运维面: mcp/servers (状态) + mcp/connect + mcp/disconnect (管理).

三个都不设 ``meta.tool``: 名字不进 tools 列表, 也就不占每拍都在的前缀.
需要时 agent 自己去看、自己去用 ---- 两条路都通, 不靠提示词:

    看  routine/list (名字) → routine/doc {name: "mcp/connect"} (用法)
    用  run_routine {name: "mcp/connect", kwargs: {"server": "fs"}}

``run_routine`` 走 create(name), 不校验 meta.tool, 所以"没暴露给 LLM"和
"调不到"是两件事. 想在某个 agent 里直接出现成工具, 再把它写进该 agent 的
enabled_tools 即可 (显式白名单不做 meta.tool 二次过滤).

披露点只有一个: ``mcp/tools`` 报告某个 server 连不上时, 该条目带 hint
指向 connect. 清单正常时运维面一个字都不出现 ---- connect 只在"连不上"
这一刻有意义, 不该常驻.

语义 (这两个也是 mcp.yaml 唯一的生效点 ---- 其它单元不读盘):
  connect     先重读 mcp.yaml 再建连. 声明变了就重建那条会话 (回
              rebuilt: true), 没变才回 reused: true. 用于预热 (npx 冷启动
              慢)、诊断"连不上", 以及让改过的配置生效.
  disconnect  先重读 mcp.yaml 再拆会话. 释放 stdio 子进程; 下次用到该
              server 会自动重连, 不需要再 connect.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field
from routine import Routine
from routine.errors import ReqError, ReqTimeout

from .client import McpError
from .lookup import mcp_req

__all__ = ['McpServers', 'McpConnect', 'McpDisconnect']

_MGMT_TIMEOUT = 120.0


class McpServersInput(BaseModel):
    """无参: 列出全部已配置 server 的连接状态."""


class McpServers(Routine):
    """MCP server 连接状态 (是否已连、握手协议、工具数)."""

    name = 'mcp/servers'

    meta: ClassVar[Dict[str, Any]] = {
        'readonly': True,
        'input_schema': McpServersInput.model_json_schema(),
        'description': (
            'List configured MCP servers and their session state '
            '(connected / transport / tool count / timeout). '
            'Does not connect anything.'),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            resp = await mcp_req(self, 'servers', {}, timeout=30.0)
        except McpError as exc:
            return {'error': str(exc)}
        except ReqTimeout:
            return {'error': 'MCP 状态查询超时'}
        except ReqError as exc:
            return {'error': f'MCP 状态查询失败: {exc}'}
        servers: List[Dict[str, Any]] = list(resp.get('servers') or [])
        cfg_err = str(resp.get('config_error') or '')
        if cfg_err:
            # 配置写错与"没配置"是两回事, 不能都回一个空清单了事.
            return {'servers': servers, 'error': cfg_err,
                    'content': f'mcp.yaml 解析失败, 未加载任何 server: {cfg_err}'}
        up = [s['name'] for s in servers if s.get('connected')]
        down = [s['name'] for s in servers if not s.get('connected')]
        text = (f'{len(servers)} 个 server, 已连接 {len(up)}'
                + (f' ({", ".join(up)})' if up else ''))
        if down:
            # 惰性连接: 未连接不等于坏, 下次 mcp/call 会自动连.
            text += (f'; 未连接 {", ".join(down)} (用到时自动连, '
                     '或 run_routine mcp/connect 预热)')
        return {'servers': servers, 'content': text}


class McpConnectInput(BaseModel):
    server: str = Field(description='Server name declared in app/mcp.yaml')


class McpConnect(Routine):
    """建连一个 MCP server, 顺带让 app/mcp.yaml 的变更生效."""

    name = 'mcp/connect'

    meta: ClassVar[Dict[str, Any]] = {
        'readonly': False,
        'input_schema': McpConnectInput.model_json_schema(),
        'description': (
            'Connect one MCP server now. This is also where app/mcp.yaml '
            'gets re-read: if that server\'s declaration changed, its old '
            'session is replaced (rebuilt=true); otherwise the live one is '
            'reused (reused=true). Use it to prewarm a slow launcher (npx '
            'cold start), to diagnose an unreachable server, or to apply '
            'config edits. Returns connected=false with an error string '
            'when the handshake fails.'),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            inp = McpConnectInput.model_validate(kwargs or {})
        except Exception as exc:
            return {'error': f'invalid arguments: {exc}'}
        try:
            result = await mcp_req(self, 'connect',
                                   {'server': inp.server.strip()},
                                   timeout=_MGMT_TIMEOUT)
        except McpError as exc:
            return {'server': inp.server, 'connected': False, 'error': str(exc)}
        except ReqTimeout:
            return {'server': inp.server, 'connected': False,
                    'error': f'握手超时 ({_MGMT_TIMEOUT:.0f}s)'}
        except ReqError as exc:
            return {'server': inp.server, 'connected': False,
                    'error': f'握手失败: {exc}'}
        if result.get('connected'):
            tools: int = int(result.get('tools') or 0)
            note = ', 配置已变更, 会话重建' if result.get('rebuilt') else ''
            result['content'] = (
                f'{inp.server}: 已连接 ({result.get("protocol", "")}, '
                f'{tools} 个工具{note})')
        return result


class McpDisconnectInput(BaseModel):
    server: str = Field(description='Server name to disconnect')


class McpDisconnect(Routine):
    """拆掉一个 server 的会话, 顺带让 app/mcp.yaml 的变更生效."""

    name = 'mcp/disconnect'

    meta: ClassVar[Dict[str, Any]] = {
        'readonly': False,
        'input_schema': McpDisconnectInput.model_json_schema(),
        'description': (
            'Drop the current session of one MCP server (closes the stdio '
            'subprocess / HTTP session), after re-reading app/mcp.yaml. '
            'Rarely needed: sessions are lazily re-established, so the next '
            'call reconnects on its own. Disconnecting a server that is not '
            'connected is a no-op.'),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            inp = McpDisconnectInput.model_validate(kwargs or {})
        except Exception as exc:
            return {'error': f'invalid arguments: {exc}'}
        try:
            result = await mcp_req(self, 'disconnect',
                                   {'server': inp.server.strip()}, timeout=30.0)
        except McpError as exc:
            return {'server': inp.server, 'disconnected': False,
                    'error': str(exc)}
        except ReqTimeout:
            return {'server': inp.server, 'disconnected': False,
                    'error': '断开超时'}
        except ReqError as exc:
            return {'server': inp.server, 'disconnected': False,
                    'error': f'断开失败: {exc}'}
        result['content'] = (f'{inp.server}: 会话已断开'
                             if result.get('disconnected')
                             else f'{inp.server}: 本来就没连接')
        return result
