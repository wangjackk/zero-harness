"""DagApproval -- 人工审批 routine(human-in-the-loop).

approval 在本框架里**不是特殊的 DAG 节点类型**----按 OQ-2 的结论,它只是
一个"等人类回复的慢 routine".所以它就是一个普通注册 routine,在 DAG 里
当普通节点用即可,模板替换 / retry / trigger_rule 全部自动复用:

    - id: review
      routine: dag_approval
      inputs:
        message: "请审批:$summarize.output"
        capture_response: true
        on_reject_prompt: "请说明意见"
        max_attempts: 3
      depends_on: [summarize]

支持:
- 消息展示 + Approve / Reject 两键
- capture_response=true → 允许附带自由文本(复用 Ask 的 allow_other)
- on_reject_prompt + max_attempts → 拒绝后重新提示,最多 N 次

UI 交互**不自己碰 ui_proxy**,而是 call 已有的 ``ask`` routine(万物皆
routine).``ask`` 会把超时 / 错误 raise 上来,``call``(create→start→wait)
把子 routine 的异常转成 RuntimeError 抛出----审批因此天然 **fail-closed**:
超时绝不会被误判成有效答复(尤其 capture_response=True 时不会 fail-open).

返回值:
- 用户点 Approve → 返回 'approved'(或自由文本,当 capture_response=True)
- max_attempts 耗尽仍被拒绝 → raise RuntimeError(节点状态=failed,可被下游 trigger_rule 处理)
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

_APPROVE = 'approve'
_REJECT = 'reject'


class DagApprovalInput(BaseModel):
    message: str = Field(description='展示给用户的审批说明(支持 $nodeId.output 已在调用前替换)')
    capture_response: bool = Field(
        default=False,
        description='True 时允许用户附带文字说明,返回值为用户输入文本而非 approved',
    )
    on_reject_prompt: str = Field(default='', description='拒绝后的追问文本;空则拒绝即 fail')
    max_attempts: int = Field(default=1, description='最多允许拒绝+追问几次(不含首次)')
    timeout: float = Field(default=300, description='每次等待超时秒数(默认 5 分钟)')


class DagApprovalOutput(BaseModel):
    text: str = Field(description="审批结果:approved 或用户附带的自由文本(capture_response=True 时)")


class DagApproval(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': 'DAG 人工审批节点:暂停工作流等待人工 approve / reject,fail-closed.',
        'input_schema': DagApprovalInput.model_json_schema(),
        'output_schema': DagApprovalOutput.model_json_schema(),
    }
    """
    DAG 审批节点:暂停工作流,等待人工审批.

    message:          展示给用户的审批说明(支持 $nodeId.output 已在调用前替换)
    capture_response: True 时允许用户附带文字说明,返回值为用户输入文本而非 'approved'
    on_reject_prompt: 拒绝后的追问文本;空则拒绝即 fail
    max_attempts:     最多允许拒绝+追问几次(不含首次)
    timeout:          每次等待超时秒数(默认 5 分钟)
    """

    async def run(
        self,
        kwargs: Dict[str, Any],
    ) -> str:
        message = kwargs['message']
        capture_response = kwargs.get('capture_response', False)
        on_reject_prompt = kwargs.get('on_reject_prompt', '')
        max_attempts = kwargs.get('max_attempts', 1)
        timeout = kwargs.get('timeout', 300)
        attempts = 0
        current_message = message

        while True:
            self._logger.info(
                'dag_approval: attempt=%d/%d message=%r capture=%s',
                attempts + 1, max_attempts + 1, current_message, capture_response,
            )

            # 复用 ask routine 做 UI;ask 失败(超时/出错)会让 call
            # 抛异常,审批据此 fail-closed.
            try:
                value = await self.call('ask', kwargs={
                    'question': current_message,
                    'props': {
                        'options': [_APPROVE, _REJECT],
                        'allow_other': capture_response,
                    },
                    'timeout': timeout,
                })
            except RuntimeError as e:
                self._logger.warning('dag_approval: ask failed (fail-closed): %s', e)
                raise RuntimeError(f'审批未完成:{e}') from e

            value = str(value)
            self._logger.info('dag_approval: user responded value=%r', value)

            # 用户批准
            if value == _APPROVE or (capture_response and value not in (_REJECT, '')):
                result = value if capture_response and value != _APPROVE else 'approved'
                self._logger.info('dag_approval: approved result=%r', result)
                return result

            # 用户拒绝
            if not on_reject_prompt or attempts >= max_attempts:
                reason = f'审批被拒绝(第 {attempts + 1} 次)'
                self._logger.warning('dag_approval: rejected -- %s', reason)
                raise RuntimeError(reason)

            # 还有追问机会
            attempts += 1
            current_message = on_reject_prompt
            self._logger.info('dag_approval: rejected, re-prompting (attempt %d/%d)', attempts, max_attempts)
