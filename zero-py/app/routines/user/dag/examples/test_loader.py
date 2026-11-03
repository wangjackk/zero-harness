"""快速验证 loader / executor / run_dag._load_spec(standalone,不依赖旧 routine3 运行时)."""
import sys, types
sys.modules.setdefault('skillkit', types.ModuleType('skillkit'))
sys.modules['skillkit'].SkillManager = type('SkillManager', (), {})  # type: ignore

import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', '..'))

from app.routines.user.dag.spec import DagNodeSpec, DagSpec, NodeOutput
from app.routines.user.dag.loader import parse_dag_yaml, DagLoadError
from app.routines.user.dag.executor import deps_all_terminal, should_run, resolve_kwargs
from pathlib import Path


def test_parse_hello_dag():
    yaml_text = (Path(__file__).parent.parent / 'dags' / 'hello_dag.yaml').read_text(encoding='utf-8')
    spec = parse_dag_yaml(yaml_text)
    assert spec.name == 'hello_dag'
    assert len(spec.nodes) == 4
    assert 'summary' in spec.outputs.values()
    node_ids = [n.id for n in spec.nodes]
    assert 'fetch_a' in node_ids
    assert 'fetch_b' in node_ids
    assert 'merge' in node_ids
    assert 'summary' in node_ids
    merge = next(n for n in spec.nodes if n.id == 'merge')
    assert set(merge.depends_on) == {'fetch_a', 'fetch_b'}
    print('[PASS] test_parse_hello_dag')


def test_cycle_detection():
    yaml_text = """
name: cyclic
description: test
nodes:
  - id: a
    routine: foo
    depends_on: [b]
  - id: b
    routine: bar
    depends_on: [a]
"""
    try:
        parse_dag_yaml(yaml_text)
        assert False, 'should have raised DagLoadError'
    except DagLoadError as e:
        assert '环路' in str(e)
    print('[PASS] test_cycle_detection')


def test_unknown_dep():
    yaml_text = """
name: bad
description: test
nodes:
  - id: a
    routine: foo
    depends_on: [nonexistent]
"""
    try:
        parse_dag_yaml(yaml_text)
        assert False, 'should have raised DagLoadError'
    except DagLoadError as e:
        assert 'nonexistent' in str(e)
    print('[PASS] test_unknown_dep')


def test_executor():
    fetch_a = DagNodeSpec(id='fetch_a', routine='foo')
    fetch_b = DagNodeSpec(id='fetch_b', routine='bar')
    merge = DagNodeSpec(
        id='merge', routine='baz',
        depends_on=['fetch_a', 'fetch_b'],
        inputs={'a': '$fetch_a.output', 'b': '$fetch_b.output', 'label': '$WI.tag'},
    )

    outputs: dict = {}
    # fetch_a and fetch_b have no deps, should be ready immediately
    assert deps_all_terminal(fetch_a, outputs)
    assert deps_all_terminal(fetch_b, outputs)
    assert not deps_all_terminal(merge, outputs)

    outputs['fetch_a'] = NodeOutput(state='completed', output='weather-a')
    assert not deps_all_terminal(merge, outputs)

    outputs['fetch_b'] = NodeOutput(state='completed', output='weather-b')
    assert deps_all_terminal(merge, outputs)
    assert should_run(merge, outputs)

    kwargs = resolve_kwargs(merge, outputs, {'tag': 'test-run'})
    assert kwargs['a'] == 'weather-a'
    assert kwargs['b'] == 'weather-b'
    assert kwargs['label'] == 'test-run'
    print('[PASS] test_executor')


def test_output_field_extraction():
    from app.routines.user.dag.executor import resolve_kwargs
    node = DagNodeSpec(
        id='c', routine='foo',
        depends_on=['a'],
        inputs={'city': '$a.output.city', 'full': '$a.output'},
    )
    outputs = {'a': NodeOutput(state='completed', output={'city': '北京', 'temp': '28°C'})}
    result = resolve_kwargs(node, outputs, {})
    assert result['city'] == '北京'
    assert result['full'] == "{'city': '北京', 'temp': '28°C'}"
    print('[PASS] test_output_field_extraction')


def test_trigger_rule_all_success_fail():
    node = DagNodeSpec(id='c', routine='foo', depends_on=['a', 'b'], trigger_rule='all_success')
    outputs = {
        'a': NodeOutput(state='failed'),
        'b': NodeOutput(state='completed'),
    }
    assert not should_run(node, outputs)
    print('[PASS] test_trigger_rule_all_success_fail')


def test_trigger_rule_all_done():
    node = DagNodeSpec(id='c', routine='foo', depends_on=['a', 'b'], trigger_rule='all_done')
    outputs = {
        'a': NodeOutput(state='failed'),
        'b': NodeOutput(state='completed'),
    }
    assert should_run(node, outputs)
    print('[PASS] test_trigger_rule_all_done')


def test_inputs_parsed_and_defaults():
    from app.routines.user.dag.run_dag import _validate_and_coerce_inputs
    yaml_text = (Path(__file__).parent.parent / 'dags' / 'hello_dag.yaml').read_text(encoding='utf-8')
    spec = parse_dag_yaml(yaml_text)
    assert len(spec.inputs) == 2
    assert spec.inputs[0].name == 'city_a'
    assert spec.inputs[0].default == '北京'
    assert not spec.inputs[0].required  # has default

    # 默认值生效(kwargs=None / 空)
    result = _validate_and_coerce_inputs(spec, {})
    assert result['city_a'] == '北京'
    assert result['city_b'] == '上海'

    # create('run_dag', {'dag': 'hello_dag', 'inputs': '{"city_a": "广州"}'})
    result2 = _validate_and_coerce_inputs(spec, {'city_a': '广州'})
    assert result2['city_a'] == '广州'
    assert result2['city_b'] == '上海'
    print('[PASS] test_inputs_parsed_and_defaults')


def test_load_spec_by_name():
    from app.routines.user.dag.run_dag import RunDag
    r = RunDag.__new__(RunDag)
    spec = r._load_spec('hello_dag')
    assert spec.name == 'hello_dag'
    assert len(spec.nodes) == 4
    print('[PASS] test_load_spec_by_name')


def test_condition_evaluator():
    from app.routines.user.dag.condition_evaluator import evaluate_condition
    from app.routines.user.dag.spec import NodeOutput

    ok  = NodeOutput(state='completed', output='ok')
    num = NodeOutput(state='completed', output='85')
    obj = NodeOutput(state='completed', output={'score': '0.95', 'label': 'pass'})

    outputs = {'a': ok, 'b': num, 'c': obj}
    dag_inputs = {'city': '南阳', 'threshold': '80'}

    # 字符串 == / !=
    assert evaluate_condition("$a.output == 'ok'", outputs) == (True, True)
    assert evaluate_condition("$a.output != 'ok'", outputs) == (False, True)
    assert evaluate_condition("$a.output == 'fail'", outputs) == (False, True)

    # 数值比较
    assert evaluate_condition("$b.output > 80", outputs) == (True, True)
    assert evaluate_condition("$b.output >= 85", outputs) == (True, True)
    assert evaluate_condition("$b.output < 80", outputs) == (False, True)
    assert evaluate_condition("$b.output <= 84", outputs) == (False, True)

    # 字段访问($node.output.field)
    assert evaluate_condition("$c.output.label == 'pass'", outputs) == (True, True)
    assert evaluate_condition("$c.output.score >= 0.9", outputs) == (True, True)

    # 简写字段访问($node.label)
    assert evaluate_condition("$c.label == 'pass'", outputs) == (True, True)

    # $WI 工作流输入
    assert evaluate_condition("$WI.city == '南阳'", outputs, dag_inputs) == (True, True)
    assert evaluate_condition("$WI.city == '北京'", outputs, dag_inputs) == (False, True)

    # 复合 && / ||
    assert evaluate_condition("$a.output == 'ok' && $b.output > 80", outputs) == (True, True)
    assert evaluate_condition("$a.output == 'ok' && $b.output < 80", outputs) == (False, True)
    assert evaluate_condition("$a.output == 'fail' || $b.output > 80", outputs) == (True, True)
    assert evaluate_condition("$a.output == 'fail' || $b.output < 80", outputs) == (False, True)

    # AND 优先级高于 OR
    #   ($a.ok && $b.ok) || $c.fail  →  True || False  →  True
    assert evaluate_condition(
        "$a.output == 'ok' && $b.output > 80 || $c.label == 'fail'", outputs
    ) == (True, True)

    # fail-closed:无法解析的表达式
    assert evaluate_condition("invalid expr", outputs) == (False, False)
    assert evaluate_condition("$x.field.sub.too_deep == 'v'", outputs) == (False, False)

    print('[PASS] test_condition_evaluator')


def test_retry_parse():
    from app.routines.user.dag.loader import parse_dag_yaml
    yaml_text = """
name: retry_test
description: retry 解析测试
nodes:
  - id: flaky
    routine: some_routine
    retry:
      max_attempts: 3
      delay_ms: 1000
      on_error: all
"""
    spec = parse_dag_yaml(yaml_text)
    node = spec.nodes[0]
    assert node.retry is not None
    assert node.retry.max_attempts == 3
    assert node.retry.delay_ms == 1000
    assert node.retry.on_error == 'all'
    print('[PASS] test_retry_parse')


def test_cancel_as_normal_routine():
    """cancel 不是特殊节点----就是普通的 routine: dag_cancel 节点."""
    from app.routines.user.dag.loader import parse_dag_yaml
    yaml_text = """
name: cancel_test
description: cancel 作为普通 routine 节点
nodes:
  - id: check
    routine: some_routine
  - id: abort
    routine: dag_cancel
    inputs:
      reason: "条件不满足,终止"
    depends_on: [check]
    when: "$check.output == 'fail'"
  - id: next
    routine: other_routine
    depends_on: [check]
"""
    spec = parse_dag_yaml(yaml_text)
    abort_node = next(n for n in spec.nodes if n.id == 'abort')
    # 没有特殊字段,就是普通节点
    assert abort_node.routine == 'dag_cancel'
    assert abort_node.inputs['reason'] == '条件不满足,终止'
    assert abort_node.when == "$check.output == 'fail'"
    print('[PASS] test_cancel_as_normal_routine')


def test_cancel_sentinel():
    """dag_cancel routine 返回的哨兵能被识别."""
    from app.routines.user.dag.spec import DAG_CANCEL_KEY
    # 模拟 dag_cancel 的返回值
    result = {DAG_CANCEL_KEY: True, 'reason': '终止原因'}
    assert isinstance(result, dict) and result.get(DAG_CANCEL_KEY)
    assert result.get('reason') == '终止原因'
    # 普通节点的 dict 返回值不会误判
    normal = {'data': 'x', 'reason': 'y'}
    assert not normal.get(DAG_CANCEL_KEY)
    print('[PASS] test_cancel_sentinel')


def test_approval_as_normal_routine():
    """approval 不是特殊节点----就是普通的 routine: dag_approval 节点."""
    from app.routines.user.dag.loader import parse_dag_yaml
    yaml_text = """
name: approval_test
description: approval 作为普通 routine 节点
inputs:
  - name: content
    type: str
nodes:
  - id: summarize
    routine: summarize_text
    inputs:
      text: "$WI.content"
  - id: review
    routine: dag_approval
    inputs:
      message: "请审批:$summarize.output"
      capture_response: true
      on_reject_prompt: "请说明意见"
      max_attempts: 3
    depends_on: [summarize]
"""
    spec = parse_dag_yaml(yaml_text)
    review = next(n for n in spec.nodes if n.id == 'review')
    # 没有特殊字段,就是普通节点
    assert review.routine == 'dag_approval'
    assert review.inputs['message'] == '请审批:$summarize.output'
    assert review.inputs['capture_response'] is True
    assert review.depends_on == ['summarize']
    print('[PASS] test_approval_as_normal_routine')


if __name__ == '__main__':
    test_parse_hello_dag()
    test_cycle_detection()
    test_unknown_dep()
    test_executor()
    test_trigger_rule_all_success_fail()
    test_trigger_rule_all_done()
    test_output_field_extraction()
    test_inputs_parsed_and_defaults()
    test_load_spec_by_name()
    test_condition_evaluator()
    test_retry_parse()
    test_cancel_as_normal_routine()
    test_cancel_sentinel()
    test_approval_as_normal_routine()
    print('\nall tests passed')
