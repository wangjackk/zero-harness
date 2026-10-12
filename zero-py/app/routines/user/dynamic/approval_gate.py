"""approval_gate ---- 危险操作的人类审批闸门 (human-in-the-loop).

问题: "执行危险操作前先问人类" 如果只写在提示词里 (BashTool prompt 现在就是
'Non-readonly commands require user approval'), 那就是自觉 ---- agent 想绕过
随时能绕过 (它手里还有 ipython / send_message / ssh 这些万能通道)。

本 routine 把这件事做成**可执行的闸门**: 危险动作先交这里, 这里阻塞问人类,
人类点头才真开火; 拒绝 / 超时 / 问不到人 => fail-closed (不执行)。

    approval_gate(action='删除 C 盘临时目录', target='ipython',
                  kwargs={'code': '...'}, danger='high')

三层语义:
  - low    : 自动放行 (记台账), 不打扰人类
  - medium/high : 必须问人类, 除非本会话内已被 "记住"
  - 问不到人(超时/异常) : 拒绝, 绝不默认放行

台账: runtime/approval/journal.jsonl ---- 每次决策一行 (谁批的/何时/批的什么),
是事后审计的唯一事实源, 与执行结果一并落盘。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar, Dict, Literal, Optional

from pydantic import BaseModel, Field

from routine import Routine

__all__ = ['ApprovalGate']

_RUNTIME = Path(__file__).resolve().parents[4] / 'runtime' / 'approval'
_JOURNAL = _RUNTIME / 'journal.jsonl'

_YES = '批准执行'
_NO = '拒绝'
_REMEMBER = '本次会话内同类免问'


class ApprovalGateInput(BaseModel):
    action: str = Field(description='人类可读的动作描述 (展示给人看的那句话)')
    target: Optional[str] = Field(default=None,
                                  description='批准后要执行的 routine 名; 空 = 只审批不执行')
    kwargs: Dict[str, Any] = Field(default_factory=dict, description='target 的入参')
    danger: Literal['low', 'medium', 'high'] = Field(
        default='high', description='危险等级: low 自动放行, medium/high 必须人类点头')
    detail: str = Field(default='', description='额外细节 (命令全文 / 影响面 / 不可逆点), 一并展示给人')
    timeout: float = Field(default=120.0, ge=5.0, le=1800.0, description='等人类回答的秒数, 超时=拒绝')
    dry_run: bool = Field(default=False, description='只走审批流程, 批准也不执行 (自验用)')


class ApprovalGate(Routine):
    name = 'approval_gate'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '危险操作人类审批闸门: 阻塞问人类, 批准才调 target routine; 拒绝/超时 fail-closed; 全程记台账',
        'input_schema': ApprovalGateInput.model_json_schema(),
    }

    def __init__(self) -> None:
        super().__init__()
        # 状态在实例上 (模块级状态 reload 即丢, 见 routine-creator 硬规则)
        self._remembered: set = set()

    # ---- 台账 ----

    def _log(self, row: Dict[str, Any]) -> None:
        _RUNTIME.mkdir(parents=True, exist_ok=True)
        with _JOURNAL.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')

    async def _invoke(self, name: str, kwargs: Dict[str, Any], timeout: float) -> Any:
        handle = await self.submit(name, dict(kwargs))
        await handle.start()
        return await asyncio.wait_for(handle, timeout=timeout)

    # ---- 主流程 ----

    async def run(self, kwargs: Dict[str, Any]):
        inp = ApprovalGateInput.model_validate(kwargs)
        t0 = time.perf_counter()
        ticket = f'AP-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:4]}'

        row: Dict[str, Any] = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'ticket': ticket,
            'action': inp.action, 'detail': inp.detail, 'danger': inp.danger,
            'target': inp.target, 'target_kwargs': inp.kwargs,
        }

        # 1. 低危直通 (不打扰人类, 但留痕)
        if inp.danger == 'low':
            row.update(decision='auto_allow', reason='danger=low', by='policy')
            result = None if (inp.dry_run or not inp.target) else \
                await self._invoke(inp.target, inp.kwargs, inp.timeout)
            row['executed'] = result is not None
            self._log(row)
            return {'approved': True, 'decision': 'auto_allow', 'ticket': ticket,
                    'action': inp.action, 'result': result,
                    'elapsed_s': round(time.perf_counter() - t0, 2)}

        # 2. 本会话内人类已经免问过同类动作
        if inp.action in self._remembered:
            row.update(decision='remembered_allow', reason='本次会话内已批准同类', by='human(earlier)')
            result = None if (inp.dry_run or not inp.target) else \
                await self._invoke(inp.target, inp.kwargs, inp.timeout)
            row['executed'] = result is not None
            self._log(row)
            return {'approved': True, 'decision': 'remembered_allow', 'ticket': ticket,
                    'action': inp.action, 'result': result,
                    'elapsed_s': round(time.perf_counter() - t0, 2)}

        # 3. 问人类 (阻塞)
        question = f'⚠️ 需要你授权: {inp.action}' + (f'\n细节: {inp.detail}' if inp.detail else '')
        answer, err = None, None
        try:
            answer = await self._invoke('ask', {
                'question': question,
                'props': {'options': [_YES, _NO, _REMEMBER], 'allow_other': True},
                'timeout': inp.timeout,
            }, timeout=inp.timeout + 15.0)
        except Exception as exc:                       # 问不到人 => 失败关闭
            err = f'{type(exc).__name__}: {exc}'

        answer = (answer or '').strip() if isinstance(answer, str) else str(answer or '')
        approved = answer in (_YES, _REMEMBER)
        if err:
            decision, reason = 'deny_no_human', f'问不到人类 ({err}) -> fail-closed'
        elif approved:
            decision, reason = ('allow_remember' if answer == _REMEMBER else 'allow'), f'人类选择: {answer}'
            if answer == _REMEMBER:
                self._remembered.add(inp.action)
        else:
            decision, reason = 'deny', f'人类选择: {answer or "(空)"} -> fail-closed'

        row.update(decision=decision, reason=reason, answer=answer, by='human',
                   error=err, waited_s=round(time.perf_counter() - t0, 2))

        result = None
        if approved and not inp.dry_run and inp.target:
            try:
                result = await self._invoke(inp.target, inp.kwargs, inp.timeout)
                row['executed'], row['exec_error'] = True, None
            except Exception as exc:
                row['executed'], row['exec_error'] = False, f'{type(exc).__name__}: {exc}'
        else:
            row['executed'] = False
        self._log(row)

        return {
            'approved': approved, 'decision': decision, 'reason': reason,
            'ticket': ticket, 'action': inp.action, 'answer': answer,
            'result': result, 'error': err,
            'elapsed_s': round(time.perf_counter() - t0, 2),
        }
