"""MCP 接入层 ---- 常驻会话 + 工具面 + 运维面.

分工 (每个名字只做一件事):
  ``mcp``             passive 常驻: 独占每个 server 的一条会话 (惰性连接), 只暴露 req
  ``mcp/tools``       工具: 列工具清单, server 留空 = 全部
  ``mcp/doc``         工具: 一个工具的 description + inputSchema (name 必填)
  ``mcp/call``        工具: 按名字调一个工具, 唯一的执行入口
  ``mcp/servers``     运维: 会话状态 (不建连)
  ``mcp/connect``     运维: 幂等建连 (预热/诊断)
  ``mcp/disconnect``  运维: 只拆会话, 配置保留

后三个不设 meta.tool: 连接生命周期不交给 LLM 管 (理由见 session.py 头注).

MCP server 本身不是 routine ---- 它是外部能力源, 同 ros_bridge 背后的 ROS、
bus_listener 背后的设备总线. 进 routine 注册表的是接入单元, 不是设备.
"""
from .call import McpCall
from .doc import McpDoc
from .server import Mcp
from .session import McpConnect, McpDisconnect, McpServers
from .tools import McpTools

__all__ = ['Mcp', 'McpTools', 'McpDoc', 'McpCall',
           'McpServers', 'McpConnect', 'McpDisconnect']
