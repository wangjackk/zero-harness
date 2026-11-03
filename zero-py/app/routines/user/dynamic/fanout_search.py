"""fanout_search ---- 并行多 query 搜索扇出 + 去重聚类 (编排原语).

为什么要它: web_search 一次只吃一个 query, 而真实的调研需求是 "一个问题 ->
多条不同角度的 query -> 合并去重 -> 交叉验证哪个结果被多路命中". 手工串行
调三次既慢又容易漏. 本 routine 把 "扇出 (fan-out) -> 汇聚 (fan-in)" 这个
最常用的编排形状固化成原语:

    queries: [q1, q2, ... qN]  --> asyncio.gather 并行 N 个 web_search
                               --> 按 url 去重, 记录被几条 query 命中 (hits)
                               --> 按 hits 降序 + 原始次序排序, 截断 top_n

hits >= 2 的条目意味着不同角度的 query 都指向它, 是天然的可信度信号,
省掉人工比对的活。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine


class FanoutSearchInput(BaseModel):
    queries: List[str] = Field(min_length=1, max_length=10,
                               description='并行搜索的 query 列表, 每个 query 独立扇出')
    count: int = Field(default=8, ge=1, le=20, description='每个 query 取多少条结果')
    time_range: Optional[str] = Field(default=None,
                                      description='时间范围: OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD')
    need_content: bool = Field(default=True, description='仅保留有正文的结果 (大模型场景建议 true)')
    top_n: int = Field(default=20, ge=1, le=60, description='汇聚后返回的条目上限')
    snippet: int = Field(default=240, ge=0, le=2000, description='每条正文摘要截断长度')


class FanoutSearch(Routine):
    name = 'fanout_search'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '并行扇出多个 web_search query, 按 url 去重并统计交叉命中次数 (hits), 返回聚合排序结果',
        'input_schema': FanoutSearchInput.model_json_schema(),
    }

    async def _one(self, query: str, inp: FanoutSearchInput) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            'query': query,
            'count': inp.count,
            'need_content': inp.need_content,
        }
        if inp.time_range:
            kwargs['time_range'] = inp.time_range
        handle = await self.create('web_search', kwargs)
        await handle.start()
        res = await asyncio.wait_for(handle, timeout=90)
        return {'query': query, 'ok': bool(res.get('ok')), 'results': res.get('results') or []}

    async def run(self, kwargs: Dict[str, Any]):
        inp = FanoutSearchInput.model_validate(kwargs)
        t0 = time.perf_counter()

        fanout = await asyncio.gather(
            *[self._one(q, inp) for q in inp.queries], return_exceptions=True)

        merged: Dict[str, Dict[str, Any]] = {}
        order = 0
        per_query: List[Dict[str, Any]] = []
        for raw in fanout:
            if isinstance(raw, BaseException):
                per_query.append({'query': None, 'ok': False, 'error': str(raw)[:200]})
                continue
            per_query.append({'query': raw['query'], 'ok': raw['ok'],
                              'n': len(raw['results'])})
            for it in raw['results']:
                url = (it.get('url') or '').strip()
                if not url:
                    continue
                cur = merged.get(url)
                if cur is None:
                    merged[url] = {
                        'title': it.get('title') or '',
                        'url': url,
                        'text': (it.get('content') or it.get('summary') or ''),
                        'hits': 1,
                        'queries': [raw['query']],
                        'order': order,
                    }
                    order += 1
                else:
                    cur['hits'] += 1
                    cur['queries'].append(raw['query'])

        items = sorted(merged.values(), key=lambda x: (-x['hits'], x['order']))[: inp.top_n]
        out = []
        for x in items:
            out.append({
                'title': x['title'],
                'url': x['url'],
                'hits': x['hits'],
                'queries': x['queries'],
                'snippet': x['text'][: inp.snippet] if inp.snippet else '',
            })

        return {
            'ok': True,
            'n_queries': len(inp.queries),
            'n_raw': sum(len(v['results']) for v in fanout if not isinstance(v, BaseException)),
            'n_unique': len(merged),
            'n_cross_hit': sum(1 for x in out if x['hits'] >= 2),
            'elapsed_s': round(time.perf_counter() - t0, 2),
            'per_query': per_query,
            'items': out,
        }
