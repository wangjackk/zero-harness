"""翻译器测试 -- 直接跑:python -m routines.user.dag.archon_translator.test_translate"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import importlib.util

import yaml

# 直接加载 translator.py,绕过包的重型依赖
_HERE = Path(__file__).resolve()
_spec = importlib.util.spec_from_file_location('translator', _HERE.parent / 'translator.py')
_mod  = importlib.util.module_from_spec(_spec)      # type: ignore
sys.modules['translator'] = _mod                    # dataclass 需要模块已在 sys.modules
_spec.loader.exec_module(_mod)                      # type: ignore
translate = _mod.translate

# ── 测试用例 ──────────────────────────────────────────────────────────────────

CASES: list[tuple[str, str]] = []  # (case_name, yaml_text)

# ① bash 节点
CASES.append(('bash_node', textwrap.dedent('''\
    name: test-bash
    description: bash node test
    nodes:
      - id: fetch-date
        bash: "date /T"
        timeout: 10000
''')))

# ② prompt 节点 + $ARGUMENTS 变量
CASES.append(('prompt_with_arguments', textwrap.dedent('''\
    name: test-prompt
    description: prompt + ARGUMENTS
    provider: claude
    nodes:
      - id: greet
        model: haiku
        prompt: "你好,用户说的是:$ARGUMENTS"
''')))

# ③ 嵌入字符串引用 ($nodeId.output 在字符串中间)
CASES.append(('embedded_ref', textwrap.dedent('''\
    name: test-embedded
    description: embedded node ref
    nodes:
      - id: fetch
        bash: "echo hello"
      - id: use-fetch
        prompt: "收到上游数据:$fetch.output,请总结"
        depends_on: [fetch]
''')))

# ④ script 命名 + script 内联
CASES.append(('script_nodes', textwrap.dedent('''\
    name: test-script
    description: script nodes
    nodes:
      - id: script-named
        script: echo-args
        runtime: bun
        timeout: 30000
      - id: script-inline
        script: |
          import sys
          print("hello from python")
        runtime: python
        depends_on: [script-named]
''')))

# ⑤ loop 节点(近似警告)
CASES.append(('loop_node', textwrap.dedent('''\
    name: test-loop
    description: loop node approximation
    nodes:
      - id: counter
        loop:
          prompt: "计数到3,当前是:$LOOP_PREV_OUTPUT"
          until: COMPLETE
          max_iterations: 5
          fresh_context: true
''')))

# ⑥ 完整 DAG:e2e-pi-all-nodes 精简版
CASES.append(('full_dag', textwrap.dedent('''\
    name: e2e-full
    description: Full DAG smoke test
    provider: claude
    model: haiku
    nodes:
      - id: prompt-node
        prompt: "Reply with ok"
        allowed_tools: []
        effort: low
      - id: bash-node
        bash: 'echo ok'
      - id: script-node
        script: echo-args
        runtime: bun
        timeout: 30000
      - id: downstream
        bash: "echo got $prompt-node.output"
        depends_on: [prompt-node]
      - id: gated
        bash: "echo gated-ok"
        depends_on: [bash-node]
        when: "$bash-node.output == 'ok'"
      - id: merge
        bash: "echo merge-ok"
        depends_on: [downstream, gated, script-node]
        trigger_rule: all_success
    outputs:
      result: merge
''')))

# ⑦ 真实 Archon 文件(如果存在)
_ARCHON_SAMPLES = [
    Path('E:/code/pyfiles/Archon/.archon/workflows/defaults/archon-test-loop-dag.yaml'),
    Path('E:/code/pyfiles/Archon/.archon/workflows/test-workflows/e2e-pi-all-nodes-smoke.yaml'),
]
for _p in _ARCHON_SAMPLES:
    if _p.exists():
        CASES.append((f'real_{_p.stem}', _p.read_text(encoding='utf-8')))


# ── 运行 ──────────────────────────────────────────────────────────────────────

SEP = '=' * 60


def run_case(name: str, src: str, verbose: bool = True) -> bool:
    print(f'\n{SEP}')
    print(f'  CASE: {name}')
    print(SEP)

    result = translate(src)

    # 验证输出是合法 YAML
    try:
        doc = yaml.safe_load(result.yaml_text)
        assert isinstance(doc, dict), 'output is not a dict'
        assert 'nodes' in doc, 'missing nodes key'
    except Exception as e:
        print(f'  [FAIL] YAML 验证失败: {e}')
        print(result.yaml_text)
        return False

    # 每个节点都有 routine
    nodes = doc.get('nodes', [])
    missing_routine = [n.get('id') for n in nodes if 'routine' not in n]
    if missing_routine:
        print(f'  [FAIL] 节点缺少 routine: {missing_routine}')
        return False

    if verbose:
        print(result.yaml_text)

    # 打印警告
    if result.warnings:
        print('  -- 警告 --')
        for w in result.warnings:
            sym = {'info': '[I]', 'warn': '[W]', 'error': '[E]'}.get(w.level, '[?]')
            print(f'  {sym} {w}')

    status = '[FAIL] HAS ERRORS' if result.has_errors else '[OK]'
    print(f'\n  结果: {status}  ({len(result.warnings)} 条警告, {len(nodes)} 个节点)')
    return not result.has_errors


def main() -> None:
    passed = 0
    failed = 0
    for name, src in CASES:
        ok = run_case(name, src, verbose='--verbose' in sys.argv or '-v' in sys.argv)
        if ok:
            passed += 1
        else:
            failed += 1

    print(f'\n{SEP}')
    print(f'  汇总: {passed} 通过 / {failed} 失败 / {passed + failed} 总计')
    print(SEP)
    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
