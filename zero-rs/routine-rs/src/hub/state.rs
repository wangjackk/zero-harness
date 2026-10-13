use crate::{
    core::{
        messaging::{RequestHandler, RequestSpec, RoutineSource, SubscribeHandler, SubscribeSpec},
        BaseModule, HandleReply, RoutineError, RoutineOutput, RoutineRegistry,
        RunContext, CreateReply, Routine,
    },
    grpc::routine::Frame,
    protocol::{JsonObject, JsonValue},
};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio::sync::{mpsc, oneshot, Mutex};
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

/// mailbox 慢回调阈值:超过打 warning,定位积压源头(对齐 Python _MAIL_SLOW_MS)。
const MAIL_SLOW: Duration = Duration::from_secs(2);

/// create 回执等待者: req_id → oneshot sender.
/// 父 routine create 后等 routine.created{req_id, child_id, modules} 回执.
pub type CreateWaiter = oneshot::Sender<CreateReply>;

/// 子 routine 完成等待者: child_id → oneshot sender.
/// 父 routine call/handle.wait 等 lifecycle.stopped{id=child_id, result, error} 中转回.
pub type HandleWaiter = oneshot::Sender<HandleReply>;

/// p2p req 回执等待者: req_id → oneshot sender(等 message.req_reply 回执)。
pub type ReqWaiter = oneshot::Sender<crate::core::context::ReqReply>;

/// mailbox 单条投递项:(source, data)。
type MailItem = (RoutineSource, JsonValue);

/// 实例 mailbox:串行投递队列 + 单消费者任务。
struct InstanceMailbox {
    tx: mpsc::UnboundedSender<MailItem>,
    depth: Arc<AtomicUsize>,
    task: JoinHandle<()>,
}

pub struct RoutineInstance {
    routine: Box<dyn Routine>,
}

impl RoutineInstance {
    pub fn new(routine: Box<dyn Routine>) -> Self {
        Self { routine }
    }

    pub async fn on_created(
        &self,
        rid: &str,
        kwargs: &JsonObject,
    ) -> Result<(), RoutineError> {
        self.routine.on_created(rid, kwargs).await
    }

    pub async fn on_started(&self) -> Result<(), RoutineError> {
        self.routine.on_started().await
    }

    pub async fn run(
        &self,
        ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        self.routine.run(ctx, params).await
    }

    pub async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
        self.routine.stop().await
    }

    /// created 态销毁的 destroy hook(与 stop 分野)。
    pub async fn destroy(&self) -> Result<Option<JsonValue>, RoutineError> {
        self.routine.destroy().await
    }

    /// 实例 mailbox 消费者逐条调(串行保序)。
    pub async fn on_message(
        &self,
        source: RoutineSource,
        data: JsonValue,
    ) -> Result<(), RoutineError> {
        self.routine.on_message(source, data).await
    }

    /// created 时收集进 InvocationState.request_handlers。
    pub fn request_specs(&self) -> Vec<RequestSpec> {
        self.routine.request_specs()
    }

    /// created 时注册本地 subscriber 表 + 发 pubsub.subscribe。
    pub fn subscribe_specs(&self) -> Vec<SubscribeSpec> {
        self.routine.subscribe_specs()
    }

    pub async fn on_stopped(
        &self,
        reason: &str,
        result: Option<&JsonValue>,
        detail: &str,
    ) -> Result<(), RoutineError> {
        self.routine.on_stopped(reason, result, detail).await
    }
}

pub type SharedRoutine = Arc<RoutineInstance>;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum InvocationStatus {
    Created,
    Starting,
    Running,
    Stopping,
    Stopped,
    Error,
}

pub struct InvocationState {
    pub instance: Option<SharedRoutine>,
    pub peer_id: String,
    pub cancellation: CancellationToken,
    pub status: InvocationStatus,
    pub started: bool,
    pub finalized: bool,
    pub run_task: Option<JoinHandle<()>>,
    pub ctx: Option<RunContext>,
    /// create 入参(lifecycle.created 投递):created 时存,start 时用。
    /// kernel 的 sendStart 不带 kwargs----对齐 Python `_init_kwargs` 模式。
    pub init_kwargs: JsonObject,
    /// routine 名(created 时填,resolve_source 补 source.name 用)。
    pub name: String,
    /// @request 分发表:event → handler(created 时从 request_specs 收集)。
    pub request_handlers: HashMap<String, RequestHandler>,
}

impl InvocationState {
    pub fn new(peer_id: impl Into<String>, instance: Option<SharedRoutine>) -> Self {
        Self {
            instance,
            peer_id: peer_id.into(),
            cancellation: CancellationToken::new(),
            status: InvocationStatus::Created,
            started: false,
            finalized: false,
            run_task: None,
            ctx: None,
            init_kwargs: JsonObject::new(),
            name: String::new(),
            request_handlers: HashMap::new(),
        }
    }
}

pub struct ServerRuntime {
    pub routines: RoutineRegistry,
    pub modules: Vec<Arc<dyn BaseModule>>,
    pub peer_to_queue: Mutex<HashMap<String, mpsc::UnboundedSender<Frame>>>,
    pub running_instances: Mutex<HashMap<String, InvocationState>>,
    pub last_heartbeat: Mutex<HashMap<String, Instant>>,
    pub watchdog_started: AtomicBool,
    /// create 回执等待表: req_id → waiter. 父 routine create 后注册,等
    /// routine.created 回执唤醒. 对齐 Python runtime.register_create_future.
    pub create_futures: Mutex<HashMap<String, CreateWaiter>>,
    /// 子 routine 完成等待表: child_id → waiter. 父 routine call/handle.wait
    /// 注册,等 lifecycle.stopped 中转回唤醒. 对齐 Python handle._wait_event.
    pub handle_waiters: Mutex<HashMap<String, HandleWaiter>>,
    /// p2p req 回执等待表: req_id → waiter. req() 发 message.req 后注册,
    /// message.req_reply_delivered 到达时唤醒.
    pub req_waiters: Mutex<HashMap<String, ReqWaiter>>,
    /// 实例 mailbox 表: prid → 串行投递队列(created 建,销毁关).
    /// 私有:经 open_mailbox/close_mailbox/deliver_mail 访问.
    mailboxes: Mutex<HashMap<String, InstanceMailbox>>,
    /// pubsub 本地订阅表: prid → (namespace, topic) → handler.
    pub subscribers: Mutex<HashMap<String, HashMap<(String, String), SubscribeHandler>>>,
}

impl ServerRuntime {
    pub fn new(
        routines: RoutineRegistry,
        modules: Vec<Arc<dyn BaseModule>>,
    ) -> Self {
        Self {
            routines,
            modules,
            peer_to_queue: Mutex::new(HashMap::new()),
            running_instances: Mutex::new(HashMap::new()),
            last_heartbeat: Mutex::new(HashMap::new()),
            watchdog_started: AtomicBool::new(false),
            create_futures: Mutex::new(HashMap::new()),
            handle_waiters: Mutex::new(HashMap::new()),
            req_waiters: Mutex::new(HashMap::new()),
            mailboxes: Mutex::new(HashMap::new()),
            subscribers: Mutex::new(HashMap::new()),
        }
    }

    pub async fn resolve_running(&self, peer_id: &str, rid: &str) -> Option<SharedRoutine> {
        if rid.is_empty() {
            return None;
        }
        let running_instances = self.running_instances.lock().await;
        running_instances
            .get(&format!("{peer_id}:{rid}"))
            .filter(|state| {
                matches!(
                    state.status,
                    InvocationStatus::Running | InvocationStatus::Stopping
                )
            })
            .and_then(|state| state.instance.clone())
    }

    /// 按 rid 找 created(含未 start)且未 finalized 的实例 + 它的 @request
    /// 分发表。message.*/req 的投递目标:created 后即可收(对齐 Python
    /// runtime.get_created)。返回 (instance, request_handlers) 供 spawn 用。
    pub async fn resolve_created(
        &self,
        peer_id: &str,
        rid: &str,
    ) -> Option<(SharedRoutine, HashMap<String, RequestHandler>)> {
        if rid.is_empty() {
            return None;
        }
        let running_instances = self.running_instances.lock().await;
        running_instances
            .get(&format!("{peer_id}:{rid}"))
            .filter(|state| !state.finalized)
            .and_then(|state| {
                state
                    .instance
                    .clone()
                    .map(|instance| (instance, state.request_handlers.clone()))
            })
    }

    /// 按 rid 补 source 的 name(source 在本 hub 时能查到)。
    pub async fn resolve_source(&self, peer_id: &str, rid: &str) -> RoutineSource {
        let mut source = RoutineSource::new(rid);
        if rid.is_empty() {
            return source;
        }
        let running_instances = self.running_instances.lock().await;
        if let Some(state) = running_instances.get(&format!("{peer_id}:{rid}")) {
            if !state.name.is_empty() {
                source.name = Some(state.name.clone());
            }
        }
        source
    }

    // --- 实例 mailbox(串行投递)---

    /// 实例 mailbox 建立 + 消费者起动(created 时调;重建先关旧的)。
    ///
    /// 消费者逐条调 on_message,同一实例严格串行----顺序语义由框架保证,
    /// 业务不再自建 reorder。慢回调只积压本实例。
    pub async fn open_mailbox(&self, prid: &str, instance: SharedRoutine) {
        self.close_mailbox(prid).await;
        let (tx, mut rx) = mpsc::unbounded_channel::<MailItem>();
        let depth = Arc::new(AtomicUsize::new(0));
        let consumer_depth = depth.clone();
        let prid_owned = prid.to_string();
        let task = tokio::spawn(async move {
            while let Some((source, data)) = rx.recv().await {
                consumer_depth.fetch_sub(1, Ordering::SeqCst);
                let t0 = Instant::now();
                if let Err(exc) = instance.on_message(source, data).await {
                    tracing::warn!("[mailbox] {prid_owned} callback failed: {exc}");
                }
                let elapsed = t0.elapsed();
                if elapsed > MAIL_SLOW {
                    tracing::warn!(
                        "[mailbox] {prid_owned} slow callback: {:.0?}s (queue depth={})",
                        elapsed.as_secs_f32(),
                        consumer_depth.load(Ordering::SeqCst),
                    );
                }
            }
        });
        self.mailboxes.lock().await.insert(
            prid.to_string(),
            InstanceMailbox { tx, depth, task },
        );
    }

    /// 实例销毁时关 mailbox:停接收 + 取消消费者(在途回调随实例终结,
    /// 不再出现销毁后回调仍捏着实例的僵尸 task)。
    pub async fn close_mailbox(&self, prid: &str) {
        if let Some(mailbox) = self.mailboxes.lock().await.remove(prid) {
            mailbox.task.abort();
        }
    }

    /// message 投递入实例 mailbox(调 on_message)。无 mailbox(实例未
    /// created / 已销毁)静默丢弃,对齐 Python deliver_mail 语义。
    pub async fn deliver_mail(&self, prid: &str, source: RoutineSource, data: JsonValue) {
        let mailboxes = self.mailboxes.lock().await;
        if let Some(mailbox) = mailboxes.get(prid) {
            mailbox.depth.fetch_add(1, Ordering::SeqCst);
            let _ = mailbox.tx.send((source, data));
        }
    }

    // --- pubsub 订阅者表 ---

    pub async fn register_subscriber(
        &self,
        prid: &str,
        namespace: &str,
        topic: &str,
        handler: SubscribeHandler,
    ) {
        self.subscribers
            .lock()
            .await
            .entry(prid.to_string())
            .or_default()
            .insert((namespace.to_string(), topic.to_string()), handler);
    }

    pub async fn remove_subscribers(&self, prid: &str) {
        self.subscribers.lock().await.remove(prid);
    }

    pub async fn subscriber_handler(
        &self,
        prid: &str,
        namespace: &str,
        topic: &str,
    ) -> Option<SubscribeHandler> {
        self.subscribers
            .lock()
            .await
            .get(prid)?
            .get(&(namespace.to_string(), topic.to_string()))
            .cloned()
    }

    // --- req 回执等待表 ---

    pub async fn register_req_future(&self, req_id: &str, sender: ReqWaiter) {
        self.req_waiters
            .lock()
            .await
            .insert(req_id.to_string(), sender);
    }

    pub async fn pop_req_future(&self, req_id: &str) -> Option<ReqWaiter> {
        self.req_waiters.lock().await.remove(req_id)
    }

    pub async fn resolve_instance(&self, prid: &str, name: &str) -> Result<SharedRoutine, String> {
        let factory = self
            .routines
            .get_routine(name)
            .ok_or_else(|| format!("routine not found: {name}"))?;
        let mut running_instances = self.running_instances.lock().await;
        let state = running_instances
            .entry(prid.to_string())
            .or_insert_with(|| InvocationState::new(split_prid(prid).0, None));
        state.name = name.to_string();
        if state.instance.is_none() {
            state.instance = Some(Arc::new(RoutineInstance::new(factory.create())));
        }
        state
            .instance
            .clone()
            .ok_or_else(|| format!("instance not found for rid={prid}"))
    }

    pub fn coerce_parent(raw: Option<&JsonObject>) -> Option<crate::protocol::ParentRef> {
        raw.map(|value| crate::protocol::ParentRef {
            id: value
                .get("id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
            name: value
                .get("name")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
        })
    }

    // --- create / wait future 表 ---

    pub async fn register_create_future(
        &self,
        req_id: &str,
        sender: CreateWaiter,
    ) {
        self.create_futures
            .lock()
            .await
            .insert(req_id.to_string(), sender);
    }

    pub async fn pop_create_future(&self, req_id: &str) -> Option<CreateWaiter> {
        self.create_futures.lock().await.remove(req_id)
    }

    pub async fn register_handle_waiter(
        &self,
        child_id: &str,
        sender: HandleWaiter,
    ) {
        self.handle_waiters
            .lock()
            .await
            .insert(child_id.to_string(), sender);
    }

    pub async fn pop_handle_waiter(&self, child_id: &str) -> Option<HandleWaiter> {
        self.handle_waiters.lock().await.remove(child_id)
    }

    /// 启动时打印一行 routine 注册统计（总数 / enabled / passive）。
    ///
    /// 框架只陈述客观计数——不渲染名字表格、不读 description/modules 等业务字段，
    /// 那些是业务侧的展示关心（业务层可遍历 `routines` 自行打印 banner）。
    /// 不走 tracing：banner 性质，无需每行时间戳/level。
    pub fn print_summary(&self) {
        use std::io::Write;

        let entries = self.routines.get_routines();
        let total = entries.len();
        let enabled_n = entries.iter().filter(|f| f.enabled()).count();
        let passive_n = entries.iter().filter(|f| f.is_passive()).count();

        println!(
            "  routine  {total} routines  ·  {enabled_n} enabled  ·  {passive_n} passive"
        );
        let _ = std::io::stdout().flush();
    }

    pub fn watchdog_is_started(&self) -> bool {
        self.watchdog_started.load(Ordering::SeqCst)
    }

    pub fn set_watchdog_started(&self, value: bool) {
        self.watchdog_started.store(value, Ordering::SeqCst);
    }
}

pub fn split_prid(prid: &str) -> (&str, &str) {
    prid.rsplit_once(':').unwrap_or(("", prid))
}
