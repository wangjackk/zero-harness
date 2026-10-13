use crate::{
    core::{BaseModule, Namespace, Routine},
    protocol::{
        envelope::{ENVELOPE_DATA, ENVELOPE_EVENT, ENVELOPE_REPLY_TO, ENVELOPE_REQ_ID},
        events::{
            LIFECYCLE_STARTED, LIFECYCLE_STOPPED, MESSAGE_REQ, MESSAGE_SEND,
            PUBSUB_PUBLISH, PUBSUB_SUBSCRIBE, PUBSUB_UNSUBSCRIBE,
            REQ_EVENT_GET_MODULE_TREE, REQ_EVENT_GET_ROUTINES, REQ_EVENT_GET_RUNNING_ROUTINES,
            ROUTINE_START, ROUTINE_CREATE, ROUTINE_YIELD,
        },
        ControlDoneReason, JsonObject, ParentRef, RawWireEvent, RoutineCatalogEntry,
        RunningRoutineInfo,
    },
};
use async_trait::async_trait;
use serde_json::Value;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::oneshot;
use tokio_util::sync::CancellationToken;

/// create 回执等待者: (child_id, modules) 或 error.
pub type CreateReply = Result<(String, Option<Vec<String>>), String>;
/// 子 routine 完成等待者: result value 或 error.
pub type HandleReply = Result<Value, String>;
/// p2p req 回执等待者: result value 或 error.
pub type ReqReply = Result<Value, String>;

// --- start 优先级(模块冲突时的抢占裁决, 只在 start 上生效; 纯数字无常量) ---
// incoming > 全部 holder 才驱逐后 start(严格大于, 平级不抢).
// 档位: 10=常规(默认档) 20=用户驱动 100=安全级; 0~9 留给低档
// (后台/可牺牲类, 可被默认档驱逐), 0 为最低档永无驱逐资格.

#[async_trait]
pub trait RoutineIo: Send + Sync {
    async fn send_raw_event(
        &self,
        payload: RawWireEvent,
        peer_id: Option<&str>,
    ) -> Result<(), String>;

    /// routine→kernel 同步查询(unary Req RPC,对齐 py Transport.req / TS
    /// GrpcClientTransport.req): event 为查询事件名(get_routines 等).
    async fn kernel_req(
        &self,
        event: &str,
        timeout_seconds: u64,
    ) -> Result<JsonObject, String>;

    async fn request_stop(&self, id: &str, peer_id: Option<&str>) -> Result<(), String>;

    // --- routine→routine create/wait future 表 ---
    // io 实现转发到 ServerRuntime 的 future 表; RunContext 通过 io 访问,
    // 避免 core→server 循环依赖.

    async fn register_create_future(
        &self,
        req_id: String,
        sender: oneshot::Sender<CreateReply>,
    );
    async fn pop_create_future(
        &self,
        req_id: &str,
    ) -> Option<oneshot::Sender<CreateReply>>;
    async fn register_handle_waiter(
        &self,
        child_id: String,
        sender: oneshot::Sender<HandleReply>,
    );
    async fn pop_handle_waiter(
        &self,
        child_id: &str,
    ) -> Option<oneshot::Sender<HandleReply>>;

    // --- p2p req 回执等待表 ---
    // req() 发 message.req 后注册 waiter, message.req_reply_delivered 到达时唤醒.

    async fn register_req_future(&self, req_id: String, sender: oneshot::Sender<ReqReply>);
    async fn pop_req_future(&self, req_id: &str) -> Option<oneshot::Sender<ReqReply>>;
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
                .with_data(&data)
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

    /// 发 message.* 定向消息(对齐 Python `ctx._send_message`):event 是 wire
    /// 事件名(message.send / message.req / message.req_reply / ...),envelope
    /// 全在 data 里,kernel 不解析只按 target_ids 转发成对应的 *delivered。
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
            RawWireEvent::new(event)
                .with_field(
                    "target_ids",
                    Value::Array(targets.into_iter().map(Value::String).collect()),
                )
                .with_data(&Value::Object(data.unwrap_or_default()))
                .with_field("source_id", self.id_value()),
        )
        .await
    }

    /// 发定向 push 消息,调 target 的 on_message(对齐 Python `routine.send`)。
    pub async fn send_to(&self, target: &str, data: Option<JsonObject>) -> Result<(), String> {
        self.send(MESSAGE_SEND, data, [target.to_string()]).await
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
                .with_data(&Value::Object(data.unwrap_or_default()))
                .with_field("source_id", self.id_value()),
        )
        .await
    }

    pub async fn send_subscribe(&self, namespace: &str, event: &str) -> Result<(), String> {
        // wire 字段是 `id`(kernel 订阅表按它登记),对齐 Python/Go。
        self.send_raw_event(
            RawWireEvent::new(PUBSUB_SUBSCRIBE)
                .with_field("namespace", Value::String(namespace.to_string()))
                .with_field("topic", Value::String(event.to_string()))
                .with_field("id", self.id_value()),
        )
        .await
    }

    pub async fn unsubscribe(&self, namespace: &str, event: &str) -> Result<(), String> {
        self.send_raw_event(
            RawWireEvent::new(PUBSUB_UNSUBSCRIBE)
                .with_field("namespace", Value::String(namespace.to_string()))
                .with_field("topic", Value::String(event.to_string()))
                .with_field("id", self.id_value()),
        )
        .await
    }

    pub fn namespace(&self, namespace: impl Into<String>) -> Namespace {
        Namespace::new(self.clone(), namespace)
    }

    pub async fn get_routines(&self) -> Result<Vec<RoutineCatalogEntry>, String> {
        let resp = self.io.kernel_req(REQ_EVENT_GET_ROUTINES, 5).await?;
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
        let resp = self.io.kernel_req(REQ_EVENT_GET_RUNNING_ROUTINES, 5).await?;
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
        let resp = self.io.kernel_req(REQ_EVENT_GET_MODULE_TREE, 5).await?;
        Ok(resp
            .get("tree")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default())
    }

    pub fn get_module<T: BaseModule + 'static>(
        &self,
        _routine: &dyn Routine,
    ) -> Result<Arc<T>, String> {
        Err("typed module lookup is not available in the minimal Rust runtime yet".to_string())
    }

    // --- routine 调 routine (create → start → wait) ---
    // 对齐 Python RunContext.call/create: 父 routine 通过 kernel 调度子 routine,
    // kernel 跨 hub 路由 (Rust hub 调 Python hub 注册的 routine 也走此路径).

    /// 提交子 routine: 发 routine.create 给 kernel, 等 routine.created 回执.
    /// 返回 (child_id, modules). 拿到 child_id 后调 start_child + wait_child.
    pub async fn create(
        &self,
        name: &str,
        kwargs: Option<JsonObject>,
    ) -> Result<(String, Option<Vec<String>>), String> {
        let req_id = new_req_id();
        let (sender, receiver) = oneshot::channel::<CreateReply>();
        self.io.register_create_future(req_id.clone(), sender).await;

        let mut payload = RawWireEvent::new(ROUTINE_CREATE)
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
            .map_err(|_| format!("create {name} timed out"))?
            .map_err(|_| format!("create {name} waiter closed"))?;
        // 清 future (若超时或正常完成都清,幂等)
        self.io.pop_create_future(&req_id).await;
        result
    }

    /// 启动已 create 的子 routine: 发 routine.start{child_id, priority} 给 kernel.
    /// 不等 lifecycle.started (简化:直接进 wait,stopped 会带 error 若 start 失败).
    /// priority: 模块冲突时 > 全部 holder 才驱逐后 start(平级不抢);
    /// 10(常规默认档)省字段 ---- wire 缺省即默认档.
    pub async fn start_child(&self, child_id: &str, priority: i64) -> Result<(), String> {
        let mut event = RawWireEvent::new(ROUTINE_START)
            .with_field("child_id", Value::String(child_id.to_string()));
        if priority != 10 {
            event = event.with_field("priority", Value::Number(priority.into()));
        }
        self.send_raw_event(event).await
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

    /// 同步拿子 routine 结果: create → start → wait 一步到位.
    /// 对齐 Python `self.call(name, kwargs, priority)`.
    pub async fn call(
        &self,
        name: &str,
        kwargs: Option<JsonObject>,
        priority: i64,
    ) -> Result<Value, String> {
        let (child_id, _modules) = self.create(name, kwargs).await?;
        self.start_child(&child_id, priority).await?;
        // 600s 超时对齐 HTTP /run 接口 (fetch_agent_state 等可能涉及外部调用)
        self.wait_child(&child_id, 600).await
    }

    // --- p2p req(HTTP-like 事务: provider 侧立即 spawn 并行处理) ---

    /// 发 p2p req 给 target routine 的 @request handler,等回执。
    /// 对齐 Python `routine.req(target, event, data, timeout)`。
    pub async fn req(
        &self,
        target: &str,
        event: &str,
        data: Option<JsonObject>,
        timeout_secs: u64,
    ) -> Result<Value, String> {
        let req_id = new_req_id();
        let (sender, receiver) = oneshot::channel::<ReqReply>();
        self.io.register_req_future(req_id.clone(), sender).await;

        let mut envelope = JsonObject::new();
        envelope.insert(ENVELOPE_EVENT.to_string(), Value::String(event.to_string()));
        envelope.insert(ENVELOPE_REQ_ID.to_string(), Value::String(req_id.clone()));
        envelope.insert(
            ENVELOPE_REPLY_TO.to_string(),
            Value::String(self.id.clone()),
        );
        envelope.insert(
            ENVELOPE_DATA.to_string(),
            Value::Object(data.unwrap_or_default()),
        );
        self.send(MESSAGE_REQ, Some(envelope), [target.to_string()])
            .await?;

        let result = tokio::time::timeout(Duration::from_secs(timeout_secs), receiver)
            .await
            .map_err(|_| format!("req {event} timed out"))?
            .map_err(|_| format!("req {event} waiter closed"))?;
        self.io.pop_req_future(&req_id).await;
        result
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
