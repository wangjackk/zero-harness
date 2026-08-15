"""Kernel venv 管理: 创建 venv + 安装 Python-backed skill.

参考 prime-agent 的 bootstrap.ts:
- venv 位置: ~/.zero/kernel-venv/
- 用 uv 创建 venv + 装包
- skill 用 hash 缓存, pyproject.toml 没变就不重装
- kernel 用 venv python 启动, skill 直接 import (不走 sys.path hack)
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from routine.logger import setup_logger

_log = setup_logger('prime.kernel_env')

_VENV_DIR = Path.home() / '.zero' / 'kernel-venv'
_HASH_FILE = _VENV_DIR / '.skills-hash'
_BOOTSTRAP_VERSION = 10  # v10: 去 --seed (无 pip), 扩 _BASE_DEPS 预装数据科学栈

# routine SDK 目录: kernel_env.py 在 zero-harness/zero/routines/user/agents/prime/,
# parents: [0]=prime [1]=agents [2]=user [3]=routines [4]=zero [5]=zero-harness.
# SDK 与 zero/ 平级, 在 zero-harness/routine-py.
_ROUTINE_SDK_DIR = Path(__file__).resolve().parents[5] / 'routine-py'


def _venv_python() -> Path:
    if sys.platform == 'win32':
        return _VENV_DIR / 'Scripts' / 'python.exe'
    return _VENV_DIR / 'bin' / 'python'


def _find_uv() -> str:
    uv = shutil.which('uv')
    if not uv:
        raise RuntimeError(
            'uv not found in PATH. Install uv: '
            'https://docs.astral.sh/uv/getting-started/installation/'
        )
    return uv


def _skill_hash(skill_dir: Path) -> str:
    pyproject = skill_dir / 'pyproject.toml'
    if not pyproject.is_file():
        return ''
    return hashlib.md5(pyproject.read_bytes()).hexdigest()


def _discover_python_skills(skills_dir: Path) -> list[Path]:
    """Find Python-backed skills (dirs with pyproject.toml)."""
    if not skills_dir.is_dir():
        return []
    result = []
    for child in sorted(skills_dir.iterdir()):
        if child.is_dir() and (child / 'pyproject.toml').is_file():
            result.append(child)
    return result


def _compute_hash(skills: list[Path]) -> str:
    h = hashlib.sha256()
    h.update(f'v{_BOOTSTRAP_VERSION}'.encode())
    h.update(b'\x00')
    for s in skills:
        # 纳入安装路径: skill 目录搬家后 editable 安装指向旧路径,
        # 内容 hash 不变检测不到 → 路径入 hash 强制重装.
        h.update(str(s.parent.resolve()).encode())
        h.update(b'\x00')
        h.update(s.name.encode())
        h.update(b'\x00')
        h.update(_skill_hash(s).encode())
        h.update(b'\x00')
    return h.hexdigest()


def _load_stored_hash() -> str:
    try:
        return _HASH_FILE.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return ''


def _store_hash(hash_val: str) -> None:
    try:
        _HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HASH_FILE.write_text(hash_val, encoding='utf-8')
    except Exception as exc:
        _log.warning('store hash failed: %r', exc)


_VERSION_FILE = _VENV_DIR / '.bootstrap-version'


def _load_stored_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return ''


def _store_version() -> None:
    try:
        _VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _VERSION_FILE.write_text(str(_BOOTSTRAP_VERSION), encoding='utf-8')
    except Exception as exc:
        _log.warning('store version failed: %r', exc)


def _run(cmd: list[str], **kwargs: Any) -> None:
    _log.info('run: %s', ' '.join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(
            f'command failed: {" ".join(cmd)}\n'
            f'stdout: {result.stdout}\nstderr: {result.stderr}'
        )


def _build_venv(uv: str, python: Path) -> None:
    """Create venv + install ipykernel (无 pip/setuptools/wheel, 只用 uv pip)."""
    if _VENV_DIR.is_dir():
        shutil.rmtree(_VENV_DIR)
    _log.info('creating kernel venv: %s', _VENV_DIR)
    _run([uv, 'venv', str(_VENV_DIR)])
    _log.info('installing ipykernel')
    _run([uv, 'pip', 'install', '--python', str(python), 'ipykernel'])


# kernel venv 的基础运行时依赖 (跟 skill 无关, kernel 本身要用的包).
# 每次 hash 变化都会重新确保安装, 不只是新建 venv 时.
_BASE_DEPS = [
    'httpx',  # routine_bridge skill 依赖
    # 数据科学 / 文件解析栈 (跟官方 prime-agent bootstrap.ts 对齐)
    'requests',
    'pyyaml',
    'tomli',
    'python-dotenv',
    'pandas',
    'numpy',
    'scipy',
    'beautifulsoup4',
    'lxml',
    'pydantic',
    'tyro',
]


def _ensure_base_deps(uv: str, python: Path) -> None:
    """确保基础依赖装好 (venv 新建 / 升级时都调).

    除 _BASE_DEPS 外, 还 editable 装 routine SDK:
    hub_routine skill 依赖 routine 包 (Routine/RoutineHub/GrpcClientTransport),
    kernel 是独立子进程, 不共享 server 的 sys.path, 必须单独装.
    """
    _log.info('ensuring base deps: %s', ', '.join(_BASE_DEPS))
    _run([uv, 'pip', 'install', '--python', str(python), *_BASE_DEPS])
    if _ROUTINE_SDK_DIR.is_dir():
        _log.info('installing routine SDK (editable): %s', _ROUTINE_SDK_DIR)
        _run([uv, 'pip', 'install', '--python', str(python), '--editable', str(_ROUTINE_SDK_DIR)])
    else:
        _log.warning('routine SDK not found at %s, hub_routine will fail to import', _ROUTINE_SDK_DIR)


def _install_skills(uv: str, python: Path, skills: list[Path]) -> None:
    _ensure_base_deps(uv, python)
    for skill in skills:
        _log.info('installing skill (editable): %s', skill.name)
        _run([uv, 'pip', 'install', '--python', str(python), '--editable', str(skill)])


def ensure_kernel_env(skills_dir: Path | None) -> str:
    """Ensure kernel venv is ready with all Python-backed skills installed.

    Returns path to venv python executable.
    """
    skills = _discover_python_skills(skills_dir) if skills_dir else []
    current_hash = _compute_hash(skills) if skills else 'empty'
    stored_hash = _load_stored_hash()
    version_changed = _load_stored_version() != str(_BOOTSTRAP_VERSION)

    python = _venv_python()

    if current_hash == stored_hash and not version_changed and python.is_file():
        _log.info('kernel venv up to date, skip')
        return str(python)

    uv = _find_uv()

    # venv 不存在 或 bootstrap version 变化时, 重建 venv 清理残留
    # (孤儿 skill 包 / 过期的 _BASE_DEPS / 旧的 --seed pip)
    if not python.is_file() or version_changed:
        _build_venv(uv, python)

    _install_skills(uv, python, skills)

    _store_hash(current_hash)
    _store_version()
    return str(python)
