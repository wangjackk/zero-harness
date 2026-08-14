"""Write ---- 创建或覆盖文件.

对齐 claude-code FileWriteTool 的语义:
- 已存在文件:必须先 Read(readFileState 校验),且 mtime 不能改变
- 新建文件:无需先 Read,直接写入
"""
from __future__ import annotations

import os
from typing import ClassVar, Dict, Any, Literal

from pydantic import BaseModel, Field

from routine import Routine
from .prompt import DESCRIPTION
from ..._shared._file_state import get_state
from zero.routines.user.agents._core.paths import AGENT_ID_KEY, display_tool_path, resolve_tool_path


class WriteInput(BaseModel):
    file_path: str = Field(
        description='Absolute path to the file to write.',
    )
    content: str = Field(
        description='The content to write. Overwrites the file completely if it already exists.',
    )


class WriteOutput(BaseModel):
    type: Literal['create', 'update'] = Field(
        description='"create" for new files, "update" for existing files.',
    )
    file_path: str


class Write(Routine):
    """Write a file to the local filesystem. Creates parent directories as needed.

    Existing files require a prior Read in this session (mtime must not have changed).
    New files can be written directly without a prior Read.
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': WriteInput.model_json_schema(),
        'output_schema': WriteOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        project_root = state.get('project_root')

        inp = WriteInput(**kwargs)
        path = resolve_tool_path(inp.file_path, project_root)

        if os.path.isdir(path):
            raise IsADirectoryError(f'Path is a directory: {path}')

        exists = os.path.exists(path)

        # ── read-before-write 校验(仅已存在文件,对齐 claude-code FileWriteTool)──
        if exists:
            if session_id:
                state = get_state(session_id)
                ok, err = state.validate_for_write(path)
                if not ok:
                    raise PermissionError(err)

        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(inp.content)

        # 写后更新 readFileState
        if session_id:
            get_state(session_id).update_after_write(path, inp.content)

        if exists:
            return f'The file {display_tool_path(path, project_root)} has been updated successfully.'
        return f'File created successfully at: {display_tool_path(path, project_root)}'
