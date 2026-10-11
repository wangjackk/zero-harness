"""zero 应用入口: 内嵌 kernel(kernel.create + hub.connect), 进程内直连.

kernel 的 grpc dial-in 口监听地址读 config.yaml (network.grpc.addr):
外部 routine hub (IPython 的 hub_routine skill / 远程设备 routine SDK) 可主动拨入;
ZERO_GRPC_ADDR 环境变量可覆盖. bind 后写 os.environ['ZERO_KERNEL_ADDR'],
IPython 子进程 bootstrap 据此注入 ZERO_KERNEL_ADDR.

用法(cd zero-py, routine SDK 是 ./routine-py 的 editable path 依赖)::

    uv sync
    uv run python -m app.main    # 或: uv run python app/main.py (入口自举项目根)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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
import sys

if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))


def _load_env() -> None:
    """读 zero-py/.env (LLM key 等; gitignored, 应用自包含不外借兄弟项目)."""
    env_file = _HERE.parent / '.env'
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# 须先于下方所有项目 import: flow.llm 在 import 期执行 _load_models(),
# 读 $ENV 解析 models.yaml 的 api_key (读不到则告警且空 key 被缓存).
_load_env()

from routine import RoutineHub, kernel
from routine.logger import configure_root

from app.config import network
from app.modules import get_modules
from app.routines import get_routines


async def main() -> None:
    configure_root(logging.INFO)
    # 降噪只压框架自己的树; routine.routine.* 是各 routine 实例的业务日志
    # (self._logger 按实例名命名), 压掉会让业务 run()/事件打印全不可见.
    for _name in ('kernel.kernel', 'routine.hub', 'routine.req'):
        logging.getLogger(_name).setLevel(logging.WARNING)
    net = network()
    modules = json.loads(
        (_HERE / 'tree.json').read_text(encoding='utf-8'))['modules']
    k = await kernel.create(
        modules,
        listen=os.environ.get('ZERO_GRPC_ADDR') or net.grpc.addr,
    )
    os.environ['ZERO_KERNEL_ADDR'] = k.address or ''
    hub = RoutineHub(get_routines(), modules=get_modules(), hub_id='zero')
    conn = await hub.connect(k)

    print(f'zero up: kernel in-process, grpc on {k.address} '
          f'(dial-in), web_server on {net.web_server.host}:{net.web_server.port}')
    try:
        await asyncio.Event().wait()   # 挂起直到 Ctrl+C
    finally:
        await conn.stop()


if __name__ == '__main__':
    print(f'pid: {os.getpid()}')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
