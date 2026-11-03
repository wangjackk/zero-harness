"""shell/registry —— 把 Shell 的 registry 控制面暴露成普通 routine.

creator 在 code 里只有 call(name, ...), 没有 req(rid, ...); 这个薄桥让
动态 routine 注册后能挂进/摘出当前 Moss 词表. 真正的词表与 meta 重扫仍
只有 Shell.registry 一份实现.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field

from routine.routine import Routine

__all__ = ['ShellRegistry']


class ShellRegistryInput(BaseModel):
    op: Literal['reg', 'unreg', 'list'] = Field(
        default='list', description='reg=挂进词表; unreg=摘出; list=读快照')
    routine: str = Field(
        default='', description='要挂/摘的注册名; op=list 时留空')


class ShellRegistry(Routine):
    """转发到当前 Shell 的词表控制面; 供 creator 与运维代码调用."""

    name = 'shell/registry'
    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'description': '维护当前 Moss 的 CTML 词表(动态 routine 注册后需挂载)',
        'input_schema': ShellRegistryInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = ShellRegistryInput.model_validate(kwargs or {})
        if inp.op != 'list' and not inp.routine.strip():
            raise ValueError('routine is required for reg/unreg')
        shell = ''
        for row in await self.get_running_routines():
            if row.get('name') == 'shell':
                shell = str(row.get('id') or '')
                break
        if not shell:
            raise RuntimeError('routine not running: shell')
        result = await self.req(shell, 'registry', {
            'op': inp.op, 'routine': inp.routine.strip(),
        })
        verb = inp.routine.strip()
        content = ({'reg': f'{verb} 已挂进当前词表',
                    'unreg': f'{verb} 已摘出当前词表'}.get(inp.op)
                   or '当前词表快照已返回')
        return {'content': content, 'details': result}

    async def stop(self) -> None:
        pass
