"""Resolve the ripgrep executable used by Claude Code tools."""
from __future__ import annotations

import os
import platform
import shutil
from abc import ABC, abstractmethod
from pathlib import Path


class RipgrepProvider(ABC):
    """Abstract source for locating a ripgrep executable."""

    @abstractmethod
    def resolve(self) -> str | None:
        """Return an executable path when this provider can satisfy the request."""


class EnvironmentRipgrepProvider(RipgrepProvider):
    """Use explicit env vars first, then PATH."""

    def resolve(self) -> str | None:
        for env_name in ('RIPGREP_PATH', 'RG_PATH'):
            for candidate in _env_candidates(os.environ.get(env_name)):
                if _is_executable(candidate):
                    return str(candidate)
        return shutil.which('rg')


def ripgrep_path() -> str:
    for provider in (EnvironmentRipgrepProvider(),):
        resolved = provider.resolve()
        if resolved:
            return resolved
    raise FileNotFoundError(
        'ripgrep executable not found. Add rg to PATH or set RIPGREP_PATH/RG_PATH.'
    )


def _env_candidates(value: str | None) -> list[Path]:
    if not value:
        return []
    candidate = Path(value.strip('"'))
    if candidate.is_dir():
        return [candidate / _binary_name()]
    return [candidate]


def _binary_name() -> str:
    return 'rg.exe' if platform.system() == 'Windows' else 'rg'


def _is_executable(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_file() and os.access(candidate, os.X_OK)
