"""deep_research ---- 一个问题进, 一份带引用的研究报告出.

为什么写它: 系统里已有 web_search / fanout_search / web_fetch / mini_agent 这些
原子, 但真实调研是 "拆角度 -> 多路搜 -> 精读原文 -> 交叉印证 -> 成文带引用" 的
固定流水线. 手工串一次要调七八个工具、贴好几轮正文, 每次都要重来. 本 routine
把这条流水线固化成一次调用:

    question
      -> [规划] LLM 把问题拆成 3~5 个互补搜索角度 (也可由 angles 手工指定)
      -> [扇出] fanout_search 并行多 query, 按 url 去重 + 交叉命中计数
      -> [精读] 对 top_n 个来源并发 web_fetch 抓全文 (失败回落到搜索摘要)
      -> [成文] LLM 基于原文写 markdown 报告, 逐条带 [n] 引用
      -> [落盘] 报告写 runtime/research/<ts>-<slug>.md

出参同时给 report (正文) / report_path (文件) / sources (编号来源表), 便于
下游 agent 直接引用或继续加工. LLM 不可用时降级为 "摘要汇编", 不整体失败.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine

__all__ = ['DeepResearch']


class DeepResearchInput(BaseModel):
    question: str = Field(description='要调研的问题或主题, 一句话说清')
    angles: List[str] = Field(default_factory=list, max_length=6,
                              description='手工指定搜索角度 (每条一个 query); 空 = 由 LLM 自动拆解')
    count: int = Field(default=8, ge=1, le=20, description='每个角度取多少条搜索结果')
    top_n: int = Field(default=6, ge=1, le=12, description='精读并引用的来源数量上限')
    fetch_chars: int = Field(default=6000, ge=500, le=20000,
                             description='单个来源抓取的正文字符上限')
    time_range: Optional[str] = Field(
        default=None,
        description='时间范围: OneDay/OneWeek/OneMonth/OneYear 或 YYYY-MM-DD..YYYY-MM-DD')
    model: Optional[str] = Field(default=None,
                                 description="成文用的模型 key (如 'deepseek/deepseek-flash'); 空 = 系统默认")
    save: bool = Field(default=True, description='是否把报告落盘到 out_dir')
    out_dir: str = Field(default='runtime/research', description='报告落盘目录 (相对项目根 / 绝对路径)')


class DeepResearch(Routine):
    name = 'deep_research'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '深度调研: 自动拆搜索角度 -> 多路并行搜索 -> 并发精读原文 -> 输出带 [n] 引用的 markdown 报告并落盘',
        'input_schema': DeepResearchInput.model_json_schema(),
    }

    _PLAN_SYS = (
        '你是研究规划员。把用户的问题拆成互补的搜索角度, 每个角度写成一条可以直接'
        '丢给搜索引擎的 query (简短、含关键实体、避免 "最新""怎么样" 这类口水词)。'
        '只输出一个 JSON 字符串数组, 不要任何解释、不要代码块标记。'
    )
    _WRITE_SYS = (
        '你是研究分析师。只根据给定的来源材料作答, 不得编造材料里没有的事实。'
        '写中文 markdown 报告, 结构: ## 结论 / ## 关键发现 / ## 细节与数据 / '
        '## 争议与不确定 / ## 来源。每一条事实性陈述后面用 [n] 标注来源编号, '
        'n 必须是给定来源的编号。材料不足就明说 "材料未覆盖", 不要猜。'
    )

    # ---------------------------------------------------------------- LLM
    async def _llm(self, user: str, instructions: str,
                   model: Optional[str], max_chars: int = 6000) -> str:
        from app.routines.user.agents._core.llm import Completed, LLMClient, TextDelta

        client = LLMClient(model)
        buf = ''
        async for ev in client.stream(
                [{'role': 'user', 'content': user}], instructions=instructions):
            if isinstance(ev, Completed):
                return (ev.text or buf).strip()
            if isinstance(ev, TextDelta):
                buf += ev.text
        return buf.strip()

    # ------------------------------------------------------------- 工具调用
    async def _call(self, name: str, kwargs: Dict[str, Any], timeout: float) -> Any:
        handle = await self.create(name, dict(kwargs))
        await handle.start()
        return await asyncio.wait_for(handle, timeout=timeout)

    # ----------------------------------------------------------------- 规划
    def _fallback_queries(self, question: str) -> List[str]:
        q = question.strip().rstrip('?？')
        return [q, f'{q} 最新进展', f'{q} 数据 报告']

    async def _plan(self, inp: DeepResearchInput) -> List[str]:
        if inp.angles:
            return [a.strip() for a in inp.angles if a.strip()][:6]
        try:
            raw = await asyncio.wait_for(
                self._llm(f'问题: {inp.question}\n请给出 3~5 个搜索角度。',
                          self._PLAN_SYS, inp.model),
                timeout=90)
        except Exception:
            return self._fallback_queries(inp.question)
        m = re.search(r'\[.*?\]', raw, re.S)
        picked: List[str] = []
        if m:
            try:
                arr = json.loads(m.group(0))
                if isinstance(arr, list):
                    picked = [str(x).strip() for x in arr if str(x).strip()]
            except Exception:
                picked = []
        if not picked:  # LLM 没给 JSON: 按行捡最像 query 的几条
            picked = [ln.strip(' -•*0123456789.、') for ln in raw.splitlines() if ln.strip()][:5]
        out = list(dict.fromkeys(picked))[:5]
        return out or self._fallback_queries(inp.question)

    # ----------------------------------------------------------------- 精读
    async def _read_one(self, item: Dict[str, Any], chars: int) -> Dict[str, Any]:
        url = item['url']
        text = item.get('snippet') or ''
        how = 'search_snippet'
        err = ''
        # markdown 优先; 失败回落纯文本 (部分站点 markdown 抽取器会崩)
        for fmt in ('markdown', 'text'):
            try:
                res = await self._call(
                    'web_fetch', {'url': url, 'char_limit': chars, 'format': fmt}, 60)
                body = (res or {}).get('content') if isinstance(res, dict) else None
                if not body and isinstance(res, dict):
                    body = res.get('text')
                if body and len(body) > len(text):
                    text, how = body, 'fetched'
                    break
            except Exception as exc:
                err = type(exc).__name__
        else:
            how = f'fetch_failed: {err}' if err else how
        return {**item, 'text': text, 'how': how, 'garbled': self._garbled(text)}

    @staticmethod
    def _garbled(text: str) -> bool:
        """粗判正文是否为乱码 (PDF 乱码 / 反爬占位): 可读字符占比过低."""
        if len(text) < 200:
            return False
        readable = sum(1 for c in text
                       if c.isalnum() or '\u4e00' <= c <= '\u9fff' or c in ' \n.,;:()[]%/-_')
        return readable / len(text) < 0.6

    def _materials(self, sources: List[Dict[str, Any]], budget: int = 42000) -> str:
        parts, used = [], 0
        for s in sources:
            flag = ' (注意: 正文疑似乱码, 只能谨慎引用, 无法核实的信息请说明)' if s.get('garbled') else ''
            block = f"[{s['n']}] {s['title']}{flag}\nURL: {s['url']}\n{s['text']}".strip()
            if used + len(block) > budget:
                block = block[: max(0, budget - used)]
            if not block:
                break
            parts.append(block)
            used += len(block)
        return '\n\n---\n\n'.join(parts)

    # --------------------------------------------------------------- 主流程
    async def run(self, kwargs: Dict[str, Any]):
        inp = DeepResearchInput.model_validate(kwargs)
        t0 = time.perf_counter()
        trace: List[Dict[str, Any]] = []

        # 1. 规划
        queries = await self._plan(inp)
        trace.append({'step': 'plan', 'queries': queries})

        # 2. 扇出搜索
        fan = await self._call('fanout_search', {
            'queries': queries,
            'count': inp.count,
            'need_content': True,
            'top_n': max(inp.top_n * 3, 15),
            'snippet': 1200,
            **({'time_range': inp.time_range} if inp.time_range else {}),
        }, timeout=180)
        hits = (fan or {}).get('items') or (fan or {}).get('results') or []
        trace.append({'step': 'search', 'n_hits': len(hits), 'n_queries': len(queries)})
        if not hits:
            return {'ok': False, 'reason': 'search_returned_nothing',
                    'question': inp.question, 'queries': queries, 'trace': trace}

        # hits 多的排前面 (交叉印证信号), 再按原序
        picked = hits[: inp.top_n]
        picked = [{**h, 'n': i + 1} for i, h in enumerate(picked)]

        # 3. 并发精读
        sources = await asyncio.gather(
            *[self._read_one(s, inp.fetch_chars) for s in picked],
            return_exceptions=True)
        sources = [s for s in sources if isinstance(s, dict)]
        trace.append({'step': 'read',
                      'fetched': sum(1 for s in sources if s['how'] == 'fetched'),
                      'total': len(sources)})

        # 4. 成文
        materials = self._materials(sources)
        task = (f'问题: {inp.question}\n\n'
                f'来源材料 (共 {len(sources)} 条, 编号即引用号):\n\n{materials}\n\n'
                '请写报告。')
        try:
            report = await asyncio.wait_for(
                self._llm(task, self._WRITE_SYS, inp.model, max_chars=8000), timeout=240)
            mode = 'llm'
        except Exception as exc:
            mode = f'degraded: {type(exc).__name__}'
            report = self._digest(inp.question, sources)
        trace.append({'step': 'write', 'mode': mode, 'chars': len(report)})

        # 5. 落盘
        report_path = None
        if inp.save and report:
            try:
                out = Path(inp.out_dir)
                if not out.is_absolute():
                    out = Path.cwd() / out
                out.mkdir(parents=True, exist_ok=True)
                slug = re.sub(r'[^\w\u4e00-\u9fff]+', '-', inp.question)[:40].strip('-') or 'report'
                fp = out / f'{datetime.now():%Y%m%d-%H%M%S}-{slug}.md'
                head = (f'# {inp.question}\n\n> 搜索角度: ' + ' / '.join(queries) +
                        f'\n> 生成时间: {datetime.now():%Y-%m-%d %H:%M}\n\n')
                fp.write_text(head + report + '\n', encoding='utf-8')
                report_path = str(fp)
            except Exception as exc:
                trace.append({'step': 'save', 'error': f'{type(exc).__name__}: {exc}'})

        return {
            'ok': True,
            'question': inp.question,
            'queries': queries,
            'mode': mode,
            'report': report,
            'report_path': report_path,
            'sources': [{'n': s['n'], 'title': s['title'], 'url': s['url'],
                         'hits': s.get('hits'), 'how': s['how'], 'garbled': s.get('garbled', False),
                         'chars': len(s['text'])} for s in sources],
            'elapsed_s': round(time.perf_counter() - t0, 1),
            'trace': trace,
        }

    def _digest(self, question: str, sources: List[Dict[str, Any]]) -> str:
        lines = ['## 摘要汇编 (LLM 不可用, 降级输出)', '']
        for s in sources:
            lines.append(f"### [{s['n']}] {s['title']}")
            lines.append(f"<{s['url']}>")
            lines.append(s['text'][:1200])
            lines.append('')
        return '\n'.join(lines)
