"""zero 应用入口: 内嵌 kernel(kernel.create + hub.connect), 进程内直连.

**启动哪套 routine 由位置参数指定的清单决定**(不是编译进代码的常量)::

    uv run python -m app.main                  # 默认 app/routines.yaml
    uv run python -m app.main profiles/car.yaml # 换一份清单
    uv run python -m app.main app/moss.yaml     # moss 装配(清单另带模块树)
    ZERO_ROUTINES_YAML=profiles/car.yaml uv run python -m app.main

优先级: 位置参数 > ``ZERO_ROUTINES_YAML`` > app/routines.yaml; 相对路径按
当前目录解析. 清单在 kernel 拉起**之前**预检(文件在 / 能解析 / 条目非空 /
条目路径存在), 不通过就退出 ---- 没有 routine 的空壳进程比启动失败更难查.
条目自身的路径基准仍是 app/(见 app/routines/loader.py), 换清单不换基准.
清单写了 ``modules:`` 就用它当 kernel 模块树(自成一体的装配需要, 如 moss 的
``code``), 没写就用 app/tree.json.

kernel 的 grpc dial-in 口监听地址读 config.yaml (network.grpc.addr):
外部 routine hub (IPython 的 hub_routine skill / 远程设备 routine SDK) 可主动拨入;
ZERO_GRPC_ADDR 环境变量可覆盖. bind 后写 os.environ['ZERO_KERNEL_ADDR'],
IPython 子进程 bootstrap 据此注入 ZERO_KERNEL_ADDR.

用法(cd zero-py, routine SDK 是 ./routine-py 的 editable path 依赖)::

    uv sync
    uv run python -m app.main    # 或: uv run python app/main.py (入口自举项目根)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
import warnings
from pathlib import Path

# zmq 在 Proactor 循环上经 selector 线程兜底工作(仅提示可换 policy)----
# 但本应用依赖 asyncio.create_subprocess(BashTool/ros_client 的 ssh
# 探测等, Windows 上仅 Proactor 支持), 不能换 SelectorEventLoopPolicy,
# 过滤此噪音.
warnings.filterwarnings(
    'ignore', message='Proactor event loop does not implement add_reader',
    category=RuntimeWarning)

_HERE = Path(__file__).resolve().parent          # zero-py/app/ (包根)

# 直接 `python app/main.py` 时 sys.path 只有包目录, 自举项目根 (zero-py/)
# 让 zero.* 可导入; `-m app.main` 场景下 cwd 已在 path, 幂等.
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))


# 须先于下方所有项目 import: flow.llm 在 import 期执行 _load_models(),
# 读 $ENV 解析 models.yaml 的 api_key (读不到则告警且空 key 被缓存).
from app.config import load_env
load_env()

from routine import RoutineHub, kernel
from routine.logger import configure_root

from app.config import network
from app.modules import get_modules
from app.routines import get_routines
from app.routines.loader import check_yaml, set_yaml_path, yaml_modules


async def main(routines: str | None = None) -> None:
    configure_root(logging.INFO)
    # 降噪只压框架自己的树; routine.routine.* 是各 routine 实例的业务日志
    # (self._logger 按实例名命名), 压掉会让业务 run()/事件打印全不可见.
    for _name in ('kernel.kernel', 'routine.hub', 'routine.req'):
        logging.getLogger(_name).setLevel(logging.WARNING)
    # 清单先定下再预检: 启动前就把"读哪份 / 那份能不能用"钉死. 放在
    # kernel.create 之前 ---- 端口都还没占, 坏配置不该进入运行期.
    path = set_yaml_path(routines)
    try:
        entries = check_yaml(path)
        tree = yaml_modules(path)
    except (OSError, ValueError) as exc:
        print(f'routines 清单不可用: {exc}', file=sys.stderr)
        raise SystemExit(2)
    # models.yaml 是 agent 层的硬依赖: 缺了它 web_server / agents 在 import 期
    # 就抛 FileNotFoundError, 而 loader 只把整批注册记成一条 ERROR ---- 进程
    # 活着、端口也没开, 比启动失败更难查. 与清单一样在 kernel 之前挡掉.
    if not (_HERE / 'models.yaml').is_file():
        print('缺少 app/models.yaml: 复制 app/models.yaml.example 并填入 api_key'
              ' (模板用 $VAR 引用时把 key 写进 zero-py/.env)', file=sys.stderr)
        raise SystemExit(2)
    print(f'--- routines: {path} ({len(entries)} 条) ---')
    net = network()
    # 模块树: 清单声明了就用自己的, 否则用 app 的
    # tree.json. 树是身体拓扑, 清单是零件名单 ---- 两者必须来自同一个 profile.
    modules = tree or json.loads(
        (_HERE / 'tree.json').read_text(encoding='utf-8'))['modules']
    k = await kernel.create(
        modules,
        listen=os.environ.get('ZERO_GRPC_ADDR') or net.grpc.addr,
    )
    os.environ['ZERO_KERNEL_ADDR'] = k.address or ''
    # hub 的模块清单 = 树上的叶子(根不是模块名); 清单自带树时按自己那份报
    hub_modules = [m for m in tree if m != 'root'] if tree else get_modules()
    hub = RoutineHub(get_routines(), modules=hub_modules, hub_id='zero')
    conn = await hub.connect(k)

    print(f'zero up: kernel in-process, grpc on {k.address} (dial-in), '
          f'modules: {", ".join(hub_modules) or "-"}')
    # Ctrl+C 两段式: 第一次只置位, 让关停级联跑完; 关停期间再按一次强退.
    # 默认(KeyboardInterrupt 从 run_until_complete 抛)会把 await conn.close()
    # 从中间劈断: 剩下的 routine 命令 task 一直等 lifecycle.stopped, 事件循环
    # 关闭时报一串 "Task was destroyed but it is pending!"(cmd:<name>-<id>).
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_sigint(signum, frame) -> None:
        if stop.is_set():
            print('\n强制退出.', file=sys.stderr, flush=True)
            os._exit(130)
        print('\n收到 Ctrl+C, 正在关停 (再按一次强制退出)...',
              file=sys.stderr, flush=True)
        loop.call_soon_threadsafe(stop.set)

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except ValueError:          # 非主线程(tests 里直接跑 main)装不上, 退回默认
        pass
    try:
        await stop.wait()
    finally:
        began = time.monotonic()
        try:
            await conn.close()
        finally:
            print(f'关停完成 ({time.monotonic() - began:.1f}s)',
                  file=sys.stderr, flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(
        description='zero 应用入口: 位置参数指定 routine 清单.')
    ap.add_argument('routines', nargs='?', metavar='YAML',
                    help='routine 清单路径(默认 app/routines.yaml); '
                         '相对路径按当前目录解析, 也可用 ZERO_ROUTINES_YAML')
    args = ap.parse_args()
    print(f'pid: {os.getpid()}')
    try:
        asyncio.run(main(args.routines))
    except KeyboardInterrupt:
        pass
