"""app/config.yaml 访问层 ---- 网络配置唯一事实源.

零依赖叶子模块 (yaml 读盘 + pydantic 校验), ``lru_cache`` 单次读盘;
main.py (grpc) / web_server / flow hub 从此取默认值, 不再各自硬编码.
改 config.yaml 重启进程生效.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

_HERE = Path(__file__).resolve().parent


def load_env() -> None:
    """读 zero-py/.env (LLM/TTS key 等; gitignored, 应用自包含不外借
    兄弟项目). 已有进程 env 不覆盖; 须先于读 $ENV 的项目 import 执行
    (如 flow.llm 的 import 期 _load_models)."""
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


class _Net(BaseModel):
    """单处监听地址 (host + port)."""

    host: str = '127.0.0.1'
    port: int = Field(default=7781, ge=1, le=65535)


class _RosBridgeStart(BaseModel):
    """rosbridge 自动拉起: WS 连不上时 ros_client 经 SSH 执行 cmd."""

    ssh: str = 'robot'   # ~/.ssh/config 主机别名(免密), 按本机自填
    cmd: str = 'sudo systemctl restart start_app_node.service'


class _Grpc(BaseModel):
    """kernel dial-in server 监听地址 (host:port 单串)."""

    addr: str = '127.0.0.1:7795'


class Network(BaseModel):
    """config.yaml 的 network 段: web_server / grpc / flow 三处监听."""

    web_server: _Net = _Net()
    grpc: _Grpc = _Grpc()
    flow: _Net = _Net(port=7790)
    # 设备事件总线 (车端 zmqproxy, voice/vision 域) 订阅端, 只读
    device_bus: str = 'tcp://127.0.0.1:6667'
    # 总线发布端 (XSUB): vision 即开即关门控 cmd (cam/pose) 发往这里
    device_bus_pub: str = 'tcp://127.0.0.1:6666'
    # 幻尔小车 rosbridge WS (ros_client 单例: 感知订阅 + 车控发布)
    ros_bridge: str = 'ws://127.0.0.1:9090'
    # rosbridge 不在线时自动拉起(冷却 60s); 省略 = 不拉起只重连
    ros_bridge_start: Optional[_RosBridgeStart] = None


@lru_cache(maxsize=1)
def network() -> Network:
    """读 app/config.yaml 的 network 段.

    config.yaml 是**本地**配置(含内网地址), 不入库: 缺失时整段走默认值
    (全部 127.0.0.1), 要覆盖就照 config.example.yaml 复制一份.
    段内缺省字段由 pydantic 默认补齐.
    """
    path = _HERE / 'config.yaml'
    if not path.exists():
        return Network()
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return Network.model_validate(data.get('network') or {})
