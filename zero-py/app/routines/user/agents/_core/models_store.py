"""models_store ---- 模型配置唯一读写引擎 (事实源: 项目根 models.yaml).

中立模型管理 routine 系列 (models/list|get|save|delete) 与 py 内部 LLM
client (_core/llm.py) 共用本模块; 其它语言 (ts pi adapter) 经 models/get
req 拿配置.

- 读: ruamel round-trip (保留注释), mtime 缓存热重载
- 写: upsert_provider / delete, 写回后 epoch bump ---- llm.py 的 client/
  provider 缓存按 epoch 失效重建
- ``$VAR`` / ``${VAR}`` 环境变量展开只发生在 flatten/providers 出口,
  文件里永远存引用
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from routine.logger import setup_logger

_log = setup_logger('prime.models_store')

_CONFIG_PATH = Path(__file__).resolve().parents[4] / 'models.yaml'

# provider 级标量字段 (非 model 入口), 展平时跳过.
# 注: 'api' 已退役 (协议选择归 agent 层); yaml 残留的 api 键在这被
# 白名单过滤, 下次 upsert 该 provider 时自然消失.
_PROVIDER_SCALAR_KEYS: frozenset[str] = frozenset(
    {'api_key', 'base_url', 'apis'})

# model 级配置字段 (键 = 实际 API id; flatten 白名单, 未知字段丢弃).
# 注意 'model' 不是 yaml 字段 ---- 出口合成的 'model' = 键 (实际 API id).
_MODEL_KEYS: tuple[str, ...] = (
    'name', 'max_context', 'max_tokens', 'image', 'reasoning', 'extra',
    'tools')

_yaml = YAML()  # 默认 rt: round-trip 保留注释与键序

_epoch: int = 0            # 配置版本号 (写操作/失效时 bump)
_cached_mtime: float = -1.0
_cached_cfg: Optional[Dict[str, Any]] = None   # 顶层 yaml dict
_flat: Dict[str, Dict[str, Any]] = {}          # 'provider/model' -> 展平配置


# ── 加载与缓存 ────────────────────────────────────────────────────────────────

def _load() -> Dict[str, Any]:
    """读 models.yaml (mtime 缓存), 失败抛错.

    外部修改 (手改文件) 与写操作同样 bump epoch: llm.py 等按 epoch
    失效的消费方对手改文件同样热生效.
    """
    global _cached_mtime, _cached_cfg, _flat, _epoch
    mtime = _CONFIG_PATH.stat().st_mtime if _CONFIG_PATH.is_file() else -1.0
    if _cached_cfg is not None and mtime == _cached_mtime:
        return _cached_cfg
    if not _CONFIG_PATH.is_file():
        raise FileNotFoundError(f'models.yaml not found: {_CONFIG_PATH}')
    with _CONFIG_PATH.open('r', encoding='utf-8') as f:
        data = _yaml.load(f) or {}
    if 'models' not in data:
        raise ValueError(f'models.yaml missing "models" section: {_CONFIG_PATH}')
    _epoch += 1
    _cached_cfg = data
    _cached_mtime = mtime
    _flat = _flatten(data)
    return data


def invalidate() -> int:
    """作废缓存 (写操作后调): 下次访问强制重读, epoch bump."""
    global _epoch, _cached_mtime
    _epoch += 1
    _cached_mtime = -1.0
    return _epoch


def epoch() -> int:
    """配置版本号 (llm.py 缓存失效依据)."""
    return _epoch


def default_key() -> str:
    """默认模型 key ('provider/model'); 未配置取展平表首项."""
    data = _load()
    default = str(data.get('default') or '')
    if not default and _flat:
        default = next(iter(_flat))
    return default


def set_default(model_key: str) -> Dict[str, Any]:
    """设默认模型 key ('provider/model'), 校验存在后写 yaml 顶层 default."""
    flat = flatten()
    if model_key not in flat:
        raise ValueError(f'unknown model {model_key!r}, '
                         f'available: {sorted(flat)}')
    data = _load()
    data['default'] = model_key
    _save(data)
    invalidate()
    _log.info('default model set: %s', model_key)
    return {'ok': True, 'default': model_key}


def flatten() -> Dict[str, Dict[str, Any]]:
    """展平配置表: 'provider/model' -> {api_key, base_url, model, ...}.

    mtime 未变时直接返回缓存 (import 级零成本, 热重载对读方透明).
    """
    _load()
    return _flat


# ── 出口: 展平表 / provider 全量 dump ───────────────────────────────────────

def _expand_env(value: str) -> str:
    """``$VAR`` / ``${VAR}`` → os.environ 取值, 非该形式原样返回.

    变量未设置时返回空串并 log warning (不炸启动 ---- 真正调用时 provider
    报 401, 提示补 zero/.env).
    """
    if not isinstance(value, str) or not value.startswith('$'):
        return value
    name = value[2:-1] if value.startswith('${') else value[1:]
    if not name.isidentifier():
        return value
    env = os.environ.get(name)
    if env is None:
        _log.warning('env %s not set (api_key empty; add it to zero/.env)', name)
        return ''
    return env


def _model_cfg(m_name: str, m_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """出口形状: name (显示别名) 透传, model 合成为键 (实际 API id)."""
    cfg = {k: m_cfg[k] for k in _MODEL_KEYS if k in m_cfg}
    cfg['model'] = m_name
    return cfg


def _flatten(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    flat: Dict[str, Dict[str, Any]] = {}
    for prov_name, prov_cfg in (data.get('models') or {}).items():
        if not isinstance(prov_cfg, dict):
            continue
        api_key = _expand_env(str(prov_cfg.get('api_key', '')))
        for m_name, m_cfg in prov_cfg.items():
            if m_name in _PROVIDER_SCALAR_KEYS or not isinstance(m_cfg, dict):
                continue
            flat[f'{prov_name}/{m_name}'] = {
                'api_key': api_key,
                'base_url': str(prov_cfg.get('base_url', '')),
                'apis': dict(prov_cfg.get('apis') or {}),
                **_model_cfg(m_name, m_cfg),
            }
    return flat


def providers(provider_id: Optional[str] = None) -> Dict[str, Any]:
    """全量 provider dump (api_key 已展开) ---- models/get 的出口.

    provider_id 给定时只返回该 provider (结构不变, 方便消费方统一处理).
    """
    data = _load()
    all_providers: Dict[str, Any] = {}
    for prov_name, prov_cfg in (data.get('models') or {}).items():
        if not isinstance(prov_cfg, dict):
            continue
        if provider_id is not None and prov_name != provider_id:
            continue
        entry: Dict[str, Any] = {}
        entry['api_key'] = _expand_env(str(prov_cfg.get('api_key', '')))
        entry['base_url'] = str(prov_cfg.get('base_url', ''))
        entry['apis'] = dict(prov_cfg.get('apis') or {})
        entry['models'] = {
            m_name: _model_cfg(m_name, m_cfg)
            for m_name, m_cfg in prov_cfg.items()
            if m_name not in _PROVIDER_SCALAR_KEYS
            and isinstance(m_cfg, dict)
        }
        all_providers[prov_name] = entry
    if provider_id is not None and provider_id not in all_providers:
        raise KeyError(f'provider {provider_id!r} not found')
    return {'default': default_key(), 'providers': all_providers}


# ── 写入 (ruamel round-trip 保留注释) ──────────────────────────────────────

def _save(data: Dict[str, Any]) -> None:
    global _cached_mtime
    with _CONFIG_PATH.open('w', encoding='utf-8') as f:
        _yaml.dump(data, f)
    _cached_mtime = _CONFIG_PATH.stat().st_mtime


def upsert_provider(provider_id: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """整体替换一个 provider 条目, 返回该 provider 现有 model 名列表.

    入参 cfg 形如 {api_key?, base_url?, apis?, models?} (routine 侧
    schema); models 为 {model_id: 配置} 映射 (键 = 实际 API id), 落盘时
    平铺到 provider 下 (models.yaml 的约定形状).
    """
    if not provider_id.strip():
        raise ValueError('provider_id: non-empty string required')
    models = cfg.get('models')
    if models is not None:
        if not isinstance(models, dict):
            raise ValueError('provider.models: mapping required '
                             '{model_name: {...}}')
        for m_name, m_cfg in models.items():
            if not isinstance(m_cfg, dict):
                raise ValueError(f'provider.models.{m_name}: mapping required')
    data = _load()
    entry = {k: v for k, v in cfg.items() if k != 'models'}
    entry.update(models or {})
    models_section: CommentedMap = data['models']
    models_section[provider_id] = entry
    _save(data)
    invalidate()
    _log.info('provider %s upserted: %s', provider_id,
              sorted((models or {}).keys()))
    return sorted((models or {}).keys())


def delete(provider_id: str, model_id: Optional[str] = None) -> Dict[str, Any]:
    """删整个 provider, 或 provider 下单个 model.

    返回 {ok, provider_id, model_id}; 目标不存在时 ok=False + error.
    """
    data = _load()
    models_section: CommentedMap = data['models']
    if provider_id not in models_section:
        return {'ok': False, 'error': f'provider {provider_id!r} not found'}
    if model_id is None:
        del models_section[provider_id]
        _save(data)
        invalidate()
        _log.info('provider %s deleted', provider_id)
        return {'ok': True, 'provider_id': provider_id, 'model_id': None}
    prov = models_section[provider_id]
    if not isinstance(prov, dict) or model_id not in prov \
            or model_id in _PROVIDER_SCALAR_KEYS:
        return {'ok': False,
                'error': f'model {provider_id}/{model_id} not found'}
    del prov[model_id]
    _save(data)
    invalidate()
    _log.info('model %s/%s deleted', provider_id, model_id)
    return {'ok': True, 'provider_id': provider_id, 'model_id': model_id}
