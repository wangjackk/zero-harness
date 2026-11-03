"""ReadImage ---- 读取本地图片文件, 图片引用随 result 返回, 由组装侧附进消息.

返回 ``{'content': 元数据文本, 'images': [路径]}``: 组装侧把图片与同一条
消息的文本合并成多模态 (xml agent 特判 read_image 合并进 ``[feedback]``;
moss 侧走回执载荷 ``images`` → ``<observe>`` 的 content blocks). 底层模型
不支持读图时组装侧剥掉图, 只留元数据文本.

原 tools/file_ops/ReadImageTool 迁来 (prime 工具集下线, read_image 是
xml agent 白名单依赖独立存活).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Dict, Optional, Tuple

from pydantic import BaseModel, Field
from routine import Routine

from app.routines.user.agents._core.paths import (
    AGENT_ID_KEY, display_tool_path, pop_project_root, resolve_tool_path,
)

#: 注册表只认 __all__(manifest 语义): 单文件条目被别的清单引用时靠它
__all__ = ['ReadImage']

MAX_IMAGE_BYTES = 20 * 1024 * 1024   # 与 uploads 上限一致

DESCRIPTION = (
    'Reads an image file from the local filesystem and attaches it to the result, '
    'so you can visually inspect it. '
    'The file_path parameter must be an absolute path, not a relative path. '
    'Supported formats: png, jpeg, gif, webp (recognized by content, extension is not required). '
    'Use it to view plots saved by plotting code, screenshots, downloaded images, or any local image file. '
    'Only useful when the current model supports image input.'
)


class ReadImageInput(BaseModel):
    file_path: str = Field(
        description=(
            'Absolute path to the image file. Must be absolute, not relative. '
            'Supported formats: png / jpeg / gif / webp.'
        ),
    )


class ReadImageOutput(BaseModel):
    content: str = Field(description='Image metadata text for the LLM (path, type, dimensions, bytes)')
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
    - The result carries metadata text plus an ``images`` path list; the
      assembling side merges the images into the same message (multimodal),
      dropping them when the underlying model cannot see images
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
        agent_id = str(kwargs.pop(AGENT_ID_KEY, '') or '')
        # 项目根只有"有调用者身份"时才去问: agent 那套才有 agent/state.
        # moss 这边没有 agent 概念(也就不注入 from_agent_id), 相对路径按
        # 进程 cwd 解析 —— 判据是"没有身份就别去问我是谁", 不是给旧写法
        # 兜底, 所以不 catch 异常(真问错了要看得见).
        project_root = pop_project_root(kwargs)
        if agent_id and not project_root:
            state = await self.call('agent/state', {'agent_id': agent_id})
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

        # result 携带图片引用 (组装侧特判: 随同一条消息合并成多模态;
        # 模型不支持读图时组装侧剥掉).
        content = (
            f'Read image: {display} ({media_type}{dim_text}, {size} bytes). '
            'The image is attached to this feedback message.'
        )
        return {'content': content, 'images': [str(Path(path))]}
