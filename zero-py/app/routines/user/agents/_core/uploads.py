"""Uploads ---- runtime/uploads/ 落盘 + data URL 转换.

图片输入通路: 前端 POST /upload 落盘, 消息里只传 '/files/<name>' 相对路径;
agent 调 LLM 前用 resolve_data_url 把本地文件转成 data URL (Responses API
的 input_image 只认 http(s) URL 或 data URL, 本机路径不可达).
"""
from __future__ import annotations

import base64
from pathlib import Path
from uuid import uuid4

# uploads.py 在 zero-py/app/routines/user/agents/_core/, parents[5] = zero-py,
# 再上一级 = harness 根 ---- uploads 是 py/ts 共享运行时资源 (web_server
# /files 路由服务, ts pi agent 历史回填落盘), 归根不归某个语言实现.
UPLOADS_DIR = (Path(__file__).resolve().parents[5].parent
               / 'runtime' / 'uploads')

_MIME_BY_EXT = {
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
}

_EXT_BY_MIME = {v: k for k, v in _MIME_BY_EXT.items()}
_EXT_BY_MIME['image/jpg'] = 'jpg'

_MAX_BYTES = 20 * 1024 * 1024


def ext_from_mime(mime: str) -> str | None:
    """content-type -> 扩展名; 非图片类型返回 None."""
    return _EXT_BY_MIME.get((mime or '').split(';')[0].strip().lower())


def ext_of(path: str) -> str:
    """文件名后缀 (小写, 无点); 无后缀默认 png."""
    ext = Path(path).suffix.lstrip('.').lower()
    return ext if ext in _MIME_BY_EXT else 'png'


def save_image(data: bytes, ext: str) -> str:
    """落盘一个图片文件, 返回 '/files/<name>' 相对路径."""
    if not data:
        raise ValueError('empty upload body')
    if len(data) > _MAX_BYTES:
        raise ValueError(f'upload too large: {len(data)} bytes')
    ext = ext if ext in _MIME_BY_EXT else 'png'
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    name = f'{uuid4().hex}.{ext}'
    (UPLOADS_DIR / name).write_bytes(data)
    return f'/files/{name}'


def resolve_data_url(path: str) -> str | None:
    """/files/<name> (或绝对路径) -> data URL; 文件不存在返回 None."""
    if path.startswith('data:'):
        return path
    if path.startswith('/files/'):
        name = path[len('/files/'):]
        if '/' in name or '..' in name or '\\' in name:
            return None
        file = UPLOADS_DIR / name
    elif Path(path).is_absolute():
        file = Path(path)
    else:
        return None
    try:
        data = file.read_bytes()
    except OSError:
        return None
    mime = _MIME_BY_EXT.get(file.suffix.lstrip('.').lower(), 'image/png')
    return f'data:{mime};base64,{base64.b64encode(data).decode()}'
