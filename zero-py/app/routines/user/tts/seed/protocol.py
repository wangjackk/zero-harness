"""
Seed WS 双向流二进制帧协议编解码。

协议结构（大端字节序）：
  Byte 0: [protocol_version=0001][header_size=0001]   → 0x11
  Byte 1: [message_type][type_specific_flags]
  Byte 2: [serialization][compression]
  Byte 3: reserved = 0x00
  Byte 4-7: event (int32, 仅当 flags & 0x4)
  [opt] uint32 id_size + id bytes   (connection_id 或 session_id)
  uint32 payload_size + payload bytes
"""

import json
import struct
from enum import IntEnum
from typing import Any


class Event(IntEnum):
    StartConnection   = 1
    FinishConnection  = 2
    ConnectionStarted = 50
    ConnectionFailed  = 51
    ConnectionFinished= 52
    StartSession      = 100
    CancelSession     = 101
    FinishSession     = 102
    SessionStarted    = 150
    SessionCanceled   = 151
    SessionFinished   = 152
    SessionFailed     = 153
    TaskRequest       = 200
    TTSSentenceStart  = 350
    TTSSentenceEnd    = 351
    TTSResponse       = 352


# ── header byte 1 定义 ─────────────────────────────────────────────────────────
# message_type (high nibble) | type_specific_flags (low nibble)
_MT_FULL_CLIENT  = 0x1   # 0001 Full-client request
_MT_FULL_SERVER  = 0x9   # 1001 Full-server response
_MT_AUDIO_SERVER = 0xB   # 1011 Audio-only response
_MT_ERROR        = 0xF   # 1111 Error information

_FLAG_WITH_EVENT = 0x4   # low nibble bit2 → 有 event 字段

_SER_RAW  = 0x00
_SER_JSON = 0x10   # high nibble of byte2

_COMP_NONE = 0x00


def _header(msg_type: int, flags: int, ser: int = _SER_JSON) -> bytes:
    return bytes([
        0x11,                         # version=1, header_size=1 (4 bytes)
        (msg_type << 4) | flags,
        ser | _COMP_NONE,
        0x00,
    ])


def build_frame(
    event: Event,
    payload: dict | None = None,
    session_id: str | None = None,
) -> bytes:
    """构建上行 Full-client request 帧（含 event、可选 session_id、JSON payload）。"""
    payload_bytes = json.dumps(payload or {}).encode()

    buf = bytearray()
    buf += _header(_MT_FULL_CLIENT, _FLAG_WITH_EVENT, _SER_JSON)
    buf += struct.pack(">i", int(event))          # event int32 大端

    if session_id is not None:
        sid_bytes = session_id.encode()
        buf += struct.pack(">I", len(sid_bytes))
        buf += sid_bytes

    buf += struct.pack(">I", len(payload_bytes))
    buf += payload_bytes
    return bytes(buf)


def build_task_request(text: str, session_id: str) -> bytes:
    """构建 TaskRequest 帧（发送文本）。"""
    return build_frame(
        Event.TaskRequest,
        {"event": int(Event.TaskRequest), "req_params": {"text": text}},
        session_id=session_id,
    )


class FrameParseError(Exception):
    pass


class ParsedFrame:
    __slots__ = ("event", "session_id", "payload_raw", "is_audio", "is_error", "error_code")

    def __init__(
        self,
        event: Event | None,
        session_id: str | None,
        payload_raw: bytes,
        is_audio: bool = False,
        is_error: bool = False,
        error_code: int = 0,
    ):
        self.event       = event
        self.session_id  = session_id
        self.payload_raw = payload_raw
        self.is_audio    = is_audio
        self.is_error    = is_error
        self.error_code  = error_code

    def payload_json(self) -> Any:
        return json.loads(self.payload_raw) if self.payload_raw else {}


def parse_frame(data: bytes) -> ParsedFrame:
    """解析服务端下行二进制帧。"""
    if len(data) < 4:
        raise FrameParseError("frame too short")

    # byte 1: [msg_type(4bit)][flags(4bit)]
    msg_type = (data[1] >> 4) & 0xF
    flags    = data[1] & 0xF
    # byte 2: [serialization(4bit)][compression(4bit)]
    ser      = (data[2] >> 4) & 0xF

    is_error = msg_type == _MT_ERROR
    is_audio = msg_type == _MT_AUDIO_SERVER
    has_event = bool(flags & _FLAG_WITH_EVENT)

    pos = 4  # 跳过 4 字节 header

    # 错误帧: byte 4-7 = error_code, 无 event
    if is_error:
        if len(data) < pos + 4:
            raise FrameParseError("error frame too short")
        error_code = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        payload_size = struct.unpack_from(">I", data, pos)[0]; pos += 4
        payload_raw = data[pos: pos + payload_size]
        return ParsedFrame(None, None, payload_raw, is_error=True, error_code=error_code)

    # event number
    event: Event | None = None
    if has_event:
        if len(data) < pos + 4:
            raise FrameParseError("missing event field")
        raw_event = struct.unpack_from(">i", data, pos)[0]; pos += 4
        try:
            event = Event(raw_event)
        except ValueError:
            event = None

    # 可选 id（connection_id 或 session_id）：服务端会带
    session_id: str | None = None
    # 判断是否有 id 字段：ConnectionStarted 带 connection_id，Session 类带 session_id
    # 协议层无法从 flags 区分，统一尝试解析：若剩余 >= 8 且前 4 字节作为 id_len 合理则读取
    if event in (
        Event.ConnectionStarted, Event.ConnectionFailed, Event.ConnectionFinished,
        Event.SessionStarted, Event.SessionFinished, Event.SessionFailed,
        Event.SessionCanceled, Event.TTSSentenceStart, Event.TTSSentenceEnd,
        Event.TTSResponse,
    ):
        if len(data) >= pos + 4:
            id_len = struct.unpack_from(">I", data, pos)[0]
            # 合理性检查：id_len < 256 且后面还有足够字节
            if id_len < 256 and len(data) >= pos + 4 + id_len:
                pos += 4
                session_id = data[pos: pos + id_len].decode(errors="replace")
                pos += id_len

    # payload
    if len(data) < pos + 4:
        return ParsedFrame(event, session_id, b"", is_audio=is_audio)
    payload_size = struct.unpack_from(">I", data, pos)[0]; pos += 4
    payload_raw = data[pos: pos + payload_size]

    return ParsedFrame(event, session_id, payload_raw, is_audio=is_audio)
