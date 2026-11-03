"""RunDag - DAG 编排 Routine.

注册一次,通过 ``dag`` 参数按名查找 YAML 文件执行:

    await self.call('run_dag', kwargs={'dag': 'hello_dag', 'inputs': '{"city_a":"广州"}'})

YAML 文件默认从 ``DAG_DIR``(本包的 ``dags/`` 子目录)按 ``{dag}.yaml`` 查找,
也可以传绝对/相对路径(含 .yaml 扩展名时当路径处理).

就绪集(ready-set)驱动:所有 depends_on 终态的节点立即并发 push,
不按拓扑层等待.

新框架适配:
- ``routine3`` -> ``routine``;``start(kwargs)`` -> ``run(kwargs)``(返回值成 result)
- 节点派发 ``self.push(routine, kwargs=...)`` -> ``create + start``(create 只建命令
  返回句柄,start 等 started ack 即返回,不等 done--节点执行并发,done 经
  ``on_stopped_handler`` 回流 done_q)
- ``handle.on_done = cb`` -> ``handle.on_stopped_handler = cb``(async callable(handle),
  notify_done 先 set result/error 再 fire,cb 里读 h.result/h.error 安全)
- ``h.extend_data`` 已删(新 SDK 无此字段)--output_format 直接按 node.output_format
  推断(json/text),不再读子 routine 声明的 format
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine, RoutineHandle
from routine.logger import setup_logger

from .executor import deps_all_terminal, resolve_kwargs, should_run
from .loader import load_dag_yaml_file
from .spec import DAG_CANCEL_KEY, DagNodeSpec, DagSpec, NodeOutput

logger = setup_logger('dag')

# DAG YAML 文件默认查找目录;可在启动时通过 RunDag.dag_dir = ... 覆盖
DAG_DIR: Path = Path(__file__).parent / 'dags'


class RunDagInput(BaseModel):
    dag: str = Field(description='DAG 名称(对应 dags/{dag}.yaml)或含扩展名的文件路径')
    inputs: str = Field(
        default='',
        description='DAG 入参 JSON 字符串,如 \'{"city_a":"南阳"}\'(有 default 时可不传)',
    )


class RunDagOutput(BaseModel):
    result: dict = Field(description='DAG 执行结果:按 outputs 别名或各节点 state/output/error 组装')


class RunDag(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '执行指定名称的 DAG workflow,按就绪集并发调度节点.',
        'input_schema': RunDagInput.model_json_schema(),
        'output_schema': RunDagOutput.model_json_schema(),
    }
    """执行指定名称的 DAG workflow.

    dag: DAG 名称(对应 dags/{dag}.yaml),或含扩展名的文件路径
    其余 inputs 作为 DAG 的外部输入,可在节点 inputs 里通过 $key 引用
    """

    dag_dir: Path = DAG_DIR

    def __init__(self) -> None:
        super().__init__()
        self._done_q: asyncio.Queue[tuple[DagNodeSpec, RoutineHandle]] = asyncio.Queue()

    async def run(self, kwargs: Dict[str, Any]) -> Any:
        dag = kwargs['dag']
        inputs = kwargs.get('inputs', '')
        # dag:    DAG 名称或 YAML 文件路径
        # inputs: DAG 入参 JSON 字符串,如 '{"city_a":"南阳"}'(有 default 时可不传)
        #
        # 注意:inputs 必须声明为 str(而非 dict).run_dag 既被前端 XML 调用,
        # 也被父 DAG 节点以 JSON 字符串透传,若声明成 dict,smartparams 会按字典
        # 校验,把 JSON 字符串整个丢弃 -> 子 DAG 收不到入参.这里统一收字符串,
        # 在内部 json.loads,是唯一对两种调用方都成立的契约.
        spec = self._load_spec(dag)
        parsed_inputs: dict[str, Any] = {}
        if inputs:
            try:
                parsed_inputs = json.loads(inputs)
            except json.JSONDecodeError as e:
                raise ValueError(f'inputs 不是合法 JSON: {e}') from e
        resolved_inputs = _validate_and_coerce_inputs(spec, parsed_inputs)
        return await self._run(spec, resolved_inputs)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _load_spec(self, dag: str) -> DagSpec:
        p = Path(dag)
        if p.suffix == '.yaml' or p.is_absolute():
            path = p
        else:
            path = self.__class__.dag_dir / f'{dag}.yaml'
        return load_dag_yaml_file(path)

    async def _run(self, dag: DagSpec, dag_inputs: dict[str, Any]) -> Any:
        run_id = self.ctx.id
        outputs: dict[str, NodeOutput] = {}
        running: set[str] = set()
        pending = {n.id for n in dag.nodes}
        # cancel 信号:(node_id, reason).由 done 循环识别 dag_cancel routine 返回
        # 的哨兵后写入--cancel 节点是普通 routine,走和其它节点完全相同的
        # push/done 路径,没有 dispatch 阶段特判.
        cancel_signal: list[tuple[str, str]] = []
        # 重试计数:{node_id: 已重试次数}
        retry_counts: dict[str, int] = {}

        await self.publish('dag_event', {'type': 'dag_run_started', 'run_id': run_id, 'dag': dag.name})

        def make_done_handler(node: DagNodeSpec):
            async def _done(h: RoutineHandle) -> None:
                self._done_q.put_nowait((node, h))
            return _done

        async def try_dispatch() -> None:
            for n in dag.nodes:
                if n.id not in pending:
                    continue
                if not deps_all_terminal(n, outputs):
                    continue
                pending.discard(n.id)

                if not should_run(n, outputs, dag_inputs):
                    logger.info('dag[%s]: skip %s (trigger_rule/when)', dag.name, n.id)
                    outputs[n.id] = NodeOutput(state='skipped')
                    await self.publish('dag_event', {
                        'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                        'node_id': n.id, 'state': 'skipped',
                    })
                    await try_dispatch()   # 跳过节点可能解锁下游
                    return

                try:
                    node_kwargs = resolve_kwargs(n, outputs, dag_inputs)
                except KeyError as e:
                    logger.error('dag[%s]: node %s resolve failed: %s', dag.name, n.id, e)
                    outputs[n.id] = NodeOutput(state='failed', error=str(e))
                    await self.publish('dag_event', {
                        'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                        'node_id': n.id, 'state': 'failed', 'error': str(e),
                    })
                    await try_dispatch()
                    return

                if n.output_format and 'response_format' not in node_kwargs:
                    node_kwargs['response_format'] = n.output_format

                logger.info('dag[%s]: dispatch %s -> %s', dag.name, n.id, n.routine)
                # create(建命令拿句柄)+ start(等 started ack 即返,不等 done).
                # on_stopped_handler 在 start 前挂(create 后 handle 已建)--
                # 保证不漏 done 事件(哪怕节点瞬间完成).
                handle = await self.create(n.routine, node_kwargs)
                handle.on_stopped_handler = make_done_handler(n)
                try:
                    await handle.start()
                except Exception as start_err:
                    # start 失败(模块冲突等):当节点 failed 处理(可被 retry/trigger_rule 接住).
                    err_msg = str(start_err)
                    logger.warning('dag[%s]: node %s start failed: %s', dag.name, n.id, err_msg)
                    outputs[n.id] = NodeOutput(state='failed', error=err_msg)
                    await self.publish('dag_event', {
                        'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                        'node_id': n.id, 'state': 'failed', 'error': err_msg,
                    })
                    await try_dispatch()
                    return
                running.add(n.id)
                await self.publish('dag_event', {
                    'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                    'node_id': n.id, 'state': 'running',
                    'attempt': retry_counts.get(n.id, 0) + 1,
                })

        await try_dispatch()

        while running or (not cancel_signal and pending):
            if cancel_signal:
                break
            node, h = await self._done_q.get()
            running.discard(node.id)
            if h.error:
                # 检查是否可重试
                spec_node = next((n for n in dag.nodes if n.id == node.id), None)
                retried = retry_counts.get(node.id, 0)
                if spec_node and spec_node.retry and retried < spec_node.retry.max_attempts:
                    retry_counts[node.id] = retried + 1
                    delay_s = spec_node.retry.delay_ms * (2 ** retried) / 1000
                    logger.warning(
                        'dag[%s]: node %s failed (attempt %d/%d), retry in %.1fs: %s',
                        dag.name, node.id, retried + 1, spec_node.retry.max_attempts,
                        delay_s, h.error,
                    )
                    await self.publish('dag_event', {
                        'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                        'node_id': node.id, 'state': 'retrying',
                        'attempt': retry_counts[node.id] + 1, 'error': h.error,
                    })
                    await asyncio.sleep(delay_s)
                    pending.add(node.id)   # 重新加入就绪队列
                else:
                    logger.warning('dag[%s]: node %s failed: %s', dag.name, node.id, h.error)
                    outputs[node.id] = NodeOutput(state='failed', error=h.error, output=h.result)
                    await self.publish('dag_event', {
                        'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                        'node_id': node.id, 'state': 'failed', 'error': h.error,
                    })
            else:
                logger.info('dag[%s]: node %s completed', dag.name, node.id)
                # extend_data 已删(新 SDK 无):output_format 直接按 node.output_format 推断.
                node_fmt = 'json' if node.output_format else 'text'
                outputs[node.id] = NodeOutput(state='completed', output=h.result, output_format=node_fmt)
                await self.publish('dag_event', {
                    'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                    'node_id': node.id, 'state': 'completed',
                    'output': _safe_output(h.result),
                    'output_format': node_fmt,
                })
                # 识别 dag_cancel routine 返回的哨兵 -> 终止整棵 DAG
                if isinstance(h.result, dict) and h.result.get(DAG_CANCEL_KEY):
                    reason = str(h.result.get('reason', ''))
                    logger.warning('dag[%s]: cancel signal from %s: %s', dag.name, node.id, reason)
                    cancel_signal.append((node.id, reason))
            if cancel_signal:
                break
            await try_dispatch()

        if cancel_signal:
            _, reason = cancel_signal[0]
            logger.warning('dag[%s]: cancelled - %s', dag.name, reason)
            for nid in list(running):
                if nid not in outputs:
                    outputs[nid] = NodeOutput(state='skipped', error='dag cancelled')
                    await self.publish('dag_event', {
                        'type': 'dag_node_event', 'run_id': run_id, 'dag': dag.name,
                        'node_id': nid, 'state': 'skipped',
                    })
            await self.publish('dag_event', {
                'type': 'dag_run_done', 'run_id': run_id, 'dag': dag.name,
                'success': False, 'error': f'DAG cancelled: {reason}',
            })
            raise RuntimeError(f'DAG cancelled: {reason}')

        logger.info('dag[%s]: finished. states=%s', dag.name,
                    {nid: o.state for nid, o in outputs.items()})

        if dag.outputs:
            result = {
                alias: (outputs[node_id].output if node_id in outputs else None)
                for alias, node_id in dag.outputs.items()
            }
        else:
            result = {
                nid: {'state': o.state, 'output': o.output, 'error': o.error}
                for nid, o in outputs.items()
            }

        await self.publish('dag_event', {
            'type': 'dag_run_done', 'run_id': run_id, 'dag': dag.name,
            'success': True, 'result': _safe_output(result),
        })
        return result

    async def stop(self) -> None:
        self._logger.info('dag: stop requested')
        await super().stop()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _safe_output(value: Any) -> Any:
    """将 output 转换为可 JSON 序列化的安全值."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {k: _safe_output(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_output(v) for v in value]
    return str(value)


# ---------------------------------------------------------------------------
# input validation helpers
# ---------------------------------------------------------------------------

_COERCE: dict[str, Any] = {
    'str': str,
    'int': int,
    'float': float,
    'bool': lambda v: v if isinstance(v, bool) else str(v).lower() in ('true', '1', 'yes', 'on'),
}


def _validate_and_coerce_inputs(spec: DagSpec, provided: dict[str, Any]) -> dict[str, Any]:
    """校验并强制转换 DAG 输入,缺少必填项时直接 raise."""
    if not spec.inputs:
        return dict(provided)

    result: dict[str, Any] = {}
    for inp in spec.inputs:
        if inp.name in provided:
            raw = provided[inp.name]
            try:
                result[inp.name] = _COERCE[inp.type](raw)
            except (ValueError, TypeError) as e:
                raise ValueError(
                    f'DAG {spec.name!r}: input {inp.name!r} 无法转换为 {inp.type}: {e}'
                ) from e
        elif inp.default is not None:
            result[inp.name] = _COERCE[inp.type](inp.default)
        elif inp.required:
            raise TypeError(
                f'DAG {spec.name!r}: 缺少必填输入 {inp.name!r}'
            )
    # 透传 inputs 声明之外的额外参数(兼容动态 kwargs)
    for k, v in provided.items():
        if k not in result:
            result[k] = v
    return result
