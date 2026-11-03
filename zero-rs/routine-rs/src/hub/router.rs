use crate::{
    core::{messaging::RoutineSource, RoutineIo},
    protocol::{
        envelope::{
            ENVELOPE_DATA, ENVELOPE_ERROR, ENVELOPE_EVENT, ENVELOPE_OK, ENVELOPE_REPLY_TO,
            ENVELOPE_REQ_ID,
        },
        events::{
            CATALOG_PUSHED, LIFECYCLE_HEARTBEAT, LIFECYCLE_START, LIFECYCLE_STARTED,
            LIFECYCLE_STOP, LIFECYCLE_STOPPED, MESSAGE_DELIVERED, MESSAGE_REQ_DELIVERED,
            MESSAGE_REQ_REPLY_DELIVERED, PUBSUB_DELIVERED, ROUTINE_CREATED,
            ROUTINE_REJECTED, ROUTINE_YIELDED,
        },
        JsonObject, RawWireEvent, TerminationReason,
    },
    hub::{transport::OutboundTransport, LifecycleManager, ServerRuntime},
};
use futures::FutureExt;
use serde_json::Value;
use std::sync::Arc;

pub struct BusinessEventRouter {
    runtime: Arc<ServerRuntime>,
    transport: Arc<OutboundTransport>,
    lifecycle: Arc<LifecycleManager>,
}

impl BusinessEventRouter {
    pub fn new(
        runtime: Arc<ServerRuntime>,
        transport: Arc<OutboundTransport>,
        lifecycle: Arc<LifecycleManager>,
    ) -> Self {
        Self {
            runtime,
            transport,
            lifecycle,
        }
    }

    pub async fn safe_route_stream(&self, peer_id: &str, msg: JsonObject) {
        if let Err(error) = self.route_stream(peer_id, msg).await {
            tracing::warn!("stream handler error: {error}");
        }
    }

    pub async fn route_stream(&self, peer_id: &str, msg: JsonObject) -> Result<(), String> {
        let event = msg
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if event.is_empty() {
            tracing::warn!("missing event field in stream message");
            return Ok(());
        }
        match event.as_str() {
            LIFECYCLE_START => self.lifecycle.handle_start(peer_id, msg).await?,
            LIFECYCLE_STOP => self.lifecycle.handle_stop(peer_id, msg).await?,
            LIFECYCLE_HEARTBEAT => self.lifecycle.handle_heartbeat(peer_id, msg).await?,
            // 父 routine 调子 routine 的回执路径:
            // - routine.created: kernel 回 create 的回执,带 child_id + modules
            // - lifecycle.stopped: 子 routine 完成后 kernel 中转回父 server,
            //   带 result/error. 父 call/wait 据此唤醒.
            //   注意:跟 lifecycle.stop(kernel 主动停本 server 的 routine)不同,
            //   stopped 是子完成通知,本 server 不持有子 instance,只唤醒 waiter.
            ROUTINE_CREATED => self.handle_routine_created(msg).await,
            LIFECYCLE_STARTED => self.handle_lifecycle_started(msg).await,
            LIFECYCLE_STOPPED => self.handle_lifecycle_stopped(msg).await,
            ROUTINE_REJECTED => self.handle_routine_rejected(msg).await,
            ROUTINE_YIELDED => self.handle_routine_yielded(msg).await,
            CATALOG_PUSHED => {
                // catalog.push 全量 push 的回执(registered/skipped)。
                // skipped = kernel 拒绝的同名 routine(先到先得,不区分 conn),
                // 必须告警让用户看见,否则同名静默失效难以排查。
                let registered = msg
                    .get("registered")
                    .and_then(Value::as_array)
                    .map(|items| items.len())
                    .unwrap_or(0);
                let skipped: Vec<String> = msg
                    .get("skipped")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|v| v.as_str().map(str::to_string))
                            .collect()
                    })
                    .unwrap_or_default();
                let req_id = msg.get("req_id").and_then(Value::as_str).unwrap_or("");
                if skipped.is_empty() {
                    tracing::info!(
                        "[catalog] pushed (req_id={}): registered={registered}, skipped=0",
                        req_id,
                    );
                } else {
                    tracing::warn!(
                        "[catalog] pushed (req_id={}): registered={}, skipped={} {:?}",
                        req_id,
                        registered,
                        skipped.len(),
                        skipped,
                    );
                }
            }
            _ => self.dispatch_business_event(peer_id, msg).await?,
        }
        Ok(())
    }

    /// 处理 routine.created 回执:唤醒 create future.
    /// wire 字段: req_id, child_id, modules?, error?
    async fn handle_routine_created(&self, msg: JsonObject) {
        let req_id = msg
            .get("req_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if req_id.is_empty() {
            tracing::warn!("routine.created missing req_id");
            return;
        }
        let Some(sender) = self.runtime.pop_create_future(&req_id).await else {
            tracing::warn!("routine.created no waiter for req_id={req_id}");
            return;
        };
        if let Some(err) = msg.get("error").and_then(Value::as_str) {
            let _ = sender.send(Err(err.to_string()));
            return;
        }
        let child_id = msg
            .get("child_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let modules = msg.get("modules").and_then(Value::as_array).map(|arr| {
            arr.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect::<Vec<_>>()
        });
        let _ = sender.send(Ok((child_id, modules)));
    }

    async fn handle_lifecycle_started(&self, msg: JsonObject) {
        let Some(handle) = self.handle_for_message(&msg).await else {
            return;
        };
        handle.notify_started();
        handle.resolve_ack(Ok(()));
    }

    /// 子完成通知：先填共享状态并唤醒所有等待者，再解控制 ACK、摘注册项。
    async fn handle_lifecycle_stopped(&self, msg: JsonObject) {
        let Some(handle) = self.handle_for_message(&msg).await else {
            return;
        };
        let reason = msg
            .get("reason")
            .and_then(Value::as_str)
            .map(TerminationReason::from_wire)
            .unwrap_or(TerminationReason::Unknown);
        let mut error = error_text(msg.get("error"));
        if error.is_none() && reason == TerminationReason::Failed {
            error = Some(format!("routine stopped with reason={}", reason.as_wire()));
        }
        let by = msg
            .get("by")
            .and_then(Value::as_str)
            .map(ToString::to_string);
        let detail = msg
            .get("detail")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        handle.notify_done(
            msg.get("result").cloned().unwrap_or(Value::Null),
            error,
            reason,
            by,
            detail,
        );
        handle.resolve_ack(Ok(()));
        self.runtime.pop_handle(handle.id()).await;
    }

    /// start 的 Err 是预期控制结果；try_start 保留 handle，普通 start 失效。
    /// stop/destroy 的拒绝同样解 ACK，避免调用方等到超时。
    async fn handle_routine_rejected(&self, msg: JsonObject) {
        let child_id = msg
            .get("child_id")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let Some(handle) = self.runtime.get_handle(child_id).await else {
            return;
        };
        let error = error_text(msg.get("error")).unwrap_or_else(|| "rejected".to_string());
        let op = msg.get("op").and_then(Value::as_str).unwrap_or_default();
        handle.resolve_ack(Err(error.clone()));
        if op == "start" && !msg.get("try").and_then(Value::as_bool).unwrap_or(false) {
            handle.invalidate(error);
            self.runtime.pop_handle(child_id).await;
        }
    }

    async fn handle_routine_yielded(&self, msg: JsonObject) {
        let Some(handle) = self.handle_for_message(&msg).await else {
            return;
        };
        let is_final = msg
            .get("is_final")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        handle.on_yield_chunk(
            msg.get("data").cloned().unwrap_or(Value::Null),
            is_final,
            error_text(msg.get("error")),
        );
    }

    async fn handle_for_message(&self, msg: &JsonObject) -> Option<crate::core::RoutineHandle> {
        let child_id = msg.get("id").and_then(Value::as_str).unwrap_or_default();
        if child_id.is_empty() {
            return None;
        }
        self.runtime.get_handle(child_id).await
    }

    // ------------------------------------------------------------------
    // 三通道入站投递(对齐 Python hub._on_*_delivered 定型语义):
    // - message.delivered → 实例 mailbox 串行(on_message 严格保序)
    // - message.req_delivered → 立即 spawn(HTTP-like 事务并行)
    // - pubsub.delivered → 每帧每订阅者立即 spawn(离散通知)
    // ------------------------------------------------------------------

    async fn dispatch_business_event(
        &self,
        peer_id: &str,
        msg: JsonObject,
    ) -> Result<(), String> {
        let event = msg
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        match event.as_str() {
            MESSAGE_DELIVERED => self.on_message_delivered(peer_id, &msg).await,
            MESSAGE_REQ_DELIVERED => self.on_message_req_delivered(peer_id, &msg).await,
            MESSAGE_REQ_REPLY_DELIVERED => self.on_message_req_reply_delivered(&msg).await,
            PUBSUB_DELIVERED => self.on_pubsub_delivered(peer_id, &msg).await,
            _ => {
                tracing::debug!("unhandled business event: {event}");
                Ok(())
            }
        }
    }

    /// message.delivered{target_id, data, source}:纯定向消息 → 实例
    /// mailbox 串行投递。created 后即可收;无 mailbox(未 created/已销毁)丢弃。
    async fn on_message_delivered(&self, peer_id: &str, msg: &JsonObject) -> Result<(), String> {
        let target_id = msg.get("target_id").and_then(Value::as_str).unwrap_or("");
        let prid = format!("{peer_id}:{target_id}");
        if self.runtime.resolve_created(peer_id, target_id).await.is_none() {
            return Ok(());
        }
        let source = self.resolve_source(peer_id, msg).await;
        let data = msg.get("data").cloned().unwrap_or(Value::Null);
        self.runtime.deliver_mail(&prid, source, data).await;
        Ok(())
    }

    /// message.req_delivered:req 到达 provider → 立即 spawn 处理。独立
    /// 事务(req_id 配对回执),相互并行----HTTP 语义,慢 handler 不堵
    /// 同实例后续 req。
    async fn on_message_req_delivered(&self, peer_id: &str, msg: &JsonObject) -> Result<(), String> {
        let target_id = msg.get("target_id").and_then(Value::as_str).unwrap_or("");
        let Some((_instance, request_handlers)) =
            self.runtime.resolve_created(peer_id, target_id).await
        else {
            return Ok(());
        };
        let source = self.resolve_source(peer_id, msg).await;
        let envelope = msg
            .get("data")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let transport = self.transport.clone();
        let provider_rid = target_id.to_string();
        tokio::spawn(async move {
            if let Err(error) =
                Self::serve_request(&transport, &provider_rid, request_handlers, envelope, source)
                    .await
            {
                tracing::warn!("[req] serve failed for rid={provider_rid}: {error}");
            }
        });
        Ok(())
    }

    /// 按 envelope 的 event 路由到 @request handler,回执经 message.req_reply
    /// 发回 reply_to。对齐 Python `Routine._serve_request`。
    async fn serve_request(
        transport: &Arc<OutboundTransport>,
        provider_rid: &str,
        request_handlers: std::collections::HashMap<String, crate::core::messaging::RequestHandler>,
        envelope: JsonObject,
        source: RoutineSource,
    ) -> Result<(), String> {
        let event = envelope
            .get(ENVELOPE_EVENT)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let req_id = envelope
            .get(ENVELOPE_REQ_ID)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let reply_to = envelope
            .get(ENVELOPE_REPLY_TO)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let payload = envelope
            .get(ENVELOPE_DATA)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();

        let handler = request_handlers.get(&event).cloned();
        let mut reply = JsonObject::new();
        reply.insert(ENVELOPE_REQ_ID.to_string(), Value::String(req_id));
        match handler {
            None => {
                tracing::warn!("[req] no @request handler for event={event} (rid={provider_rid})");
                reply.insert(ENVELOPE_OK.to_string(), Value::Bool(false));
                reply.insert(
                    ENVELOPE_ERROR.to_string(),
                    Value::String(format!("no @request handler for {event:?}")),
                );
            }
            Some(handler) => {
                // 源实例销毁后 in-flight req 随实例终结:provider 侧仍持有
                // request_handlers 的 Arc clone,但实例在投递前已判活。
                match handler(source, payload).await {
                    Ok(result) => {
                        reply.insert(ENVELOPE_OK.to_string(), Value::Bool(true));
                        reply.insert(ENVELOPE_DATA.to_string(), result);
                    }
                    Err(error) => {
                        tracing::warn!("[req] handler failed for event={event}: {error}");
                        reply.insert(ENVELOPE_OK.to_string(), Value::Bool(false));
                        reply.insert(ENVELOPE_ERROR.to_string(), Value::String(error.to_string()));
                    }
                }
            }
        }
        let wire = RawWireEvent::new(crate::protocol::events::MESSAGE_REQ_REPLY)
            .with_field(
                "target_ids",
                Value::Array(vec![Value::String(reply_to)]),
            )
            .with_data(&Value::Object(reply))
            .with_field("source_id", Value::String(provider_rid.to_string()));
        transport.send_raw_event(wire, None).await
    }

    /// message.req_reply_delivered:req 回执到达 caller,resolve req waiter。
    async fn on_message_req_reply_delivered(&self, msg: &JsonObject) -> Result<(), String> {
        let Some(data) = msg.get("data").and_then(Value::as_object) else {
            return Ok(());
        };
        let req_id = data
            .get(ENVELOPE_REQ_ID)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let Some(sender) = self.runtime.pop_req_future(&req_id).await else {
            return Ok(());
        };
        if data.get(ENVELOPE_OK).and_then(Value::as_bool).unwrap_or(false) {
            let _ = sender.send(Ok(data.get(ENVELOPE_DATA).cloned().unwrap_or(Value::Null)));
        } else {
            let error = data
                .get(ENVELOPE_ERROR)
                .and_then(Value::as_str)
                .unwrap_or("req failed");
            let _ = sender.send(Err(error.to_string()));
        }
        Ok(())
    }

    /// kernel fanout 回来的 pubsub.delivered(已按 hub 分组,一条含
    /// subscriber_ids 列表)。逐个 subscriber 按 (namespace, topic) 找本地
    /// handler,每帧每订阅者立即 spawn(离散通知,不进 mailbox)。
    async fn on_pubsub_delivered(&self, peer_id: &str, msg: &JsonObject) -> Result<(), String> {
        let topic = msg.get("topic").and_then(Value::as_str).unwrap_or("");
        let namespace = msg.get("namespace").and_then(Value::as_str).unwrap_or("");
        let data = msg.get("data").cloned().unwrap_or(Value::Null);
        let source = self.resolve_source(peer_id, msg).await;
        let subscriber_ids = msg
            .get("subscriber_ids")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(ToString::to_string)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        for rid in subscriber_ids {
            let prid = format!("{peer_id}:{rid}");
            let Some(handler) = self
                .runtime
                .subscriber_handler(&prid, namespace, topic)
                .await
            else {
                continue;
            };
            let source = source.clone();
            let data = data.clone();
            tokio::spawn(async move {
                if let Err(error) = handler(source, data).boxed().await {
                    tracing::warn!("[pubsub] subscriber handler failed: {error}");
                }
            });
        }
        Ok(())
    }

    /// 从 delivered 消息的 source 字段构造 RoutineSource(补 name:source 在
    /// 本 hub 则能查到)。
    async fn resolve_source(&self, peer_id: &str, msg: &JsonObject) -> RoutineSource {
        let source_id = msg
            .get("source")
            .and_then(Value::as_object)
            .and_then(|src| src.get("id"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        self.runtime.resolve_source(peer_id, source_id).await
    }
}

fn error_text(value: Option<&Value>) -> Option<String> {
    match value {
        None | Some(Value::Null) => None,
        Some(Value::String(text)) if text.is_empty() => None,
        Some(Value::String(text)) => Some(text.clone()),
        Some(Value::Object(object)) => object
            .get("msg")
            .or_else(|| object.get("message"))
            .and_then(Value::as_str)
            .map(ToString::to_string)
            .or_else(|| Some(Value::Object(object.clone()).to_string())),
        Some(other) => Some(other.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        core::{
            messaging::{request_spec, subscribe_spec},
            RoutineHandle, RoutineInfo, RoutineOutput, RoutineRegistry, RunContext,
            RunContextOptions,
        },
        protocol::{events::LIFECYCLE_CREATED, JsonValue},
    };
    use async_trait::async_trait;
    use futures::FutureExt;
    use serde_json::json;
    use std::sync::{Arc, Mutex};
    use tokio::sync::mpsc;
    use tokio_util::sync::CancellationToken;

    /// 测试用 routine:on_message 记录顺序、@request 带延迟、@subscribe 记录事件。
    #[derive(Default)]
    struct ProbeRoutine {
        events: Arc<Mutex<Vec<String>>>,
    }

    #[async_trait]
    impl crate::core::Routine for ProbeRoutine {
        async fn run(
            &self,
            _ctx: crate::core::RunContext,
            _params: JsonObject,
        ) -> Result<RoutineOutput, crate::core::routine::RoutineError> {
            Ok(RoutineOutput::Empty)
        }

        async fn on_message(
            &self,
            _source: RoutineSource,
            data: JsonValue,
        ) -> Result<(), crate::core::routine::RoutineError> {
            let text = data.as_str().unwrap_or_default().to_string();
            self.events.lock().unwrap().push(format!("msg:{text}"));
            Ok(())
        }

        fn request_specs(&self) -> Vec<crate::core::messaging::RequestSpec> {
            let events = self.events.clone();
            vec![request_spec("probe", move |_source, data| {
                let events = events.clone();
                async move {
                    let delay = data
                        .get("delay_ms")
                        .and_then(serde_json::Value::as_u64)
                        .unwrap_or(0);
                    events.lock().unwrap().push("req:start".into());
                    tokio::time::sleep(std::time::Duration::from_millis(delay)).await;
                    events.lock().unwrap().push("req:end".into());
                    Ok(serde_json::json!({"ok": true}))
                }
                .boxed()
            })]
        }

        fn subscribe_specs(&self) -> Vec<crate::core::messaging::SubscribeSpec> {
            let events = self.events.clone();
            vec![subscribe_spec(
                "probe.topic",
                "",
                move |_source, data| {
                    let events = events.clone();
                    async move {
                        let text = data.as_str().unwrap_or_default().to_string();
                        events.lock().unwrap().push(format!("pub:{text}"));
                        Ok(())
                    }
                    .boxed()
                },
            )]
        }
    }

    impl RoutineInfo for ProbeRoutine {
        const NAME: &'static str = "probe";
    }

    struct ProbeFactory {
        events: Arc<Mutex<Vec<String>>>,
    }

    impl crate::core::RoutineFactory for ProbeFactory {
        fn routine_name(&self) -> &str {
            "probe"
        }

        fn create(&self) -> Box<dyn crate::core::Routine> {
            Box::new(ProbeRoutine {
                events: self.events.clone(),
            })
        }
    }

    async fn setup() -> (
        Arc<ServerRuntime>,
        Arc<OutboundTransport>,
        Arc<LifecycleManager>,
        Arc<BusinessEventRouter>,
        Arc<Mutex<Vec<String>>>,
        mpsc::UnboundedReceiver<crate::grpc::routine::Frame>,
    ) {
        let events = Arc::new(Mutex::new(Vec::new()));
        let registry = RoutineRegistry::from_factories([Arc::new(ProbeFactory {
            events: events.clone(),
        }) as Arc<dyn crate::core::RoutineFactory>]);
        let runtime = Arc::new(ServerRuntime::new(registry, Vec::new()));
        let transport = Arc::new(OutboundTransport::new(runtime.clone()));
        let lifecycle = Arc::new(LifecycleManager::new(runtime.clone(), transport.clone()));
        let router = Arc::new(BusinessEventRouter::new(
            runtime.clone(),
            transport.clone(),
            lifecycle.clone(),
        ));
        let (tx, rx) = mpsc::unbounded_channel();
        runtime
            .peer_to_queue
            .lock()
            .await
            .insert("peer".to_string(), tx);
        (runtime, transport, lifecycle, router, events, rx)
    }

    fn created_msg() -> JsonObject {
        let mut msg = JsonObject::new();
        msg.insert("event".to_string(), Value::String(LIFECYCLE_CREATED.to_string()));
        msg.insert("id".to_string(), Value::String("rid".to_string()));
        msg.insert("name".to_string(), Value::String("probe".to_string()));
        msg.insert("kwargs".to_string(), Value::Object(JsonObject::new()));
        msg
    }

    fn parent_context(transport: &Arc<OutboundTransport>) -> RunContext {
        RunContext::new(RunContextOptions {
            id: "parent".to_string(),
            name: "parent".to_string(),
            peer_id: "peer".to_string(),
            io: transport.clone(),
            control_type: None,
            push_parent: None,
            token_parent: None,
            available_routines: None,
            cancellation: CancellationToken::new(),
        })
    }

    async fn recv_event(
        rx: &mut mpsc::UnboundedReceiver<crate::grpc::routine::Frame>,
    ) -> JsonObject {
        let frame = tokio::time::timeout(std::time::Duration::from_secs(1), rx.recv())
            .await
            .expect("wire event should arrive")
            .expect("wire channel should stay open");
        crate::grpc::frame_to_json(&frame)
    }

    fn handle_msg(event: &str, child_id: &str) -> JsonObject {
        let mut msg = JsonObject::new();
        msg.insert("event".to_string(), Value::String(event.to_string()));
        msg.insert("id".to_string(), Value::String(child_id.to_string()));
        msg
    }

    async fn create_handle(
        ctx: RunContext,
        router: &Arc<BusinessEventRouter>,
        rx: &mut mpsc::UnboundedReceiver<crate::grpc::routine::Frame>,
        child_id: &str,
    ) -> RoutineHandle {
        let creating = tokio::spawn(async move { ctx.create("probe", None).await });
        let request = recv_event(rx).await;
        assert_eq!(
            request.get("event").and_then(Value::as_str),
            Some(crate::protocol::events::ROUTINE_CREATE)
        );
        let req_id = request
            .get("req_id")
            .and_then(Value::as_str)
            .expect("create req_id")
            .to_string();
        router
            .route_stream(
                "peer",
                json!({
                    "event": crate::protocol::events::ROUTINE_CREATED,
                    "req_id": req_id,
                    "child_id": child_id,
                    "modules": ["body"],
                })
                .as_object()
                .unwrap()
                .clone(),
            )
            .await
            .unwrap();
        tokio::time::timeout(std::time::Duration::from_secs(1), creating)
            .await
            .expect("create should finish")
            .unwrap()
            .unwrap()
    }

    fn delivered_msg(event: &str, data: Value) -> JsonObject {
        let mut msg = JsonObject::new();
        msg.insert("event".to_string(), Value::String(event.to_string()));
        msg.insert("target_id".to_string(), Value::String("rid".to_string()));
        msg.insert("data".to_string(), data);
        msg.insert(
            "source".to_string(),
            serde_json::json!({"id": "src-1", "name": "other"}),
        );
        msg
    }

    #[tokio::test]
    async fn message_delivered_is_serialized_via_mailbox() {
        let (_runtime, _transport, lifecycle, router, events, _rx) = setup().await;
        lifecycle.handle_created("peer", created_msg()).await.unwrap();

        for i in 1..=3 {
            let msg = delivered_msg(
                crate::protocol::events::MESSAGE_DELIVERED,
                Value::String(format!("n{i}")),
            );
            router.route_stream("peer", msg).await.unwrap();
        }
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        assert_eq!(
            *events.lock().unwrap(),
            vec!["msg:n1", "msg:n2", "msg:n3"],
            "mailbox consumer must deliver in strict send order"
        );
    }

    #[tokio::test]
    async fn req_delivered_handlers_run_in_parallel() {
        let (_runtime, _transport, lifecycle, router, events, _rx) = setup().await;
        lifecycle.handle_created("peer", created_msg()).await.unwrap();

        for _ in 0..2 {
            let envelope = json!({
                "event": "probe",
                "__req_id__": "q1",
                "__reply_to__": "src-1",
                "data": {"delay_ms": 200},
            });
            let msg = delivered_msg(
                crate::protocol::events::MESSAGE_REQ_DELIVERED,
                envelope,
            );
            router.route_stream("peer", msg).await.unwrap();
        }
        tokio::time::sleep(std::time::Duration::from_millis(350)).await;
        let recorded = events.lock().unwrap().clone();
        assert_eq!(
            recorded,
            vec!["req:start", "req:start", "req:end", "req:end"],
            "two independent req must interleave (HTTP-like parallel): {recorded:?}"
        );
    }

    #[tokio::test]
    async fn pubsub_delivered_spawns_subscriber_handler() {
        let (_runtime, _transport, lifecycle, router, events, _rx) = setup().await;
        lifecycle.handle_created("peer", created_msg()).await.unwrap();

        let mut msg = JsonObject::new();
        msg.insert(
            "event".to_string(),
            Value::String(crate::protocol::events::PUBSUB_DELIVERED.to_string()),
        );
        msg.insert("namespace".to_string(), Value::String(String::new()));
        msg.insert("topic".to_string(), Value::String("probe.topic".to_string()));
        msg.insert("data".to_string(), Value::String("hello".to_string()));
        msg.insert(
            "subscriber_ids".to_string(),
            Value::Array(vec![Value::String("rid".to_string())]),
        );
        msg.insert(
            "source".to_string(),
            serde_json::json!({"id": "src-1", "name": "other"}),
        );
        router.route_stream("peer", msg).await.unwrap();

        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        assert_eq!(*events.lock().unwrap(), vec!["pub:hello"]);
    }

    #[tokio::test]
    async fn req_round_trip_replies_via_wire() {
        let (_runtime, _transport, lifecycle, router, _events, mut rx) = setup().await;
        lifecycle.handle_created("peer", created_msg()).await.unwrap();

        let envelope = json!({
            "event": "probe",
            "__req_id__": "q-roundtrip",
            "__reply_to__": "src-1",
            "data": {"delay_ms": 0},
        });
        let msg = delivered_msg(
            crate::protocol::events::MESSAGE_REQ_DELIVERED,
            envelope,
        );
        router.route_stream("peer", msg).await.unwrap();

        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(2);
        let mut reply: Option<JsonObject> = None;
        loop {
            tokio::select! {
                biased;
                _ = tokio::time::sleep_until(deadline) => break,
                frame = rx.recv() => {
                    let Some(frame) = frame else { break };
                    let event = crate::grpc::frame_to_json(&frame);
                    if event.get("event").and_then(Value::as_str)
                        == Some(crate::protocol::events::MESSAGE_REQ_REPLY)
                    {
                        reply = Some(event);
                        break;
                    }
                }
            }
        }
        let reply = reply.expect("message.req_reply must be sent to reply_to target");
        assert_eq!(
            reply.get("target_ids").and_then(Value::as_array).map(|v| v.len()),
            Some(1),
            "reply must target the reply_to routine"
        );
        let data = reply.get("data").and_then(Value::as_object).unwrap();
        assert_eq!(
            data.get("__req_id__").and_then(Value::as_str),
            Some("q-roundtrip")
        );
        assert_eq!(data.get("ok").and_then(Value::as_bool), Some(true));
    }

    #[tokio::test]
    async fn handle_create_start_stop_wait_and_callbacks_share_state() {
        let (runtime, transport, _lifecycle, router, events, mut rx) = setup().await;
        let handle = create_handle(parent_context(&transport), &router, &mut rx, "child-1").await;
        assert_eq!(handle.name(), "probe");
        assert_eq!(handle.modules(), Some(vec!["body".to_string()]));

        handle.on_started({
            let events = events.clone();
            move |handle| {
                let events = events.clone();
                async move {
                    events.lock().unwrap().push(format!("started:{}", handle.id()));
                }
                .boxed()
            }
        });
        handle.on_stopped({
            let events = events.clone();
            move |handle| {
                let events = events.clone();
                async move {
                    let reason = handle.reason().unwrap();
                    events
                        .lock()
                        .unwrap()
                        .push(format!("stopped:{}", reason.as_wire()));
                }
                .boxed()
            }
        });

        let starting = {
            let handle = handle.clone();
            tokio::spawn(async move { handle.start(20).await })
        };
        let start = recv_event(&mut rx).await;
        assert_eq!(
            start.get("event").and_then(Value::as_str),
            Some(crate::protocol::events::ROUTINE_START)
        );
        assert_eq!(start.get("priority").and_then(Value::as_i64), Some(20));
        router
            .route_stream(
                "peer",
                handle_msg(crate::protocol::events::LIFECYCLE_STARTED, "child-1"),
            )
            .await
            .unwrap();
        starting.await.unwrap().unwrap();
        handle.wait_started().await;
        assert!(handle.is_started());
        assert_eq!(handle.priority(), Some(20));

        let stopping = {
            let handle = handle.clone();
            tokio::spawn(async move { handle.stop().await })
        };
        let stop = recv_event(&mut rx).await;
        assert_eq!(
            stop.get("event").and_then(Value::as_str),
            Some(crate::protocol::events::ROUTINE_STOP)
        );
        let mut stopped = handle_msg(crate::protocol::events::LIFECYCLE_STOPPED, "child-1");
        stopped.insert("reason".to_string(), Value::String("PREEMPTED".to_string()));
        stopped.insert("result".to_string(), json!({"ok": true}));
        stopped.insert("by".to_string(), Value::String("evictor".to_string()));
        stopped.insert(
            "detail".to_string(),
            Value::String("evicted by evictor".to_string()),
        );
        router.route_stream("peer", stopped).await.unwrap();
        stopping.await.unwrap().unwrap();

        let termination = handle.wait_detailed().await.unwrap();
        assert_eq!(termination.result, json!({"ok": true}));
        assert_eq!(termination.reason, TerminationReason::Preempted);
        assert_eq!(termination.by.as_deref(), Some("evictor"));
        assert_eq!(termination.detail, "evicted by evictor");
        // 同一终止状态支持重复等待，不再依赖一次性 oneshot。
        assert_eq!(handle.wait().await.unwrap(), json!({"ok": true}));
        assert!(runtime.get_handle("child-1").await.is_none());

        tokio::time::sleep(std::time::Duration::from_millis(30)).await;
        assert_eq!(
            *events.lock().unwrap(),
            vec!["started:child-1", "stopped:PREEMPTED"]
        );
    }

    #[tokio::test]
    async fn try_start_rejection_can_retry_but_start_rejection_invalidates() {
        let (runtime, transport, _lifecycle, router, _events, mut rx) = setup().await;
        let handle = create_handle(parent_context(&transport), &router, &mut rx, "child-try").await;

        let trying = {
            let handle = handle.clone();
            tokio::spawn(async move { handle.try_start().await })
        };
        let start = recv_event(&mut rx).await;
        assert_eq!(start.get("try").and_then(Value::as_bool), Some(true));
        assert_eq!(start.get("priority").and_then(Value::as_i64), Some(0));
        router
            .route_stream(
                "peer",
                json!({
                    "event": crate::protocol::events::ROUTINE_REJECTED,
                    "op": "start",
                    "child_id": "child-try",
                    "try": true,
                    "error": "module blocked",
                })
                .as_object()
                .unwrap()
                .clone(),
            )
            .await
            .unwrap();
        assert_eq!(trying.await.unwrap().unwrap_err(), "module blocked");
        assert!(!handle.is_done());
        assert!(runtime.get_handle("child-try").await.is_some());

        let retrying = {
            let handle = handle.clone();
            tokio::spawn(async move { handle.try_start().await })
        };
        let _ = recv_event(&mut rx).await;
        router
            .route_stream(
                "peer",
                handle_msg(crate::protocol::events::LIFECYCLE_STARTED, "child-try"),
            )
            .await
            .unwrap();
        retrying.await.unwrap().unwrap();
        assert!(handle.is_started());

        let other = create_handle(parent_context(&transport), &router, &mut rx, "child-fail").await;
        let starting = {
            let handle = other.clone();
            tokio::spawn(async move { handle.start(10).await })
        };
        let _ = recv_event(&mut rx).await;
        router
            .route_stream(
                "peer",
                json!({
                    "event": crate::protocol::events::ROUTINE_REJECTED,
                    "op": "start",
                    "child_id": "child-fail",
                    "error": "fatal start failure",
                })
                .as_object()
                .unwrap()
                .clone(),
            )
            .await
            .unwrap();
        assert_eq!(starting.await.unwrap().unwrap_err(), "fatal start failure");
        assert_eq!(other.wait_detailed().await.unwrap_err(), "fatal start failure");
        assert!(other.destroy().await.is_err());
        assert!(runtime.get_handle("child-fail").await.is_none());
    }

    #[tokio::test]
    async fn handle_destroy_and_yield_stream_close_cleanly() {
        let (runtime, transport, _lifecycle, router, _events, mut rx) = setup().await;
        let streaming = create_handle(parent_context(&transport), &router, &mut rx, "child-stream").await;
        for (value, is_final) in [(json!(1), false), (json!(2), false), (Value::Null, true)] {
            let mut yielded = handle_msg(crate::protocol::events::ROUTINE_YIELDED, "child-stream");
            yielded.insert("data".to_string(), value);
            yielded.insert("is_final".to_string(), Value::Bool(is_final));
            router.route_stream("peer", yielded).await.unwrap();
        }
        assert_eq!(streaming.next().await.unwrap().unwrap(), json!(1));
        assert_eq!(streaming.next().await.unwrap().unwrap(), json!(2));
        assert!(streaming.next().await.is_none());
        assert!(streaming.next().await.is_none());

        let created = create_handle(parent_context(&transport), &router, &mut rx, "child-destroy").await;
        let destroying = {
            let handle = created.clone();
            tokio::spawn(async move { handle.destroy().await })
        };
        let destroy = recv_event(&mut rx).await;
        assert_eq!(
            destroy.get("event").and_then(Value::as_str),
            Some(crate::protocol::events::ROUTINE_DESTROY)
        );
        let mut stopped = handle_msg(crate::protocol::events::LIFECYCLE_STOPPED, "child-destroy");
        stopped.insert("reason".to_string(), Value::String("DESTROYED".to_string()));
        router.route_stream("peer", stopped).await.unwrap();
        destroying.await.unwrap().unwrap();
        assert_eq!(
            created.wait_detailed().await.unwrap().reason,
            TerminationReason::Destroyed
        );
        assert!(runtime.get_handle("child-destroy").await.is_none());
    }

    #[tokio::test]
    async fn call_uses_create_start_wait_handle_chain() {
        let (_runtime, transport, _lifecycle, router, _events, mut rx) = setup().await;
        let calling = tokio::spawn(async move {
            parent_context(&transport).call("probe", None, 10).await
        });

        let create = recv_event(&mut rx).await;
        assert_eq!(
            create.get("event").and_then(Value::as_str),
            Some(crate::protocol::events::ROUTINE_CREATE)
        );
        let req_id = create.get("req_id").and_then(Value::as_str).unwrap();
        router
            .route_stream(
                "peer",
                json!({
                    "event": crate::protocol::events::ROUTINE_CREATED,
                    "req_id": req_id,
                    "child_id": "child-call",
                    "modules": [],
                })
                .as_object()
                .unwrap()
                .clone(),
            )
            .await
            .unwrap();

        let start = recv_event(&mut rx).await;
        assert_eq!(
            start.get("event").and_then(Value::as_str),
            Some(crate::protocol::events::ROUTINE_START)
        );
        assert_eq!(start.get("child_id").and_then(Value::as_str), Some("child-call"));
        router
            .route_stream(
                "peer",
                handle_msg(crate::protocol::events::LIFECYCLE_STARTED, "child-call"),
            )
            .await
            .unwrap();

        let mut stopped = handle_msg(crate::protocol::events::LIFECYCLE_STOPPED, "child-call");
        stopped.insert("reason".to_string(), Value::String("COMPLETED".to_string()));
        stopped.insert("result".to_string(), json!({"called": true}));
        router.route_stream("peer", stopped).await.unwrap();
        assert_eq!(
            calling.await.unwrap().unwrap(),
            json!({"called": true})
        );
    }

    #[tokio::test]
    async fn peer_down_settles_parent_handle_as_disconnected() {
        let (runtime, transport, lifecycle, router, _events, mut rx) = setup().await;
        let handle = create_handle(parent_context(&transport), &router, &mut rx, "child-down").await;
        let waiting = {
            let handle = handle.clone();
            tokio::spawn(async move { handle.wait_detailed().await })
        };

        lifecycle.stop_all_local("peer").await;
        let error = waiting.await.unwrap().unwrap_err();
        assert!(error.contains("transport peer down"));
        assert_eq!(handle.reason(), Some(TerminationReason::Disconnected));
        assert_eq!(handle.by().as_deref(), Some("peer"));
        assert!(handle.is_done());
        assert!(runtime.get_handle("child-down").await.is_none());
    }
}
