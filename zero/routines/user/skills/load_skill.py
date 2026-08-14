"""load_skill — 加载并返回 skill 的完整内容(作为 tool result 注入对话上下文)

Progressive Disclosure 模式:
  LLM 先 list_skills 看有哪些技能(只有名字+简介),
  确定需要时调 load_skill(name),完整提示词作为 tool 返回值出现在对话里,
  之后 LLM 自然遵循这些指令(无需改 system prompt,保留推理因果链)。

通用层 routine: 通过 ctx.req 反向获取 agent_state.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from zero.routines.user.agents._core.paths import AGENT_ID_KEY

from .registry import build_registry


class LoadSkillInput(BaseModel):
    name: str = Field(description='Skill name (from list_skills output)')


class LoadSkillOutput(BaseModel):
    name: str
    instructions: str


def do_load(skill_dir: str | None, name: str) -> Dict[str, Any]:
    """纯函数: 加载指定 skill 的完整正文."""
    if not name:
        return {'error': 'name is required'}
    reg = build_registry(skill_dir)
    try:
        content = reg.invoke(name)
    except KeyError:
        available = [s.name for s in reg.list_skills()]
        return {'error': f'skill not found: {name!r}. Available: {available}'}
    return {'name': name, 'instructions': content}


class LoadSkill(Routine):
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Load a skill\'s full instructions into the conversation. After loading, follow the skill\'s instructions carefully.',
        'input_schema': LoadSkillInput.model_json_schema(),
        'output_schema': LoadSkillOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        skill_dir = state.get('skill_dir')
        name = (kwargs.get('name') or '').strip()
        return do_load(skill_dir, name)
