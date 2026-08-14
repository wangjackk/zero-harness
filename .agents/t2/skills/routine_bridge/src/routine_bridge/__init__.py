"""Routine skill — kernel-side ``run_routine`` HTTP bridge.

提供 ``run_routine()``: 从 IPython kernel 内部调任意 routine,
走 HTTP bridge (WebServer, 默认 127.0.0.1:7780).

使用::

    from routine_bridge import run_routine

    content = await run_routine('read', path='foo.py')
    result = await run_routine('edit', path='foo.py', old_str='a', new_str='b')

bridge 地址通过环境变量 ``KSHHELL_HTTP_ADDR`` 或 ``ZERO_HTTP_ADDR`` 覆盖,
格式 ``host:port`` (如 ``127.0.0.1:7780``).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

__all__ = ["run_routine"]

_DEFAULT_BRIDGE = "http://127.0.0.1:7780"


def _bridge_base() -> str:
    env = os.environ.get("KSHHELL_HTTP_ADDR") or os.environ.get("ZERO_HTTP_ADDR")
    if env:
        if not env.startswith("http"):
            env = f"http://{env}"
        return env.rstrip("/")
    return _DEFAULT_BRIDGE


async def run_routine(name: str, kwargs: dict[str, Any] | None = None) -> Any:
    """Call a routine by name via the HTTP bridge.

    Args:
        name: Routine name (e.g. 'read', 'edit', 'grep', 'list_routines').
        kwargs: Routine input parameters as a dict (e.g. {'path': 'foo.py'}).
            None 等价于空 dict. 用 dict 包参数, 避免跟 routine 自身的 name
            等参数冲突.

    Returns:
        The routine's result on success. 如果 result 是 dict 且含 ``for_llm``
        字段, 自动提取 ``for_llm`` 返回 (LLM-friendly 文本摘要, 适合 kernel
        agent 直接处理); 否则原样返回 result.

    Raises:
        RuntimeError: If the bridge returns an error or the routine fails.
        httpx.HTTPError: If the HTTP request itself fails.
    """
    agent_id = os.environ.get("KSHHELL_AGENT_ID", "user")
    url = f"{_bridge_base()}/agents/{agent_id}/run/{name}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=kwargs or {})
    data = resp.json()
    if not isinstance(data, dict) or not data.get("ok"):
        error = (
            (data or {}).get("error", "unknown error")
            if isinstance(data, dict)
            else str(data)
        )
        raise RuntimeError(f"run_routine({name!r}) failed: {error}")
    result = data.get("result")
    # 自动提取 for_llm: kernel agent 通常只需要 LLM-friendly 文本摘要,
    # 不需要结构化数据 (如 list_routines 的 routines 列表).
    if isinstance(result, dict) and "for_llm" in result:
        return result["for_llm"]
    return result
