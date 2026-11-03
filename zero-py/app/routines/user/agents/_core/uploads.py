"""Uploads ---- runtime/uploads/ 统一文件落盘 + MIME + data URL 转换.

一个目录服务所有文件: 图片附件、APK、日志、导出包。HTTP 面只有三条路由
(定义见 ``web_server/app.py``):

  POST /files/upload?name=foo.apk   流式落盘, 返回 size/sha256/url/download
  GET  /files/<name>                inline 读取 (图片 <img src> 直接用这条)
  GET  /files/download/<name>       强制 attachment 下载

消息里只存 ``/files/<name>`` 相对路径; agent 调 LLM 前用 ``resolve_data_url``
把本机图片转成 data URL (Responses API 的 input_image 只认 http(s) URL 或
data URL, 本机路径不可达).

不分"图片上传"和"文件上传"两套: 落盘只有一种, 差别只在 MIME 和大小上限。

生命周期: **只增不删**. 消息历史里的 ``/files/<name>`` 是引用而非副本,
文件一旦删除, 那条历史消息就成了永久坏图 (消费端 ``resolve_data_url``
返回 None 时静默跳过, 不会报错, 只是图消失). 因此清理不能按时间或大小扫,
只能清"未被任何消息历史引用"的文件.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from uuid import uuid4

# uploads.py 在 zero-py/app/routines/user/agents/_core/, parents[5] = zero-py,
# 再上一级 = harness 根 ---- uploads 是 py/ts 共享运行时资源 (web_server
# /files 路由服务, ts pi agent 历史回填落盘), 归根不归某个语言实现.
UPLOADS_DIR = (Path(__file__).resolve().parents[5].parent
               / 'runtime' / 'uploads')

# 单文件上限. 本地开发环境没有鉴权, 大包 (APK/模型/视频) 也能直接推。
MAX_FILE_BYTES = 200 * 1024 * 1024

# 扩展名 -> 下载/内联 MIME. 未知扩展统一 octet-stream.
MIME_BY_EXT = {
    # 图片 (inline 展示)
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
    'bmp': 'image/bmp',
    'svg': 'image/svg+xml',
    'heic': 'image/heic',
    # 音视频
    'mp3': 'audio/mpeg',
    'wav': 'audio/wav',
    'm4a': 'audio/mp4',
    'mp4': 'video/mp4',
    'webm': 'video/webm',
    # 包与压缩
    'apk': 'application/vnd.android.package-archive',
    'aab': 'application/octet-stream',
    'zip': 'application/zip',
    'tar': 'application/x-tar',
    'gz': 'application/gzip',
    # 文档与文本
    'pdf': 'application/pdf',
    'json': 'application/json',
    'txt': 'text/plain; charset=utf-8',
    'md': 'text/markdown; charset=utf-8',
    'log': 'text/plain; charset=utf-8',
    'csv': 'text/csv; charset=utf-8',
    'bin': 'application/octet-stream',
}

# MIME -> 扩展名 (只用于没给文件名时补后缀; 同 MIME 多扩展名取先出现的).
EXT_BY_MIME = {mime: ext for ext, mime in MIME_BY_EXT.items()}
EXT_BY_MIME['image/jpg'] = 'jpg'

_UNSAFE_NAME_CHARS = re.compile(r'[^\w.\-]+', re.UNICODE)


def file_mime(name: str) -> str:
    """文件名 -> 响应 MIME; 未知扩展 octet-stream."""
    ext = Path(name).suffix.lstrip('.').lower()
    return MIME_BY_EXT.get(ext, 'application/octet-stream')


def safe_existing_name(raw: str) -> str | None:
    """校验读取名：只能是 uploads 目录下的单层文件名。

    不允许路径分隔符、`..`、NUL、Windows ADS 冒号；不做模糊修正，
    不合规直接返回 None。
    """
    name = (raw or '').strip()
    if not name or name in ('.', '..') or name.startswith('.'):
        return None
    if any(ch in name for ch in ('/', '\\', '\x00', ':')):
        return None
    if name != name.rstrip(' .') or name != Path(name).name:
        return None
    return name


def stored_name(raw: str, content_type: str = '', digest: str | None = '') -> str:
    """把用户提供的文件名变成安全、唯一的落盘名。

    保留可读的 stem 和扩展名；没有扩展名时按 Content-Type 补（未知则 .bin）。

    前缀：给了 ``digest``（内容 sha256）就用它的前 12 位 —— 内容寻址，同一份
    内容重复上传得到同一个名字，调用方（截图、重放、幂等重传）不会堆文件；
    传 ``None`` 或空串则退回随机前缀（纯上传通道用不着算 sha 时）。
    """
    base = (raw or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    base = base.replace('\x00', '')
    base = _UNSAFE_NAME_CHARS.sub('_', base).strip(' ._-') or 'file'

    suffix = Path(base).suffix.lower()
    if not suffix:
        mime = (content_type or '').split(';', 1)[0].strip().lower()
        suffix = '.' + (EXT_BY_MIME.get(mime) or 'bin')
    elif len(suffix) > 16:
        # 超长"扩展名"通常不是扩展名，按无后缀处理
        base = base[:80]
        suffix = ''

    stem = Path(base).stem[:80] or 'file'
    prefix = (digest or uuid4().hex)[:12]
    return f'{prefix}-{stem}{suffix}'


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
    mime = file_mime(file.name)
    if mime == 'application/octet-stream':
        # 历史数据可能没有扩展名, 按图片通路兜底
        mime = 'image/png'
    return f'data:{mime};base64,{base64.b64encode(data).decode()}'
