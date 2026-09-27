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
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from routine.logger import setup_logger

_log = setup_logger('prime.kernel_env')

_VENV_DIR = Path.home() / '.zero' / 'kernel-venv'
_HASH_FILE = _VENV_DIR / '.skills-hash'
_BOOTSTRAP_VERSION = 16  # v16: 项目根更名 zero-py, 包下沉到 zero-py/app/

# routine SDK 所在目录 (routine 包的父目录): kernel_env.py 在
# zero-py/app/routines/user/agents/prime/, parents: [0]=prime [1]=agents
# [2]=user [3]=routines [4]=zero [5]=zero-py. SDK 是 zero-py/routine-py/
# (含 routine/ + routine/kernel/), bootstrap 写 site-packages .pth 把该目录
# 挂进 venv sys.path. 两项目 (zero-py/zero-ts) 共享同一 kernel venv,
# 两边 _BOOTSTRAP_VERSION 必须一致, 否则互相重建 venv 打架
# (ZERO_ROUTINE_SDK 环境变量可覆盖, 指向含 routine/ 包的目录).
_ROUTINE_SDK_DIR = Path(os.environ.get(
    'ZERO_ROUTINE_SDK',
    str(Path(__file__).resolve().parents[5] / 'routine-py'),
))


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
    'grpcio',  # routine SDK GrpcClientTransport 依赖 (hub_routine skill)
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


def _site_packages() -> Path | None:
    """venv 的 site-packages 目录 (win: Lib/, posix: lib/pythonX.Y/)."""
    if sys.platform == 'win32':
        return _VENV_DIR / 'Lib' / 'site-packages'
    lib = _VENV_DIR / 'lib'
    if not lib.is_dir():
        return None
    for child in sorted(lib.iterdir()):
        if child.name.startswith('python') and (child / 'site-packages').is_dir():
            return child / 'site-packages'
    return None


def _install_sdk_pth() -> None:
    """写 .pth 把 routine SDK 目录挂进 venv sys.path (免安装).

    routine-py/ 不做 editable 安装(免 venv 重建); kernel 是独立子进程,
    靠 .pth 让 ``import routine`` 解析到源码目录.
    """
    site = _site_packages()
    if site is None:
        _log.warning('site-packages not found under %s', _VENV_DIR)
        return
    if (_ROUTINE_SDK_DIR / 'routine' / '__init__.py').is_file():
        pth = site / 'routine-sdk.pth'
        pth.write_text(str(_ROUTINE_SDK_DIR.resolve()) + '\n', encoding='utf-8')
        _log.info('routine SDK .pth installed: %s -> %s', pth, _ROUTINE_SDK_DIR)
    else:
        _log.warning('routine SDK not found at %s, hub_routine will fail to import', _ROUTINE_SDK_DIR)


def _ensure_base_deps(uv: str, python: Path) -> None:
    """确保基础依赖装好 (venv 新建 / 升级时都调).

    routine SDK 不安装 (routine-py 走 .pth 挂载), 由 _install_sdk_pth
    写 .pth 挂载: hub_routine skill 依赖 routine 包
    (Routine/RoutineHub/GrpcClientTransport).
    """
    _log.info('ensuring base deps: %s', ', '.join(_BASE_DEPS))
    _run([uv, 'pip', 'install', '--python', str(python), *_BASE_DEPS])
    _install_sdk_pth()


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
