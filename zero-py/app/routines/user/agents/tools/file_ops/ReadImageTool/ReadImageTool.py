"""ReadImage ---- 读取本地图片文件, 图片引用随 result 返回, 由调用方 agent 合并.

返回 ``{'for_llm': 元数据文本, 'images': [路径]}``: agent 侧特判 read_image 的
result, 把图片与 feedback 文本合并成一条多模态消息进 LLM 上下文 -- 消息位置
天然在 assistant 之后, 增量窗口内. 底层模型不支持读图时 agent 组装侧剥掉图,
只留元数据文本.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Tuple

from pydantic import BaseModel, Field
from routine import Routine

from .prompt import DESCRIPTION

from app.routines.user.agents._core.paths import AGENT_ID_KEY, resolve_tool_path, display_tool_path

MAX_IMAGE_BYTES = 20 * 1024 * 1024   # 与 uploads 上限一致


class ReadImageInput(BaseModel):
    file_path: str = Field(
        description=(
            'Absolute path to the image file. Must be absolute, not relative. '
            'Supported formats: png / jpeg / gif / webp.'
        ),
    )


class ReadImageOutput(BaseModel):
    for_llm: str = Field(description='Image metadata text for the LLM (path, type, dimensions, bytes)')
    images: list[str] = Field(
        default_factory=list,
        description='Image file path list; the caller agent merges them into the feedback message.',
    )


def sniff_image(head: bytes) -> Optional[Tuple[str, str]]:
    """magic bytes -> (media_type, ext); 非图片返回 None."""
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png', 'png'
    if head[:3] == b'\xff\xd8\xff':
        return 'image/jpeg', 'jpg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif', 'gif'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp', 'webp'
    return None


def image_dims(data: bytes, media_type: str) -> Optional[Tuple[int, int]]:
    """best-effort 宽高解析; 解不出返回 None."""
    try:
        if media_type == 'image/png' and len(data) >= 24:
            return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
        if media_type == 'image/gif' and len(data) >= 10:
            return int.from_bytes(data[6:8], 'little'), int.from_bytes(data[8:10], 'little')
        if media_type == 'image/jpeg':
            # 扫 SOF marker (C0-CF, 排除 C4/C8/CC): 段内 5-8 字节是 height/width
            pos = 2
            while pos + 9 < len(data):
                if data[pos] != 0xFF:
                    pos += 1
                    continue
                marker = data[pos + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    pos += 2
                    continue
                seg_len = int.from_bytes(data[pos + 2:pos + 4], 'big')
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h = int.from_bytes(data[pos + 5:pos + 7], 'big')
                    w = int.from_bytes(data[pos + 7:pos + 9], 'big')
                    return w, h
                pos += 2 + seg_len
    except (IndexError, ValueError):
        pass
    return None


class ReadImage(Routine):
    """Read an image from the local filesystem.

    - file_path must be absolute
    - The result carries metadata text plus an ``images`` path list; the caller
      agent merges the images into the feedback message (multimodal), dropping
      them when the underlying model cannot see images
    - Formats are recognized by magic bytes, extension is not required
    """

    name = 'read_image'
    meta: ClassVar[Dict[str, Any]] = {
        'tool': True,
        'readonly': True,
        'input_schema': ReadImageInput.model_json_schema(),
        'output_schema': ReadImageOutput.model_json_schema(),
        'description': DESCRIPTION,
    }

    async def run(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        agent_id = kwargs.pop(AGENT_ID_KEY, '')
        state = await self.call('fetch_agent_state', {'agent_id': agent_id})
        project_root = state.get('project_root')
        inp = ReadImageInput(**kwargs)
        path = resolve_tool_path(inp.file_path, project_root)

        if not os.path.exists(path):
            raise FileNotFoundError(f'File does not exist: {path}')
        if os.path.isdir(path):
            raise IsADirectoryError(f'Path is a directory, not a file: {path}')

        size = os.path.getsize(path)
        if size > MAX_IMAGE_BYTES:
            raise ValueError(
                f'Image too large ({size / 1024 / 1024:.1f} MB, max {MAX_IMAGE_BYTES // 1024 // 1024} MB): {path}'
            )
        if size == 0:
            raise ValueError(f'File is empty: {path}')

        with open(path, 'rb') as f:
            data = f.read()

        sniffed = sniff_image(data)
        if sniffed is None:
            raise ValueError(f'File is not a supported image (png/jpeg/gif/webp): {path}')
        media_type, _ext = sniffed

        dims = image_dims(data, media_type)
        dim_text = f', {dims[0]}x{dims[1]}' if dims else ''
        display = display_tool_path(path, project_root)

        # result 携带图片引用 (agent 侧特判 read_image: 随 feedback 合并成一条
        # 多模态消息进 LLM 上下文; 模型不支持读图时 agent 组装侧剥掉).
        for_llm = (
            f'Read image: {display} ({media_type}{dim_text}, {size} bytes). '
            'The image is attached to this feedback message.'
        )
        return {'for_llm': for_llm, 'images': [str(Path(path))]}
