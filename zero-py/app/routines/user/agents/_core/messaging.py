"""Agent 消息约定: 优先级常量 + from 头包装 + content 数组协议.

priority: 决定接收方是否打断当前 react. 高 (>=PRIORITY_HIGH) 立即处理 (打断);
低排队等 idle. user 默认高, agent 默认低.

``wrap_from``: 给 agent 消息正文加 ``from: <id>`` 头. 用户消息不加.

消息协议 (chat_message req / user_input pubsub, 跨语言 py/ts 一致)::

    {
        "from": "user" | <agent_id>,
        "priority": int,                      # 可选; 缺省高
        "content": [                          # discriminated union 数组
            {"type": "text", "text": str},
            {"type": "image", "path": str},    # 绝对路径 | data: URL
        ],
    }

content 由入口层 (send_message / web_server) 组装: 前端上传返回的
``/files/<name>`` 引用在这里 resolve 成绝对路径, 消费方 (含 ts 侧) 无需
web_server 目录知识. 未知 type 立即抛错 (协议违规直接暴露).
"""
from __future__ import annotations

from pathlib import Path

USER_SOURCE = 'user'

# priority: 决定接收方是否打断当前 react. 高 (>=PRIORITY_HIGH) 立即处理 (打断);
# 低排队等 idle. user 默认高, agent 默认低.
PRIORITY_HIGH = 10
PRIORITY_LOW = 0


def wrap_from(content: str, from_agent: str) -> str:
    """给 agent 消息正文加 ``from: <id>`` 头. 用户消息原样返回不加头."""
    if not from_agent or from_agent == USER_SOURCE:
        return content
    return f'from: {from_agent}\n{content}'


def _resolve_path(path: str) -> str:
    """/files/<name> → uploads 绝对路径; 绝对路径校验存在后透传; data: 透传.

    引用型输入在这里就确认文件存在: 否则坏路径会一路传到 provider 的
    ``read_bytes()`` 才抛错, 报错点离原因很远。data: 无需检查 (内容自带)。
    """
    if path.startswith('/files/'):
        from .uploads import UPLOADS_DIR
        name = path[len('/files/'):]
        if not name or '/' in name or '..' in name or '\\' in name:
            raise ValueError(f'invalid files ref: {path!r}')
        file = UPLOADS_DIR / name
        if not file.is_file():
            raise ValueError(f'image not found: {path!r}')
        return str(file)
    if path.startswith('data:'):
        return path
    file = Path(path)
    if file.is_absolute():
        if not file.is_file():
            raise ValueError(f'image not found: {path!r}')
        return path
    raise ValueError(f'image path must be /files/<name>, absolute, or data: URL, got {path!r}')


def build_content(text: str, images: list[str] | None = None) -> list[dict]:
    """入口层组装: {text, images 引用} → content 数组.

    web_server 的 user_input 输入是 text+images 两个表单字段
    (UI 输入框 + 贴图), 在这里一步组装.
    """
    content: list[dict] = []
    if text:
        content.append({'type': 'text', 'text': text})
    for p in images or []:
        content.append({'type': 'image', 'path': _resolve_path(str(p))})
    return content


def resolve_content(content: list[dict]) -> list[dict]:
    """入口层 resolve: image block 的 path (/files/<name> → 绝对路径).

    send_message 的输入已是 content 数组 (text 与 image 平权),
    组装只差 path resolve.
    """
    out: list[dict] = []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'image':
            out.append({**block, 'path': _resolve_path(str(block.get('path') or ''))})
        else:
            out.append(block)
    return out


def to_files_ref(path: str) -> str:
    """绝对路径 → '/files/<name>': 只有 uploads 目录内的图前端能直接引用.

    send_message 已在入口把 /files 引用 resolve 成绝对路径, 回显入站消息时
    要转回去 (前端 img 只认 /files/<name>). data: / http / 目录外原样返回.
    """
    if not path or path.startswith(('data:', 'http://', 'https://')):
        return path
    parent, _, name = path.replace('\\', '/').rpartition('/')
    if name and parent.rpartition('/')[2] == 'uploads':
        return f'/files/{name}'
    return path


def split_content(content: list[dict]) -> tuple[str, list[str]]:
    """消费层拆解: content 数组 → (text 主体, image path 列表).

    多个 text block 以 ``\n`` 连接成单一主体 (LLM 接口是 text 主体 + images 附件).
    """
    texts: list[str] = []
    images: list[str] = []
    for block in content or []:
        if not isinstance(block, dict):
            raise ValueError(f'content block must be object, got {block!r}')
        btype = block.get('type')
        if btype == 'text':
            texts.append(str(block.get('text') or ''))
        elif btype == 'image':
            images.append(str(block.get('path') or ''))
        else:
            raise ValueError(f'unsupported content type: {btype!r}')
    return '\n'.join(t for t in texts if t), images