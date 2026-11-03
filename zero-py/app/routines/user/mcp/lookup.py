"""按名字解析常驻 ``mcp`` 实例的 rid ---- req 的对象是实例 id, 不是名字.

同 skills_req / resolve_agent_rid: 每次现查 ``get_running_routines``, 本地不
缓存 id. 一次内核往返相对 LLM 调用可忽略, 换来重启/换 id 后零失效协议.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from routine import Routine

from .client import McpError

MCP_ROUTINE = 'mcp'


def connect_hint(name: str = '', *, server: str = '') -> str:
    """链路故障时递到调用方手里的一行: 可照抄 ``mcp/connect`` 调用.

    ``mcp/connect`` 没有 ``meta.tool``, 不进任何提示词 ---- 它只在这一刻
    (会话起不来 / 断了) 才被提到, 所以文案必须自带调用形状, 让模型能直接抄,
    而不是再去查一次.

    ``name`` 是 ``<server>/<tool>``; 调用方手上只有裸 server 名时用
    ``server=`` 直接给 (``mcp/tools`` 就是这种).
    """
    srv = str(server).strip()
    if not srv and '/' in str(name):
        srv = str(name).split('/', 1)[0].strip()
    where = ('{"server": "%s"}' % srv) if srv else '{...}'
    return ('连接问题可单独处理: run_routine '
            '{name: "mcp/connect", kwargs: ' + where + '}')


class McpUnavailable(McpError):
    """mcp 常驻实例不在跑: 未配置 server, 或被停掉了."""


async def mcp_rid(routine: Routine) -> Optional[str]:
    try:
        running = await routine.get_running_routines()
    except Exception:
        return None
    for item in running or []:
        if str(item.get('name') or '') != MCP_ROUTINE:
            continue
        rid = str(item.get('id') or '').strip()
        if rid:
            return rid
    return None


async def mcp_req(routine: Routine, event: str,
                  data: Optional[Dict[str, Any]] = None,
                  timeout: float = 60.0) -> Dict[str, Any]:
    """对常驻 mcp 发一跳 req; 不在跑就抛 McpUnavailable (调用方收成 error)."""
    rid = await mcp_rid(routine)
    if rid is None:
        raise McpUnavailable(
            'MCP 未运行: app/mcp.yaml 没有配置 server, 或 mcp 常驻实例未启动')
    result = await routine.req(rid, event, data or {}, timeout=timeout)
    return result if isinstance(result, dict) else {'result': result}
