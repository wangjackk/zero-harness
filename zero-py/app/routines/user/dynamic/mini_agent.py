"""mini_agent ---- 一个我自己写的、可以被调用的轻量子 agent.

为什么写它: pi / xml 引擎 agent 是重装部队 (会话持久化 / 上下文压缩 / 多 provider
/ 子 agent 编排), 但大量子任务只需要 "一个带人设的脑子 + 顺手调几个 routine"。
本 routine 就是那个最小可调用单元:

    task + role  -->  LLM 循环  --> 需要时真调系统里任意 routine  -->  answer

工具协议 (写在系统提示里, 模型按此输出):

    ```tool
    {"name": "sysinfo", "kwargs": {"top": 3}}
    ```

出现 tool 块 => 执行, 把结果作为 user 消息喂回去继续; 没有 tool 块 => 那段
文本就是最终答案。白名单外 / 解析失败 => 当作答案收尾 (不空转)。

出参: {answer, steps, model, elapsed_s, turns}
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field

from routine import Routine

__all__ = ['MiniAgent']


class MiniAgentInput(BaseModel):
    task: str = Field(description='交给这个子 agent 的任务')
    role: str = Field(default='通用助手', description='人设 / 角色设定')
    model: Optional[str] = Field(default=None,
                                 description="模型 key (如 'deepseek/deepseek-flash'); 空 = 系统默认")
    tools: List[str] = Field(default_factory=list,
                             description='允许它调用的 routine 名白名单; 空 = 不能调工具, 纯回答')
    max_steps: int = Field(default=4, ge=1, le=8, description='工具轮数上限')
    timeout_s: float = Field(default=90.0, ge=5.0, le=300.0, description='单次 routine 调用超时')


class MiniAgent(Routine):
    name = 'mini_agent'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '轻量子 agent: 给 task+role, 它自己驱动 LLM 循环, 需要时真调白名单里的 routine, 同步返回答案',
        'input_schema': MiniAgentInput.model_json_schema(),
    }

    _TOOL_RE = re.compile(r'```tool\s*(\{.*?\})\s*```', re.S)

    async def _llm(self, messages: List[Dict[str, Any]], instructions: str,
                   model: Optional[str]) -> str:
        from app.routines.user.agents._core.llm import Completed, LLMClient, TextDelta

        client = LLMClient(model)
        text = ''
        async for ev in client.stream(messages, instructions=instructions):
            if isinstance(ev, Completed):
                return (ev.text or text).strip()
            if isinstance(ev, TextDelta):
                text += ev.text
        return text.strip()

    def _system(self, role: str, tools: List[str]) -> str:
        lines = [
            f'你是 {role}。',
            '你运行在 zero 平台上, 可以通过工具调用本机 routine 来观察世界 / 执行动作。',
            '回答要直接、稠密、不要客套话。',
        ]
        if tools:
            lines += [
                '',
                '可用工具 (只能从下列名字里选):',
                *[f'- {t}' for t in tools],
                '',
                '需要工具时, 只输出一个代码块, 不要带别的解释:',
                '```tool',
                '{"name": "<工具名>", "kwargs": {...}}',
                '```',
                '拿到工具结果后继续思考; 已经能回答时, 直接输出最终答案 (不要带 tool 块)。',
            ]
        else:
            lines += ['', '你没有工具, 直接凭推理作答。']
        return '\n'.join(lines)

    async def _call_tool(self, name: str, kwargs: Dict[str, Any], timeout: float) -> Any:
        handle = await self.create(name, dict(kwargs))
        await handle.start()
        return await asyncio.wait_for(handle, timeout=timeout)

    async def run(self, kwargs: Dict[str, Any]):
        inp = MiniAgentInput.model_validate(kwargs)
        t0 = time.perf_counter()
        tools = list(dict.fromkeys(inp.tools))
        system = self._system(inp.role, tools)

        messages: List[Dict[str, Any]] = [{'role': 'user', 'content': inp.task}]
        steps: List[Dict[str, Any]] = []
        answer = ''
        model_key = None

        for turn in range(1, inp.max_steps + 1):
            text = await self._llm(messages, system, inp.model)
            answer = text or answer
            m = self._TOOL_RE.search(text)
            if not m:
                break
            messages.append({'role': 'assistant', 'content': text})
            try:
                call = json.loads(m.group(1))
                name = str(call.get('name') or '')
                tkwargs = call.get('kwargs') or {}
            except Exception as exc:
                messages.append({'role': 'user',
                                 'content': f'工具调用解析失败: {exc}. 请输出合法 JSON 或直接给最终答案。'})
                continue

            if name not in tools:
                messages.append({'role': 'user',
                                 'content': f'工具 {name!r} 不在白名单里, 不许调用。请直接给最终答案。'})
                steps.append({'n': turn, 'tool': name, 'rejected': 'not_in_whitelist'})
                continue

            step: Dict[str, Any] = {'n': turn, 'tool': name, 'kwargs': tkwargs}
            try:
                result = await self._call_tool(name, tkwargs, inp.timeout_s)
                step['ok'] = True
                step['result'] = result
            except Exception as exc:
                result = {'error': f'{type(exc).__name__}: {exc}'}
                step['ok'] = False
                step['result'] = result
            steps.append(step)
            messages.append({'role': 'user',
                             'content': '工具结果:\n' + json.dumps(result, ensure_ascii=False, default=str)[:4000]
                                        + '\n请据此继续: 要么再调一个工具, 要么直接给最终答案。'})

        if not answer and steps:
            answer = f'(步数用尽, 最后一步结果: {json.dumps(steps[-1].get("result"), ensure_ascii=False, default=str)[:800]})'

        try:
            from app.routines.user.agents._core.llm import LLMClient
            model_key = LLMClient(inp.model).model_key
        except Exception:
            model_key = inp.model

        return {
            'answer': answer,
            'steps': steps,
            'turns': len(steps) + 1,
            'model': model_key,
            'elapsed_s': round(time.perf_counter() - t0, 2),
        }
