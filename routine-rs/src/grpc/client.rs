//! gRPC dial-in client:routine 主动连接 kernel,走 bidi Stream + 退避重连。
//!
//! 对齐 Python `routine/routine/grpc_client.py` 的 `GrpcClientTransport`。
//! 连接成功后:注册出站通道 → push catalog → recv loop 分发生命周期事件。

use super::routine::routine_service_client::RoutineServiceClient;
use crate::{
    core::RoutineIo,
    protocol::{events::*, struct_to_json, JsonObject, RawWireEvent},
    server::{LifecycleManager, QueryService, ServerRuntime},
};
use prost_types::Struct;
use serde_json::Value;
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::sync::mpsc;
use tokio_stream::{wrappers::UnboundedReceiverStream, StreamExt};
use tonic::transport::Endpoint;

/// dial-in 下 kernel 的固定 peer_id(出站事件全发给它)。
const PEER_ID: &str = "kernel";

const BACKOFF_INITIAL: Duration = Duration::from_millis(200);
const BACKOFF_MAX: Duration = Duration::from_secs(5);
const BACKOFF_FACTOR: f32 = 1.5;
const STABLE_SECONDS: f32 = 2.0;

/// catalog.push 的 req_id 计数器(对齐 Python `_catalog_req_counter`,格式 `cat{N}`)。
static CATALOG_REQ_COUNTER: AtomicU64 = AtomicU64::new(0);

pub struct GrpcClient {
    address: String,
    hub_id: String,
    runtime: Arc<ServerRuntime>,
    lifecycle: Arc<LifecycleManager>,
    query: Arc<QueryService>,
    transport: Arc<crate::server::OutboundTransport>,
    stopped: Arc<AtomicBool>,
}

pub struct GrpcClientOptions {
    pub address: String,
    pub hub_id: String,
}

impl GrpcClient {
    pub fn new(
        runtime: Arc<ServerRuntime>,
        lifecycle: Arc<LifecycleManager>,
        query: Arc<QueryService>,
        transport: Arc<crate::server::OutboundTransport>,
        options: GrpcClientOptions,
    ) -> Self {
        Self {
            address: options.address,
            hub_id: options.hub_id,
            runtime,
            lifecycle,
            query,
            transport,
            stopped: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn stop(&self) {
        self.stopped.store(true, Ordering::SeqCst);
    }

    /// 外层重连主循环:connect → post_connect → recv_loop → 断后退避重试。
    pub async fn run(&self) {
        let mut backoff = BACKOFF_INITIAL;
        while !self.stopped.load(Ordering::SeqCst) {
            let connected_at = tokio::time::Instant::now();
            match self.connect_once().await {
                Ok(()) => tracing::info!("[client] stream closed cleanly"),
                Err(e) => tracing::warn!("[client] 连接异常: {e}"),
            }
            if self.stopped.load(Ordering::SeqCst) {
                break;
            }
            // peer down:强制清理该 peer 名下所有 running instance
            let prefix = format!("{PEER_ID}:");
            let prids: Vec<String> = {
                let running_instances = self.runtime.running_instances.lock().await;
                running_instances
                    .keys()
                    .filter(|k| k.starts_with(&prefix))
                    .cloned()
                    .collect()
            };
            for prid in prids {
                let _ = self.lifecycle.stop_instance(&prid).await;
            }
            if connected_at.elapsed().as_secs_f32() >= STABLE_SECONDS {
                backoff = BACKOFF_INITIAL;
            }
            tracing::info!("[client] 🔄 {:.1}s 后重连...", backoff.as_secs_f32());
            tokio::time::sleep(backoff).await;
            backoff = Duration::from_secs_f32(
                (backoff.as_secs_f32() * BACKOFF_FACTOR).min(BACKOFF_MAX.as_secs_f32()),
            );
        }
    }

    /// 单次连接:建 channel → spawn stream future → 注册出站 → 发 catalog.push → 等 HEADERS → recv loop。
    ///
    /// 关键:tonic 的 `client.stream(outbound).await` 会等 server 的 response HEADERS,
    /// 但 Go kernel 的 Stream handler 不主动发 HEADERS(只在首次 send msg 时带)。
    /// kernel 只有收到 catalog.push 后才回 lifecycle.created(首条带 HEADERS 的 msg)。
    /// 所以必须先 spawn stream future(让 tonic 并发 drive outbound + 等 HEADERS),
    /// 再发 catalog.push 触发 kernel 回消息。先 await 再发会死锁。
    async fn connect_once(&self) -> Result<(), String> {
        eprintln!("[client] connecting to {}...", self.address);
        let endpoint = Endpoint::from_shared(format!("http://{}", self.address))
            .map_err(|e| e.to_string())?
            .connect_timeout(Duration::from_secs(5))
            .tcp_nodelay(true);
        let channel = endpoint.connect().await.map_err(|e| {
            eprintln!("[client] connect failed: {e}");
            e.to_string()
        })?;
        eprintln!("[client] channel ready");
        let mut client = RoutineServiceClient::new(channel);

        let (tx, rx) = mpsc::unbounded_channel::<Struct>();
        let outbound = UnboundedReceiverStream::new(rx);

        // 注册出站通道(spawn 前注册,确保 send_catalog_push 能发到 channel)
        self.runtime
            .peer_to_queue
            .lock()
            .await
            .insert(PEER_ID.to_string(), tx);

        // spawn stream future:tonic 并发 drive outbound stream + 等 response HEADERS
        // 用 async move 把 client 移入 task(stream() 是 &mut self,需 'static)
        let stream_handle = tokio::spawn(async move {
            client.stream(outbound).await
        });

        // 发 catalog.push(进 channel → tonic drive outbound → kernel 收到 → 回 lifecycle.created 带 HEADERS)
        self.send_catalog_push().await;
        eprintln!("[client] catalog.push sent, waiting for stream ready...");

        // 等 stream 建立(kernel 处理 catalog.push 后回消息,带 HEADERS,stream future resolve)
        let response = tokio::time::timeout(Duration::from_secs(10), stream_handle)
            .await
            .map_err(|e| {
                eprintln!("[client] stream open TIMEOUT after 10s: {e}");
                format!("stream open timeout: {e}")
            })?
            .map_err(|e| {
                eprintln!("[client] join error: {e}");
                e.to_string()
            })?
            .map_err(|e| {
                eprintln!("[client] stream open failed: {e}");
                e.to_string()
            })?;
        let mut inbound = response.into_inner();
        eprintln!("[client] 🔗 stream opened to kernel: {}", self.address);

        // recv loop:阻塞直到 stream 断
        while let Some(item) = inbound.next().await {
            match item {
                Ok(msg) => {
                    let json_msg = struct_to_json(&msg);
                    self.dispatch_inbound(json_msg).await;
                }
                Err(e) => {
                    tracing::warn!("[client] recv error: {e}");
                    break;
                }
            }
        }

        // 清理出站通道
        self.runtime
            .peer_to_queue
            .lock()
            .await
            .remove(PEER_ID);
        tracing::info!("[client] stream disconnected");
        Ok(())
    }

    /// 连接成功后主动 push catalog(routines + modules + hub_id)。
    ///
    /// 带 req_id 触发 kernel 回 `catalog.pushed`:{registered, skipped}。
    /// 关键:这条回执是 kernel 首条 `stream.Send`,携带 HTTP/2 HEADERS,
    /// 让 client 端 `client.stream(outbound).await` resolve(否则 kernel 不主动
    /// 发 HEADERS,client 死等)。对齐 Python `send_catalog_push` 的 req_id 机制。
    async fn send_catalog_push(&self) {
        let routines = self.query.build_routines();
        let modules: Vec<Value> = self
            .runtime
            .modules
            .iter()
            .map(|m| Value::String(m.module_id().to_string()))
            .collect();
        let req_id = format!("cat{}", CATALOG_REQ_COUNTER.fetch_add(1, Ordering::SeqCst));
        let payload = RawWireEvent::new(CATALOG_PUSH)
            .with_field("req_id", Value::String(req_id.clone()))
            .with_field("routines", Value::Array(routines))
            .with_field("modules", Value::Array(modules))
            .with_field("hub_id", Value::String(self.hub_id.clone()));
        if let Err(e) = self
            .transport
            .send_raw_event(payload, Some(PEER_ID))
            .await
        {
            tracing::warn!("[client] catalog.push failed: {e}");
        } else {
            tracing::info!(
                "[client] 📦 catalog.push sent (req_id={}, {} routines, hub_id={})",
                req_id,
                self.runtime.routines.get_routines().len(),
                self.hub_id
            );
        }
    }

    /// 入站事件分发:按 event 字段路由到 lifecycle/query。
    async fn dispatch_inbound(&self, msg: JsonObject) {
        let event = msg.get("event").and_then(Value::as_str).unwrap_or("");
        match event {
            LIFECYCLE_CREATED => {
                if let Err(e) = self.lifecycle.handle_created(PEER_ID, msg).await {
                    tracing::warn!("[client] handle_created error: {e}");
                }
            }
            LIFECYCLE_START => {
                if let Err(e) = self.lifecycle.handle_start(PEER_ID, msg).await {
                    tracing::warn!("[client] handle_start error: {e}");
                }
            }
            LIFECYCLE_STOP => {
                if let Err(e) = self.lifecycle.handle_stop(PEER_ID, msg).await {
                    tracing::warn!("[client] handle_stop error: {e}");
                }
            }
            LIFECYCLE_DESTROY => {
                if let Err(e) = self.lifecycle.handle_destroy(PEER_ID, msg).await {
                    tracing::warn!("[client] handle_destroy error: {e}");
                }
            }
            MODULE_TREE => {
                tracing::info!("[client] 🌳 module.tree received");
            }
            CATALOG_PUSHED => {
                let req_id = msg.get("req_id").and_then(Value::as_str).unwrap_or("");
                let registered = msg
                    .get("registered")
                    .and_then(Value::as_array)
                    .map(|v| v.len())
                    .unwrap_or(0);
                let skipped: Vec<String> = msg
                    .get("skipped")
                    .and_then(Value::as_array)
                    .map(|v| {
                        v.iter()
                            .filter_map(|s| s.as_str().map(String::from))
                            .collect()
                    })
                    .unwrap_or_default();
                if skipped.is_empty() {
                    tracing::info!(
                        "[client] ✅ catalog.pushed (req_id={}): registered={registered}",
                        req_id
                    );
                } else {
                    tracing::warn!(
                        "[client] ⚠️ catalog.pushed (req_id={}): registered={}, skipped={:?}",
                        req_id,
                        registered,
                        skipped
                    );
                }
            }
            _ => {
                tracing::debug!("[client] unhandled event: {event}");
            }
        }
    }
}
