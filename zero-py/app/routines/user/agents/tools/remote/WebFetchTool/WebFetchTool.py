"""FetchUrl ---- 网页正文抓取 tool.

从 URL 抓 HTML,用 trafilatura 提取干净正文(去导航/广告/侧栏),返回 markdown
或纯文本.配合 ``WebSearch`` 串联使用:搜索拿 URL → FetchUrl 抓完整正文.

借鉴 hermes web_tools 的 truncate-and-store 策略:
- 正文 ≤ char_limit(默认 15000):整页返回
- 正文 > char_limit:返回 head 75% + tail 25%,中间写 footer 告诉 agent 完整
  正文存哪个文件、用 read_file 怎么翻看.全文存到 cache/web/{host}-{hash}.md
  (上限 2MB,避免无界写盘).

安全性:
- SSRF 防护:拒绝内网地址(localhost/127.x/10.x/172.16-31.x/192.168.x/169.254.x)
- 只允许 http/https 协议
- 超时 15s,跟随重定向最多 5 跳
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from pathlib import Path
from typing import Any, ClassVar, Dict, Literal, Optional
from urllib.parse import urlparse

import httpx
import trafilatura
from pydantic import BaseModel, Field
from routine import Routine

from .prompt import DESCRIPTION

# 长正文截断阈值(字符数).超过则 head+tail + 写全文到 cache.
DEFAULT_CHAR_LIMIT = 15000
# 写盘全文的硬上限(字符),避免无界写盘.
MAX_STORED_CHARS = 2_000_000
# 抓取超时(秒).
FETCH_TIMEOUT = 15.0
# 缓存目录(相对项目根).
CACHE_DIR = Path('cache/web')


class WebFetchInput(BaseModel):
    url: str = Field(description='要抓取正文的网页 URL(http/https)')
    char_limit: Optional[int] = Field(
        default=None,
        description='返回正文的字符上限.超过则 head+tail 截断 + 全文写文件.默认 15000.',
    )
    format: Literal['markdown', 'text'] = Field(
        default='markdown',
        description='输出格式:markdown(默认,保留链接/图片 URL)/ text(纯文本)',
    )


class WebFetchOutput(BaseModel):
    url: str = Field(description='实际抓取的 URL(可能跟输入不同,跟随重定向后)')
    title: str = Field(description='网页标题')
    content: str = Field(description='正文内容(整页或 head+tail+footer)')
    truncated: bool = Field(description='是否被截断')
    chars_total: int = Field(description='正文总字符数')
    chars_shown: int = Field(description='实际返回的字符数')
    stored_path: Optional[str] = Field(
        default=None,
        description='全文存储路径(仅 truncated=True 时有值,用 read_file 翻看中间)',
    )


class WebFetch(Routine):
    """Fetch a URL and extract clean page text via trafilatura.

    Long pages are head+tail truncated with full text saved to cache/web/;
    use Read tool with offset/limit to read the omitted middle. Pair with
    WebSearch: search to find URLs, then FetchUrl to read full content.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'concurrency_safe': True,
        'input_schema': WebFetchInput.model_json_schema(),
        'output_schema': WebFetchOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = WebFetchInput(**kwargs)
        url = inp.url.strip()
        char_limit = inp.char_limit or DEFAULT_CHAR_LIMIT
        # 钳制到合理范围
        char_limit = max(2000, min(char_limit, 500_000))
        out_format = inp.format

        # SSRF 防护
        await self._check_safe_url(url)

        self._logger.info('fetch_url %s (limit=%d, fmt=%s)', url, char_limit, out_format)

        # 抓 HTML
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; zero-fetch-url/1.0)',
                'Accept': 'text/html,application/xhtml+xml',
            },
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            final_url = str(resp.url)

        # 提取正文
        content = trafilatura.extract(
            html,
            output_format='markdown' if out_format == 'markdown' else 'txt',
            include_links=True,
            include_images=True,
            include_tables=True,
            with_metadata=False,
            url=final_url,
        )
        if not content:
            raise RuntimeError(f'web_fetch: trafilatura 未能从 {url} 提取正文(可能是 JS 渲染页或空内容)')

        # 提取标题(trafilatura 的 metadata 接口)
        title = ''
        try:
            meta = trafilatura.extract(
                html, output_format='json', with_metadata=True, url=final_url,
            )
            if meta:
                import json as _json
                title = (_json.loads(meta).get('title') or '').strip()
        except Exception:
            pass
        if not title:
            # fallback:从 HTML <title> 提取
            m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
            if m:
                title = m.group(1).strip()[:200]

        # base64 图片占位(防 token bomb,跟 hermes 一致)
        content = self._replace_base64_images(content)

        chars_total = len(content)
        if chars_total <= char_limit:
            # 整页返回
            self._logger.info('fetch_url %s whole (%d chars)', url, chars_total)
            return WebFetchOutput(
                url=final_url,
                title=title,
                content=content,
                truncated=False,
                chars_total=chars_total,
                chars_shown=chars_total,
                stored_path=None,
            ).model_dump()

        # head+tail 截断 + 全文写盘
        head, tail, stored_path = self._truncate_and_store(
            content, final_url, char_limit,
        )
        middle_start_line = head.count('\n') + 2
        footer = (
            f"\n\n{'─' * 8} [TRUNCATED] {'─' * 8}\n"
            f"Showing {len(head):,} chars (head) + {len(tail):,} chars (tail) "
            f"of {chars_total:,} total clean characters.\n"
            + (f"Full text saved to: {stored_path}\n"
               f'To read the omitted middle: read_file path="{stored_path}" '
               f"offset={middle_start_line} limit=200\n"
               if stored_path else
               "Full text could not be stored.\n")
            + '─' * 29
        )
        model_text = head + '\n\n[... middle omitted — see footer ...]\n\n' + tail + footer

        self._logger.info(
            'fetch_url %s truncated (%d -> %d chars, stored=%s)',
            url, chars_total, len(model_text), bool(stored_path),
        )
        return WebFetchOutput(
            url=final_url,
            title=title,
            content=model_text,
            truncated=True,
            chars_total=chars_total,
            chars_shown=len(model_text),
            stored_path=stored_path,
        ).model_dump()

    async def _check_safe_url(self, url: str) -> None:
        """SSRF 防护:拒绝内网地址和非 http(s) 协议."""
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f'fetch_url: 只允许 http/https 协议,拒绝 {parsed.scheme!r}')
        host = parsed.hostname or ''
        if not host:
            raise ValueError('fetch_url: URL 缺少 hostname')

        # 解析主机 IP
        try:
            # 先尝试直接当 IP 解析
            ip = ipaddress.ip_address(host)
            ips = [ip]
        except ValueError:
            # 域名,走 DNS
            try:
                infos = await self._resolve(host)
                ips = [ipaddress.ip_address(info) for info in infos]
            except Exception as exc:
                raise RuntimeError(f'fetch_url: DNS 解析 {host} 失败: {exc}') from exc

        for ip in ips:
            # 只拦真正危险的 SSRF 目标(loopback/link_local/私网非全局).
            # 例外:IANA benchmark 网段 198.18.0.0/15 被 Python ipaddress
            # 标记为 is_private,但实际常被 VPN/代理工具用于公网 DNS 劫持
            # (本机网络环境就是这种),不构成 SSRF,放行.
            in_benchmark = ip in ipaddress.ip_network('198.18.0.0/15')
            if ip.is_loopback or ip.is_link_local:
                raise ValueError(
                    f'fetch_url: 拒绝 loopback/link-local 地址 {host} -> {ip}(SSRF 防护)'
                )
            if ip.is_private and not in_benchmark:
                raise ValueError(
                    f'fetch_url: 拒绝私网地址 {host} -> {ip}(SSRF 防护)'
                )

    @staticmethod
    async def _resolve(host: str) -> list[str]:
        """异步 DNS 解析(在线程池里跑,避免阻塞事件循环)."""
        import asyncio
        return await asyncio.to_thread(
            lambda: [info[4][0] for info in socket.getaddrinfo(host, None)],
        )

    @staticmethod
    def _replace_base64_images(text: str) -> str:
        """把 inline base64 图片替换成 [IMAGE: alt] 占位(防 token bomb).

        保留真实 http/https 图片 URL.
        """
        # markdown 图片 with base64
        def _md_repl(m: re.Match) -> str:
            alt = (m.group('alt') or '').strip()
            return f'[IMAGE: {alt}]' if alt else '[IMAGE]'

        md_b64 = re.compile(
            r'!\[(?P<alt>[^\]]*)\]\(\s*data:image/[^;]+;base64,[A-Za-z0-9+/=\s]+\)'
        )
        out = md_b64.sub(_md_repl, text)
        # 括号包裹的 base64
        out = re.sub(
            r'\(\s*data:image/[^;]+;base64,[A-Za-z0-9+/=\s]+\)', '[IMAGE]', out,
        )
        # 裸 base64
        out = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[IMAGE]', out)
        return out

    def _truncate_and_store(
        self, content: str, url: str, char_limit: int,
    ) -> tuple[str, str, Optional[str]]:
        """head+tail 截断 + 全文写盘.返回 (head, tail, stored_path)."""
        head_budget = int(char_limit * 0.75)
        tail_budget = char_limit - head_budget

        head = content[:head_budget]
        tail = content[-tail_budget:]
        # head 切到最后一个换行
        nl = head.rfind('\n')
        if nl > head_budget * 0.5:
            head = head[:nl]
        # tail 切到下一个换行
        nl = tail.find('\n')
        if 0 <= nl < tail_budget * 0.5:
            tail = tail[nl + 1:]

        # 写全文
        stored_path = None
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            host = (urlparse(url).hostname or 'page').replace(':', '_')
            slug = re.sub(r'[^A-Za-z0-9._-]', '-', host)[:60].strip('-') or 'page'
            digest = hashlib.sha256(url.encode('utf-8')).hexdigest()[:10]
            path = CACHE_DIR / f'{slug}-{digest}.md'
            to_store = content
            if len(to_store) > MAX_STORED_CHARS:
                to_store = (
                    to_store[:MAX_STORED_CHARS]
                    + f"\n\n[... stored copy truncated at {MAX_STORED_CHARS:,} chars "
                    f"of {len(content):,}; re-extract a more specific URL for the rest ...]"
                )
            path.write_text(to_store, encoding='utf-8')
            stored_path = str(path.resolve())
        except Exception as exc:
            self._logger.warning('web_fetch: 存全文失败: %s', exc)

        return head, tail, stored_path
