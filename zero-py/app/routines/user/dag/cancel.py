"""DagCancel -- 终止整个 DAG 的控制流 routine.

cancel 在本框架里**不是特殊节点类型**,而是一个普通注册 routine.它跑起来
就返回一个 cancel 哨兵(result 里带 DAG_CANCEL_KEY);父 RunDag 在它本来就
在消费的 done 循环里识别这个哨兵,终止整棵 DAG.

这正是"子 routine 发消息给父 routine"----子实例 ``lifecycle.stopped`` 带回的
``result`` 就是子→父的消息投递,result 通道本身就是消息载体,不需要额外的
send/event 通道.

在 DAG 里当普通节点用,reason 支持模板替换($nodeId.output / $WI.xxx):

    - id: abort
      routine: dag_cancel
      inputs:
        reason: "分类失败:$classify.output"
      depends_on: [classify]
      when: "$classify.output == 'error'"
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from .spec import DAG_CANCEL_KEY


class DagCancelInput(BaseModel):
    reason: str = Field(default='', description='终止原因(会冒泡到父 RunDag 抛出的异常里)')


class DagCancelOutput(BaseModel):
    cancel: bool = Field(description='固定 True,标记这是 cancel 哨兵')
    reason: str = Field(default='', description='终止原因')


class DagCancel(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '终止整个 DAG workflow 的控制流节点(普通 routine,靠返回值哨兵与父 RunDag 通信).',
        'input_schema': DagCancelInput.model_json_schema(),
        'output_schema': DagCancelOutput.model_json_schema(),
    }
    """终止整个 DAG workflow 的控制流节点.

    reason: 终止原因(会冒泡到父 RunDag 抛出的异常里)
    """

    async def run(self, kwargs: Dict[str, Any]) -> dict[str, Any]:
        reason = kwargs.get('reason', '')
        self._logger.warning('dag_cancel triggered: %s', reason)
        return {DAG_CANCEL_KEY: True, 'reason': reason}
