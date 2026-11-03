"""MCP client ---- 最小 JSON-RPC 2.0 客户端 (stdio + Streamable HTTP).

只实现 call 侧需要的三段: initialize / tools/list / tools/call.
不引第三方 mcp SDK: httpx 已经在依赖里, stdio 就是按行 JSON-RPC, 协议面
(版本协商、cursor 分页、内容块归一) 全部内聚在本文件, 上层零协议知识.

两条传输对外同构 (request/notify), 因为差异只在"一条消息怎么走":
  stdio  写到子进程 stdin, 从 stdout 按行读; 回执按 id 匹配 pending future
  http   每条消息一个 POST; 回执可能是 json, 也可能是 SSE 流里的一帧
         (本机/内网目标绕过环境代理, 见 ``_is_internal``)
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from .config import ServerSpec

# 客户端声明的协议版本; 服务端可在 initialize 回执里改写成它支持的版本,
# 之后请求头 (MCP-Protocol-Version) 一律用协商结果.
PROTOCOL_VERSION = '2025-06-18'

_CLIENT_INFO = {'name': 'zero-harness', 'version': '0.1.0'}

# 内网地址判定: 这些目标不该经过环境代理.
_PRIVATE_SUFFIX = ('.local', '.internal', '.lan')


def _is_internal(url: str) -> bool:
    """目标是否是本机/内网地址 (整条 URL 或裸 host 都吃).

    开发机上常挂着 ``HTTP_PROXY``, 而 httpx 默认 ``trust_env=True`` ——
    连 ``127.0.0.1`` 的 MCP server 也会被交给代理. 代理按绝对 URI 转发,
    服务端读不到请求体, 后面的请求与残留 body 错位: 表现成 404 / 501,
    看起来像"协议不兼容", 排查方向完全被带偏.

    内网地址一律绕过代理: 它们本来就不该出网, 而 NO_PROXY 是全局配置,
    不该由每个 MCP server 的装配者替运维记.
    """
    raw = str(url).strip()
    if not raw:
        return False
    host = (urlparse(raw).hostname or raw).strip().strip('[]').lower()
    if not host:
        return False
    if host in ('localhost', 'localhost.localdomain'):
        return True
    if host.endswith(_PRIVATE_SUFFIX):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local

_EXT_BY_MIME = {
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'image/svg+xml': 'svg',
    'audio/mpeg': 'mp3',
    'audio/wav': 'wav',
    'audio/ogg': 'ogg',
    'application/pdf': 'pdf',
}

_SLUG_RE = re.compile(r'[^A-Za-z0-9_-]+')


class McpError(RuntimeError):
    """MCP 协议或传输错误: 连接失败 / server 回 error / 超时."""


class McpLinkError(McpError):
    """链路层故障: 起不来 / 断了 / 传输超时 ---- 重连可能有用的一类.

    与 ``McpError`` 分开是为了让上面能分辨"该重连"和"该改参数":
    "工具不存在"也是 McpError, 但它重连一百次还是不存在; 只有传输层的
    故障才值得把 ``mcp/connect`` 递到调用方手里.
    """


def _result(msg: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-RPC 回执 -> result; 带 error 的直接抛 (调用方统一收成 error 字段)."""
    err = msg.get('error')
    if err is not None:
        if isinstance(err, dict):
            raise McpError(f"{err.get('code')}: {err.get('message')}")
        raise McpError(str(err))
    result = msg.get('result')
    return result if isinstance(result, dict) else {}


def _save_b64(data_b64: str, mime: str, server: str) -> str:
    """base64 内容块 -> uploads 落盘, 返回 ``/files/<name>`` (前端 img 直接用).

    落 uploads 而不是随手找个临时目录: ``/files/<name>`` 是前端唯一认的图片
    引用形式 (见 uploads.py 头注), 落到别处前端就看不见.
    目录常量延迟导入: 该模块会带出 agent core 的模型配置初始化, 而 mcp 是
    通用接入层, 不该在 import 阶段付这笔账.
    """
    from app.routines.user.agents._core.uploads import UPLOADS_DIR

    mime = (mime or '').split(';')[0].strip().lower()
    ext = _EXT_BY_MIME.get(mime, 'bin')
    slug = _SLUG_RE.sub('-', server).strip('-') or 'server'
    path = UPLOADS_DIR / f'mcp-{slug}-{uuid.uuid4().hex[:10]}.{ext}'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data_b64))
    return f'/files/{path.name}'


class _Transport:
    """一条 server 连接的消息通道; request 返回 result, notify 不等回执."""

    protocol_version: str = PROTOCOL_VERSION

    @property
    def alive(self) -> bool:  # pragma: no cover - 子类实现
        return False

    async def start(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                      timeout: float = 60.0) -> Dict[str, Any]:
        raise NotImplementedError

    async def notify(self, method: str,
                     params: Optional[Dict[str, Any]] = None) -> None:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError


class StdioTransport(_Transport):
    """子进程 stdin/stdout, 消息按换行分隔 (MCP stdio 规范)."""

    def __init__(self, spec: ServerSpec,
                 log: Optional[logging.Logger] = None) -> None:
        self._spec = spec
        self._log = log or logging.getLogger(f'mcp.{spec.name}')
        self._proc: Any = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader: Optional[asyncio.Task] = None
        self._stderr: Optional[asyncio.Task] = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self._spec.env)
        env.setdefault('PYTHONUNBUFFERED', '1')
        argv = list(self._spec.command)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._spec.cwd or None,
                env=env,
            )
        except FileNotFoundError as exc:
            raise McpLinkError(
                f'{self._spec.name}: 启动命令不存在 {argv[0]!r}') from exc
        except OSError as exc:
            raise McpLinkError(f'{self._spec.name}: 起子进程失败: {exc}') from exc
        self._reader = asyncio.create_task(self._read_loop())
        self._stderr = asyncio.create_task(self._drain_stderr())

    async def _write(self, msg: Dict[str, Any]) -> None:
        if not self.alive:
            raise McpLinkError(f'{self._spec.name}: stdio server 未运行')
        # json.dumps 会把字符串内换行转义, 不会破坏行分隔
        line = json.dumps(msg, ensure_ascii=False) + '\n'
        self._proc.stdin.write(line.encode('utf-8'))
        await self._proc.stdin.drain()

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                      timeout: float = 60.0) -> Dict[str, Any]:
        self._next_id += 1
        rid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        msg: Dict[str, Any] = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            msg['params'] = params
        try:
            await self._write(msg)
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise McpLinkError(
                f'{self._spec.name}: {method} 超时 ({timeout:.0f}s)') from None
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str,
                     params: Optional[Dict[str, Any]] = None) -> None:
        msg: Dict[str, Any] = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            msg['params'] = params
        await self._write(msg)

    # ── 读循环 ──

    async def _read_loop(self) -> None:
        stdout = self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                text = line.decode('utf-8', 'replace').strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    # server 往 stdout 写了非协议内容 = 违规, 记下但不断流
                    self._log.warning('%s: 非 JSON 输出: %.200s',
                                      self._spec.name, text)
                    continue
                await self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        finally:
            self._fail_pending(f'{self._spec.name}: server 关闭了 stdout')

    async def _dispatch(self, msg: Dict[str, Any]) -> None:
        rid = msg.get('id')
        if rid is not None and 'method' not in msg:
            fut = self._pending.get(rid)
            if fut is not None and not fut.done():
                try:
                    fut.set_result(_result(msg))
                except McpError as exc:
                    fut.set_exception(exc)
            return
        # server -> client 请求: 我们不实现 sampling/roots, 回 method not found,
        # 否则对端会一直等这条回执
        method = msg.get('method')
        if method and rid is not None:
            self._log.debug('%s: 暂不支持的 server 请求 %s',
                            self._spec.name, method)
            await self._write({
                'jsonrpc': '2.0', 'id': rid,
                'error': {'code': -32601,
                          'message': f'method not supported: {method}'},
            })

    async def _drain_stderr(self) -> None:
        stderr = self._proc.stderr
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    return
                self._log.debug('%s stderr: %s', self._spec.name,
                                line.decode('utf-8', 'replace').rstrip())
        except asyncio.CancelledError:
            raise

    def _fail_pending(self, reason: str) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(McpLinkError(reason))

    async def close(self) -> None:
        for task in (self._reader, self._stderr):
            if task is not None:
                task.cancel()
        for task in (self._reader, self._stderr):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._reader = self._stderr = None
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            self._fail_pending(f'{self._spec.name}: closed')
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            pass
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        self._fail_pending(f'{self._spec.name}: closed')


class HttpTransport(_Transport):
    """Streamable HTTP: 每条消息一个 POST, 回执可能是 json 或 SSE 流里的一帧."""

    def __init__(self, spec: ServerSpec,
                 log: Optional[logging.Logger] = None) -> None:
        self._spec = spec
        self._log = log or logging.getLogger(f'mcp.{spec.name}')
        self._client: Optional[httpx.AsyncClient] = None
        self._session_id = ''
        self._next_id = 0

    @property
    def alive(self) -> bool:
        return self._client is not None

    async def start(self) -> None:
        # 每台 server 一个 client, 所以"要不要读环境代理"可以按目标一次决定:
        # 内网/本机绕过, 公网照旧走 env (公司网里访问远程 MCP 要靠它).
        self._client = httpx.AsyncClient(
            timeout=None, follow_redirects=True,
            trust_env=not _is_internal(self._spec.url))

    def _headers(self) -> Dict[str, str]:
        headers = {
            'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': self.protocol_version,
        }
        headers.update(self._spec.headers)
        if self._session_id:
            headers['Mcp-Session-Id'] = self._session_id
        return headers

    def _capture_session(self, resp: httpx.Response) -> None:
        sid = resp.headers.get('mcp-session-id')
        if sid:
            self._session_id = sid

    def _post(self, msg: Dict[str, Any], timeout: float):
        assert self._client is not None
        return self._client.stream('POST', self._spec.url, json=msg,
                                   headers=self._headers(), timeout=timeout)

    async def request(self, method: str, params: Optional[Dict[str, Any]] = None,
                      timeout: float = 60.0) -> Dict[str, Any]:
        if self._client is None:
            raise McpLinkError(f'{self._spec.name}: http 未连接')
        self._next_id += 1
        rid = self._next_id
        msg: Dict[str, Any] = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            msg['params'] = params
        try:
            async with self._post(msg, timeout) as resp:
                self._capture_session(resp)
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode('utf-8', 'replace')[:300]
                    raise McpLinkError(
                        f'{self._spec.name}: HTTP {resp.status_code} {body}')
                ctype = (resp.headers.get('content-type') or '').lower()
                if 'text/event-stream' in ctype:
                    async for line in resp.aiter_lines():
                        if not line.startswith('data:'):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        try:
                            frame = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        if frame.get('id') == rid:
                            return _result(frame)
                    raise McpLinkError(
                        f'{self._spec.name}: {method} 的 SSE 在回执前关闭')
                raw = await resp.aread()
                data = json.loads(raw.decode('utf-8', 'replace') or '{}')
                if isinstance(data, list):
                    for frame in data:
                        if frame.get('id') == rid:
                            return _result(frame)
                    raise McpLinkError(f'{self._spec.name}: {method} 无对应回执')
                return _result(data)
        except httpx.TimeoutException:
            raise McpLinkError(
                f'{self._spec.name}: {method} 超时 ({timeout:.0f}s)') from None
        except httpx.HTTPError as exc:
            raise McpLinkError(f'{self._spec.name}: 传输错误: {exc}') from exc

    async def notify(self, method: str,
                     params: Optional[Dict[str, Any]] = None) -> None:
        if self._client is None:
            raise McpLinkError(f'{self._spec.name}: http 未连接')
        msg: Dict[str, Any] = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            msg['params'] = params
        try:
            async with self._post(msg, 30.0) as resp:
                self._capture_session(resp)
                await resp.aread()
                if resp.status_code >= 400:
                    raise McpLinkError(
                        f'{self._spec.name}: HTTP {resp.status_code}')
        except httpx.HTTPError as exc:
            raise McpLinkError(f'{self._spec.name}: 传输错误: {exc}') from exc

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        if self._session_id:
            try:
                await client.delete(self._spec.url, headers=self._headers(),
                                    timeout=5.0)
            except Exception:
                pass   # 显式终止会话是礼貌, 不是必须
            self._session_id = ''
        await client.aclose()


class McpSession:
    """一个 MCP server 的一条会话: 握手、列工具、调工具."""

    def __init__(self, spec: ServerSpec,
                 log: Optional[logging.Logger] = None) -> None:
        self.spec = spec
        self._log = log or logging.getLogger(f'mcp.{spec.name}')
        self._transport: _Transport = (
            StdioTransport(spec, self._log) if spec.transport == 'stdio'
            else HttpTransport(spec, self._log))
        self.tools: List[Dict[str, Any]] = []
        self.protocol_version = ''
        self.server_info: Dict[str, Any] = {}

    @property
    def alive(self) -> bool:
        return self._transport.alive

    async def start(self) -> None:
        await self._transport.start()
        try:
            result = await self._transport.request('initialize', {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {},
                'clientInfo': dict(_CLIENT_INFO),
            }, timeout=self.spec.timeout)
            # 版本协商: 服务端说了算, 后续请求头跟着它
            self.protocol_version = str(
                result.get('protocolVersion') or PROTOCOL_VERSION)
            self._transport.protocol_version = self.protocol_version
            self.server_info = dict(result.get('serverInfo') or {})
            await self._transport.notify('notifications/initialized')
            await self.list_tools()
        except Exception:
            await self.close()
            raise
        self._log.info('%s: 握手完成 (%s, %d tools)',
                       self.spec.name, self.protocol_version, len(self.tools))

    async def list_tools(self, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """列全部工具 (跟 cursor 翻页), 按 spec.tools 白名单过滤后缓存."""
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: Dict[str, Any] = {'cursor': cursor} if cursor else {}
            result = await self._transport.request(
                'tools/list', params, timeout=timeout or self.spec.timeout)
            tools.extend(t for t in (result.get('tools') or [])
                         if isinstance(t, dict) and t.get('name'))
            cursor = result.get('nextCursor')
            if not cursor:
                break
        self.tools = [t for t in tools if self.spec.allows(str(t['name']))]
        return self.tools

    async def call_tool(self, name: str, arguments: Dict[str, Any],
                        timeout: Optional[float] = None) -> Dict[str, Any]:
        raw = await self._transport.request(
            'tools/call',
            {'name': name, 'arguments': dict(arguments or {})},
            timeout=timeout or self.spec.timeout)
        return self._normalize(name, raw)

    def _normalize(self, tool: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """MCP content 数组 -> 本系统的统一形状 (文本合流 + 资源落盘)."""
        blocks: List[Dict[str, Any]] = []
        texts: List[str] = []
        for block in raw.get('content') or []:
            if not isinstance(block, dict):
                continue
            btype = block.get('type')
            if btype == 'text':
                text = str(block.get('text') or '')
                texts.append(text)
                blocks.append({'type': 'text', 'text': text})
            elif btype in ('image', 'audio'):
                mime = str(block.get('mimeType') or '')
                data = str(block.get('data') or '')
                path = ''
                if data:
                    try:
                        path = _save_b64(data, mime, self.spec.name)
                    except Exception as exc:
                        self._log.warning('%s: %s 落盘失败: %r',
                                          self.spec.name, btype, exc)
                blocks.append({'type': btype, 'mimeType': mime, 'path': path})
                texts.append(f'[{btype}: {path}]' if path else f'[{btype}]')
            elif btype == 'resource':
                res = block.get('resource') or {}
                uri = str(res.get('uri') or '')
                if res.get('text'):
                    text = str(res['text'])
                    blocks.append({'type': 'resource', 'uri': uri, 'text': text})
                    texts.append(text)
                elif res.get('blob'):
                    path = ''
                    try:
                        path = _save_b64(str(res['blob']),
                                         str(res.get('mimeType') or ''),
                                         self.spec.name)
                    except Exception as exc:
                        self._log.warning('%s: resource 落盘失败: %r',
                                          self.spec.name, exc)
                    blocks.append({'type': 'resource', 'uri': uri, 'path': path})
                    texts.append(f'[resource: {path}]' if path else f'[resource: {uri}]')
            else:
                blocks.append(dict(block))
        if not texts:
            # 纯结构化返回: 把原文交给 LLM, 否则工具结果看起来是空的
            texts.append(json.dumps(raw, ensure_ascii=False)[:4000])
        out: Dict[str, Any] = {
            'server': self.spec.name,
            'tool': tool,
            'is_error': bool(raw.get('isError')),
            'text': '\n'.join(texts),
            'content': blocks,
        }
        if raw.get('structuredContent') is not None:
            out['structured'] = raw['structuredContent']
        return out

    async def close(self) -> None:
        await self._transport.close()
