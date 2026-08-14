use crate::{
    core::RoutineIo,
    protocol::{events::SHELL_REQ, JsonObject, RawWireEvent},
    server::{OutboundTransport, ServerRuntime},
};
use serde_json::Value;
use std::{collections::HashMap, sync::Arc, time::Duration};
use tokio::sync::{oneshot, Mutex};
use uuid::Uuid;

pub struct ShellReqManager {
    _runtime: Arc<ServerRuntime>,
    transport: Arc<OutboundTransport>,
    pending: Mutex<HashMap<String, oneshot::Sender<Result<JsonObject, String>>>>,
}

impl ShellReqManager {
    pub fn new(runtime: Arc<ServerRuntime>, transport: Arc<OutboundTransport>) -> Self {
        Self {
            _runtime: runtime,
            transport,
            pending: Mutex::new(HashMap::new()),
        }
    }

    pub async fn send(
        &self,
        topic: &str,
        data: Option<JsonObject>,
        timeout_seconds: u64,
    ) -> Result<JsonObject, String> {
        let request_id = Uuid::new_v4()
            .simple()
            .to_string()
            .chars()
            .take(12)
            .collect::<String>();
        let (sender, receiver) = oneshot::channel();
        self.pending.lock().await.insert(request_id.clone(), sender);
        self.transport
            .send_raw_event(
                RawWireEvent::new(SHELL_REQ)
                    .with_field("request_id", Value::String(request_id.clone()))
                    .with_field("topic", Value::String(topic.to_string()))
                    .with_field("data", Value::Object(data.unwrap_or_default())),
                None,
            )
            .await?;
        let result = tokio::time::timeout(Duration::from_secs(timeout_seconds), receiver)
            .await
            .map_err(|_| format!("shell req {topic} timed out"))?
            .map_err(|_| format!("shell req {topic} waiter closed"))?;
        self.pending.lock().await.remove(&request_id);
        result
    }

    pub async fn handle_reply(&self, msg: JsonObject) {
        let request_id = msg
            .get("request_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if request_id.is_empty() {
            return;
        }
        let Some(sender) = self.pending.lock().await.remove(&request_id) else {
            return;
        };
        if msg.get("ok").and_then(Value::as_bool).unwrap_or(false) {
            let data = msg
                .get("data")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let _ = sender.send(Ok(data));
        } else {
            let error = msg
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("unknown shell req error")
                .to_string();
            let _ = sender.send(Err(error));
        }
    }
}
