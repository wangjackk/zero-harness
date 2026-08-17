pub mod client;
pub mod routine {
    tonic::include_proto!("routine");
}

use crate::{
    protocol::{json_to_payload, payload_to_json, JsonObject},
    server::{BusinessEventRouter, LifecycleManager, QueryService, ServerRuntime},
};
use futures::Stream;
use routine::routine_service_server::{RoutineService, RoutineServiceServer};
use std::{pin::Pin, sync::Arc};
use tokio::sync::mpsc;
use tokio_stream::{wrappers::UnboundedReceiverStream, StreamExt};
use tonic::{Request, Response, Status};

/// 边界编码:JsonObject → Frame(payload=JSON 文本)。
pub fn json_to_frame(data: &JsonObject) -> routine::Frame {
    routine::Frame {
        payload: json_to_payload(data),
    }
}

/// 边界解码:Frame → JsonObject。格式不对 panic 暴露协议破坏(见 protocol::frame)。
pub fn frame_to_json(frame: &routine::Frame) -> JsonObject {
    payload_to_json(&frame.payload)
}

#[derive(Clone)]
pub struct GrpcRoutineService {
    runtime: Arc<ServerRuntime>,
    business: Arc<BusinessEventRouter>,
    lifecycle: Arc<LifecycleManager>,
    query: Arc<QueryService>,
}

impl GrpcRoutineService {
    pub fn new(
        runtime: Arc<ServerRuntime>,
        business: Arc<BusinessEventRouter>,
        lifecycle: Arc<LifecycleManager>,
        query: Arc<QueryService>,
    ) -> Self {
        Self {
            runtime,
            business,
            lifecycle,
            query,
        }
    }

    pub fn into_tonic_service(self) -> RoutineServiceServer<Self> {
        RoutineServiceServer::new(self)
    }
}

#[tonic::async_trait]
impl RoutineService for GrpcRoutineService {
    type StreamStream = Pin<Box<dyn Stream<Item = Result<routine::Frame, Status>> + Send + Sync + 'static>>;

    async fn stream(
        &self,
        request: Request<tonic::Streaming<routine::Frame>>,
    ) -> Result<Response<Self::StreamStream>, Status> {
        let peer_id = request
            .remote_addr()
            .map(|addr| addr.to_string())
            .unwrap_or_else(|| "unknown".to_string());
        tracing::info!("[Stream] connected: {peer_id}");
        let mut inbound = request.into_inner();
        let (sender, receiver) = mpsc::unbounded_channel::<routine::Frame>();
        self.runtime
            .peer_to_queue
            .lock()
            .await
            .insert(peer_id.clone(), sender);

        let runtime = self.runtime.clone();
        let business = self.business.clone();
        let lifecycle = self.lifecycle.clone();
        let peer_for_inbound = peer_id.clone();
        tokio::spawn(async move {
            while let Some(item) = inbound.next().await {
                match item {
                    Ok(message) => {
                        let msg = frame_to_json(&message);
                        business.safe_route_stream(&peer_for_inbound, msg).await;
                    }
                    Err(error) => {
                        tracing::warn!("[Stream] error: {peer_for_inbound}: {error}");
                        break;
                    }
                }
            }
            runtime.peer_to_queue.lock().await.remove(&peer_for_inbound);
            let prefix = format!("{peer_for_inbound}:");
            let prids = runtime
                .running_instances
                .lock()
                .await
                .keys()
                .filter(|key| key.starts_with(&prefix))
                .cloned()
                .collect::<Vec<_>>();
            for prid in prids {
                let _ = lifecycle.stop_instance(&prid).await;
            }
            tracing::info!("[Stream] disconnected: {peer_for_inbound}");
        });

        let stream = UnboundedReceiverStream::new(receiver).map(Ok);
        Ok(Response::new(Box::pin(stream) as Self::StreamStream))
    }

    async fn req(&self, request: Request<routine::Frame>) -> Result<Response<routine::Frame>, Status> {
        Ok(Response::new(
            self.query.handle_req(request.into_inner()).await,
        ))
    }
}
