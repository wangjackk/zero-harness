"""Read ---- 读取本地文件内容."""
from __future__ import annotations

import os
import sys
from typing import ClassVar, Dict, Any

from pydantic import BaseModel, Field
from routine import Routine

from .prompt import DESCRIPTION

from ..._shared._file_state import get_state
from zero.routines.user.agents._core.paths import AGENT_ID_KEY, resolve_tool_path

MAX_FILE_BYTES = 256 * 1024   # 256 KB,显式全量读取时的保护上限
class ReadInput(BaseModel):
    file_path: str = Field(
        description=(
            'Absolute path to the file to read. Must be absolute, not relative. '
            'It is okay if the file does not exist -- an error will be returned.'
        ),
    )
    offset: int = Field(
        1,
        description='1-based line number to start reading from. Negative values count backward from EOF (-1 is the last line).',
    )
    limit: int = Field(
        description='Required. Number of lines to read. Pass 0 only when you explicitly need to read to EOF.',
    )


class ReadOutput(BaseModel):
    content: str = Field(description='File contents with line numbers (cat -n format)')
    total_lines: int = Field(description='Total lines in file')
    truncated: bool = Field(description='Whether more lines remain after the requested range')


class Read(Routine):
    """Read a file from the local filesystem.

    - file_path must be an absolute path
    - Requires an explicit limit to avoid flooding the model context
    - Negative offsets count backward from EOF; offset=-1 starts at the last line
    - Pass limit=0 only when you intentionally need to read to EOF
    - Returns content with line numbers (cat -n format) for easy reference
    - Works with text files; binary files return an error
    - If the file does not exist, returns an error (does not crash)
    """

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': ReadInput.model_json_schema(),
        'output_schema': ReadOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        session_id = state.get('session_id') or ''
        project_root = state.get('project_root')
        inp = ReadInput(**kwargs)
        path = resolve_tool_path(inp.file_path, project_root)

        if not os.path.exists(path):
            raise FileNotFoundError(f'File does not exist: {path}')
        if os.path.isdir(path):
            raise IsADirectoryError(f'Path is a directory, not a file: {path}. Use Bash(ls) to list directories.')

        if inp.limit < 0:
            raise ValueError('limit must be >= 0. Pass 0 only to explicitly read to EOF.')

        full_read = inp.offset in (0, 1) and inp.limit == 0
        if full_read:
            size = os.path.getsize(path)
            if size > MAX_FILE_BYTES:
                raise ValueError(
                    f'File too large to read in full ({size / 1024:.0f} KB, max {MAX_FILE_BYTES // 1024} KB). '
                    'Use offset/limit to read a specific line range.'
                )

        try:
            selected, total, truncated, state_content, start_idx = path_read_lines(
                path,
                offset=inp.offset,
                limit=inp.limit,
                keep_full_text=full_read,
            )
        except UnicodeDecodeError:
            raise ValueError(f'File appears to be binary and cannot be read as text: {path}')

        # cat -n 格式:右对齐行号,与文件真实行号对齐
        width = len(str(total))
        lines_out = [
            f'{start_idx + i + 1:>{width}}|{line}'
            for i, line in enumerate(selected)
        ]

        if not lines_out:
            return 'File is empty.' if total == 0 else 'No lines in requested range.'

        result = '\n'.join(lines_out)
        if truncated:
            result += f'\n\n[File truncated. Use offset/limit to read more. Total lines: {total}.]'

        # 写入 readFileState 供 Edit/Write 做 read-before-edit 校验
        if session_id:
            get_state(session_id).set(
                path,
                state_content,
                offset=None if full_read else inp.offset,
                limit=None if full_read else inp.limit,
            )

        return result


def path_read_lines(
    path: str,
    *,
    offset: int,
    limit: int,
    keep_full_text: bool,
) -> tuple[list[str], int, bool, str, int]:
    enc = sys.stdout.encoding or 'utf-8'
    total = _count_lines(path, enc)
    start_idx = _resolve_start_idx(offset, total)
    end_idx = None if limit == 0 else start_idx + limit
    selected: list[str] = []
    all_lines: list[str] | None = [] if keep_full_text else None

    try:
        with open(path, encoding='utf-8') as f:
            for idx, raw_line in enumerate(f):
                line = raw_line.rstrip('\n').rstrip('\r')
                if all_lines is not None:
                    all_lines.append(line)
                if idx >= start_idx and (end_idx is None or idx < end_idx):
                    selected.append(line)
    except UnicodeDecodeError:
        selected = []
        all_lines = [] if keep_full_text else None
        with open(path, encoding=enc, errors='replace') as f:
            for idx, raw_line in enumerate(f):
                line = raw_line.rstrip('\n').rstrip('\r')
                if all_lines is not None:
                    all_lines.append(line)
                if idx >= start_idx and (end_idx is None or idx < end_idx):
                    selected.append(line)

    truncated = end_idx is not None and end_idx < total
    state_content = '\n'.join(all_lines if all_lines is not None else selected)
    return selected, total, truncated, state_content, start_idx


def _resolve_start_idx(offset: int, total: int) -> int:
    if offset < 0:
        return max(0, total + offset)
    return max(0, offset - 1)


def _count_lines(path: str, encoding: str) -> int:
    try:
        with open(path, encoding='utf-8') as f:
            return sum(1 for _ in f)
    except UnicodeDecodeError:
        with open(path, encoding=encoding, errors='replace') as f:
            return sum(1 for _ in f)
