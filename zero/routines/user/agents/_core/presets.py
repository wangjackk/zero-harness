"""Agent preset 仓库: 两根目录 + copy-only 创作.

preset = 纯声明文件 (preset.yaml), 不含代码:
  - 随附根 ``zero/agent-presets/``  (随部署走, 只读语义, copy 的已知良好起点)
  - 用户根 ``~/.zero/agent-presets/`` (副本, 可写可删)

copy-only: 唯一创建入口是 ``copy_preset`` (整目录复制), 没有空白新建、
没有 write 接口 ---- 文件就是编辑器, 改 preset.yaml 即改定义.
agent 自验: 改完 req manager create_agent(preset=<id>) 试跑一轮真实对话,
比静态检查强 (能抓住 model 不存在 / skill 名拼错 / 工具名无效等).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, List

import yaml

from routine.logger import setup_logger

_log = setup_logger('agent_presets')

# zero 包根 (…/zero-harness/zero)
_PACKAGE_ROOT = Path(__file__).resolve().parents[4]
SHIPPED_ROOT = _PACKAGE_ROOT / 'agent-presets'
USER_ROOT = Path.home() / '.zero' / 'agent-presets'

_PRESET_FILE = 'preset.yaml'
_ID_RE = re.compile(r'^[a-z][a-z0-9_]*$')

# preset.yaml 允许的字段 (透传给 manager spawn; 冗余字段拒绝, 防拼错静默失效).
# copied_from 由 copy_preset 自动写入, 标记来源.
_FIELDS = {
    'name', 'description', 'agent_routine', 'model',
    'enabled_tools', 'preload_skills', 'level1_skills',
    'extra_instructions', 'copied_from',
}


def _parse_id(pid: str) -> str:
    pid = str(pid or '').strip()
    if not _ID_RE.match(pid):
        raise ValueError(
            f"preset id '{pid}' invalid: lowercase letters/digits/underscore, "
            'must start with a letter'
        )
    return pid


def _preset_dir(root: Path, pid: str) -> Path:
    return root / pid


def _read_preset_yaml(path: Path) -> Dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    if not isinstance(data, dict):
        raise ValueError(f'{path}: preset.yaml must be a mapping')
    unknown = set(data) - _FIELDS
    if unknown:
        raise ValueError(f'{path}: unknown field(s) {sorted(unknown)}')
    return data


def list_presets() -> List[Dict[str, Any]]:
    """列两根全部 preset. user 与 shipped 同名时 user 遮蔽 (正常不会发生: copy 拒同名)."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for root, source in ((USER_ROOT, 'user'), (SHIPPED_ROOT, 'shipped')):
        if not root.is_dir():
            continue
        for yml in sorted(root.glob(f'*/{_PRESET_FILE}')):
            pid = yml.parent.name
            if pid in seen:
                continue
            seen.add(pid)
            try:
                data = _read_preset_yaml(yml)
            except Exception as exc:
                _log.warning('preset %s unreadable: %s', pid, exc)
                data = {'name': pid, 'description': f'(broken: {exc})'}
            out.append({
                'id': pid,
                'name': str(data.get('name') or pid),
                'description': str(data.get('description') or ''),
                'source': source,
                'path': str(yml.parent),
                'extra_instructions': str(data.get('extra_instructions') or ''),
                'model': data.get('model') or None,
                'enabled_tools': list(data.get('enabled_tools') or []),
                'preload_skills': list(data.get('preload_skills') or []),
                'level1_skills': list(data.get('level1_skills') or []),
            })
    return out


def load_preset(pid: str) -> Dict[str, Any]:
    """读一个 preset 的声明 (user 根优先). 返回含 id + 全部字段."""
    pid = _parse_id(pid)
    for root in (USER_ROOT, SHIPPED_ROOT):
        yml = _preset_dir(root, pid) / _PRESET_FILE
        if yml.is_file():
            data = _read_preset_yaml(yml)
            data['id'] = pid
            return data
    raise FileNotFoundError(f"preset '{pid}' not found in either root")


def copy_preset(from_id: str, new_id: str, name: str | None = None) -> Dict[str, Any]:
    """整目录复制一个 preset 到用户根 (copy-only 唯一创建入口).

    - 拒绝 new_id 与任一根已有 preset 同名 (shipped 同名副本会被遮蔽,
      copy 落下一个永不生效的目录)
    - 重写副本元数据: name 用传入值 (缺省沿用来源), 记 copied_from;
      来源 description 保留
    - 失败回滚 (不留半拷目录)
    """
    from_id = _parse_id(from_id)
    new_id = _parse_id(new_id)
    if from_id == new_id:
        raise ValueError('new preset id must differ from source id')

    src = None
    for root in (USER_ROOT, SHIPPED_ROOT):
        d = _preset_dir(root, from_id)
        if (d / _PRESET_FILE).is_file():
            src = d
            break
    if src is None:
        raise FileNotFoundError(f"preset '{from_id}' not found")
    if load_preset_opt(new_id) is not None:
        raise FileExistsError(f"preset id '{new_id}' already exists in either root")
    dst = _preset_dir(USER_ROOT, new_id)
    if dst.exists():
        raise FileExistsError(f'{dst} exists but is not a preset; remove it first')

    USER_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dst)
        yml_path = dst / _PRESET_FILE
        data = _read_preset_yaml(yml_path)
        data['name'] = str(name or data.get('name') or new_id)
        data['copied_from'] = from_id
        _write_preset_yaml(yml_path, data)
    except Exception:
        shutil.rmtree(dst, ignore_errors=True)
        raise
    _log.info('preset copied: %s -> %s (%s)', from_id, new_id, dst)
    return {'id': new_id, 'path': str(dst)}


def delete_preset(pid: str) -> None:
    """删一个 preset. 仅允许用户根 ---- 随附 preset 不可删 (升级会覆盖,
    它们是副本的已知良好起点)."""
    pid = _parse_id(pid)
    d = _preset_dir(USER_ROOT, pid)
    if d.is_dir():
        shutil.rmtree(d)
        _log.info('preset deleted: %s (%s)', pid, d)
        return
    if (_preset_dir(SHIPPED_ROOT, pid) / _PRESET_FILE).is_file():
        raise PermissionError(f"shipped preset '{pid}' is read-only")
    raise FileNotFoundError(f"preset '{pid}' not found in user root")


def load_preset_opt(pid: str) -> Dict[str, Any] | None:
    """load_preset 的非抛版本 (存在性探测)."""
    try:
        return load_preset(pid)
    except (FileNotFoundError, ValueError):
        return None


def _write_preset_yaml(path: Path, data: Dict[str, Any]) -> None:
    out = {k: v for k, v in data.items() if k != 'id'}
    path.write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False),
        encoding='utf-8',
    )
