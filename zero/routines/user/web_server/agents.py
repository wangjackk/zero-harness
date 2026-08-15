"""Agent 管理的 HTTP/WS handler 函数.

由 ``server.WebServer._on_client_message`` 和 ``app.build_app`` 调用.
模块级函数(接收 ``inst`` 第一参数),复用 self.ctx.req 查询 manager routine.

harness 只有一种 agent: prime, 由常驻 passive routine ``prime_agent_manager``
管理 (可能跑在另一个进程, 故按 name 查 running 列表拿 id).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PRIME_MANAGER_NAME = 'prime_agent_manager'
_CREATE_ENTRY_NAME = 'create_prime_agent'
_MANAGER_REQ_TIMEOUT = 10.0


async def find_manager_id(inst, name: str) -> str | None:
    """按 name 查 running manager routine id(跨进程正确)."""
    try:
        routines = await inst.ctx.get_running_routines()
    except Exception as exc:
        logger.warning('[bridge] get_running failed (%r)', exc)
        return None
    for r in routines:
        if str(r.get('name') or '') == name:
            rid = str(r.get('id') or '').strip()
            if rid:
                return rid
    return None


async def _req_prime_manager(inst, event: str, payload: dict) -> dict:
    """req prime manager, manager 不在时返回统一错误."""
    manager_id = await find_manager_id(inst, _PRIME_MANAGER_NAME)
    if manager_id is None:
        return {'ok': False, 'error': 'prime manager not running'}
    try:
        return await inst.ctx.req(
            manager_id, event, payload,
            timeout=_MANAGER_REQ_TIMEOUT,
        )
    except Exception as exc:
        logger.warning('[bridge] %s error: %s', event, exc)
        return {'ok': False, 'error': str(exc)}


async def on_create_agent(inst, msg: dict, reply) -> None:
    """创建 prime agent. msg 字段: project_dir/agent_id/model/..."""
    req_id = msg.get('id')
    payload = {k: v for k, v in msg.items() if k not in ('type', 'id', 'kind')}
    try:
        result = await inst.call(_CREATE_ENTRY_NAME, payload)
        await reply({'type': 'agent_created', 'id': req_id, 'kind': 'prime', **result})
    except Exception as exc:
        logger.warning('[bridge] create_agent error: %s', exc)
        await reply({'type': 'agent_created', 'id': req_id, 'kind': 'prime',
                     'ok': False, 'error': str(exc)})


async def on_list_agents(inst, msg: dict, reply) -> None:
    """列出所有 prime agent."""
    req_id = msg.get('id')
    try:
        result = await _req_prime_manager(inst, 'list_agents', {})
        items = list(result.get('agents') or [])
        for it in items:
            it['kind'] = 'prime'
        await reply({'type': 'agents', 'id': req_id, 'agents': items})
    except Exception as exc:
        logger.warning('[bridge] list_agents error: %s', exc)
        await reply({'type': 'agents', 'id': req_id, 'agents': [], 'error': str(exc)})


async def on_resume_agent(inst, msg: dict, reply) -> None:
    """恢复 prime agent. msg 字段: agent_id 必传, model/... 可选."""
    req_id = msg.get('id')
    payload = {k: v for k, v in msg.items() if k not in ('type', 'id', 'kind')}
    result = await _req_prime_manager(inst, 'resume_agent', payload)
    await reply({'type': 'agent_resumed', 'id': req_id, 'kind': 'prime', **result})


async def on_stop_agent(inst, msg: dict, reply) -> None:
    """停止 prime agent."""
    req_id = msg.get('id')
    agent_id = str(msg.get('agent_id') or '').strip()
    if not agent_id:
        await reply({'type': 'agent_stopped', 'id': req_id, 'ok': False,
                     'error': 'agent_id is required'})
        return
    result = await _req_prime_manager(inst, 'stop_agent', {'agent_id': agent_id})
    # 清理本地 ns 缓存(由 register_agent 填充)
    inst._agent_ns.pop(agent_id, None)
    inst._agent_names.pop(agent_id, None)
    inst._agent_rids.pop(agent_id, None)
    await reply({'type': 'agent_stopped', 'id': req_id, 'agent_id': agent_id,
                 'kind': 'prime', **result})


async def on_delete_agent(inst, msg: dict, reply) -> None:
    """删除 prime agent (DB agents 行 + messages). live 的拒绝删除, 删完不可恢复."""
    req_id = msg.get('id')
    agent_id = str(msg.get('agent_id') or '').strip()
    if not agent_id:
        await reply({'type': 'agent_deleted', 'id': req_id, 'ok': False,
                     'error': 'agent_id is required'})
        return
    result = await _req_prime_manager(inst, 'delete_agent', {'agent_id': agent_id})
    await reply({'type': 'agent_deleted', 'id': req_id, 'agent_id': agent_id,
                 'kind': 'prime', **result})


async def on_list_presets(inst, msg: dict, reply) -> None:
    """列出全部 agent preset (随附 + 用户根). 走 agent_preset routine (op=list)."""
    req_id = msg.get('id')
    try:
        result = await inst.call('agent_preset', {'op': 'list'})
        await reply({'type': 'presets', 'id': req_id,
                     'presets': (result or {}).get('presets') or []})
    except Exception as exc:
        logger.warning('[bridge] list_presets error: %s', exc)
        await reply({'type': 'presets', 'id': req_id, 'presets': [], 'error': str(exc)})


async def on_copy_preset(inst, msg: dict, reply) -> None:
    """复制一个 preset 到用户根 (copy-only). 走 agent_preset routine (op=copy)."""
    req_id = msg.get('id')
    payload = {
        'op': 'copy',
        'from': str(msg.get('from') or ''),
        'id': str(msg.get('preset_id') or ''),
        'name': msg.get('name'),
    }
    try:
        result = await inst.call('agent_preset', payload)
        await reply({'type': 'preset_copied', 'id': req_id, **(result or {})})
    except Exception as exc:
        logger.warning('[bridge] copy_preset error: %s', exc)
        await reply({'type': 'preset_copied', 'id': req_id, 'ok': False, 'error': str(exc)})


async def on_delete_preset(inst, msg: dict, reply) -> None:
    """删除用户根 preset (随附只读). 走 agent_preset routine (op=delete)."""
    req_id = msg.get('id')
    pid = str(msg.get('preset_id') or '').strip()
    if not pid:
        await reply({'type': 'preset_deleted', 'id': req_id, 'ok': False,
                     'error': 'preset_id is required'})
        return
    try:
        result = await inst.call('agent_preset', {'op': 'delete', 'id': pid})
        await reply({'type': 'preset_deleted', 'id': req_id, 'preset_id': pid, **(result or {})})
    except Exception as exc:
        logger.warning('[bridge] delete_preset error: %s', exc)
        await reply({'type': 'preset_deleted', 'id': req_id, 'preset_id': pid,
                     'ok': False, 'error': str(exc)})
