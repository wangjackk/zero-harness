use crate::{
    core::{BaseModule, Namespace, WireRoutine},
    protocol::{
        events::{
            LIFECYCLE_STARTED, LIFECYCLE_STOPPED, P2P_SEND,
            PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE,
            ROUTINE_START, ROUTINE_SUBMIT, ROUTINE_YIELD,
        },
        ControlDoneReason, JsonObject, ParentRef, RawWireEvent, RoutineCatalogEntry, RunningRoutineInfo,
    },
};
use async_trait::async_trait;
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::oneshot;
use tokio_util::sync::CancellationToken;

/// submit 回执等待者: (child_id, modules) 或 error.
pub type SubmitReply = Result<(String, Option<Vec<String>>), String>;
/// 子 routine 完成等待者: result value 或 error.
pub type HandleReply = Result<Value, String>;

#[async_trait]
pub trait RoutineIo: Send + Sync {
    async fn send_raw_event(
        &self,
        payload: RawWireEvent,
        peer_id: Option<&str>,
    ) -> Result<(), String>;

    async fn send_shell_req(
        &self,
        action: &str,
        data: Option<JsonObject>,
        timeout_seconds: u64,
    ) -> Result<JsonObject, String>;

    async fn request_stop(&self, id: &str, peer_id: Option<&str>) -> Result<(), String>;

    // --- routine→routine submit/wait future 表 ---
    // io 实现转发到 ServerRuntime 的 future 表; RunContext 通过 io 访问,
    // 避免 core→server 循环依赖.

    async fn register_submit_future(
        &self,
        req_id: String,
        sender: oneshot::Sender<SubmitReply>,
    );
    async fn pop_submit_future(
        &self,
        req_id: &str,
    ) -> Option<oneshot::Sender<SubmitReply>>;
    async fn register_handle_waiter(
        &self,
        child_id: String,
        sender: oneshot::Sender<HandleReply>,
    );
    async fn pop_handle_waiter(
        &self,
        child_id: &str,
    ) -> Option<oneshot::Sender<HandleReply>>;
}

#[derive(Clone)]
pub struct RunContext {
    id: String,
    name: String,
    peer_id: String,
    io: Arc<dyn RoutineIo>,
    control_type: Option<String>,
    push_parent: Option<ParentRef>,
    token_parent: Option<ParentRef>,
    available_routines: Option<Vec<String>>,
    cancellation: CancellationToken,
    // Run 中调过 yield_item → 框架在 Run 结束后自动发 is_final=true 收尾.
    // Arc 共享:RunContext clone 时共享同一标志(对齐 Python async generator 自然结束).
    yield_used: Arc<AtomicBool>,
}

pub struct RunContextOptions {
    pub id: String,
    pub name: String,
    pub peer_id: String,
    pub io: Arc<dyn RoutineIo>,
    pub control_type: Option<String>,
    pub push_parent: Option<ParentRef>,
    pub token_parent: Option<ParentRef>,
    pub available_routines: Option<Vec<String>>,
    pub cancellation: CancellationToken,
}

impl RunContext {
    pub fn new(options: RunContextOptions) -> Self {
        Self {
            id: options.id,
            name: options.name,
            peer_id: options.peer_id,
            io: options.io,
            control_type: options.control_type,
            push_parent: options.push_parent,
            token_parent: options.token_parent,
            available_routines: options.available_routines,
            cancellation: options.cancellation,
            yield_used: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn id(&self) -> &str {
        &self.id
    }

    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn peer_id(&self) -> &str {
        &self.peer_id
    }

    pub fn control_type(&self) -> Option<&str> {
        self.control_type.as_deref()
    }

    pub fn push_parent(&self) -> Option<&ParentRef> {
        self.push_parent.as_ref()
    }

    pub fn token_parent(&self) -> Option<&ParentRef> {
        self.token_parent.as_ref()
    }

    pub fn available_routines(&self) -> Option<&[String]> {
        self.available_routines.as_deref()
    }

    pub fn cancellation(&self) -> &CancellationToken {
        &self.cancellation
    }

    /// yield 一项给 parent(对齐 Python `yield item`).
    /// 业务在 run 中循环调 yield_item 推送流式产出,框架在 run 结束后自动发
    /// is_final=true 收尾(对齐 Python async generator 自然结束).无需手动收尾.
    /// run 返回 Err 时,框架自动发 is_final=true + error.
    pub async fn yield_item(&self, data: Value) -> Result<(), String> {
        self.yield_used.store(true, Ordering::SeqCst);
        self.send_raw_event(
            RawWireEvent::new(ROUTINE_YIELD)
                .with_field("id", self.id_value())
                .with_field("source_id", self.id_value())
                .with_field("data", data)
                .with_field("is_final", Value::Bool(false)),
        )
        .await
    }

    /// 框架用:检查 run 中是否调过 yield_item,决定是否自动发 is_final.
    pub fn is_yield_used(&self) -> bool {
        self.yield_used.load(Ordering::SeqCst)
    }

    pub async fn ack_start(&self) -> Result<(), String> {
        self.send_raw_event(RawWireEvent::new(LIFECYCLE_STARTED).with_field("id", self.id_value()))
            .await
    }

    pub async fn ack_stop(&self) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(LIFECYCLE_STOPPED)
                .with_field("id", self.id_value())
                .with_field(
                    "reason",
                    Value::String(ControlDoneReason::Stop.as_wire().to_string()),
                ),
        )
        .await
    }

    pub async fn send_raw_event(&self, payload: RawWireEvent) -> Result<(), String> {
        self.io.send_raw_event(payload, Some(&self.peer_id)).await
    }

    pub async fn request_stop(&self) -> Result<(), String> {
        self.io.request_stop(&self.id, Some(&self.peer_id)).await
    }

    pub async fn send(
        &self,
        event: &str,
        data: Option<JsonObject>,
        to: impl IntoIterator<Item = String>,
    ) -> Result<(), String> {
        let targets = to
            .into_iter()
            .filter(|id| !id.is_empty())
            .collect::<Vec<_>>();
        if targets.is_empty() {
            return Ok(());
        }
        self.send_raw_event(
            RawWireEvent::new(P2P_SEND)
                .with_field(
                    "target_ids",
                    Value::Array(targets.into_iter().map(Value::String).collect()),
                )
                .with_field("topic", Value::String(event.to_string()))
                .with_field("data", Value::Object(data.unwrap_or_default()))
                .with_field("source_id", self.id_value()),
        )
        .await
    }

    pub async fn publish(
        &self,
        event: &str,
        data: Option<JsonObject>,
        namespace: Option<&str>,
    ) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(PUBSUB_PUBLISH)
                .with_field(
                    "namespace",
                    Value::String(namespace.unwrap_or("").to_string()),
                )
                .with_field("topic", Value::String(event.to_string()))
                .with_field("data", Value::Object(data.unwrap_or_default()))
                .with_field("source_id", self.id_value()),
        )
        .await
    }

    pub async fn send_subscribe(&self, namespace: &str, event: &str) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(PUBSUB_SUBSCRIBE)
                .with_field("namespace", Value::String(namespace.to_string()))
                .with_field("topic", Value::String(event.to_string()))
                .with_field("subscriber_id", self.id_value())
                .with_field("source_id", self.id_value()),
        )
        .await
    }

    pub async fn unsubscribe(&self, namespace: &str, event: &str) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(PUBSUB_UNSUBSCRIBE)
                .with_field("namespace", Value::String(namespace.to_string()))
                .with_field("topic", Value::String(event.to_string()))
                .with_field("subscriber_id", self.id_value())
                .with_field("source_id", self.id_value()),
        )
        .await
    }

    pub fn namespace(&self, namespace: impl Into<String>) -> Namespace {
        Namespace::new(self.clone(), namespace)
    }

    pub async fn get_all_routines(&self) -> Result<Vec<RoutineCatalogEntry>, String> {
        let resp = self.io.send_shell_req("get_all_routines", None, 5).await?;
        Ok(resp
            .get("routines")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| serde_json::from_value(item.clone()).ok())
                    .collect()
            })
            .unwrap_or_default())
    }

    pub async fn get_running_routines(&self) -> Result<Vec<RunningRoutineInfo>, String> {
        let resp = self
            .io
            .send_shell_req("get_running_routines", None, 5)
            .await?;
        Ok(resp
            .get("routines")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| serde_json::from_value(item.clone()).ok())
                    .collect()
            })
            .unwrap_or_default())
    }

    pub async fn get_module_tree(&self) -> Result<JsonObject, String> {
        let resp = self.io.send_shell_req("get_module_tree", None, 5).await?;
        Ok(resp
            .get("tree")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default())
    }

    pub fn get_module<T: BaseModule + 'static>(
        &self,
        _routine: &dyn WireRoutine,
    ) -> Result<Arc<T>, String> {
        Err("typed module lookup is not available in the minimal Rust runtime yet".to_string())
    }

    // --- routine 调 routine (submit → start → wait) ---
    // 对齐 Python RunContext.call/submit: 父 routine 通过 kernel 调度子 routine,
    // kernel 跨 hub 路由 (Rust hub 调 Python hub 注册的 routine 也走此路径).

    /// 提交子 routine: 发 routine.submit 给 kernel, 等 routine.submitted 回执.
    /// 返回 (child_id, modules). 拿到 child_id 后调 start_child + wait_child.
    pub async fn submit(
        &self,
        name: &str,
        kwargs: Option<JsonObject>,
    ) -> Result<(String, Option<Vec<String>>), String> {
        let req_id = new_req_id();
        let (sender, receiver) = oneshot::channel::<SubmitReply>();
        self.io.register_submit_future(req_id.clone(), sender).await;

        let mut payload = RawWireEvent::new(ROUTINE_SUBMIT)
            .with_field("req_id", Value::String(req_id.clone()))
            .with_field("parent_id", self.id_value())
            .with_field("name", Value::String(name.to_string()));
        if let Some(kwargs) = kwargs {
            payload = payload.with_field("kwargs", Value::Object(kwargs));
        } else {
            payload = payload.with_field("kwargs", Value::Object(JsonObject::new()));
        }
        self.send_raw_event(payload).await?;

        let result = tokio::time::timeout(Duration::from_secs(30), receiver)
            .await
            .map_err(|_| format!("submit {name} timed out"))?
            .map_err(|_| format!("submit {name} waiter closed"))?;
        // 清 future (若超时或正常完成都清,幂等)
        self.io.pop_submit_future(&req_id).await;
        result
    }

    /// 启动已 submit 的子 routine: 发 routine.start{child_id} 给 kernel.
    /// 不等 lifecycle.started (简化:直接进 wait,stopped 会带 error 若 start 失败).
    pub async fn start_child(&self, child_id: &str) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(ROUTINE_START)
                .with_field("child_id", Value::String(child_id.to_string())),
        )
        .await
    }

    /// 等子 routine 完成: 注册 handle waiter, 等 lifecycle.stopped 中转回.
    /// 返回子的 result value, 或 error.
    pub async fn wait_child(&self, child_id: &str, timeout_secs: u64) -> Result<Value, String> {
        let (sender, receiver) = oneshot::channel::<HandleReply>();
        self.io.register_handle_waiter(child_id.to_string(), sender).await;
        let result = tokio::time::timeout(Duration::from_secs(timeout_secs), receiver)
            .await
            .map_err(|_| format!("wait_child {child_id} timed out"))?
            .map_err(|_| format!("wait_child {child_id} waiter closed"))?;
        // 清 waiter (幂等)
        self.io.pop_handle_waiter(child_id).await;
        result
    }

    /// 同步拿子 routine 结果: submit → start → wait 一步到位.
    /// 对齐 Python `self.call(name, kwargs)`.
    pub async fn call(
        &self,
        name: &str,
        kwargs: Option<JsonObject>,
    ) -> Result<Value, String> {
        let (child_id, _modules) = self.submit(name, kwargs).await?;
        self.start_child(&child_id).await?;
        // 600s 超时对齐 HTTP /run 接口 (fetch_agent_state 等可能涉及外部调用)
        self.wait_child(&child_id, 600).await
    }

    fn id_value(&self) -> Value {
        Value::String(self.id.clone())
    }
}

fn new_req_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    format!("r{n}")
}
