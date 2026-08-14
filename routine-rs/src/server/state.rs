use crate::{
    core::{
        BaseModule, HandleReply, RouterRoutine, RoutineError, RoutineOutput,
        RoutineRegistry, RunContext, SubmitReply, WireRoutine,
    },
    protocol::{JsonObject, JsonValue},
};
use prost_types::Struct;
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Instant,
};
use tokio::sync::{mpsc, oneshot, Mutex, RwLock};
use tokio::task::JoinHandle;
use tokio_util::sync::CancellationToken;

/// submit 回执等待者: req_id → oneshot sender.
/// 父 routine submit 后等 routine.submitted{req_id, child_id, modules} 回执.
pub type SubmitWaiter = oneshot::Sender<SubmitReply>;

/// 子 routine 完成等待者: child_id → oneshot sender.
/// 父 routine call/handle.wait 等 lifecycle.stopped{id=child_id, result, error} 中转回.
pub type HandleWaiter = oneshot::Sender<HandleReply>;

pub struct RoutineInstance {
    routine: Box<dyn WireRoutine>,
}

impl RoutineInstance {
    pub fn new(routine: Box<dyn WireRoutine>) -> Self {
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
    /// submit 入参(lifecycle.created 投递):created 时存,start 时用。
    /// kernel 的 sendStart 不带 kwargs----对齐 Python `_init_kwargs` 模式。
    pub init_kwargs: JsonObject,
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
        }
    }
}

pub struct ServerRuntime {
    pub routines: RoutineRegistry,
    pub modules: Vec<Arc<dyn BaseModule>>,
    pub routers: RwLock<HashMap<String, Arc<dyn RouterRoutine>>>,
    pub peer_to_queue: Mutex<HashMap<String, mpsc::UnboundedSender<Struct>>>,
    pub running_instances: Mutex<HashMap<String, InvocationState>>,
    pub last_heartbeat: Mutex<HashMap<String, Instant>>,
    pub watchdog_started: AtomicBool,
    /// submit 回执等待表: req_id → waiter. 父 routine submit 后注册,等
    /// routine.submitted 回执唤醒. 对齐 Python runtime.register_submit_future.
    pub submit_futures: Mutex<HashMap<String, SubmitWaiter>>,
    /// 子 routine 完成等待表: child_id → waiter. 父 routine call/handle.wait
    /// 注册,等 lifecycle.stopped 中转回唤醒. 对齐 Python handle._wait_event.
    pub handle_waiters: Mutex<HashMap<String, HandleWaiter>>,
}

impl ServerRuntime {
    pub fn new(
        routines: RoutineRegistry,
        modules: Vec<Arc<dyn BaseModule>>,
        routers: Vec<Arc<dyn RouterRoutine>>,
    ) -> Self {
        let router_map = routers
            .into_iter()
            .map(|router| (router.name().to_string(), router))
            .collect();
        Self {
            routines,
            modules,
            routers: RwLock::new(router_map),
            peer_to_queue: Mutex::new(HashMap::new()),
            running_instances: Mutex::new(HashMap::new()),
            last_heartbeat: Mutex::new(HashMap::new()),
            watchdog_started: AtomicBool::new(false),
            submit_futures: Mutex::new(HashMap::new()),
            handle_waiters: Mutex::new(HashMap::new()),
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

    pub async fn resolve_instance(&self, prid: &str, name: &str) -> Result<SharedRoutine, String> {
        let factory = self
            .routines
            .get_routine(name)
            .ok_or_else(|| format!("routine not found: {name}"))?;
        let mut running_instances = self.running_instances.lock().await;
        let state = running_instances
            .entry(prid.to_string())
            .or_insert_with(|| InvocationState::new(split_prid(prid).0, None));
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

    // --- submit / wait future 表 ---

    pub async fn register_submit_future(
        &self,
        req_id: &str,
        sender: SubmitWaiter,
    ) {
        self.submit_futures
            .lock()
            .await
            .insert(req_id.to_string(), sender);
    }

    pub async fn pop_submit_future(&self, req_id: &str) -> Option<SubmitWaiter> {
        self.submit_futures.lock().await.remove(req_id)
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
