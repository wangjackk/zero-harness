"""SkillTool ---- 模型侧 `skill` 工具 (dsh dsh-tool-skill 工具面移植).

对齐 dsh: 模型传精确 kebab-case name, 工具按调用 agent 的 cwd 解析
winner, 返回正文全文. 结果即工具结果 (不追加合成上下文消息).

身份链: 框架注入 from_agent_id → fetch_agent_state (生态协作, 非
import) → project_root 作为 cwd. 无 agent 上下文时只扫用户级根.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine
from routine.logger import setup_logger

from .skills import render_skill_content, skills_req
from .wire import AGENT_ID_KEY

_log = setup_logger('flow.skill_tool')


class SkillInput(BaseModel):
    name: str = Field(description='Exact kebab-case skill name from the '
                                  'available skills listing.')


class SkillOutput(BaseModel):
    for_llm: str = Field(description='Skill content, resource base guidance, '
                                     'or an error message.')


class SkillTool(Routine):
    """Load a skill's full instructions by exact name."""

    name = 'skill'

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': SkillInput.model_json_schema(),
        'output_schema': SkillOutput.model_json_schema(),
        'description': (
            'Load a skill (a reusable set of task-specific instructions) '
            'by its exact name from the available skills listing.'
        ),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        inp = SkillInput(**kwargs)

        cwd = ''
        if agent_id:
            try:
                state = await self.call('fetch_agent_state',
                                        {'agent_id': agent_id})
                if isinstance(state, dict) and state.get('ok'):
                    cwd = str(state.get('project_root') or '')
            except Exception as exc:
                _log.warning('fetch_agent_state failed: %r', exc)

        try:
            result = await skills_req(self, 'load',
                                      {'name': inp.name, 'cwd': cwd,
                                       'channel': 'model'})
        except Exception as exc:
            _log.warning('skill load failed: %r', exc)
            return SkillOutput(
                for_llm=f'skill registry request failed: {exc}'
            ).model_dump()
        if result is None:
            return SkillOutput(
                for_llm='skill registry is not running'
            ).model_dump()
        if not result.get('ok'):
            err = str(result.get('error') or 'skill load failed')
            return SkillOutput(for_llm=err).model_dump()

        return SkillOutput(for_llm=render_skill_content(
            str(result['name']), str(result['resource_base']),
            str(result['content']))).model_dump()
