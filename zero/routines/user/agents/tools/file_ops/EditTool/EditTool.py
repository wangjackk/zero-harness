"""Edit ---- 字符串替换式局部修改文件.

对齐 claude-code FileEditTool 的核心语义:
- 执行前检查 readFileState(需先 Read,且 mtime 不能比 read 时更新)
- old_string 必须在文件中唯一匹配(除非 replace_all=True)
- old_string 为空且文件不存在时,相当于创建新文件(无需先 Read)
"""
from __future__ import annotations

import os
from typing import ClassVar, Dict, Any, Optional

from pydantic import BaseModel, Field
from routine import Modules, Routine

from .prompt import DESCRIPTION

from ..._shared._file_state import get_state
from zero.routines.user.agents._core.paths import AGENT_ID_KEY, PROJECT_DIR_ROOT_PATH_KEY, display_tool_path, resolve_tool_path


class EditInput(BaseModel):
    file_path: str = Field(
        description='Absolute path to the file to edit.',
    )
    old_string: str = Field(
        description=(
            'The exact string to replace. Must match exactly, including whitespace and indentation. '
            'Must be unique in the file unless replace_all=true. '
            'Use empty string to create a new file (file must not exist).'
        ),
    )
    new_string: str = Field(
        description='The replacement string. Use empty string to delete old_string.',
    )
    replace_all: bool = Field(
        False,
        description='Replace all occurrences. Default false (requires old_string to be unique).',
    )


class EditOutput(BaseModel):
    file_path: str
    replacements: int = Field(description='Number of replacements made')


class Edit(Routine):
    """Partial file modification via exact string replacement.

    Requires the file to have been Read in this session first.
    Errors if old_string is not unique (unless replace_all=True).
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': False,
        'input_schema': EditInput.model_json_schema(),
        'output_schema': EditOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        project_root = state.get('project_root')
        inp = EditInput(**kwargs)
        path = resolve_tool_path(inp.file_path, project_root)

        if inp.old_string == inp.new_string:
            raise ValueError('old_string and new_string are identical - no changes to make.')

        # 新文件创建路径(old_string 为空)
        if inp.old_string == '':
            return await self._create_new_file(path, inp.new_string, project_root, session_id)

        if not os.path.exists(path):
            raise FileNotFoundError(
                f'File does not exist: {path}. '
                'To create a new file, use old_string="" with new_string=<content>.'
            )

        # read-before-edit 校验(对齐 claude-code FileEditTool.validateInput)
        if session_id:
            state = get_state(session_id)
            ok, err = state.validate_for_write(path)
            if not ok:
                raise PermissionError(err)

        with open(path, encoding='utf-8', newline='') as f:
            content = f.read()

        count = content.count(inp.old_string)
        if count == 0:
            raise ValueError(
                f'String not found in file: {path!r}\n'
                f'String: {inp.old_string[:120]!r}'
            )
        if count > 1 and not inp.replace_all:
            raise ValueError(
                f'Found {count} occurrences of the string in {path!r}. '
                'Either add more context to make it unique, or set replace_all=true.'
            )

        if inp.replace_all:
            new_content = content.replace(inp.old_string, inp.new_string)
            replacements = count
        else:
            new_content = content.replace(inp.old_string, inp.new_string, 1)
            replacements = 1

        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_content)

        # 写后更新 readFileState
        if session_id:
            get_state(session_id).update_after_write(path, new_content)

        return (
            f'The file {display_tool_path(path, project_root)} has been updated successfully. '
            f'({replacements} replacement{"s" if replacements > 1 else ""})'
        )

    async def _create_new_file(self, path: str, content: str,
                               project_root: str | None = None,
                               session_id: str = '') -> str:
        if os.path.exists(path):
            raise FileExistsError(
                f'Cannot create file - already exists: {path}. '
                'Use Write to overwrite, or provide old_string to edit.'
            )
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
        # 新建文件写入 state(后续 edit 不需要再 read)
        if session_id:
            get_state(session_id).update_after_write(path, content)
        return f'File created successfully at: {display_tool_path(path, project_root)}'
