"""mcp ---- MCP 会话常驻持有者 (passive).

MCP server 不是 routine: 它是外部能力源 (设备/服务), 跟 ros_bridge 背后的
ROS、bus_listener 背后的设备总线同一位置. 本 routine 是它的接入层, 独占
每个 server 的一条会话, 对外只暴露语义化 req 面 ---- 调用方零协议知识:

    req 'servers'     {}                            → server 状态清单
    req 'tools/list'  {server?, brief?}             → 工具清单 (含 inputSchema)
    req 'tool/doc'    {name}                        → 单个工具的说明与 schema
    req 'tools/call'  {name, arguments, timeout?}   → 归一化调用结果
    req 'connect'     {server}                      → 建连 + 重读配置 (生效点)
    req 'disconnect'  {server}                      → 拆会话 + 重读配置

mcp.yaml 只在两处被读: 启动装载一次, 以及 connect / disconnect 时重读.
其余 req 一律只用内存清单. 配置的生效点必须是显式的连接动作, 不做文件旁听,
也不归 routines 的 watcher: 那份 watcher 盯的是注册单元的生命周期
(routines.yaml + 源码), 让它认识某个单元的私有配置格式是反向耦合.

上层不直接 req 本实例: 用 ``mcp/tools`` ``mcp/doc`` ``mcp/call`` 三个工具单元,
以及 ``mcp/servers`` ``mcp/connect`` ``mcp/disconnect`` 三个运维单元.
一一对应, 每个单元只做一件事.

连接惰性建立: 第一次用到某个 server 才拉起并握手, 失败可重试. 配置里列了
十个 server 不代表每次启动都要等十个握手, 也不代表十个 server 必须都在线.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from routine import Routine, request
from routine.logger import setup_logger

from .client import McpError, McpLinkError, McpSession
from .config import CONFIG_PATH, ServerSpec, load_specs, specs_from_items

__all__ = ['Mcp']

_log = setup_logger('mcp')

_BRIEF_DESC = 160   # 全量清单里单条描述的截断长度


class McpInput(BaseModel):
    """常驻实例的部署配置 (routines.yaml 条目 kwargs 可覆盖 mcp.yaml)."""

    servers: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='server 清单; 缺省读 app/mcp.yaml')


class Mcp(Routine):
    """持有全部 MCP server 会话, 提供 tools/list 与 tools/call 的 req 面."""

    name = 'mcp'
    is_passive = True
    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            'MCP 会话常驻持有者: 惰性连接 app/mcp.yaml 声明的 server '
            '(改完配置调 connect 生效), '
            'req tools/list 列工具 (含 inputSchema), req tools/call 调工具, '
            'req servers 看连接状态'),
        'input_schema': McpInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = asyncio.Event()
        self._specs: List[ServerSpec] = []
        self._sessions: Dict[str, McpSession] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        # mcp.yaml 解析失败的原因. 空串 = 配置正常 (空清单另说).
        self._config_error: str = ''
        # mcp.yaml 是本单元自己的私有配置, 不归 watcher (后者盯的是
        # routines.yaml 里注册单元的生命周期), 也不靠 mtime 旁观文件 ----
        # 配置的生效点是 connect / disconnect: 要建连或要拆连时顺手重读,
        # 除此之外的 req 一律只用内存里的清单, 不去碰盘.
        self._yaml_path: Path = CONFIG_PATH
        # 部署参数覆盖 mcp.yaml 时 (routines.yaml 条目里写了 servers) 文件
        # 不再有话语权, 不去看它, 免得两处配置互相打架.
        self._deployed: bool = False

    # ── 生命周期 ──

    def _load(self, items: List[Dict[str, Any]]) -> None:
        """装载配置; 失败不抛 ---- passive 起不来的话连诊断入口都没了.

        解析错误若直接冒出去, 结果是 mcp 带着空清单活着: ``mcp/servers`` 回
        一个空列表, 把"yaml 写错了"伪装成"你还没接任何 server". 所以错误要
        留下来, 由 req 面原样报给调用方.
        """
        try:
            self._specs = (specs_from_items(items) if items
                           else load_specs(self._yaml_path))
            self._config_error = ''
        except Exception as exc:
            self._specs = []
            self._config_error = f'{type(exc).__name__}: {exc}'
            _log.error('mcp 配置解析失败: %s', self._config_error)

    async def _refresh(self, keep: str = '') -> None:
        """重读 mcp.yaml, 换上内存清单, 关掉已被删掉的 server 的会话.

        由 connect / disconnect 调用: 这两个动作本身就意味着"我要动连接",
        此刻才知道配置该不该跟上. 其它 req 一律用已有清单 ---- 读到一半被
        改的文件, 只在要建连的这一刻才值得冒这个险.

        解析失败保留旧清单继续服务: 一处手误不该让全部 server 下线, 原因
        留在 ``config_error`` 里等下一次 req 报出去.

        ``keep`` 是本次 disconnect 的目标: 它的会话留给调用方拆, 否则这里
        先关掉会让人以为"根本没连过".
        """
        if self._deployed:
            return
        try:
            specs = load_specs(self._yaml_path)
        except Exception as exc:
            self._config_error = f'{type(exc).__name__}: {exc}'
            _log.error('mcp.yaml 重读失败, 沿用旧清单: %s', self._config_error)
            return
        self._config_error = ''
        self._specs = specs
        # 配置里已经没有的 server: 会话留着就是孤儿子进程
        names = {s.name for s in specs}
        for name in [n for n in self._sessions if n not in names and n != keep]:
            self._locks.pop(name, None)
            sess = self._sessions.pop(name, None)
            if sess is None:
                continue
            try:
                await sess.close()
            except Exception:
                _log.warning('%s: 关闭已移除 server 的会话失败', name,
                             exc_info=True)
            else:
                _log.info('%s: 已从配置移除, 会话关闭', name)

    async def run(self, kwargs: Dict[str, Any]) -> None:
        try:
            inp = McpInput.model_validate(kwargs or {})
        except Exception as exc:
            _log.error('mcp 启动参数非法: %s', exc)
            self._specs, self._config_error = [], f'bad deploy kwargs: {exc}'
        else:
            self._stop_event.clear()
            self._deployed = bool(inp.servers)
            self._load(inp.servers)
        _log.info('mcp up: %d server(s) configured%s', len(self._specs),
                  ': ' + ', '.join(s.name for s in self._specs)
                  if self._specs else ' (mcp.yaml empty)')
        try:
            await self._stop_event.wait()
        finally:
            await self._close_all()

    async def stop(self) -> None:
        self._stop_event.set()

    # ── req 面 ──

    @request('servers')
    async def _req_servers(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        out = []
        for spec in self._specs:
            sess = self._sessions.get(spec.name)
            out.append({
                'name': spec.name,
                'transport': spec.transport,
                'connected': bool(sess and sess.alive),
                'tools': len(sess.tools) if sess else 0,
                'timeout': spec.timeout,
            })
        return {'servers': out, 'config_error': self._config_error}

    @request('tools/list')
    async def _req_tools_list(self, source,
                              data: Dict[str, Any]) -> Dict[str, Any]:
        payload = data or {}
        brief = bool(payload.get('brief'))
        name = str(payload.get('server') or '').strip()
        names = [name] if name else [s.name for s in self._specs]

        async def one(sname: str) -> Dict[str, Any]:
            try:
                sess = await self._session(sname)
            except Exception as exc:
                # 单个 server 连不上不连累整份清单 (mcp/doc 全量列举要能出结果)
                return {'server': sname, 'error': str(exc), 'tools': []}
            tools = sess.tools
            if brief:
                tools = [{'name': t['name'],
                          'description': str(t.get('description') or '')
                          [:_BRIEF_DESC]}
                         for t in tools]
            return {'server': sname, 'tools': tools,
                    'server_info': sess.server_info}

        # 并发建连: 全量列举时多个 server 的首次握手不该串行叠加
        return {'servers': list(await asyncio.gather(*(one(n) for n in names))),
                'config_error': self._config_error}

    @request('tool/doc')
    async def _req_tool_doc(self, source,
                            data: Dict[str, Any]) -> Dict[str, Any]:
        """单个工具的说明与 inputSchema (与 tools/call 共用同一套名字解析)."""
        try:
            server, tool = await self._resolve(
                str((data or {}).get('name') or ''))
            sess = await self._session(server)
        except McpLinkError as exc:
            return {'error': str(exc), 'link_error': True}
        for entry in sess.tools:
            if str(entry.get('name') or '') == tool:
                schema = entry.get('inputSchema')
                return {
                    'server': server,
                    'tool': tool,
                    'description': str(entry.get('description') or ''),
                    'input_schema': schema if isinstance(schema, dict) else {},
                }
        known = ', '.join(str(t.get('name') or '') for t in sess.tools) or '(空)'
        raise McpError(f'{server}: 工具 {tool!r} 不存在; 可用: {known}')

    @request('tools/call')
    async def _req_tools_call(self, source,
                              data: Dict[str, Any]) -> Dict[str, Any]:
        payload = data or {}
        arguments = payload.get('arguments')
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise McpError('arguments 必须是对象')
        try:
            server, tool = await self._resolve(str(payload.get('name') or ''))
            sess = await self._session(server)
            timeout = float(payload.get('timeout') or 0) or None
            return await sess.call_tool(tool, arguments, timeout)
        except McpLinkError as exc:
            # 跨 kernel 的 req 只搬字符串, 异常类型活不到调用方. 链路故障在
            # 这里就落成结构化字段 ---- 上层据此分辨"该重连"与"该改参数";
            # 靠错误文本里找关键词是不可靠的 (措辞一改就失效).
            return {'error': str(exc), 'link_error': True}

    @request('connect')
    async def _req_connect(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """幂等建连: 已连直接回状态, 未连则握手.

        顺带重读 mcp.yaml ---- 这里才是配置的生效点. 若该 server 的声明变
        了 (命令/超时/白名单), 旧会话是按旧 spec 建的, 留着只会让新配置永
        远不生效, 所以强制重建一条; 声明没变才回 ``reused: true``.

        失败回 ``connected: false`` 而不是抛 ---- 诊断要的是原因, 不是 ok=false
        的栈.
        """
        name = str((data or {}).get('server') or '').strip()
        if not name:
            raise McpError('connect 需要 server 名; 用 mcp/servers 看已配置的')
        await self._refresh()
        had = self._sessions.get(name)
        try:
            spec = self._spec(name)
            stale = bool(had is not None and had.alive and had.spec != spec)
            sess = await self._session(name, force=stale)
        except Exception as exc:
            return {'server': name, 'connected': False, 'error': str(exc)}
        return {
            'server': name,
            'connected': True,
            'reused': bool(had is not None and had.alive and not stale),
            'rebuilt': stale,
            'tools': len(sess.tools),
            'protocol': sess.protocol_version,
            'server_info': sess.server_info,
        }

    @request('disconnect')
    async def _req_disconnect(self, source,
                              data: Dict[str, Any]) -> Dict[str, Any]:
        """只拆会话, 不动配置: 下次用到该 server 会自动重连."""
        name = str((data or {}).get('server') or '').strip()
        if not name:
            raise McpError('disconnect 需要 server 名; 用 mcp/servers 看已连接的')
        await self._refresh(keep=name)
        sess = self._sessions.pop(name, None)
        if sess is None:
            return {'server': name, 'disconnected': False}
        await sess.close()
        _log.info('%s: 会话已断开 (配置保留, 下次用到自动重连)', name)
        return {'server': name, 'disconnected': True}

    # ── 连接与名字解析 ──

    def _spec(self, name: str) -> ServerSpec:
        for spec in self._specs:
            if spec.name == name:
                return spec
        known = ', '.join(s.name for s in self._specs) or '(未配置)'
        # 清单是上次 connect/disconnect 时读的; 说清"可能是配置没重读",
        # 否则新写的 server 找不到会被当成名字写错.
        tail = (f'; 上次读 mcp.yaml 失败: {self._config_error}'
                if self._config_error else '; 新加的 server 需 connect 一次才会被读到')
        raise McpError(f'未知 MCP server {name!r}; 已配置: {known}{tail}')

    async def _session(self, name: str, force: bool = False) -> McpSession:
        """取可用会话; 无则建连. 同 server 并发首连只握手一次.

        ``force`` 只在 connect 发现声明已变时用: 那条会话是按旧 spec 建的,
        复用等于让新配置永远不生效.
        """
        spec = self._spec(name)
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            sess = self._sessions.get(name)
            if sess is not None and sess.alive and not force:
                return sess
            if sess is not None:
                await sess.close()
                self._sessions.pop(name, None)
            sess = McpSession(spec)
            try:
                await sess.start()
            except Exception:
                await sess.close()
                raise
            self._sessions[name] = sess
            return sess

    async def _resolve(self, name: str) -> Tuple[str, str]:
        """'<server>/<tool>' 精确解析; 短名只在无歧义时放行.

        短名不做"扫全部 server 找同名工具": 那要求把所有 server 都连上, 代价
        远大于让调用方写全名 (mcp/doc 会告诉它怎么写).
        """
        name = name.strip()
        if not name:
            raise McpError('缺工具名; 先 mcp/doc 查可用工具')
        if '/' in name:
            server, _, tool = name.partition('/')
            server, tool = server.strip(), tool.strip()
            spec = self._spec(server)
            if not tool:
                raise McpError(f'{server}: 缺工具名, 应写 "{server}/<tool>"')
            if not spec.allows(tool):
                raise McpError(
                    f'{server}: 工具 {tool!r} 不在白名单内 '
                    f'(已放行: {", ".join(spec.tools)})')
            return spec.name, tool
        if len(self._specs) == 1:
            spec = self._specs[0]
            if not spec.allows(name):
                raise McpError(f'{spec.name}: 工具 {name!r} 不在白名单内')
            return spec.name, name
        hits = [sname for sname, sess in self._sessions.items()
                if any(str(t.get('name')) == name for t in sess.tools)]
        if len(hits) == 1:
            return hits[0], name
        if not hits:
            raise McpError(
                f'工具 {name!r} 未解析: 多 server 下请写 "<server>/<tool>"; '
                '先 mcp/doc 查清单')
        raise McpError(
            f'工具 {name!r} 在多个 server 同名 ({", ".join(hits)}): '
            '请写 "<server>/<tool>"')

    async def _close_all(self) -> None:
        sessions = list(self._sessions.items())
        self._sessions.clear()
        for name, sess in sessions:
            try:
                await sess.close()
            except Exception:
                _log.warning('%s: 关闭会话失败', name, exc_info=True)
