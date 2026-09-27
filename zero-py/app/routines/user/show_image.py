"""ShowImage -- 把本地图片经 WS 推到前端对话栏展示.

链路: 本 routine publish('show_image') → web_server @subscribe 转发 WS 广播
→ 前端 App.vue 收到后渲染 image-bubble (AgentPanel 已有模板).

agent 上下文: tool routine 被 agent push 时框架注入 ``AGENT_ID_KEY``
(调用方 agent 的 agent_id), 图片投递到该 agent 的 panel; 非 agent 上下文
调用 (无注入) 投递到 user panel.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, ClassVar, Dict

from pydantic import BaseModel, Field

from routine import Routine
from routine.logger import setup_logger

from app.routines.user.agents._core.paths import AGENT_ID_KEY

__all__ = ['ShowImage']

_log = setup_logger('show_image')

# 原始文件大小上限: base64 膨胀 4/3, WS 广播是全客户端 fan-out, 太大会卡.
_MAX_BYTES = 8 * 1024 * 1024


class ShowImageInput(BaseModel):
    path: str = Field(description='本地图片文件路径 (png/jpg/gif/webp/svg)')
    caption: str = Field('', description='图片说明文字, 展示在图片下方 (可选)')
    agent_id: str | None = Field(
        default=None,
        json_schema_extra={'x-hidden': True},
        description='目标 agent_id (框架注入, LLM 不填).',
    )


class ShowImageOutput(BaseModel):
    ok: bool = Field(True)


class ShowImage(Routine):
    """把本地图片发到前端对话栏展示 (WS 推送)."""

    meta: ClassVar[Dict[str, Any]] = {
        'description': '把本地图片发到前端对话栏展示 (WS 实时推送). '
                       '路径须是本机可读的图片文件.',
        'input_schema': ShowImageInput.model_json_schema(),
        'output_schema': ShowImageOutput.model_json_schema(),
    }

    async def run(self, kwargs: Dict[str, Any]) -> dict:
        inp = ShowImageInput.model_validate(kwargs)
        agent_id = inp.agent_id or str(kwargs.get(AGENT_ID_KEY) or '') or 'user'

        p = Path(inp.path).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f'image not found: {p}')
        mime = mimetypes.guess_type(str(p))[0]
        if not mime or not mime.startswith('image/'):
            raise ValueError(f'not a recognized image file: {p}')
        raw = p.read_bytes()
        if len(raw) > _MAX_BYTES:
            raise ValueError(
                f'image too large: {len(raw)} bytes (max {_MAX_BYTES})')

        data = base64.b64encode(raw).decode('ascii')
        await self.publish('show_image', {
            'agent_id': agent_id,
            'data': data,
            'mime': mime,
            'caption': inp.caption or '',
        })
        _log.info('show_image: %s (%s, %d bytes) -> panel %s',
                  p.name, mime, len(raw), agent_id)
        return {'ok': True}
