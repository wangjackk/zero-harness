"""Agent 管理的 HTTP/WS handler 函数.

由 ``server.WebServer._on_client_message`` 和 ``app.build_app`` 调用.
模块级函数(接收 ``inst`` 第一参数),复用 self.ctx.req 查询 manager routine.

kind -> manager 路由: prime (coding agent) / xml (reactive chat agent).
manager routine 是独立 passive routine, 可能跑在另一个进程, 故按 name 查
running 列表拿 id.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PRIME_MANAGER_NAME = 'prime_agent_manager'
_XML_MANAGER_NAME = 'xml_agents'
_MANAGER_REQ_TIMEOUT = 10.0

# kind -> (entry routine name, manager name)
_KIND_ROUTING = {
    'prime': ('create_prime_agent', _PRIME_MANAGER_NAME),
    'xml': ('create_xml_agent', _XML_MANAGER_NAME),
}


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


async def _list_from_manager(inst, manager_name: str, kind: str) -> list:
    """单个 manager 的 agent 列表(标注 kind; manager 不在时返回空)."""
    manager_id = await find_manager_id(inst, manager_name)
    if manager_id is None:
        return []
    try:
        result = await inst.ctx.req(
            manager_id, 'list_agents', {},
            timeout=_MANAGER_REQ_TIMEOUT,
        )
    except Exception as exc:
        logger.warning('[bridge] list_agents (%s) error: %s', kind, exc)
        return []
    items = list(result.get('agents') or [])
    for it in items:
        it['kind'] = kind
    return items


async def on_create_agent(inst, msg: dict, reply) -> None:
    """创建 agent (prime / xml). msg 字段: kind/project_dir/agent_id/model/..."""
    req_id = msg.get('id')
    kind = str(msg.get('kind') or 'prime').strip().lower() or 'prime'
    routing = _KIND_ROUTING.get(kind)
    if routing is None:
        await reply({'type': 'agent_created', 'id': req_id, 'kind': kind,
                     'ok': False, 'error': f'unknown agent kind: {kind}'})
        return
    entry_name, _ = routing
    payload = {k: v for k, v in msg.items() if k not in ('type', 'id', 'kind')}
    try:
        result = await inst.call(entry_name, payload)
        await reply({'type': 'agent_created', 'id': req_id, 'kind': kind, **result})
    except Exception as exc:
        logger.warning('[bridge] create_agent (%s) error: %s', kind, exc)
        await reply({'type': 'agent_created', 'id': req_id, 'kind': kind,
                     'ok': False, 'error': str(exc)})


async def on_list_agents(inst, msg: dict, reply) -> None:
    """列出所有 agent (prime + xml, 聚合两个 manager)."""
    req_id = msg.get('id')
    try:
        items = []
        for manager_name, kind in (
            (_PRIME_MANAGER_NAME, 'prime'),
            (_XML_MANAGER_NAME, 'xml'),
        ):
            items.extend(await _list_from_manager(inst, manager_name, kind))
        await reply({'type': 'agents', 'id': req_id, 'agents': items})
    except Exception as exc:
        logger.warning('[bridge] list_agents error: %s', exc)
        await reply({'type': 'agents', 'id': req_id, 'agents': [], 'error': str(exc)})


async def on_resume_agent(inst, msg: dict, reply) -> None:
    """恢复 agent. msg 字段: kind/agent_id/model/...

    直接 req manager (不走 entry routine): resume 是对已存在 agent 的操作,
    跟 stop 语义一致.
    """
    req_id = msg.get('id')
    kind = str(msg.get('kind') or 'prime').strip().lower() or 'prime'
    routing = _KIND_ROUTING.get(kind)
    if routing is None:
        await reply({'type': 'agent_resumed', 'id': req_id, 'kind': kind,
                     'ok': False, 'error': f'unknown agent kind: {kind}'})
        return
    _, manager_name = routing
    payload = {k: v for k, v in msg.items() if k not in ('type', 'id', 'kind')}
    manager_id = await find_manager_id(inst, manager_name)
    if manager_id is None:
        await reply({'type': 'agent_resumed', 'id': req_id, 'kind': kind,
                     'ok': False, 'error': f'{kind} manager not running'})
        return
    try:
        result = await inst.ctx.req(
            manager_id, 'resume_agent', payload,
            timeout=_MANAGER_REQ_TIMEOUT,
        )
        await reply({'type': 'agent_resumed', 'id': req_id, 'kind': kind, **result})
    except Exception as exc:
        logger.warning('[bridge] resume_agent (%s) error: %s', kind, exc)
        await reply({'type': 'agent_resumed', 'id': req_id, 'kind': kind,
                     'ok': False, 'error': str(exc)})


async def on_stop_agent(inst, msg: dict, reply) -> None:
    """停止 agent. 先 prime, 再 xml."""
    req_id = msg.get('id')
    agent_id = str(msg.get('agent_id') or '').strip()
    if not agent_id:
        await reply({'type': 'agent_stopped', 'id': req_id, 'ok': False,
                     'error': 'agent_id is required'})
        return
    result = None
    for manager_name, kind in (
        (_PRIME_MANAGER_NAME, 'prime'),
        (_XML_MANAGER_NAME, 'xml'),
    ):
        manager_id = await find_manager_id(inst, manager_name)
        if manager_id is None:
            continue
        try:
            res = await inst.ctx.req(
                manager_id, 'stop_agent', {'agent_id': agent_id},
                timeout=_MANAGER_REQ_TIMEOUT,
            )
        except Exception as exc:
            logger.warning('[bridge] stop_agent (%s) error: %s', kind, exc)
            continue
        if res.get('ok'):
            result = {**res, 'kind': kind}
            break
        result = result or res
    # 清理本地 ns 缓存(由 register_agent 填充)
    inst._agent_ns.pop(agent_id, None)
    inst._agent_names.pop(agent_id, None)
    inst._agent_rids.pop(agent_id, None)
    if result is None:
        result = {'ok': False, 'error': 'no agent manager running'}
    await reply({'type': 'agent_stopped', 'id': req_id, 'agent_id': agent_id, **result})


async def on_delete_agent(inst, msg: dict, reply) -> None:
    """删除 agent (DB agents 行 + messages). 先 prime, 再 xml.

    live 的 agent 拒绝删除 (前端应先 stop 再 delete). 删完不可恢复.
    """
    req_id = msg.get('id')
    agent_id = str(msg.get('agent_id') or '').strip()
    if not agent_id:
        await reply({'type': 'agent_deleted', 'id': req_id, 'ok': False,
                     'error': 'agent_id is required'})
        return
    result = None
    for manager_name, kind in (
        (_PRIME_MANAGER_NAME, 'prime'),
        (_XML_MANAGER_NAME, 'xml'),
    ):
        manager_id = await find_manager_id(inst, manager_name)
        if manager_id is None:
            continue
        try:
            res = await inst.ctx.req(
                manager_id, 'delete_agent', {'agent_id': agent_id},
                timeout=_MANAGER_REQ_TIMEOUT,
            )
        except Exception as exc:
            logger.warning('[bridge] delete_agent (%s) error: %s', kind, exc)
            continue
        if res.get('ok'):
            result = {**res, 'kind': kind}
            break
        result = result or res
    if result is None:
        result = {'ok': False, 'error': 'no agent manager running'}
    await reply({'type': 'agent_deleted', 'id': req_id, 'agent_id': agent_id, **result})


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
