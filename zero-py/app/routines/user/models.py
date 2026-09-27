"""models ---- passive 常驻的中立模型管理 hub + bridge routines.

事实源 models.yaml 的唯一读写引擎在 agents/_core/models_store.py, 本模块分两层:

  - ModelHub (name 'models', passive 常驻): 持有写锁, req handlers:
      list / get / save / delete / set_default
  - bridge routines (models/list|get|save|delete|set_default): 一次性
    routine, run 内解析 hub rid 后 req 转发 ---- 所有消费方统一按 name
    call, 写操作都经 hub 写锁串行化 (read-modify-write 并发覆盖防护).

进程内只读消费方 (llm.py, web_server /models 端点) 直接 import
models_store, 不经 req.
"""
from __future__ import annotations

import asyncio
from asyncio import Event
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from routine import Routine, request

from .agents._core import models_store

__all__ = ['ModelHub', 'ModelsList', 'ModelsGet', 'ModelsSave', 'ModelsDelete',
           'ModelsDefault']


# ──────────────────────────────────────────────────────────────────────────────
# schema (req payload 校验)

class _ProviderCfg(BaseModel):
    """model 级配置 (键 = 实际 API id, 全部字段可选)."""
    model_config = ConfigDict(extra='forbid')

    name: Optional[str] = Field(
        default=None, description='显示别名 (纯展示, 不作索引)')
    max_context: Optional[int] = None
    max_tokens: Optional[int] = None
    image: Optional[bool] = Field(
        default=None, description='支持图片输入 (text 恒支持)')
    reasoning: Optional[Dict[str, Any]] = Field(
        default=None,
        description='thinking 声明: 事实 {can_disable, off} + 默认策略 '
                    '{effort: 默认档位, agent 侧可覆盖}',
    )
    extra: Optional[Dict[str, Any]] = Field(
        default=None, description='API 请求体透传字段')
    tools: Optional[List[Dict[str, Any]]] = Field(
        default=None, description='原生 API tools 字段项')


class _ProviderInput(BaseModel):
    """provider 级配置 (整体替换该 provider 条目)."""
    model_config = ConfigDict(extra='forbid')

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    apis: Optional[Dict[str, Dict[str, Any]]] = Field(
        default=None,
        description='协议矩阵 {协议: {能力声明}}. 能力是 (provider, 协议) 的'
                    '客观事实, 如 {openai-responses: {previous_response_id: '
                    'false}}; 协议选择完全归 agent 层 (显式 protocol > 矩阵'
                    '推导 openai-responses > openai-completions > '
                    'anthropic-messages)')
    models: Optional[Dict[str, _ProviderCfg]] = Field(
        default=None,
        description='{model_id: 配置} (键 = 实际 API id, 整体替换)')


class _SaveInput(BaseModel):
    provider_id: str = Field(description='provider 标识 (models.yaml key)')
    provider: _ProviderInput


class _GetInput(BaseModel):
    provider: Optional[str] = Field(
        default=None, description='缺省返回全部 provider')


class _DeleteInput(BaseModel):
    provider_id: str
    model_id: Optional[str] = Field(
        default=None, description='缺省删除整个 provider')


# ──────────────────────────────────────────────────────────────────────────────
# routine

class ModelHub(Routine):
    """中立模型管理 hub:models.yaml 的 req 出口 (读写均落 models_store)."""

    name = 'models'
    is_passive = True

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '中立模型管理 hub (passive 常驻). req events: list (展平清单, '
            '不含 api_key) / get {provider?} (完整配置含 api_key) / save '
            '{provider_id, provider} (upsert) / delete {provider_id, '
            'model_id?} / set_default {model}. 事实源 models.yaml, '
            '写入即时生效.'
        ),
    }

    def __init__(self):
        super().__init__()
        self._stop_event: Optional[Event] = None
        # 写锁: upsert/delete 是 read-modify-write, 并发写会互相覆盖
        self._write_lock = asyncio.Lock()

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        self._stop_event = Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()

    # ── req handlers ──

    @request('list')
    async def on_list(self, source, data) -> Dict[str, Any]:
        out = []
        for key, mc in models_store.flatten().items():
            out.append({
                'key': key,
                'provider': key.split('/')[0],
                'name': mc.get('name') or mc.get('model') or key,
                'max_context': int(mc.get('max_context') or 0),
                'max_tokens': int(mc.get('max_tokens') or 0),
                'reasoning_effort': (mc.get('reasoning') or {}).get('effort'),
                'has_tools': bool(mc.get('tools')),
                # 该 model 可选协议 (provider apis 矩阵键, 声明序). 空列表 =
                # 仅隐式 openai-responses; 协议选择归 agent 层.
                'protocols': list((mc.get('apis') or {}).keys()),
            })
        return {'default': models_store.default_key(), 'models': out}

    @request('get')
    async def on_get(self, source, data) -> Dict[str, Any]:
        inp = _GetInput.model_validate(data or {})
        return models_store.providers(inp.provider)

    @request('save')
    async def on_save(self, source, data) -> Dict[str, Any]:
        inp = _SaveInput.model_validate(data or {})
        cfg = inp.provider.model_dump(exclude_none=True)
        if inp.provider.models is not None:
            cfg['models'] = {
                m_name: m_cfg.model_dump(exclude_none=True)
                for m_name, m_cfg in inp.provider.models.items()
            }
        async with self._write_lock:
            models = models_store.upsert_provider(inp.provider_id, cfg)
        return {'ok': True, 'provider_id': inp.provider_id, 'models': models}

    @request('delete')
    async def on_delete(self, source, data) -> Dict[str, Any]:
        inp = _DeleteInput.model_validate(data or {})
        async with self._write_lock:
            return models_store.delete(inp.provider_id, inp.model_id)

    @request('set_default')
    async def on_set_default(self, source, data) -> Dict[str, Any]:
        model = str((data or {}).get('model') or '')
        if not model:
            raise ValueError('model: non-empty string required')
        async with self._write_lock:
            return models_store.set_default(model)


# ──────────────────────────────────────────────────────────────────────────────
# bridge routines (一次性, run 内 req 常驻 hub; 写操作经 hub 写锁串行化)

class _SetDefaultInput(BaseModel):
    model: str = Field(description='默认模型 key (provider/model)')


async def _find_hub_rid(routine: Routine) -> str:
    """按 name 'models' 查常驻 hub 的 rid (启动竞态兜底重试 ~3s)."""
    for _ in range(30):
        for r in await routine.get_running_routines():
            if str(r.get('name') or '') == 'models':
                rid = str(r.get('id') or '').strip()
                if rid:
                    return rid
        await asyncio.sleep(0.1)
    raise RuntimeError('models hub routine not running (name: models)')


class ModelsList(Routine):
    name = 'models/list'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '列出 models.yaml 全部可用模型 (不含 api_key). '
                       '返回 {default, models: [{key, provider, name, '
                       'max_context, max_tokens, reasoning_effort, '
                       'has_tools}]}.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'list', {})


class ModelsGet(Routine):
    name = 'models/get'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '取模型 provider 完整配置 (含 api_key, base_url, api, '
                       'models). 给定 provider 时只返回该 provider; 缺省全部.',
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'get', kwargs or {})


class ModelsSave(Routine):
    name = 'models/save'
    meta: ClassVar[Dict[str, Any]] = {
        'description': 'Upsert 一个 provider 定义到 models.yaml (整体替换该 '
                       'provider 条目, 含其 models). 写入即时生效.',
        'input_schema': _SaveInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'save', kwargs or {})


class ModelsDelete(Routine):
    name = 'models/delete'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '删除 models.yaml 中整个 provider, 或 provider 下单个 '
                       'model (给定 model_id). 写入即时生效.',
        'input_schema': _DeleteInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'delete', kwargs or {})


class ModelsDefault(Routine):
    name = 'models/set_default'
    meta: ClassVar[Dict[str, Any]] = {
        'description': '设默认模型 (provider/model). 写入即时生效.',
        'input_schema': _SetDefaultInput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return await self.req(await _find_hub_rid(self), 'set_default',
                              kwargs or {})
