use crate::{
    core::{HandleReply, RoutineIo, SubmitReply},
    protocol::{
        events::{
            LIFECYCLE_CREATED, LIFECYCLE_HEARTBEAT_ACK,
            LIFECYCLE_STARTED, LIFECYCLE_STOP, LIFECYCLE_STOPPED,
        },
        ControlDoneReason, JsonObject, RawWireEvent,
    },
    server::{ServerRuntime, ShellReqManager},
};
use async_trait::async_trait;
use serde_json::Value;
use std::sync::{Arc, RwLock};
use tokio::sync::oneshot;

pub struct OutboundTransport {
    runtime: Arc<ServerRuntime>,
    shell_req: RwLock<Option<Arc<ShellReqManager>>>,
}

impl OutboundTransport {
    pub fn new(runtime: Arc<ServerRuntime>) -> Self {
        Self {
            runtime,
            shell_req: RwLock::new(None),
        }
    }

    pub fn attach_shell_req(&self, shell_req: Arc<ShellReqManager>) {
        *self.shell_req.write().expect("shell_req lock poisoned") = Some(shell_req);
    }

    pub async fn send_lifecycle_created(
        &self,
        id: &str,
        name: &str,
        modules: &[String],
        peer_id: Option<&str>,
    ) -> Result<(), String> {
        let module_values: Vec<Value> = modules
            .iter()
            .map(|m| Value::String(m.clone()))
            .collect();
        self.send_raw_event(
            RawWireEvent::new(LIFECYCLE_CREATED)
                .with_field("id", Value::String(id.to_string()))
                .with_field("name", Value::String(name.to_string()))
                .with_field("modules", Value::Array(module_values)),
            peer_id,
        )
        .await
    }

    pub async fn send_lifecycle_started(
        &self,
        id: &str,
        peer_id: Option<&str>,
    ) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(LIFECYCLE_STARTED).with_field("id", Value::String(id.to_string())),
            peer_id,
        )
        .await
    }

    pub async fn send_lifecycle_heartbeat_ack(
        &self,
        id: &str,
        peer_id: Option<&str>,
    ) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(LIFECYCLE_HEARTBEAT_ACK)
                .with_field("id", Value::String(id.to_string())),
            peer_id,
        )
        .await
    }

    pub async fn send_lifecycle_stopped(
        &self,
        id: &str,
        reason: ControlDoneReason,
        result: Option<Value>,
        error: Option<JsonObject>,
        peer_id: Option<&str>,
    ) -> Result<(), String> {
        let mut payload = RawWireEvent::new(LIFECYCLE_STOPPED)
            .with_field("id", Value::String(id.to_string()))
            .with_field("reason", Value::String(reason.as_wire().to_string()));
        if let Some(result) = result {
            payload = payload.with_field("result", result);
        }
        if let Some(error) = error {
            payload = payload.with_field("error", Value::Object(error));
        }
        self.send_raw_event(payload, peer_id).await
    }
}

#[async_trait]
impl RoutineIo for OutboundTransport {
    async fn send_raw_event(
        &self,
        payload: RawWireEvent,
        peer_id: Option<&str>,
    ) -> Result<(), String> {
        let message = crate::grpc::json_to_frame(&payload.into_json_object());
        let peers = self.runtime.peer_to_queue.lock().await;
        if let Some(peer_id) = peer_id {
            if let Some(queue) = peers.get(peer_id) {
                let _ = queue.send(message);
            }
            return Ok(());
        }
        for queue in peers.values() {
            let _ = queue.send(message.clone());
        }
        Ok(())
    }

    async fn send_shell_req(
        &self,
        action: &str,
        data: Option<JsonObject>,
        timeout_seconds: u64,
    ) -> Result<JsonObject, String> {
        let shell_req = self
            .shell_req
            .read()
            .expect("shell_req lock poisoned")
            .clone()
            .ok_or_else(|| "ShellReqManager not attached".to_string())?;
        shell_req.send(action, data, timeout_seconds).await
    }

    async fn request_stop(&self, id: &str, peer_id: Option<&str>) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(LIFECYCLE_STOP).with_field("id", Value::String(id.to_string())),
            peer_id,
        )
        .await
    }

    async fn register_submit_future(
        &self,
        req_id: String,
        sender: oneshot::Sender<SubmitReply>,
    ) {
        // SubmitReply 和 SubmitWaiter 内部类型一致 (Result<(String, Option<Vec<String>>), String>),
        // 直接转发到 runtime 的 future 表.
        self.runtime
            .register_submit_future(&req_id, sender)
            .await;
    }

    async fn pop_submit_future(
        &self,
        req_id: &str,
    ) -> Option<oneshot::Sender<SubmitReply>> {
        self.runtime.pop_submit_future(req_id).await
    }

    async fn register_handle_waiter(
        &self,
        child_id: String,
        sender: oneshot::Sender<HandleReply>,
    ) {
        self.runtime
            .register_handle_waiter(&child_id, sender)
            .await;
    }

    async fn pop_handle_waiter(
        &self,
        child_id: &str,
    ) -> Option<oneshot::Sender<HandleReply>> {
        self.runtime.pop_handle_waiter(child_id).await
    }
}
