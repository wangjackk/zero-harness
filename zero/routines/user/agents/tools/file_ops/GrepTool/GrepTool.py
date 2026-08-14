"""Grep - search file contents with ripgrep."""
from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Dict, Literal

from pydantic import BaseModel, Field
from routine import Routine

from ..._shared._ripgrep import ripgrep_path
from zero.routines.user.agents._core.paths import display_tool_path, pop_project_root, resolve_optional_tool_path
from .prompt import DESCRIPTION

_DEFAULT_HEAD_LIMIT = 250
_VCS_DIRS = ('.git', '.svn', '.hg', '.bzr', '.jj')


class GrepInput(BaseModel):
    pattern: str = Field(
        description='The regular expression pattern to search for in file contents',
    )
    path: str | None = Field(
        None,
        description='File or directory to search in. Defaults to current working directory.',
    )
    glob: str | None = Field(
        None,
        description='Glob pattern to filter files, e.g. "*.py", "*.{ts,tsx}"',
    )
    type: str | None = Field(
        None,
        description='File type filter, e.g. "py", "js". More efficient than glob for standard types.',
    )
    output_mode: Literal['content', 'files_with_matches', 'count'] = Field(
        'files_with_matches',
        description=(
            '"content" shows matching lines; '
            '"files_with_matches" shows file paths (default); '
            '"count" shows match counts per file.'
        ),
    )
    context: int = Field(
        0,
        description='Lines of context before/after each match. Only applies to output_mode=content.',
    )
    case_insensitive: bool = Field(False, description='Case-insensitive search.')
    head_limit: int = Field(
        _DEFAULT_HEAD_LIMIT,
        description='Max results to return. Pass 0 for unlimited (use sparingly).',
    )
    offset: int = Field(0, description='Skip first N results. Use with head_limit for pagination.')
    multiline: bool = Field(False, description='Enable multiline matching where . matches newlines.')


class GrepOutput(BaseModel):
    content: str = Field(description='Formatted search results')
    num_matches: int = Field(description='Number of result lines returned')
    truncated: bool = Field(description='Whether results were truncated by head_limit')


class Grep(Routine):
    """Search file contents with ripgrep. Prefer this over shell grep/rg commands."""

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': GrepInput.model_json_schema(),
        'output_schema': GrepOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        project_root = pop_project_root(kwargs)
        inp = GrepInput(**kwargs)

        search_path = resolve_optional_tool_path(inp.path, project_root)
        lines = await _search(inp, search_path, project_root)
        lines, truncated = _apply_limit(lines, inp.head_limit, inp.offset)
        return _format_output(lines, truncated, inp.head_limit, inp.offset)


async def _search(inp: GrepInput, search_path: str, project_root: str | None = None) -> list[str]:
    try:
        return await RipgrepGrepImplementation().search(inp, search_path, project_root)
    except FileNotFoundError:
        return await PythonGrepImplementation().search(inp, search_path, project_root)


class GrepImplementation(ABC):
    @abstractmethod
    async def search(self, inp: GrepInput, search_path: str, project_root: str | None = None) -> list[str]:
        """Search file contents and return formatted result lines."""


class RipgrepGrepImplementation(GrepImplementation):
    async def search(self, inp: GrepInput, search_path: str, project_root: str | None = None) -> list[str]:
        args = _build_rg_args(inp)
        lines = await _run_rg(args, search_path)
        return [_rewrite_ripgrep_line(line, search_path, project_root) for line in lines]


class PythonGrepImplementation(GrepImplementation):
    async def search(self, inp: GrepInput, search_path: str, project_root: str | None = None) -> list[str]:
        flags = re.IGNORECASE if inp.case_insensitive else 0
        if inp.multiline:
            flags |= re.DOTALL | re.MULTILINE
        regex = re.compile(inp.pattern, flags)

        results: list[str] = []
        max_results = _max_results_needed(inp)
        for file_path in _iter_candidate_files(Path(search_path), inp):
            if _should_stop(results, max_results):
                break
            try:
                text = file_path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue

            if inp.multiline:
                matches = list(regex.finditer(text))
                if not matches:
                    continue
                rel = _relative_to_search_path(file_path, search_path, project_root)
                if inp.output_mode == 'files_with_matches':
                    results.append(rel)
                elif inp.output_mode == 'count':
                    results.append(f'{rel}:{len(matches)}')
                else:
                    for match in matches:
                        line_no = text.count('\n', 0, match.start()) + 1
                        snippet = match.group(0).splitlines()[0] if match.group(0) else ''
                        results.append(f'{rel}:{line_no}:{snippet}')
                        if _should_stop(results, max_results):
                            break
                continue

            lines = text.splitlines()
            match_indexes = [i for i, line in enumerate(lines) if regex.search(line)]
            if not match_indexes:
                continue

            rel = _relative_to_search_path(file_path, search_path, project_root)
            if inp.output_mode == 'files_with_matches':
                results.append(rel)
            elif inp.output_mode == 'count':
                results.append(f'{rel}:{len(match_indexes)}')
            else:
                included: set[int] = set()
                for i in match_indexes:
                    start = max(0, i - inp.context)
                    end = min(len(lines), i + inp.context + 1)
                    included.update(range(start, end))
                for i in sorted(included):
                    results.append(f'{rel}:{i + 1}:{lines[i]}')
                    if _should_stop(results, max_results):
                        break

        return results


def _build_rg_args(inp: GrepInput) -> list[str]:
    args = [ripgrep_path(), '--hidden', '--max-columns', '500']
    for directory in _VCS_DIRS:
        args += ['--glob', f'!{directory}']

    if inp.multiline:
        args += ['-U', '--multiline-dotall']
    if inp.case_insensitive:
        args.append('-i')

    if inp.output_mode == 'files_with_matches':
        args.append('-l')
    elif inp.output_mode == 'count':
        args.append('-c')
    else:
        args.append('-n')
        if inp.context:
            args += ['-C', str(inp.context)]

    if inp.glob:
        for glob_pattern in inp.glob.replace(',', ' ').split():
            args += ['--glob', glob_pattern]

    if inp.type:
        args += ['--type', inp.type]

    if inp.pattern.startswith('-'):
        args += ['-e', inp.pattern]
    else:
        args.append(inp.pattern)

    return args


async def _run_rg(args: list[str], cwd: str) -> list[str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode not in (0, 1):
        err = stderr.decode(errors='replace').strip()
        raise RuntimeError(f'ripgrep failed (exit {proc.returncode}): {err}')
    text = stdout.decode(errors='replace')
    return [line for line in text.splitlines() if line]


def _iter_candidate_files(search_path: Path, inp: GrepInput):
    if search_path.is_file():
        if _matches_filters(search_path, search_path.parent, inp):
            yield search_path
        return

    for root, dirs, files in os.walk(search_path):
        dirs[:] = [d for d in dirs if d not in _VCS_DIRS]
        root_path = Path(root)
        for filename in files:
            file_path = root_path / filename
            if _matches_filters(file_path, search_path, inp):
                yield file_path


def _matches_filters(file_path: Path, base: Path, inp: GrepInput) -> bool:
    if _is_binary_like(file_path):
        return False

    rel = file_path.relative_to(base) if _is_relative_to(file_path, base) else file_path
    rel_text = rel.as_posix()

    if inp.glob:
        patterns = [
            expanded
            for raw in inp.glob.replace(',', ' ').split()
            for expanded in _expand_brace_glob(raw)
        ]
        if not any(fnmatch.fnmatch(rel_text, pattern) or fnmatch.fnmatch(file_path.name, pattern) for pattern in patterns):
            return False

    if inp.type:
        extensions = _TYPE_EXTENSIONS.get(inp.type, {f'.{inp.type}'})
        if file_path.suffix not in extensions:
            return False

    return True


def _is_binary_like(file_path: Path) -> bool:
    if file_path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    try:
        return file_path.stat().st_size > _MAX_FALLBACK_FILE_BYTES
    except OSError:
        return True


def _max_results_needed(inp: GrepInput) -> int | None:
    if inp.head_limit == 0:
        return None
    return inp.offset + inp.head_limit + 1


def _should_stop(results: list[str], max_results: int | None) -> bool:
    return max_results is not None and len(results) >= max_results


def _expand_brace_glob(pattern: str) -> list[str]:
    match = re.search(r'\{([^{}]+)\}', pattern)
    if not match:
        return [pattern]
    prefix = pattern[:match.start()]
    suffix = pattern[match.end():]
    return [prefix + option + suffix for option in match.group(1).split(',')]


def _rewrite_ripgrep_line(line: str, search_path: str, project_root: str | None = None) -> str:
    path_text, sep, rest = line.partition(':')
    if not sep:
        return display_tool_path(Path(search_path) / line, project_root)

    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = Path(search_path) / candidate
    return f'{display_tool_path(candidate, project_root)}:{rest}'


def _relative_to_search_path(file_path: Path, search_path: str, project_root: str | None = None) -> str:
    base = Path(search_path)
    if base.is_file():
        base = base.parent
    if project_root:
        return display_tool_path(file_path, project_root)
    try:
        return file_path.relative_to(base).as_posix()
    except ValueError:
        return str(file_path)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


_TYPE_EXTENSIONS = {
    'py': {'.py'},
    'js': {'.js', '.jsx', '.mjs', '.cjs'},
    'ts': {'.ts', '.tsx', '.mts', '.cts'},
    'tsx': {'.tsx'},
    'jsx': {'.jsx'},
    'json': {'.json'},
    'md': {'.md', '.markdown'},
    'rs': {'.rs'},
    'go': {'.go'},
    'java': {'.java'},
    'html': {'.html', '.htm'},
    'css': {'.css'},
    'vue': {'.vue'},
}

_MAX_FALLBACK_FILE_BYTES = 2 * 1024 * 1024
_BINARY_EXTENSIONS = {
    '.7z', '.bin', '.bmp', '.db', '.dll', '.doc', '.docx', '.exe', '.gif',
    '.ico', '.jar', '.jpeg', '.jpg', '.lockb', '.mp3', '.mp4', '.pdf',
    '.png', '.pyc', '.sqlite', '.sqlite3', '.tar', '.tiff', '.wasm', '.wal',
    '.webp', '.xls', '.xlsx', '.zip',
}


def _apply_limit(lines: list[str], head_limit: int, offset: int) -> tuple[list[str], bool]:
    sliced = lines[offset:]
    if head_limit == 0:
        return sliced, False
    return sliced[:head_limit], len(sliced) > head_limit


def _format_output(lines: list[str], truncated: bool, head_limit: int, offset: int) -> str:
    if not lines:
        return 'No matches found'
    result = '\n'.join(lines)
    notes = []
    if truncated:
        notes.append(f'showing first {head_limit} results')
    if offset:
        notes.append(f'offset {offset}')
    if notes:
        result += f'\n\n[{", ".join(notes)}; pass head_limit=0 for unlimited]'
    return result
