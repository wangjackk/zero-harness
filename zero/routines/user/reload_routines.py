"""ReloadRoutines -- 手动强制重注册 routine / routine 包(watcher 逃生口).

重注册逻辑收归 RoutinesWatcher(``@request('reload')``), 本 routine 只是
用户 / agent 侧的薄代理: 按名找 watcher rid → req 转发 → 透传结果摘要.
怀疑热重载没收敛(如 yaml 条目增删误杀包内同名 routine)时用它强制重载.

用法::

    run_routine('reload_routines')                                 # 全部条目
    run_routine('reload_routines', {'path': 'routines/user/xml'})   # 单条目(包)
    run_routine('reload_routines', {'path': 'routines/user/ask.py'})

注意: reload 替换的是模块里的类对象, 已在跑的实例仍持旧类到自然结束;
agent 这类常驻实例要用新代码需重建实例.
"""
from typing import Any, Dict

from pydantic import BaseModel, Field

from routine import Routine


class ReloadRoutinesInput(BaseModel):
    """reload_routines input: path 是 routines.yaml 条目路径, 缺省全部."""

    path: str | None = Field(
        None,
        description='routines.yaml 条目路径(如 routines/user/xml 或 '
                    'routines/user/ask.py); 缺省重载全部条目',
    )


class ReloadRoutines(Routine):
    """强制重载并重注册 routine/包(转发给 routines_watcher)."""

    meta = {
        'description': '强制重载并重注册 routine/包(watcher 热重载逃生口)',
        'input_schema': ReloadRoutinesInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = ReloadRoutinesInput(**kwargs)
        rid = None
        for it in await self.get_running_routines():
            if it.get('name') == 'routines_watcher':
                rid = it.get('id')
                break
        if rid is None:
            return {'error': 'routines_watcher not running', 'reloaded': []}
        return await self.req(rid, 'reload', {'path': inp.path}, timeout=60.0)
