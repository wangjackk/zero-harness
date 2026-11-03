"""豆包(火山 SAUC)大模型流式识别 —— 二进制 WS 协议 (bigmodel_async).

官方文档: https://docs.volcengine.com/docs/6561/1354869

帧格式(客户端 → 服务端)::

    header(4B) + seq(4B) + payload_size(4B) + gzip(payload)

    header[0] = (版本 << 4) | header 字数
    header[1] = (message_type << 4) | flags      flags: 序号/尾包/事件 位
    header[2] = (序列化 << 4) | 压缩

服务端回包同构, 但字段按 flags 位可选(带序号 / 尾包 / 事件), 且错误响应
(int32 code + size) 与正常响应(u32 size)头部不同 —— 逐位解, 不猜。

本模块只做"帧的语法": 组 init / 组音频 / 解响应。连接、重连、分句策略
在 ``ear.py``; 语义(哪句进脑)在 ``routine.py``。
"""
from __future__ import annotations

import gzip
import io
import json
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

__all__ = [
    'DEFAULT_URL',
    'DEFAULT_RESOURCE_ID',
    'DEFAULT_MODEL',
    'default_params',
    'build_init',
    'build_audio',
    'Response',
    'parse_response',
]

# 方舟 Agent Plan 套餐通道(与 seed-tts 的 /plan/tts 同一把专属 Key)
DEFAULT_URL = 'wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async'
# 豆包 2.0 (搭配 X-Api-Key 鉴权)
DEFAULT_RESOURCE_ID = 'volc.seedasr.sauc.duration'
DEFAULT_MODEL = 'bigmodel'

# ── 协议常量 ──────────────────────────────────────────────────────

_VERSION = 0x01
_HEADER_WORDS = 0x01          # header 长度, 单位 4 字节

_FULL_CLIENT_REQUEST = 0x01   # init(带 JSON)
_AUDIO_ONLY_REQUEST = 0x02
_FULL_SERVER_RESPONSE = 0x09
_SERVER_ERROR_RESPONSE = 0x0F

_POS_SEQUENCE = 0x01
_NEG_WITH_SEQUENCE = 0x03     # 负序号 = 该流最后一块音频

_JSON = 0x01
_GZIP = 0x01

_FLAG_SEQ = 0x01              # 回包: 带 payload_sequence
_FLAG_LAST = 0x02             # 回包: 整条流最后一个包
_FLAG_EVENT = 0x04            # 回包: 带会话事件号


def default_params() -> Dict[str, Any]:
    """行为旋钮默认值(每次 init 下发; 与协议同层, 不做成配置类).

    - ``end_window_size``: VAD 静音判停阈值(ms), 800 = 说完约 0.8s 出定稿;
    - ``enable_nonstream``: 二遍识别 —— 关掉走流式(低延迟, 定稿由
      utterance 的 definite 给出; 与 asrserver-rs 的 volc.rs 同参);
    - ``force_to_speech_time``: 开头强制按有声处理, 防起始弱音被 VAD 切掉。
    """
    return {
        'model_name': DEFAULT_MODEL,
        'enable_nonstream': False,
        'enable_itn': True,
        'enable_punc': True,
        'enable_ddc': True,
        'show_utterances': True,
        'result_type': 'full',
        'end_window_size': 800,
        'force_to_speech_time': 1000,
        'vad_segment_duration': 3000,
    }


def _gzip(data: bytes) -> bytes:
    if not data:
        return b''
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as f:
        f.write(data)
    return buf.getvalue()


def _ungzip(data: bytes) -> bytes:
    if not data:
        return b''
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode='rb') as f:
        return f.read()


def _frame(message_type: int, flags: int, seq: int,
           payload: bytes, compression: int) -> bytes:
    header = bytearray(4)
    header[0] = (_VERSION << 4) | _HEADER_WORDS
    header[1] = (message_type << 4) | flags
    header[2] = (_JSON << 4) | compression
    header[3] = 0x00
    return b''.join([bytes(header), struct.pack('>i', seq),
                     struct.pack('>I', len(payload)), payload])


def build_init(uid: str, *, sample_rate: int = 16000, bits: int = 16,
               channel: int = 1, params: Optional[Dict[str, Any]] = None,
               context: Optional[Dict[str, Any]] = None) -> bytes:
    """开流首包: 音频格式 + 行为参数(可选热词/上下文)。

    ``uid`` 每次识别流一份(服务端按它归并)。
    """
    p = dict(default_params())
    if params:
        p.update(params)
    request: Dict[str, Any] = dict(p)
    if context:
        request['context'] = json.dumps(context, ensure_ascii=False)
    payload = {
        'user': {'uid': uid},
        'audio': {'format': 'pcm', 'codec': 'raw', 'rate': sample_rate,
                  'bits': bits, 'channel': channel},
        'request': request,
    }
    body = _gzip(json.dumps(payload).encode('utf-8'))
    return _frame(_FULL_CLIENT_REQUEST, _POS_SEQUENCE, 1, body, _GZIP)


def build_audio(pcm: bytes, seq: int, is_last: bool = False) -> bytes:
    """音频块(已是 16k/16bit/单声道 PCM)。

    ``is_last=True``: 负序号 = 本流最后一块 —— 服务端回尾包后流结束。
    空 payload 不能标 gzip(服务端解压会 EOF), 改 NO_COMPRESSION。
    """
    seq_value = -seq if is_last else seq
    flags = _NEG_WITH_SEQUENCE if is_last else _POS_SEQUENCE
    compression = _GZIP if pcm else 0x00
    body = _gzip(pcm) if pcm else b''
    return _frame(_AUDIO_ONLY_REQUEST, flags, seq_value, body, compression)


@dataclass
class Response:
    """解出的一条回包。

    ``text`` 是全量(累计)文本; ``utterances`` 是分句数组(每条带
    ``definite``: 是否定稿)。``is_last`` 表示整条流最后包。
    """

    code: int = 0
    event: int = 0
    is_last: bool = False
    seq: int = 0
    text: str = ''
    utterances: List[dict] = field(default_factory=list)
    error: str = ''
    raw: str = ''   # 错误响应原文(结构不同于正常 payload, 不建模)


def parse_response(data: bytes) -> Response:
    """解二进制回包 —— 按 header 的 flags 位逐段切, 容错优先于严格。"""
    resp = Response()
    if len(data) < 4:
        return resp
    header_size = data[0] & 0x0F
    message_type = (data[1] >> 4) & 0x0F
    flags = data[1] & 0x0F
    serialization = (data[2] >> 4) & 0x0F
    compression = data[2] & 0x0F
    body = data[header_size * 4:]

    if flags & _FLAG_SEQ and len(body) >= 4:
        resp.seq = struct.unpack('>i', body[:4])[0]
        body = body[4:]
    if flags & _FLAG_LAST:
        resp.is_last = True
    if flags & _FLAG_EVENT and len(body) >= 4:
        resp.event = struct.unpack('>i', body[:4])[0]
        body = body[4:]

    if message_type == _FULL_SERVER_RESPONSE and len(body) >= 4:
        size = struct.unpack('>I', body[:4])[0]
        body = body[4:][:size]
    elif message_type == _SERVER_ERROR_RESPONSE and len(body) >= 8:
        resp.code = struct.unpack('>i', body[:4])[0]
        body = body[8:]
    else:
        return resp

    if not body:
        return resp
    if compression == _GZIP:
        try:
            body = _ungzip(body)
        except OSError:
            return resp
    if serialization != _JSON:
        return resp

    text = body.decode('utf-8', 'replace')
    if resp.code != 0:
        resp.error = text
        resp.raw = text
        return resp
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return resp
    result = (msg or {}).get('result') or {}
    resp.text = str(result.get('text') or '')
    resp.utterances = [u for u in (result.get('utterances') or [])
                       if isinstance(u, dict)]
    return resp
