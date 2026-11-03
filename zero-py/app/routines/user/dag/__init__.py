"""DAG 编排子包.

公共接口
--------
spec:
    DagSpec, DagNodeSpec, NodeOutput, InputSpec

loader:
    parse_dag_yaml(text) → DagSpec
    load_dag_yaml_file(path) → DagSpec
    DagLoadError

condition_evaluator:
    evaluate_condition(expr, outputs, dag_inputs) → (result, parsed)

run_dag:
    RunDag
    DAG_DIR
"""
from routine import Routines

from .spec import DagSpec, DagNodeSpec, NodeOutput, InputSpec, RetrySpec
from .loader import parse_dag_yaml, load_dag_yaml_file, DagLoadError
from .condition_evaluator import evaluate_condition
from .run_dag import RunDag, DAG_DIR
from .browse import DagList, DagSpecView
from .approval import DagApproval
from .cancel import DagCancel
from .archon_translator import TranslateArchon
from .demo_routines import Echo, MergeWeather, SummaryWeather, FlakyTask, AlwaysFail

routines = Routines()
routines.register(RunDag, DagList, DagSpecView,
                  DagApproval, DagCancel, TranslateArchon,
                  Echo, MergeWeather, SummaryWeather, FlakyTask, AlwaysFail)

__all__ = [
    'routines',
    'DagSpec',
    'DagNodeSpec',
    'NodeOutput',
    'parse_dag_yaml',
    'load_dag_yaml_file',
    'DagLoadError',
    'RunDag',
    'DAG_DIR',
    'DagList',
    'DagSpecView',
    'DagApproval',
    'DagCancel',
    'TranslateArchon',
    'Echo',
    'MergeWeather',
    'SummaryWeather',
    'FlakyTask',
    'AlwaysFail',
]
