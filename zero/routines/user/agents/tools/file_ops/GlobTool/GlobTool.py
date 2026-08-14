"""Glob - find files by glob pattern with ripgrep."""
from __future__ import annotations

import asyncio
import fnmatch
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field
from routine import Routine

from ..._shared._ripgrep import ripgrep_path
from zero.routines.user.agents._core.paths import display_tool_path, pop_project_root, resolve_optional_tool_path
from .prompt import DESCRIPTION

_MAX_RESULTS = 100
_IGNORED_DIRS = {
    '.git',
    '.svn',
    '.hg',
    '.bzr',
    '.jj',
    '.idea',
    '.vscode',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.tox',
    '.venv',
    'node_modules',
}
_IGNORED_FILE_PATTERNS = (
    '*.pyc',
    '*.pyo',
)


class GlobInput(BaseModel):
    pattern: str = Field(
        description=(
            'Glob pattern to match files against. Examples: "*.py", "**/*.ts", "src/**/*.vue"'
        ),
    )
    path: str | None = Field(
        None,
        description=(
            'Directory to search in. Defaults to current working directory. '
            'Must be a valid directory path if provided. Do NOT pass "undefined" or "null".'
        ),
    )


class GlobOutput(BaseModel):
    filenames: list[str] = Field(description='Matched file paths, sorted by modification time')
    num_files: int
    truncated: bool = Field(description='Whether results were limited to 100 files')
    duration_ms: int


class Glob(Routine):
    """Find files matching a glob pattern, sorted by modification time."""

    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': GlobInput.model_json_schema(),
        'output_schema': GlobOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> str:
        project_root = pop_project_root(kwargs)
        inp = GlobInput(**kwargs)

        started_at = _now_ms()
        base = Path(resolve_optional_tool_path(inp.path, project_root))
        if not base.exists():
            raise FileNotFoundError(f'Directory does not exist: {base}')
        if not base.is_dir():
            raise NotADirectoryError(f'Path is not a directory: {base}')

        files = await _find_files(inp.pattern, base)
        truncated = len(files) > _MAX_RESULTS
        filenames = [display_tool_path(path, project_root) for path in files[:_MAX_RESULTS]]

        if not filenames:
            return 'No files found'

        result = '\n'.join(filenames)
        if truncated:
            result += '\n\n(Results truncated at 100 files. Use a more specific pattern.)'
        result += f'\n\n(duration_ms: {_now_ms() - started_at}, num_files: {len(filenames)})'
        return result


async def _find_files(pattern: str, base: Path) -> list[Path]:
    try:
        return await RipgrepGlobImplementation().find(pattern, base)
    except FileNotFoundError:
        return await PythonGlobImplementation().find(pattern, base)


class GlobImplementation(ABC):
    @abstractmethod
    async def find(self, pattern: str, base: Path) -> list[Path]:
        """Find files matching the glob pattern."""


class RipgrepGlobImplementation(GlobImplementation):
    async def find(self, pattern: str, base: Path) -> list[Path]:
        args = [
            ripgrep_path(),
            '--files',
            '--glob',
            pattern,
            '--sort=modified',
            *_rg_ignore_args(),
            str(base),
        ]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode not in (0, 1):
            err = stderr.decode(errors='replace').strip()
            raise RuntimeError(f'ripgrep failed (exit {proc.returncode}): {err}')

        return [
            path if path.is_absolute() else base / path
            for path in (Path(line) for line in stdout.decode(errors='replace').splitlines() if line)
            if not _is_ignored_path(path if path.is_absolute() else base / path, base)
        ]


class PythonGlobImplementation(GlobImplementation):
    async def find(self, pattern: str, base: Path) -> list[Path]:
        patterns = [pattern]
        if not pattern.startswith('**/'):
            patterns.append(f'**/{pattern}')

        matched: dict[Path, None] = {}
        for root, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not _is_ignored_dir(dirname)
            ]
            root_path = Path(root)
            for filename in filenames:
                if _is_ignored_file(filename):
                    continue
                path = root_path / filename
                rel = path.relative_to(base).as_posix()
                if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(filename, pat) for pat in patterns):
                    matched[path] = None
        return sorted(matched, key=lambda path: path.stat().st_mtime)

def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _is_ignored_dir(dirname: str) -> bool:
    return dirname in _IGNORED_DIRS


def _is_ignored_file(filename: str) -> bool:
    return any(fnmatch.fnmatch(filename, pattern) for pattern in _IGNORED_FILE_PATTERNS)


def _is_ignored_path(path: Path, base: Path) -> bool:
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = path
    if any(part in _IGNORED_DIRS for part in rel.parts[:-1]):
        return True
    return _is_ignored_file(path.name)


def _rg_ignore_args() -> list[str]:
    args: list[str] = []
    for dirname in sorted(_IGNORED_DIRS):
        args.extend(['--glob', f'!**/{dirname}/**'])
    for pattern in _IGNORED_FILE_PATTERNS:
        args.extend(['--glob', f'!**/{pattern}'])
    return args
