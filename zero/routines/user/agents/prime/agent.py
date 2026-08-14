"""PrimeAgent ---- 极简 agent, 只有 ipython 一个 tool.

继承 ReactorAgent, 复用其 ContextProvider + ReactLoop + 全部生命周期.
与 reactor 的区别: 用 prime 风格系统提示词 (IPython 作为持久控制环境),
manager 端硬编码 enabled_tools=['ipython'].

设计参考: E:\\code\\pyfiles\\prime-agent
prime skills (routine, hub_routine) seed 到 workspace, 和经典 builtin skill 共存,
统一从 workspace/skills/ 加载.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, ClassVar, Dict

from routine.logger import setup_logger
from .kernel_env import PRIME_SKILLS_DIR
from .prompt import build_prime_system_prompt
from .reactor import ReactorAgent, ReactorAgentInput, ReactorAgentOutput

_log = setup_logger('prime.agent')


class PrimeAgentInput(ReactorAgentInput):
    pass


class PrimeAgentOutput(ReactorAgentOutput):
    pass


class PrimeAgent(ReactorAgent):
    """Prime agent: ReactorAgent restricted to the ipython tool, with a
    prime-style system prompt that treats IPython as the persistent control
    environment."""

    name = 'prime_agent'

    meta: ClassVar[Dict[str, Any]] = {
        'hidden': True,
        'input_schema': PrimeAgentInput.model_json_schema(),
        'output_schema': PrimeAgentOutput.model_json_schema(),
        'description': (
            'Prime agent with only the ipython tool. '
            'Uses IPython as a persistent control environment for reasoning, '
            'state, and tool orchestration.'
        ),
    }

    def _seed_extra_skills(self, workspace: Path) -> int:
        """把 prime/skills/ 下的 skill 拷到 workspace/skills/, 和经典 builtin 共存."""
        target = workspace / 'skills'
        target.mkdir(parents=True, exist_ok=True)
        count = 0
        if not PRIME_SKILLS_DIR.is_dir():
            return 0
        for child in sorted(PRIME_SKILLS_DIR.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith('_') or child.name.startswith('.'):
                continue
            if not (child / 'SKILL.md').is_file():
                continue
            dst = target / child.name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(child, dst)
            count += 1
        return count

    def _build_system_prompt(
        self, *, params: ReactorAgentInput,
        skill_summaries: list[tuple[str, str]],
    ) -> str:
        """prime 风格系统提示词."""
        return build_prime_system_prompt(
            project_root=params.project_dir_root_path,
            extra=params.extra_instructions,
            agent_id=self._agent_id,
            skill_summaries=skill_summaries,
        )

    async def _register_with_bridge(self) -> None:
        from .._core.bridge import register_with_bridge
        await register_with_bridge(
            agent_id=self._agent_id,
            routine_id=self.id,
            name_prefix='Prime-',
            bridge_name=self._BRIDGE_NAME,
            ctx=self.ctx,
            stop_event=self._stop,
            logger=_log,
            on_success=self._emit_session_history,
        )
