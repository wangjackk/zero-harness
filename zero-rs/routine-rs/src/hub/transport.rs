use crate::{
    core::{HandleReply, ReqReply, RoutineIo, CreateReply},
    grpc,
    protocol::{
        events::{
            LIFECYCLE_CREATED, LIFECYCLE_HEARTBEAT_ACK,
            LIFECYCLE_STARTED, LIFECYCLE_STOP, LIFECYCLE_STOPPED,
        },
        ControlDoneReason, JsonObject, RawWireEvent,
    },
    hub::ServerRuntime,
};
use async_trait::async_trait;
use serde_json::Value;
use std::{sync::Arc, time::Duration};
use tokio::sync::{oneshot, RwLock};
use tonic::transport::Channel;

pub struct OutboundTransport {
    runtime: Arc<ServerRuntime>,
    /// kernel 的 grpc channel 槽(dial-in 模式由 GrpcClient attach/detach):
    /// routine 查询(get_routines 等)复用它发 unary Req,对齐 py/TS 的
    /// transport.req 复用已连 channel.
    kernel_channel: RwLock<Option<Channel>>,
}

impl OutboundTransport {
    pub fn new(runtime: Arc<ServerRuntime>) -> Self {
        Self {
            runtime,
            kernel_channel: RwLock::new(None),
        }
    }

    pub async fn attach_kernel_channel(&self, channel: Channel) {
        *self.kernel_channel.write().await = Some(channel);
    }

    pub async fn detach_kernel_channel(&self) {
        *self.kernel_channel.write().await = None;
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

    async fn kernel_req(
        &self,
        event: &str,
        timeout_seconds: u64,
    ) -> Result<JsonObject, String> {
        let channel = self
            .kernel_channel
            .read()
            .await
            .clone()
            .ok_or_else(|| "kernel channel not attached".to_string())?;
        let mut client = grpc::routine::routine_service_client::RoutineServiceClient::new(channel);
        let mut payload = JsonObject::new();
        payload.insert("event".to_string(), Value::String(event.to_string()));
        let frame = grpc::json_to_frame(&payload);
        let reply = tokio::time::timeout(
            Duration::from_secs(timeout_seconds),
            client.req(frame),
        )
        .await
        .map_err(|_| format!("kernel req '{event}' timed out"))?
        .map_err(|e| format!("kernel req '{event}' failed: {e}"))?;
        Ok(grpc::frame_to_json(&reply.into_inner()))
    }

    async fn request_stop(&self, id: &str, peer_id: Option<&str>) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(LIFECYCLE_STOP).with_field("id", Value::String(id.to_string())),
            peer_id,
        )
        .await
    }

    async fn register_create_future(
        &self,
        req_id: String,
        sender: oneshot::Sender<CreateReply>,
    ) {
        // CreateReply 和 CreateWaiter 内部类型一致 (Result<(String, Option<Vec<String>>), String>),
        // 直接转发到 runtime 的 future 表.
        self.runtime
            .register_create_future(&req_id, sender)
            .await;
    }

    async fn pop_create_future(
        &self,
        req_id: &str,
    ) -> Option<oneshot::Sender<CreateReply>> {
        self.runtime.pop_create_future(req_id).await
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

    async fn register_req_future(&self, req_id: String, sender: oneshot::Sender<ReqReply>) {
        self.runtime.register_req_future(&req_id, sender).await;
    }

    async fn pop_req_future(&self, req_id: &str) -> Option<oneshot::Sender<ReqReply>> {
        self.runtime.pop_req_future(req_id).await
    }
}
