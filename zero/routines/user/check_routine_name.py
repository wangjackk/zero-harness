"""check_routine_name ---- 确认 routine 名字是否可用.

热重载是同名覆盖: dynamic 里起了与正式 routine 相同的 name 会劫持其路由.
新建 routine 起名前先查一次.

调用示例:
    run_routine({name: 'check_routine_name', kwargs: {name: 'my_thing'}})
"""
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field

from routine import Routine


class CheckRoutineNameInput(BaseModel):
    name: str = Field(description='待确认的 routine name (name 属性, 非类名/文件名)')


class CheckRoutineNameOutput(BaseModel):
    free: bool = Field(description='True = 名字可用; False = 已被占用')
    conflict: Optional[Dict[str, Any]] = Field(
        None,
        description='占用者信息 {name, hub_id, is_passive}; free=True 时为 null',
    )
    for_llm: str = Field(default='', description='一句话结论, 直接喂给 LLM')


class CheckRoutineName(Routine):
    """确认 routine 名字未被占用(新建前查重)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '确认 routine 名字是否可用. 热重载同名覆盖会劫持路由, '
                       '新建 routine 起名前先查.',
        'input_schema': CheckRoutineNameInput.model_json_schema(),
        'output_schema': CheckRoutineNameOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        inp = CheckRoutineNameInput.model_validate(kwargs)
        try:
            routines = await self.get_routines()
        except NotImplementedError:
            return {'free': True, 'conflict': None,
                    'for_llm': 'get_routines not implemented, 无法查重, 视为可用'}
        except Exception as exc:
            return {'free': False, 'conflict': None,
                    'for_llm': f'查询失败: {type(exc).__name__}: {exc}'}

        for r in routines:
            if r.get('name') == inp.name:
                return {'free': False, 'conflict': r,
                        'for_llm': f"'{inp.name}' 已被占用 (hub: "
                                   f"{r.get('hub_id', '?')}), 换个名字"}
        return {'free': True, 'conflict': None, 'for_llm': f"'{inp.name}' 可用"}
