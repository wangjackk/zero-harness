"""WebSearch ---- 豆包搜索(联网搜索)tool.

调火山引擎豆包搜索 Custom 版 API,返回结构化网页/图片搜索结果.用于补充
LLM 训练数据截止后的最新事实、核验信息出处、获取时效性内容.

API 文档: https://www.volcengine.com/docs/87772/2272953
鉴权: Agent Plan 专属 API Key(Authorization: Bearer <key>),跟豆包 LLM 共用.

跟 FetchUrl 配对使用:WebSearch 拿 URL 列表 → FetchUrl 抓完整正文.
"""
from __future__ import annotations

import os
from typing import Any, ClassVar, Dict, List, Literal, Optional

import httpx
from pydantic import BaseModel, Field
from routine import Routine

from .prompt import DESCRIPTION

# Agent Plan API Key(跟豆包 LLM 共用同一个),从环境变量 SEED_API_KEY 读取
# (.env 加载于 main.py 启动早期, 早于 routine import, run 时直接读即可).
_SEARCH_URL = 'https://open.feedcoopapi.com/search_api/web_search'
_REQ_TIMEOUT = 15.0


class WebSearchInput(BaseModel):
    query: str = Field(description='搜索关键词,1~100 字符(过长会截断)')
    search_type: Literal['web', 'image'] = Field(
        default='web',
        description='搜索类型:web(网页,默认)/ image(图片)',
    )
    count: int = Field(
        default=10,
        ge=1,
        le=50,
        description='返回结果条数,web 最多 50,image 最多 5',
    )
    time_range: Optional[str] = Field(
        default=None,
        description='时间范围:OneDay/OneWeek/OneMonth/OneYear,'
                    '或 YYYY-MM-DD..YYYY-MM-DD',
    )
    sites: Optional[str] = Field(
        default=None,
        description='限定搜索站点,| 分隔,如 "aliyun.com|mp.qq.com"',
    )
    block_hosts: Optional[str] = Field(
        default=None,
        description='屏蔽站点,| 分隔',
    )
    need_content: bool = Field(
        default=True,
        description='仅返回有正文的结果(大模型场景建议 true)',
    )
    content_format: Literal['text', 'markdown'] = Field(
        default='markdown',
        description='正文格式:text / markdown(默认)',
    )
    query_rewrite: bool = Field(
        default=False,
        description='开启 Query 改写(口语化长问题改写为搜索式 query,会增加耗时)',
    )
    auth_only: bool = Field(
        default=False,
        description='仅返回非常权威来源的内容(结果会减少)',
    )


class WebSearchOutput(BaseModel):
    ok: bool = Field(description='搜索是否成功')
    query: str = Field(description='实际搜索的关键词')
    search_type: str = Field(description='搜索类型(web/image)')
    count: int = Field(description='返回结果条数')
    results: List[Dict[str, Any]] = Field(
        default_factory=list,
        description='搜索结果列表,每项含 title/url/snippet/summary/content 等',
    )
    log_id: Optional[str] = Field(default=None, description='请求追踪 ID')
    error: Optional[str] = Field(default=None, description='失败时的错误信息')


class WebSearch(Routine):
    """Search the web via Doubao Search API. Returns structured results.

    Pair with FetchUrl: WebSearch to find URLs, then FetchUrl to read full
    page content.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'concurrency_safe': True,
        'input_schema': WebSearchInput.model_json_schema(),
        'output_schema': WebSearchOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = WebSearchInput(**kwargs)

        api_key = os.environ.get('SEED_API_KEY', '')
        if not api_key:
            return {'ok': False,
                    'error': 'SEED_API_KEY not configured (zero/.env)'}
        payload = self._build_payload(inp)

        self._logger.info('web_search: query=%r type=%s count=%d',
                          inp.query, inp.search_type, inp.count)

        async with httpx.AsyncClient(timeout=_REQ_TIMEOUT) as client:
            resp = await client.post(
                _SEARCH_URL,
                json=payload,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                },
            )

        if resp.status_code != 200:
            self._logger.error('web_search HTTP %s: %s',
                               resp.status_code, resp.text[:500])
            return {
                'ok': False,
                'error': f'HTTP {resp.status_code}',
                'detail': resp.text[:500],
            }

        data = resp.json()

        # 检查 API 层错误
        meta = data.get('ResponseMetadata') or {}
        err = meta.get('Error')
        if err:
            self._logger.error('web_search API error: %s', err)
            return {
                'ok': False,
                'error': str(err.get('Message') or err.get('Code') or err),
                'request_id': meta.get('RequestId'),
            }

        result = data.get('Result') or {}
        if inp.search_type == 'image':
            items = self._extract_image_results(result)
        else:
            items = self._extract_web_results(result)

        self._logger.info('web_search: got %d results (count=%d, cost=%sms)',
                          len(items), result.get('ResultCount', 0),
                          result.get('TimeCost', '?'))

        return {
            'ok': True,
            'query': inp.query,
            'search_type': inp.search_type,
            'count': len(items),
            'results': items,
            'log_id': result.get('LogId'),
        }

    def _build_payload(self, inp: WebSearchInput) -> Dict[str, Any]:
        """构造 API 请求 body."""
        payload: Dict[str, Any] = {
            'Query': inp.query,
            'SearchType': inp.search_type,
            'Count': inp.count,
        }

        filt: Dict[str, Any] = {}
        if inp.need_content:
            filt['NeedContent'] = True
        if inp.sites:
            filt['Sites'] = inp.sites
        if inp.block_hosts:
            filt['BlockHosts'] = inp.block_hosts
        if inp.auth_only:
            filt['AuthInfoLevel'] = 1
        if filt:
            payload['Filter'] = filt

        if inp.time_range:
            payload['TimeRange'] = inp.time_range

        payload['QueryControl'] = {'QueryRewrite': inp.query_rewrite}

        if inp.search_type == 'web':
            payload['ContentFormats'] = inp.content_format

        return payload

    def _extract_web_results(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """提取 web 搜索结果,只保留 agent 有用的字段."""
        out = []
        for item in (result.get('WebResults') or []):
            out.append({
                'title': item.get('Title', ''),
                'url': item.get('Url', ''),
                'site': item.get('SiteName', ''),
                'snippet': item.get('Snippet', ''),
                'summary': item.get('Summary', ''),
                'content': item.get('Content', ''),
                'publish_time': item.get('PublishTime', ''),
                'auth_level': item.get('AuthInfoDes', ''),
                'content_format': item.get('ContentFormats', ''),
            })
        return out

    def _extract_image_results(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """提取 image 搜索结果.图片信息嵌套在 Image 子对象里."""
        out = []
        for item in (result.get('ImageResults') or []):
            img = item.get('Image') or {}
            out.append({
                'title': item.get('Title', ''),
                'url': item.get('Url', ''),
                'image_url': img.get('Url', ''),
                'width': img.get('Width'),
                'height': img.get('Height'),
                'shape': img.get('Shape', ''),
                'site': item.get('SiteName', ''),
            })
        return out
