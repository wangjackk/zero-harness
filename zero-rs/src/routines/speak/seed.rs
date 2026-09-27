use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use routine::core::RoutineError;
use serde_json::{json, Value as JsonValue};
use tokio_tungstenite::{
    connect_async,
    tungstenite::{client::IntoClientRequest, http::HeaderValue, Message},
    MaybeTlsStream, WebSocketStream,
};
use uuid::Uuid;

use super::protocol::{decode_frame, encode_frame, Event, Frame, FrameKind};

/// 等待服务端事件的全局超时。
const EVENT_TIMEOUT: Duration = Duration::from_secs(30);

/// Seed TTS 服务连接配置。唯一来源是环境变量(对齐 py 侧 `tts/__init__.py`):
/// `../zero-py/.env` 或进程环境提供。
///
/// `SEED_TTS_API_KEY` 必填; 其余可选,缺省见各自 default。
#[derive(Debug, Clone)]
pub struct SeedConfig {
    pub api_key: String,
    pub ws_url: String,
    pub speaker_id: String,
    pub resource_id: String,
}

fn default_ws_url() -> String {
    "wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection".into()
}

fn default_speaker_id() -> String {
    "zh_female_xiaohe_uranus_bigtts".into()
}

fn default_resource_id() -> String {
    "seed-tts-2.0".into()
}

impl SeedConfig {
    /// 从环境变量加载配置(变量名与 py 侧 `create_tts()` 一一对应)。
    pub fn load() -> Result<Self, RoutineError> {
        let api_key = std::env::var("SEED_TTS_API_KEY").unwrap_or_default();
        if api_key.is_empty() {
            return Err(RoutineError::Message(
                "env SEED_TTS_API_KEY not set (add it to zero-py/.env)".into(),
            ));
        }
        let ws_url = std::env::var("SEED_TTS_WS_URL").unwrap_or_else(|_| default_ws_url());
        let speaker_id =
            std::env::var("SEED_TTS_SPEAKER_ID").unwrap_or_else(|_| default_speaker_id());
        let resource_id =
            std::env::var("SEED_TTS_RESOURCE_ID").unwrap_or_else(|_| default_resource_id());
        Ok(Self {
            api_key,
            ws_url,
            speaker_id,
            resource_id,
        })
    }
}

type Socket = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;

/// 与 Seed TTS 服务的双向 WebSocket 连接。
///
/// 连接建立后可开启多个 session，此处我们每次 speak 只用一个 session。
pub(super) struct SeedClient {
    socket: Socket,
}

impl SeedClient {
    /// 建立连接并完成 connection-level 握手。
    pub async fn connect(config: &SeedConfig) -> Result<Self, RoutineError> {
        let mut request = config
            .ws_url
            .as_str()
            .into_client_request()
            .map_err(|e| RoutineError::Message(format!("build seed request: {e}")))?;
        let headers = request.headers_mut();
        headers.insert(
            "X-Api-Key",
            HeaderValue::from_str(&config.api_key)
                .map_err(|e| RoutineError::Message(format!("invalid api key header: {e}")))?,
        );
        headers.insert(
            "X-Api-Resource-Id",
            HeaderValue::from_str(&config.resource_id)
                .map_err(|e| RoutineError::Message(format!("invalid resource id header: {e}")))?,
        );
        headers.insert(
            "X-Api-Connect-Id",
            HeaderValue::from_str(&Uuid::new_v4().to_string())
                .map_err(|e| RoutineError::Message(format!("invalid connect id header: {e}")))?,
        );

        let (socket, _) = connect_async(request)
            .await
            .map_err(|e| RoutineError::Message(format!("connect seed tts: {e}")))?;

        let mut client = Self { socket };
        client
            .send(Event::StartConnection, &json!({}), None)
            .await?;
        client.expect_event(Event::ConnectionStarted).await?;
        Ok(client)
    }

    /// 开启一个 TTS session，配置 speaker 和音频输出格式。
    pub async fn start_session(
        &mut self,
        session_id: &str,
        speaker: &str,
        sample_rate: u32,
    ) -> Result<(), RoutineError> {
        let payload = json!({
            "user": { "uid": "zero-rs" },
            "event": Event::StartSession as i32,
            "req_params": {
                "speaker": speaker,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": sample_rate,
                }
            }
        });
        self.send(Event::StartSession, &payload, Some(session_id))
            .await?;
        self.expect_event(Event::SessionStarted).await?;
        Ok(())
    }

    /// 发送一片要合成的文本。
    ///
    /// Seed 双向 TTS 支持流式增发：一个 session 内可多次调用本方法，
    /// 每次喂入一段文本片段，服务端边收边合成返回 PCM。最后用
    /// [`finish_session`](Self::finish_session) 收口。
    ///
    /// 非流式场景等价于只调用一次后立即 `finish_session`。
    pub async fn send_text(&mut self, session_id: &str, text: &str) -> Result<(), RoutineError> {
        let payload = json!({
            "event": Event::TaskRequest as i32,
            "req_params": { "text": text }
        });
        self.send(Event::TaskRequest, &payload, Some(session_id))
            .await
    }

    /// 通知服务端文本发送完毕。
    pub async fn finish_session(&mut self, session_id: &str) -> Result<(), RoutineError> {
        self.send(Event::FinishSession, &json!({}), Some(session_id))
            .await
    }

    /// 请求服务端取消当前 session（打断场景）。
    pub async fn cancel_session(&mut self, session_id: &str) -> Result<(), RoutineError> {
        self.send(Event::CancelSession, &json!({}), Some(session_id))
            .await
    }

    /// 等待并返回下一个事件帧（自动跳过 keepalive/其它帧，错误帧转为 Err）。
    pub async fn next_event(&mut self) -> Result<Frame, RoutineError> {
        loop {
            let frame = self.recv_frame().await?;
            match frame.kind {
                FrameKind::Error { code } => {
                    return Err(RoutineError::Message(format!(
                        "seed server error code={code}: {}",
                        String::from_utf8_lossy(&frame.payload)
                    )));
                }
                FrameKind::Event(_) => return Ok(frame),
                FrameKind::Other => continue, // keepalive 等
            }
        }
    }

    // ── 底层 send/recv ────────────────────────────────────────────

    async fn send(
        &mut self,
        event: Event,
        payload: &JsonValue,
        session_id: Option<&str>,
    ) -> Result<(), RoutineError> {
        let data = encode_frame(event, payload, session_id);
        self.socket
            .send(Message::Binary(data.into()))
            .await
            .map_err(|e| RoutineError::Message(format!("seed send frame: {e}")))
    }

    async fn recv_frame(&mut self) -> Result<Frame, RoutineError> {
        loop {
            let Some(msg) = self.socket.next().await else {
                return Err(RoutineError::Message(
                    "seed tts closed the connection unexpectedly".into(),
                ));
            };
            let msg = msg.map_err(|e| RoutineError::Message(format!("seed recv frame: {e}")))?;
            match msg {
                Message::Binary(data) => return decode_frame(data),
                Message::Text(t) => {
                    return Err(RoutineError::Message(format!(
                        "unexpected seed text frame: {t}"
                    )));
                }
                Message::Close(frame) => {
                    return Err(RoutineError::Message(format!("seed tts closed: {frame:?}")));
                }
                Message::Ping(_) | Message::Pong(_) | Message::Frame(_) => {}
            }
        }
    }

    async fn expect_event(&mut self, expected: Event) -> Result<Frame, RoutineError> {
        let deadline = tokio::time::sleep(EVENT_TIMEOUT);
        tokio::pin!(deadline);
        loop {
            tokio::select! {
                _ = &mut deadline => {
                    return Err(RoutineError::Message(format!(
                        "timeout waiting for event {expected:?} ({EVENT_TIMEOUT:?})"
                    )));
                }
                frame = self.next_event() => {
                    let frame = frame?;
                    if let FrameKind::Event(ev) = frame.kind {
                        if ev == expected {
                            return Ok(frame);
                        }
                    }
                }
            }
        }
    }
}
