"""mcp.yaml ---- MCP server 清单: 外部能力源的唯一配置事实源.

为什么不写进 routines.yaml: 后者回答"系统由哪些单元构成", 这里是"系统外
有哪些能力源". MCP server 不是 routine ---- 它是设备/外部服务 (同 ros_bridge
背后的 ROS、bus_listener 背后的设备总线), 由常驻 ``mcp`` routine 持有会话,
再经 ``mcp/call`` ``mcp/doc`` 两个接入单元对外提供.

字段:
  name       引用名, mcp/call 里写 "<name>/<tool>"
  transport  stdio | http (缺省按 command/url 推断)
  command    stdio: 启动命令, list 或单行 str
  env        stdio: 追加子进程环境变量
  cwd        stdio: 子进程工作目录
  url        http: MCP endpoint (Streamable HTTP)
  headers    http: 附加请求头 (鉴权在这里)
  timeout    单次调用超时秒 (缺省 120)
  tools      工具白名单; 缺省/空 = 该 server 全部工具
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

# app/routines/user/mcp/config.py -> parents[3] = app/
CONFIG_PATH = Path(__file__).resolve().parents[3] / 'mcp.yaml'

_ENV_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')


def _env(value: Any) -> Any:
    """展开 ${VAR}. 未定义的变量原样保留 ---- 配置错误留到建连时暴露,
    不在加载阶段静默变成空串."""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_env(v) for v in value]
    return value


@dataclass(frozen=True)
class ServerSpec:
    """一个 MCP server 的静态声明 (不含任何运行时连接状态)."""

    name: str
    transport: str = 'stdio'
    command: Tuple[str, ...] = ()
    env: Dict[str, str] = field(default_factory=dict)
    cwd: str = ''
    url: str = ''
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: float = 120.0
    tools: Tuple[str, ...] = ()

    def allows(self, tool: str) -> bool:
        """工具是否在暴露白名单内 (空白名单 = 全放)."""
        return not self.tools or tool in self.tools


def _split_command(value: str) -> Tuple[str, ...]:
    """单行命令 -> argv. posix=False 保住 Windows 路径里的反斜杠.

    posix 模式会把 ``E:\\work`` 的反斜杠当转义吃掉, 而本项目的 server 命令
    多半带盘符路径; 代价是引号会留在 token 里, 这里再剥一层.
    """
    parts = shlex.split(value, posix=False)
    return tuple(
        p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in '"\'' else p
        for p in parts)


def _as_tuple(value: Any, *, split: bool = False) -> Tuple[str, ...]:
    """str | list -> tuple[str]; split=True 时单行命令按 shell 规则切词."""
    if value is None or value == '':
        return ()
    if isinstance(value, str):
        return _split_command(value) if split else (value,)
    return tuple(str(v) for v in value)


def _spec(item: Dict[str, Any], idx: int) -> ServerSpec:
    if not isinstance(item, dict):
        raise ValueError(f'mcp.yaml: servers[{idx}] 必须是映射, 得到 {type(item).__name__}')
    item = _env(dict(item))
    name = str(item.get('name') or '').strip()
    if not name:
        raise ValueError(f'mcp.yaml: servers[{idx}] 缺 name')
    # 名字是工具全名的前缀 ("<server>/<tool>"), 自己带斜杠或空白会让解析
    # 永远切在错的位置 ---- 在加载时就拒, 别等到调用时表现成"工具不存在".
    if '/' in name:
        raise ValueError(f"mcp.yaml: server 名 {name!r} 不能含 '/'")
    if any(c.isspace() for c in name):
        raise ValueError(f'mcp.yaml: server 名 {name!r} 不能含空白')
    command = _as_tuple(item.get('command'), split=True)
    url = str(item.get('url') or '').strip()
    transport = str(item.get('transport') or '').strip().lower()
    if not transport:
        transport = 'http' if url else 'stdio'
    if transport not in ('stdio', 'http'):
        raise ValueError(f'mcp.yaml: {name}: 未知 transport {transport!r}')
    if transport == 'stdio' and not command:
        raise ValueError(f'mcp.yaml: {name}: stdio 需要 command')
    if transport == 'http' and not url:
        raise ValueError(f'mcp.yaml: {name}: http 需要 url')
    return ServerSpec(
        name=name,
        transport=transport,
        command=command,
        env={str(k): str(v) for k, v in (item.get('env') or {}).items()},
        cwd=str(item.get('cwd') or ''),
        url=url,
        headers={str(k): str(v) for k, v in (item.get('headers') or {}).items()},
        timeout=float(item.get('timeout') or 120.0),
        tools=_as_tuple(item.get('tools')),
    )


def specs_from_items(items: List[Dict[str, Any]]) -> List[ServerSpec]:
    """内联清单 (routines.yaml kwargs 覆盖用) -> specs; 与文件同一套校验."""
    return [_spec(item, i) for i, item in enumerate(items or [])]


def load_specs(path: Path | str | None = None) -> List[ServerSpec]:
    """读 mcp.yaml. 文件不存在 = 未接入任何外部能力源 (返回空清单, 不报错)."""
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding='utf-8')) or {}
    items = raw.get('servers') or []
    if not isinstance(items, list):
        raise ValueError(f'{p}: "servers" 必须是列表')
    specs = [_spec(item, i) for i, item in enumerate(items)]
    seen: Dict[str, int] = {}
    for s in specs:
        if s.name in seen:
            raise ValueError(f'{p}: server 重名 {s.name!r}')
        seen[s.name] = 1
    return specs
