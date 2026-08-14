use crate::{
    core::{
        routine::drain_run_result, RoutineIo, RunContext,
        RunContextOptions,
    },
    protocol::{
        events::ROUTINE_YIELD, ChainedError, ControlDoneReason, JsonObject,
        JsonValue, RawWireEvent,
    },
    server::{
        state::{split_prid, InvocationStatus, SharedRoutine},
        OutboundTransport, ServerRuntime,
    },
};
use serde_json::Value;
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::task::JoinHandle;

pub struct LifecycleManager {
    runtime: Arc<ServerRuntime>,
    transport: Arc<OutboundTransport>,
}

impl LifecycleManager {
    pub const STOP_TIMEOUT_SECONDS: u64 = 3;
    pub const HEARTBEAT_TIMEOUT_SECONDS: u64 = 15;
    pub const HEARTBEAT_INTERVAL_SECONDS: u64 = 5;

    pub fn new(runtime: Arc<ServerRuntime>, transport: Arc<OutboundTransport>) -> Self {
        Self { runtime, transport }
    }

    pub fn ensure_watchdog(self: &Arc<Self>) {
        if self.runtime.watchdog_is_started() {
            return;
        }
        self.runtime.set_watchdog_started(true);
        let this = self.clone();
        tokio::spawn(async move {
            this.heartbeat_watchdog().await;
        });
    }

    pub fn stop_watchdog(&self) {
        self.runtime.set_watchdog_started(false);
    }

    pub async fn heartbeat_watchdog(&self) {
        while self.runtime.watchdog_is_started() {
            tokio::time::sleep(Duration::from_secs(Self::HEARTBEAT_INTERVAL_SECONDS)).await;
            let now = Instant::now();
            let stale = {
                let heartbeats = self.runtime.last_heartbeat.lock().await;
                heartbeats
                    .iter()
                    .filter_map(|(prid, last)| {
                        (now.duration_since(*last)
                            > Duration::from_secs(Self::HEARTBEAT_TIMEOUT_SECONDS))
                        .then(|| prid.clone())
                    })
                    .collect::<Vec<_>>()
            };
            for prid in stale {
                tracing::warn!("heartbeat timeout: {prid}");
                let _ = self.stop_instance(&prid).await;
            }
        }
    }

    /// dial-in:收到 kernel 发来的 lifecycle.created → 实例化 routine + 存 init_kwargs
    /// + 调 on_created hook + 回报 created(带 modules)。
    ///
    /// modules 从 `RoutineInfo::modules(params)` 关联函数取（单一真理源,对齐 Python
    /// `on_created` 返回值语义）。kwargs 由 kernel 在 created 投递(start 不带),
    /// 对齐 Python `_init_kwargs` 模式。
    pub async fn handle_created(&self, peer_id: &str, msg: JsonObject) -> Result<(), String> {
        let rid = coerce_string(&msg, "id").unwrap_or_default();
        let name = coerce_string(&msg, "name").unwrap_or_default();
        let kwargs = coerce_object(&msg, "kwargs").unwrap_or_default();
        let prid = format!("{peer_id}:{rid}");

        let instance = self.runtime.resolve_instance(&prid, &name).await?;
        {
            let mut running_instances = self.runtime.running_instances.lock().await;
            if let Some(state) = running_instances.get_mut(&prid) {
                state.status = InvocationStatus::Created;
                state.init_kwargs = kwargs.clone();
            }
        }

        // on_created hook（轻量初始化,不返回 modules----modules 走关联函数）。
        // 异常不阻断:失败按空 modules 回报,让 start 阶段兜底。对齐 Python 语义。
        if let Err(exc) = instance.on_created(&rid, &kwargs).await {
            tracing::warn!(%prid, %name, %exc, "on_created failed");
        }

        // modules 从 factory 关联函数取（类方法语义,不建实例）。
        let modules = self
            .runtime
            .routines
            .get_routine(&name)
            .map(|factory| factory.modules(Some(&kwargs)))
            .unwrap_or_default();

        self.transport
            .send_lifecycle_created(&rid, &name, &modules, Some(peer_id))
            .await?;
        tracing::info!("[lifecycle] created: {name} ({rid}) modules={:?}", modules);
        Ok(())
    }

    pub async fn handle_start(&self, peer_id: &str, msg: JsonObject) -> Result<(), String> {
        let invocation = self
            .resolve_lifecycle_invocation(peer_id, msg.clone())
            .await?;
        {
            let mut running_instances = self.runtime.running_instances.lock().await;
            if let Some(state) = running_instances.get_mut(&invocation.prid) {
                state.status = InvocationStatus::Starting;
            }
        }
        self.touch_heartbeat(&invocation.prid).await;
        let available_routines = coerce_available_routines(&msg).or_else(|| {
            Some(
                self.runtime
                    .routines
                    .get_routines()
                    .into_iter()
                    .filter(|routine| !routine.is_passive())
                    .map(|routine| routine.routine_name().to_string())
                    .collect(),
            )
        });
        let cancellation = {
            let running_instances = self.runtime.running_instances.lock().await;
            running_instances
                .get(&invocation.prid)
                .map(|state| state.cancellation.clone())
                .unwrap_or_default()
        };
        let io: Arc<dyn RoutineIo> = self.transport.clone();
        let ctx = RunContext::new(RunContextOptions {
            id: invocation.rid.clone(),
            name: invocation.name.clone(),
            peer_id: peer_id.to_string(),
            io,
            control_type: coerce_string(&msg, "rtype").or_else(|| coerce_string(&msg, "type")),
            push_parent: coerce_object(&msg, "push_parent")
                .as_ref()
                .and_then(|value| ServerRuntime::coerce_parent(Some(value))),
            token_parent: coerce_object(&msg, "token_parent")
                .as_ref()
                .and_then(|value| ServerRuntime::coerce_parent(Some(value))),
            available_routines,
            cancellation,
        });
        {
            let mut running_instances = self.runtime.running_instances.lock().await;
            if let Some(state) = running_instances.get_mut(&invocation.prid) {
                state.ctx = Some(ctx.clone());
            }
        }
        let this = self.clone_for_task();
        let prid = invocation.prid.clone();
        let run_task = tokio::spawn(async move {
            if let Err(error) = this
                .run_instance(invocation.prid, invocation.instance, ctx, invocation.params)
                .await
            {
                tracing::error!("run instance failed: {error}");
            }
        });
        let mut running_instances = self.runtime.running_instances.lock().await;
        if let Some(state) = running_instances.get_mut(&prid) {
            state.run_task = Some(run_task);
        }
        Ok(())
    }

    pub async fn handle_stop(&self, peer_id: &str, msg: JsonObject) -> Result<(), String> {
        let rid = coerce_string(&msg, "id").unwrap_or_default();
        let prid = format!("{peer_id}:{rid}");
        let (instance, run_task) = {
            let mut running_instances = self.runtime.running_instances.lock().await;
            let Some(state) = running_instances.get_mut(&prid) else {
                return Ok(());
            };
            if state.finalized {
                return Ok(());
            }
            state.status = InvocationStatus::Stopping;
            state.cancellation.cancel();
            (state.instance.clone(), state.run_task.take())
        };
        if let Some(instance) = instance {
            let this = self.clone_for_task();
            tokio::spawn(async move {
                let _ = this.stop_runner(prid, instance, run_task).await;
            });
        }
        Ok(())
    }

    /// dial-in:收到 kernel 发来的 lifecycle.destroy → 清理 created 态(未 start)的 routine。
    pub async fn handle_destroy(&self, peer_id: &str, msg: JsonObject) -> Result<(), String> {
        let rid = coerce_string(&msg, "id").unwrap_or_default();
        let prid = format!("{peer_id}:{rid}");
        let mut running_instances = self.runtime.running_instances.lock().await;
        if let Some(state) = running_instances.get_mut(&prid) {
            if state.finalized {
                return Ok(());
            }
            state.cancellation.cancel();
            state.finalized = true;
            state.status = InvocationStatus::Stopped;
        }
        running_instances.remove(&prid);
        tracing::info!("[lifecycle] destroyed: {prid}");
        Ok(())
    }

    pub async fn handle_heartbeat(&self, peer_id: &str, msg: JsonObject) -> Result<(), String> {
        let rid = coerce_string(&msg, "id").unwrap_or_default();
        let prid = format!("{peer_id}:{rid}");
        let should_ack = {
            let running_instances = self.runtime.running_instances.lock().await;
            running_instances.get(&prid).is_some_and(|state| {
                !state.finalized
                    && matches!(
                        state.status,
                        InvocationStatus::Running | InvocationStatus::Stopping
                    )
            })
        };
        if !should_ack {
            tracing::warn!("heartbeat for non-running routine: {rid}");
            return Ok(());
        }
        self.touch_heartbeat(&prid).await;
        self.transport
            .send_lifecycle_heartbeat_ack(&rid, Some(peer_id))
            .await
    }

    pub async fn stop_instance(&self, prid: &str) -> Result<(), String> {
        let (instance, run_task) = {
            let mut running_instances = self.runtime.running_instances.lock().await;
            let Some(state) = running_instances.get_mut(prid) else {
                return Ok(());
            };
            state.status = InvocationStatus::Stopping;
            state.cancellation.cancel();
            (
                state.instance.clone(),
                state.run_task.take(),
            )
        };
        if let Some(instance) = instance {
            Self::abort_run_task(run_task).await;
            let _ = Self::stop_with_timeout(instance.clone()).await;
            self.finalize_invocation(
                prid,
                ControlDoneReason::Stop,
                None,
                None,
                "disconnect",
                None,
            )
            .await?;
        }
        Ok(())
    }

    async fn run_instance(
        &self,
        prid: String,
        instance: SharedRoutine,
        ctx: RunContext,
        params: JsonObject,
    ) -> Result<(), String> {
        ctx.ack_start().await?;
        {
            let mut running_instances = self.runtime.running_instances.lock().await;
            if let Some(state) = running_instances.get_mut(&prid) {
                state.status = InvocationStatus::Running;
                state.started = true;
            }
        }
        self.touch_heartbeat(&prid).await;
        let routine_name = ctx.name().to_string();

        // on_started hook（started 回报后、run 之前）。失败按 error 收口。
        if let Err(error) = instance.on_started().await {
            let error = error.to_string();
            let wire = ChainedError::leaf(&routine_name, &error).to_wire();
            let error_obj = serde_json::to_value(wire)
                .ok()
                .and_then(|value| value.as_object().cloned());
            tracing::warn!(%prid, %routine_name, %error, "on_started failed");
            self.finalize_invocation(
                &prid,
                ControlDoneReason::Error,
                None,
                error_obj,
                "error",
                Some(instance.clone()),
            )
            .await?;
            return Ok(());
        }

        // run 阶段失败按 Python 语义走 error 收口：on_stopped(reason='error') +
        // send_lifecycle_stopped(ERROR, error=ChainedError.leaf(name, msg)) + cleanup。
        let result = match self.run_instance_body(&prid, &instance, &ctx, &routine_name, params).await {
            Ok(result) => result,
            Err(error) => {
                let wire = ChainedError::leaf(&routine_name, &error).to_wire();
                let error_obj = serde_json::to_value(wire)
                    .ok()
                    .and_then(|value| value.as_object().cloned());
                tracing::warn!(%prid, %routine_name, %error, "routine run failed");
                self.finalize_invocation(
                    &prid,
                    ControlDoneReason::Error,
                    None,
                    error_obj,
                    "error",
                    Some(instance.clone()),
                )
                .await?;
                return Ok(());
            }
        };

        self.finalize_invocation(
            &prid,
            ControlDoneReason::Stop,
            result,
            None,
            "auto",
            Some(instance),
        )
        .await
    }

    /// `run_instance` 的成功路径主体：run → 自动收尾 yield / drain。
    /// 任一步失败返回 `Err(msg)`，由调用方收口成 error lifecycle.stopped。
    async fn run_instance_body(
        &self,
        _prid: &str,
        instance: &SharedRoutine,
        ctx: &RunContext,
        routine_name: &str,
        params: JsonObject,
    ) -> Result<Option<JsonValue>, String> {
        let run_result = instance.run(ctx.clone(), params).await;
        // routine yield 自动收尾(对齐 Python async generator 自然结束):
        // run 中调过 ctx.yield_item → 框架发 is_final=true;run 返回 Err 则带 error.
        // yield 模式下不再 drain RoutineOutput::Stream(Python yield 不返回值).
        if ctx.is_yield_used() {
            let (is_final_err, err_msg) = match &run_result {
                Ok(_) => (false, String::new()),
                Err(e) => (true, e.to_string()),
            };
            let mut payload = RawWireEvent::new(ROUTINE_YIELD)
                .with_field("id", Value::String(ctx.id().to_string()))
                .with_field("source_id", Value::String(ctx.id().to_string()))
                .with_field("is_final", Value::Bool(true));
            if is_final_err {
                let wire = ChainedError::leaf(routine_name, &err_msg).to_wire();
                payload = payload.with_field(
                    "error",
                    serde_json::to_value(wire).unwrap_or(Value::Null),
                );
            }
            ctx.send_raw_event(payload).await.map_err(|e| e)?;
            return Ok(None);
        }
        let output = run_result.map_err(|error| error.to_string())?;
        drain_run_result(output, ctx, routine_name)
            .await
            .map_err(|error| error.to_string())
    }

    async fn stop_runner(
        &self,
        prid: String,
        instance: SharedRoutine,
        run_task: Option<JoinHandle<()>>,
    ) -> Result<(), String> {
        Self::abort_run_task(run_task).await;
        let result = Self::stop_with_timeout(instance.clone()).await;
        self.finalize_invocation(
            &prid,
            ControlDoneReason::Stop,
            result,
            None,
            "stop",
            Some(instance),
        )
        .await
    }

    async fn abort_run_task(run_task: Option<JoinHandle<()>>) {
        if let Some(run_task) = run_task {
            run_task.abort();
            let _ = run_task.await;
        }
    }

    async fn stop_with_timeout(instance: SharedRoutine) -> Option<JsonValue> {
        tokio::time::timeout(
            Duration::from_secs(Self::STOP_TIMEOUT_SECONDS),
            async move { instance.stop().await },
        )
        .await
        .ok()
        .and_then(Result::ok)
        .flatten()
    }

    async fn resolve_lifecycle_invocation(
        &self,
        peer_id: &str,
        msg: JsonObject,
    ) -> Result<LifecycleInvocation, String> {
        let rid = coerce_string(&msg, "id").unwrap_or_default();
        let name = coerce_string(&msg, "name").unwrap_or_default();
        let prid = format!("{peer_id}:{rid}");
        // kernel 的 sendStart 不带 kwargs----回退用 created 时存的 init_kwargs。
        // 对齐 Python:handle_start 从 instance._init_kwargs 灌进 run()。
        let params = match coerce_object(&msg, "kwargs") {
            Some(kwargs) => kwargs,
            None => {
                let running_instances = self.runtime.running_instances.lock().await;
                running_instances
                    .get(&prid)
                    .map(|state| state.init_kwargs.clone())
                    .unwrap_or_default()
            }
        };
        let instance = match self.runtime.resolve_instance(&prid, &name).await {
            Ok(instance) => instance,
            Err(error) => {
                let wire = ChainedError::leaf(&name, &error).to_wire();
                let error_obj = serde_json::to_value(wire)
                    .ok()
                    .and_then(|value| value.as_object().cloned());
                self.transport
                    .send_lifecycle_stopped(
                        &rid,
                        ControlDoneReason::Error,
                        None,
                        error_obj,
                        Some(peer_id),
                    )
                    .await?;
                return Err(error);
            }
        };
        Ok(LifecycleInvocation {
            rid,
            name,
            params,
            prid,
            instance,
        })
    }

    async fn finalize_invocation(
        &self,
        prid: &str,
        reason: ControlDoneReason,
        result: Option<JsonValue>,
        error: Option<JsonObject>,
        done_reason: &str,
        instance: Option<SharedRoutine>,
    ) -> Result<(), String> {
        let (peer_id, rid) = {
            let mut running_instances = self.runtime.running_instances.lock().await;
            let Some(state) = running_instances.get_mut(prid) else {
                return Ok(());
            };
            if state.finalized {
                return Ok(());
            }
            state.finalized = true;
            state.status = if matches!(reason, ControlDoneReason::Error) {
                InvocationStatus::Error
            } else {
                InvocationStatus::Stopped
            };
            state.cancellation.cancel();
            let (peer_id, rid) = split_prid(prid);
            (peer_id.to_string(), rid.to_string())
        };
        if let Some(instance) = instance {
            // on_stopped hook（stopped 回报前）。异常不阻断 stopped 发送。
            if let Err(exc) = instance.on_stopped(done_reason, result.as_ref(), "").await {
                tracing::warn!(%prid, %exc, "on_stopped failed");
            }
            self.transport
                .send_lifecycle_stopped(&rid, reason, result, error, Some(&peer_id))
                .await?;
        } else {
            self.transport
                .send_lifecycle_stopped(&rid, reason, result, error, Some(&peer_id))
                .await?;
        }
        self.cleanup(prid).await;
        Ok(())
    }

    async fn cleanup(&self, prid: &str) {
        self.runtime.last_heartbeat.lock().await.remove(prid);
        self.runtime.running_instances.lock().await.remove(prid);
    }

    async fn touch_heartbeat(&self, prid: &str) {
        self.runtime
            .last_heartbeat
            .lock()
            .await
            .insert(prid.to_string(), Instant::now());
    }

    fn clone_for_task(&self) -> Arc<Self> {
        Arc::new(Self {
            runtime: self.runtime.clone(),
            transport: self.transport.clone(),
        })
    }
}

struct LifecycleInvocation {
    rid: String,
    name: String,
    params: JsonObject,
    prid: String,
    instance: SharedRoutine,
}

fn coerce_string(msg: &JsonObject, key: &str) -> Option<String> {
    msg.get(key)
        .and_then(Value::as_str)
        .map(ToString::to_string)
}

fn coerce_object(msg: &JsonObject, key: &str) -> Option<JsonObject> {
    msg.get(key).and_then(Value::as_object).cloned()
}

fn coerce_available_routines(msg: &JsonObject) -> Option<Vec<String>> {
    let items = msg.get("available_routines")?.as_array()?;
    if items.iter().any(|item| item.as_str() == Some("*")) {
        return None;
    }
    Some(
        items
            .iter()
            .filter_map(Value::as_str)
            .map(ToString::to_string)
            .collect(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        core::{
            RoutineOutput, RoutineRegistry, WireRoutine,
            WireRoutineFactory,
        },
        protocol::events::{LIFECYCLE_START, LIFECYCLE_STOP},
        server::{OutboundTransport, ServerRuntime},
    };
    use async_trait::async_trait;
    use std::sync::{Arc as StdArc, Mutex as StdMutex};
    use tokio::sync::mpsc;

    #[derive(Clone)]
    struct RecordingFactory {
        events: StdArc<StdMutex<Vec<String>>>,
    }

    impl WireRoutineFactory for RecordingFactory {
        fn routine_name(&self) -> &str {
            "recording"
        }

        fn create(&self) -> Box<dyn WireRoutine> {
            Box::new(RecordingRoutine {
                events: self.events.clone(),
            })
        }
    }

    struct RecordingRoutine {
        events: StdArc<StdMutex<Vec<String>>>,
    }

    #[async_trait]
    impl WireRoutine for RecordingRoutine {
        async fn run(
            &self,
            _ctx: RunContext,
            _params: JsonObject,
        ) -> Result<RoutineOutput, crate::core::routine::RoutineError> {
            self.events.lock().unwrap().push("start".to_string());
            Ok(RoutineOutput::Empty)
        }
    }

    #[derive(Clone)]
    struct BlockingFactory {
        events: StdArc<StdMutex<Vec<String>>>,
    }

    impl WireRoutineFactory for BlockingFactory {
        fn routine_name(&self) -> &str {
            "blocking"
        }

        fn create(&self) -> Box<dyn WireRoutine> {
            Box::new(BlockingRoutine {
                events: self.events.clone(),
            })
        }
    }

    struct BlockingRoutine {
        events: StdArc<StdMutex<Vec<String>>>,
    }

    #[async_trait]
    impl WireRoutine for BlockingRoutine {
        async fn run(
            &self,
            _ctx: RunContext,
            _params: JsonObject,
        ) -> Result<RoutineOutput, crate::core::routine::RoutineError> {
            self.events.lock().unwrap().push("start".to_string());
            tokio::time::sleep(Duration::from_secs(60)).await;
            self.events.lock().unwrap().push("finished".to_string());
            Ok(RoutineOutput::Empty)
        }

        async fn stop(&self) -> Result<Option<JsonValue>, crate::core::routine::RoutineError> {
            self.events.lock().unwrap().push("stop".to_string());
            Ok(None)
        }
    }

    #[derive(Clone)]
    struct FailingFactory;

    impl WireRoutineFactory for FailingFactory {
        fn routine_name(&self) -> &str {
            "failing"
        }

        fn create(&self) -> Box<dyn WireRoutine> {
            Box::new(FailingRoutine)
        }
    }

    struct FailingRoutine;

    #[async_trait]
    impl WireRoutine for FailingRoutine {
        async fn run(
            &self,
            _ctx: RunContext,
            _params: JsonObject,
        ) -> Result<RoutineOutput, crate::core::routine::RoutineError> {
            Err(crate::core::routine::RoutineError::Message(
                "boom: music dir not found".to_string(),
            ))
        }
    }

    #[tokio::test]
    async fn start_failure_sends_error_stopped_with_chained_error() {
        // start() 报错必须走 error 收口：发 lifecycle.stopped(reason=error, error=…)
        // 回给调用方，而不是 ? 提前 return 让错误被吞掉、调用方收不到 stopped。
        // 对齐 Python lifecycle `except Exception` 分支。
        let registry = RoutineRegistry::from_factories([StdArc::new(FailingFactory)
            as StdArc<dyn WireRoutineFactory>]);
        let runtime = Arc::new(ServerRuntime::new(registry, Vec::new(), Vec::new()));
        let transport = Arc::new(OutboundTransport::new(runtime.clone()));
        let lifecycle = LifecycleManager::new(runtime.clone(), transport);
        let (tx, mut rx) = mpsc::unbounded_channel();
        runtime
            .peer_to_queue
            .lock()
            .await
            .insert("peer".to_string(), tx);

        let mut start = JsonObject::new();
        start.insert(
            "event".to_string(),
            Value::String(LIFECYCLE_START.to_string()),
        );
        start.insert("id".to_string(), Value::String("rid".to_string()));
        start.insert("name".to_string(), Value::String("failing".to_string()));
        start.insert("kwargs".to_string(), Value::Object(JsonObject::new()));
        lifecycle.handle_start("peer", start).await.unwrap();

        // start 立即失败，stopped 应很快到达。
        let mut stopped_event: Option<JsonObject> = None;
        let deadline = tokio::time::Instant::now() + Duration::from_secs(2);
        loop {
            tokio::select! {
                biased;
                _ = tokio::time::sleep_until(deadline) => break,
                message = rx.recv() => {
                    let event = crate::protocol::struct_to_json(&message.expect("event"));
                    if event.get("event").and_then(Value::as_str) == Some("lifecycle.stopped") {
                        stopped_event = Some(event);
                        break;
                    }
                }
            }
        }

        let stopped = stopped_event.expect("lifecycle.stopped should arrive after start failure");
        assert_eq!(
            stopped.get("reason").and_then(Value::as_str),
            Some("ERROR"),
            "start failure must stop with reason=ERROR"
        );
        let error = stopped
            .get("error")
            .and_then(Value::as_object)
            .expect("stopped must carry an error object");
        assert_eq!(
            error.get("name").and_then(Value::as_str),
            Some("failing"),
            "error.name should be the routine name"
        );
        assert!(
            error
                .get("msg")
                .and_then(Value::as_str)
                .is_some_and(|msg| msg.contains("music dir not found")),
            "error.msg should carry the start failure detail"
        );
    }

    #[tokio::test]
    async fn stop_aborts_running_start_task_before_calling_stop() {
        let events = StdArc::new(StdMutex::new(Vec::new()));
        let registry = RoutineRegistry::from_factories([StdArc::new(BlockingFactory {
            events: events.clone(),
        })
            as StdArc<dyn WireRoutineFactory>]);
        let runtime = Arc::new(ServerRuntime::new(registry, Vec::new(), Vec::new()));
        let transport = Arc::new(OutboundTransport::new(runtime.clone()));
        let lifecycle = LifecycleManager::new(runtime.clone(), transport);
        let (tx, _rx) = mpsc::unbounded_channel();
        runtime
            .peer_to_queue
            .lock()
            .await
            .insert("peer".to_string(), tx);

        let mut start = JsonObject::new();
        start.insert(
            "event".to_string(),
            Value::String(LIFECYCLE_START.to_string()),
        );
        start.insert("id".to_string(), Value::String("rid".to_string()));
        start.insert("name".to_string(), Value::String("blocking".to_string()));
        start.insert("kwargs".to_string(), Value::Object(JsonObject::new()));
        lifecycle.handle_start("peer", start).await.unwrap();

        tokio::time::sleep(Duration::from_millis(50)).await;

        let mut stop = JsonObject::new();
        stop.insert(
            "event".to_string(),
            Value::String(LIFECYCLE_STOP.to_string()),
        );
        stop.insert("id".to_string(), Value::String("rid".to_string()));
        lifecycle.handle_stop("peer", stop).await.unwrap();

        tokio::time::sleep(Duration::from_millis(100)).await;

        let recorded = events.lock().unwrap().clone();
        assert_eq!(recorded, vec!["start", "stop"]);
    }
}
