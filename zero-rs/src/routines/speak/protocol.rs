use routine::core::RoutineError;
use serde_json::Value as JsonValue;
use tokio_tungstenite::tungstenite::Bytes;

/// Seed TTS 双向 WebSocket 协议事件码。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub(super) enum Event {
    StartConnection = 1,
    FinishConnection = 2,
    ConnectionStarted = 50,
    ConnectionFinished = 52,
    StartSession = 100,
    CancelSession = 101,
    FinishSession = 102,
    SessionStarted = 150,
    SessionCanceled = 151,
    SessionFinished = 152,
    SessionFailed = 153,
    TaskRequest = 200,
    TtsResponse = 352,
}

impl TryFrom<i32> for Event {
    type Error = ();

    fn try_from(value: i32) -> Result<Self, ()> {
        match value {
            1 => Ok(Self::StartConnection),
            2 => Ok(Self::FinishConnection),
            50 => Ok(Self::ConnectionStarted),
            52 => Ok(Self::ConnectionFinished),
            100 => Ok(Self::StartSession),
            101 => Ok(Self::CancelSession),
            102 => Ok(Self::FinishSession),
            150 => Ok(Self::SessionStarted),
            151 => Ok(Self::SessionCanceled),
            152 => Ok(Self::SessionFinished),
            153 => Ok(Self::SessionFailed),
            200 => Ok(Self::TaskRequest),
            352 => Ok(Self::TtsResponse),
            _ => Err(()),
        }
    }
}

/// 从 WebSocket 收到并解析后的一帧。
#[derive(Debug)]
pub(super) struct Frame {
    pub kind: FrameKind,
    #[allow(dead_code)]
    pub session_id: Option<String>,
    pub payload: Bytes,
}

#[derive(Debug)]
pub(super) enum FrameKind {
    /// 携带事件的业务帧。
    Event(Event),
    /// 服务端错误帧，附带错误码。
    Error { code: u32 },
    /// 无事件码的 keepalive / 裸 payload 帧。
    Other,
}

/// 编码一帧二进制消息发送给 Seed 服务。
pub(super) fn encode_frame(event: Event, payload: &JsonValue, session_id: Option<&str>) -> Vec<u8> {
    let payload_bytes = payload.to_string().into_bytes();
    let mut buf = Vec::with_capacity(64 + payload_bytes.len());
    buf.extend_from_slice(&[0x11, 0x14, 0x10, 0x00]);
    buf.extend_from_slice(&(event as i32).to_be_bytes());
    if let Some(sid) = session_id {
        let sid_bytes = sid.as_bytes();
        buf.extend_from_slice(&(sid_bytes.len() as u32).to_be_bytes());
        buf.extend_from_slice(sid_bytes);
    }
    buf.extend_from_slice(&(payload_bytes.len() as u32).to_be_bytes());
    buf.extend_from_slice(&payload_bytes);
    buf
}

/// 解码从 Seed 服务收到的一帧二进制消息。
pub(super) fn decode_frame(data: Bytes) -> Result<Frame, RoutineError> {
    let raw = data.as_ref();
    if raw.len() < 4 {
        return Err(RoutineError::Message("seed frame too short".into()));
    }

    let msg_type = (raw[1] >> 4) & 0x0f;
    let flags = raw[1] & 0x0f;
    let mut pos = 4usize;

    // 0x0f = 错误帧
    if msg_type == 0x0f {
        let code = read_u32(raw, &mut pos, "seed error frame missing code")?;
        let payload_size =
            read_u32(raw, &mut pos, "seed error frame missing payload size")? as usize;
        let payload = read_payload(data, &mut pos, payload_size)?;
        return Ok(Frame {
            kind: FrameKind::Error { code },
            session_id: None,
            payload,
        });
    }

    let event = if flags & 0x04 != 0 {
        let event = read_u32(raw, &mut pos, "seed frame missing event")?;
        Event::try_from(event as i32).ok()
    } else {
        None
    };

    let mut session_id = None;
    if matches!(
        event,
        Some(
            Event::ConnectionStarted
                | Event::ConnectionFinished
                | Event::SessionStarted
                | Event::SessionFinished
                | Event::SessionCanceled
                | Event::SessionFailed
                | Event::TtsResponse
        )
    ) && remaining(raw, pos) >= 4
    {
        let id_len = read_u32(raw, &mut pos, "seed frame missing session id length")? as usize;
        if id_len < 256 && remaining(raw, pos) >= id_len {
            session_id = Some(String::from_utf8_lossy(&raw[pos..pos + id_len]).into_owned());
            pos += id_len;
        }
    }

    let payload = if remaining(raw, pos) >= 4 {
        let payload_size = read_u32(raw, &mut pos, "seed frame missing payload size")? as usize;
        read_payload(data, &mut pos, payload_size)?
    } else {
        Bytes::default()
    };

    Ok(Frame {
        kind: match event {
            Some(ev) => FrameKind::Event(ev),
            None => FrameKind::Other,
        },
        session_id,
        payload,
    })
}

#[inline]
fn remaining(data: &[u8], pos: usize) -> usize {
    data.len().saturating_sub(pos)
}

fn read_u32(data: &[u8], pos: &mut usize, err_msg: &str) -> Result<u32, RoutineError> {
    if remaining(data, *pos) < 4 {
        return Err(RoutineError::Message(err_msg.into()));
    }
    let bytes = data[*pos..*pos + 4].try_into().unwrap();
    *pos += 4;
    Ok(u32::from_be_bytes(bytes))
}

fn read_payload(data: Bytes, pos: &mut usize, size: usize) -> Result<Bytes, RoutineError> {
    let raw = data.as_ref();
    if remaining(raw, *pos) < size {
        return Err(RoutineError::Message("seed frame payload truncated".into()));
    }
    let start = *pos;
    let end = start + size;
    *pos = end;
    Ok(data.slice(start..end))
}
