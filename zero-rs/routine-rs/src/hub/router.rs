use crate::{
    core::{messaging::RoutineSource, RoutineIo},
    protocol::{
        envelope::{
            ENVELOPE_DATA, ENVELOPE_ERROR, ENVELOPE_EVENT, ENVELOPE_OK, ENVELOPE_REPLY_TO,
            ENVELOPE_REQ_ID,
        },
        events::{
            CATALOG_PUSHED, LIFECYCLE_HEARTBEAT, LIFECYCLE_START, LIFECYCLE_STOP,
            LIFECYCLE_STOPPED, MESSAGE_DELIVERED, MESSAGE_REQ_DELIVERED,
            MESSAGE_REQ_REPLY_DELIVERED, PUBSUB_DELIVERED, ROUTINE_SUBMITTED,
        },
        JsonObject, RawWireEvent,
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
            // - routine.submitted: kernel 回 submit 的回执,带 child_id + modules
            // - lifecycle.stopped: 子 routine 完成后 kernel 中转回父 server,
            //   带 result/error. 父 call/wait 据此唤醒.
            //   注意:跟 lifecycle.stop(kernel 主动停本 server 的 routine)不同,
            //   stopped 是子完成通知,本 server 不持有子 instance,只唤醒 waiter.
            ROUTINE_SUBMITTED => self.handle_routine_submitted(msg).await,
            LIFECYCLE_STOPPED => self.handle_lifecycle_stopped(msg).await,
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

    /// 处理 routine.submitted 回执:唤醒 submit future.
    /// wire 字段: req_id, child_id, modules?, error?
    async fn handle_routine_submitted(&self, msg: JsonObject) {
        let req_id = msg
            .get("req_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if req_id.is_empty() {
            tracing::warn!("routine.submitted missing req_id");
            return;
        }
        let Some(sender) = self.runtime.pop_submit_future(&req_id).await else {
            tracing::warn!("routine.submitted no waiter for req_id={req_id}");
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

    /// 处理 lifecycle.stopped 中转事件:唤醒 handle waiter.
    /// wire 字段: id(=child_id), reason, result?, error?
    async fn handle_lifecycle_stopped(&self, msg: JsonObject) {
        let child_id = msg
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if child_id.is_empty() {
            return;
        }
        let Some(sender) = self.runtime.pop_handle_waiter(&child_id).await else {
            // 不是父 routine 等待的子 routine,忽略(可能是本 server 自己发出的 stopped
            // 被 kernel echo 回来,或无父等待的子完成).
            return;
        };
        if let Some(err) = msg.get("error").and_then(Value::as_str) {
            let _ = sender.send(Err(err.to_string()));
            return;
        }
        let result = msg.get("result").cloned().unwrap_or(Value::Null);
        let _ = sender.send(Ok(result));
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        core::{
            messaging::{request_spec, subscribe_spec},
            RoutineInfo, RoutineOutput, RoutineRegistry,
        },
        protocol::{events::LIFECYCLE_CREATED, JsonValue},
    };
    use async_trait::async_trait;
    use futures::FutureExt;
    use serde_json::json;
    use std::sync::{Arc, Mutex};
    use tokio::sync::mpsc;

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
}
