"""并行跑 test_dispatch: 多进程分片, 全局状态(模块级 ``_log`` / 假脑类
属性)进程间天然隔离 —— 同进程 gather 会互相污染(每用例开头
``_log.clear()``), 分片隔离是唯一安全形态.

用法::

    python -m app.routines.user.moss.tests.run_parallel          # 默认 8 片
    python -m app.routines.user.moss.tests.run_parallel -j 12    # 12 片
    python -m app.routines.user.moss.tests.run_parallel --quick  # 先冒烟 3 片

分片策略 round-robin(定义序跨片散开): 单用例耗时差异大, 跨片交错
比连续切块均衡. 汇总: 全片 ALL PASS 才算过, 任一失败打印该片 stdout
尾部.
"""
from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_FRAMEWORK = _HERE.parents[4] / 'routine-py'
if _FRAMEWORK.is_dir() and str(_FRAMEWORK) not in sys.path:
    sys.path.insert(0, str(_FRAMEWORK))

import inspect

from . import test_dispatch

# 定义序用例表(与 main() 手写序列同源, 自动同步)
CASES = [n for n, f in test_dispatch.__dict__.items()
         if n.startswith('t_') and inspect.iscoroutinefunction(f)]

PY = sys.executable
MOD = 'app.routines.user.moss.tests.test_dispatch'


def _run_chunk(idx: int, names: list[str]) -> tuple[int, bool, str]:
    """起一个子进程跑一片, 返回 (片号, 是否全绿, stdout 尾部)."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [PY, '-m', MOD, *names],
        cwd=str(_HERE.parents[4]),   # zero-py 根(-m 的包根)
        capture_output=True, text=True, timeout=1200,
    )
    dt = time.perf_counter() - t0
    tail = (proc.stdout or '') + (proc.stderr or '')
    ok = proc.returncode == 0 and 'ALL PASS' in (proc.stdout or '')
    print(f'[chunk {idx}] {"PASS" if ok else "FAIL"} '
          f'{len(names)} cases in {dt:.0f}s: {" ".join(names)}', flush=True)
    if not ok:
        lines = tail.strip().splitlines()
        print(f'--- chunk {idx} tail ---\n' + '\n'.join(lines[-25:]),
              flush=True)
    return idx, ok, tail


def main() -> None:
    args = [a for a in sys.argv[1:]]
    jobs = 8
    if '-j' in args:
        jobs = int(args[args.index('-j') + 1])
    quick = '--quick' in args
    cases = CASES[::4] if quick else CASES   # 冒烟: 每 4 个抽 1 个
    # round-robin 分片: 定义序跨片散开, 均衡单用例耗时差
    chunks: list[list[str]] = [[] for _ in range(jobs)]
    for i, name in enumerate(cases):
        chunks[i % jobs].append(name)
    chunks = [c for c in chunks if c] or [cases]

    print(f'{len(cases)} cases / {len(chunks)} parallel processes',
          flush=True)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        results = list(pool.map(lambda p: _run_chunk(*p), enumerate(chunks)))
    dt = time.perf_counter() - t0

    failed = [idx for idx, ok, _ in results if not ok]
    print(f'{"ALL PASS" if not failed else "FAILED: chunks " + str(failed)}'
          f' — {len(cases)} cases in {dt:.0f}s wall '
          f'(parallel x{len(chunks)})', flush=True)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
