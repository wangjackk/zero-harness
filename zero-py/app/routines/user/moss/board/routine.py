"""Board —— 黑板连接持有者: 被动常驻, **不在词表里**, 惰性连接.

模型手上没有这个名字: 它拿的是 ``bb_write`` 这些动词; 动词自己按名字找到
本实例(同 chan 找 shell), 把一笔交给它, 由它上线. 谁都能画, 线只有一根.

三个 request::

    act      {act: {...}, id?}   推一笔; 没给 id 就由这里编号(x1, x2…)
    objects  ()                  最近一次对象表快照(全量)
    status   ()                  连没连上 / 推了几笔 / 收了几份快照

为什么惰性: 装配期不该假定黑板服务在跑. 第一次 ``act`` 才连, 连不上就报错
(错误帧自带注意债, 模型当拍就知道"画不上去"), 而不是在装配期把 moss 拖死.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, ClassVar, Dict, Optional

from pydantic import BaseModel, Field
from routine.logger import setup_logger
from routine.routine import Routine, request

from .client import Client

__all__ = ['Board', 'DEFAULT_URL']

_log = setup_logger('moss.board')

DEFAULT_URL = 'http://localhost:3001'


class BoardInput(BaseModel):
    """黑板连接的装配参数(env ``BB_URL`` 优先, 其次这里)."""

    url: str = Field(default='', description='黑板服务地址, 空则用 BB_URL')
    connect_timeout: float = Field(default=3.0,
                                   description='连不上等多久报错(秒)')


class Board(Routine):
    """一块黑板一根线: 收一笔 → 上线 · 收快照 → 留着给 bb_objects."""

    name = 'board'
    is_passive = True

    meta: ClassVar[Dict[str, Any]] = {
        'description': (
            '黑板连接(被动常驻): bb_* 这些动词把一笔交给它上线, 它留着'
            '对象表快照. 不是词表动词 —— 模型看不见这个名字'),
        'input_schema': BoardInput.model_json_schema(),
        'example': '地址走环境变量 BB_URL(默认 http://localhost:3001).',
    }

    def __init__(self) -> None:
        super().__init__()
        self._client: Optional[Client] = None
        self._url = ''
        self._timeout = 3.0
        self._seq = 0                 # 自己编的 id 用(x1, x2…)
        self._halt: Optional[asyncio.Event] = None

    async def on_created(self, rid: str, kwargs: Dict[str, Any]) -> None:
        inp = BoardInput.model_validate(dict(kwargs or {}))
        self._url = (inp.url or os.environ.get('BB_URL') or DEFAULT_URL).strip()
        self._timeout = max(0.2, float(inp.connect_timeout))
        self._client = Client(self._url)
        self._halt = asyncio.Event()

    async def run(self, kwargs: Dict[str, Any]) -> None:
        # 连接是惰性的: run 只负责活着, 第一次 act 才真连
        _log.info('board: %s (lazy connect)', self._url)
        await self._halt.wait()

    async def stop(self) -> None:
        if self._halt is not None:
            self._halt.set()
        if self._client is not None:
            await self._client.stop()

    # ── request 面 ────────────────────────────────────────────────

    @request('act')
    async def _req_act(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """推一笔动作. ``id`` 只对"之后要引用的对象"有意义; 其余由这里编号."""
        d = dict(data or {})
        act = d.get('act')
        if not isinstance(act, dict) or not act.get('do'):
            raise RuntimeError('board.act needs {"act": {"do": ...}}')
        ident = str(d.get('id') or '').strip() or self._auto_id()
        await self._client.ensure(self._timeout)
        await self._client.emit_actions([{'id': ident, 'act': act}])
        return {'ok': True, 'id': ident}

    @request('objects')
    async def _req_objects(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        """对象表: 黑板侧量出来的 bbox 只它知道, 我们只转手."""
        snap = self._client.snapshot() if self._client else {}
        return {'ok': True,
                'connected': bool(self._client and self._client.connected),
                'from': snap.get('from', ''),
                'seq': snap.get('seq', 0),
                'objects': snap.get('objects') or []}

    @request('status')
    async def _req_status(self, source, data: Dict[str, Any]) -> Dict[str, Any]:
        return {'ok': True, 'url': self._url,
                'connected': bool(self._client and self._client.connected),
                'error': self._client.error if self._client else '',
                'seq': self._seq,
                **({'stats': dict(self._client.stats)}
                   if self._client else {})}

    def _auto_id(self) -> str:
        self._seq += 1
        return f'x{self._seq}'
