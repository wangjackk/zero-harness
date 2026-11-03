//! 父侧子 routine 句柄：控制、等待、生命周期通知与 yield 流共享同一状态。
//!
//! hub 按 child_id 持有句柄并分发 kernel 中转的 started/stopped/rejected/
//! yielded。句柄只弱引用 IO，避免 runtime → handle → transport → runtime
//! 形成强引用环。

use super::context::{RoutineIo, RunContext};
use crate::protocol::{
    events::{MESSAGE_SEND, ROUTINE_DESTROY, ROUTINE_START, ROUTINE_STOP},
    JsonObject, JsonValue, RawWireEvent, TerminationReason,
};
use futures::{future::BoxFuture, Stream};
use serde_json::Value;
use std::{
    fmt,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex, Weak,
    },
    time::Duration,
};
use tokio::sync::{mpsc, oneshot, Notify};

const ACK_TIMEOUT: Duration = Duration::from_secs(30);

pub type HandleCallback =
    Arc<dyn Fn(RoutineHandle) -> BoxFuture<'static, ()> + Send + Sync + 'static>;

#[derive(Clone, Debug)]
pub struct RoutineTermination {
    pub result: JsonValue,
    pub reason: TerminationReason,
    pub by: Option<String>,
    pub detail: String,
}

struct HandleState {
    started: bool,
    done: bool,
    priority: Option<i64>,
    result: JsonValue,
    error: Option<String>,
    reason: Option<TerminationReason>,
    by: Option<String>,
    detail: String,
    invalidated: Option<String>,
    body_done: bool,
    body_closed: bool,
}

impl Default for HandleState {
    fn default() -> Self {
        Self {
            started: false,
            done: false,
            priority: None,
            result: Value::Null,
            error: None,
            reason: None,
            by: None,
            detail: String::new(),
            invalidated: None,
            body_done: false,
            body_closed: false,
        }
    }
}

struct AckSlot {
    id: u64,
    sender: oneshot::Sender<Result<(), String>>,
}

enum BodyEvent {
    Item(JsonValue),
    Error(String),
    Done,
}

struct HandleInner {
    id: String,
    name: String,
    modules: Option<Vec<String>>,
    parent_id: String,
    peer_id: String,
    io: Weak<dyn RoutineIo>,
    state: Mutex<HandleState>,
    started: Notify,
    done: Notify,
    ack: Mutex<Option<AckSlot>>,
    ack_sequence: AtomicU64,
    body_sender: mpsc::UnboundedSender<BodyEvent>,
    body_receiver: tokio::sync::Mutex<mpsc::UnboundedReceiver<BodyEvent>>,
    on_started: Mutex<Option<HandleCallback>>,
    on_stopped: Mutex<Option<HandleCallback>>,
}

/// `create()` 返回的父侧句柄。克隆后仍观察同一次子调用。
#[derive(Clone)]
pub struct RoutineHandle {
    inner: Arc<HandleInner>,
}

impl fmt::Debug for RoutineHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let state = self.state();
        let phase = if state.done {
            if state.error.is_some() { "error" } else { "done" }
        } else if state.started {
            "started"
        } else {
            "pending"
        };
        formatter
            .debug_struct("RoutineHandle")
            .field("id", &self.inner.id)
            .field("name", &self.inner.name)
            .field("state", &phase)
            .finish()
    }
}

impl RoutineHandle {
    #[doc(hidden)]
    pub fn new(
        child_id: impl Into<String>,
        name: impl Into<String>,
        modules: Option<Vec<String>>,
        ctx: &RunContext,
    ) -> Self {
        let (body_sender, body_receiver) = mpsc::unbounded_channel();
        Self {
            inner: Arc::new(HandleInner {
                id: child_id.into(),
                name: name.into(),
                modules,
                parent_id: ctx.id().to_string(),
                peer_id: ctx.peer_id().to_string(),
                io: Arc::downgrade(&ctx.io),
                state: Mutex::new(HandleState::default()),
                started: Notify::new(),
                done: Notify::new(),
                ack: Mutex::new(None),
                ack_sequence: AtomicU64::new(0),
                body_sender,
                body_receiver: tokio::sync::Mutex::new(body_receiver),
                on_started: Mutex::new(None),
                on_stopped: Mutex::new(None),
            }),
        }
    }

    fn state(&self) -> std::sync::MutexGuard<'_, HandleState> {
        self.inner.state.lock().expect("routine handle state poisoned")
    }

    pub fn id(&self) -> &str {
        &self.inner.id
    }

    pub fn name(&self) -> &str {
        &self.inner.name
    }

    pub fn modules(&self) -> Option<Vec<String>> {
        self.inner.modules.clone()
    }

    pub fn priority(&self) -> Option<i64> {
        self.state().priority
    }

    pub fn result(&self) -> JsonValue {
        self.state().result.clone()
    }

    pub fn error(&self) -> Option<String> {
        self.state().error.clone()
    }

    pub fn reason(&self) -> Option<TerminationReason> {
        self.state().reason.clone()
    }

    pub fn by(&self) -> Option<String> {
        self.state().by.clone()
    }

    pub fn detail(&self) -> String {
        self.state().detail.clone()
    }

    pub fn is_started(&self) -> bool {
        self.state().started
    }

    pub fn is_done(&self) -> bool {
        self.state().done
    }

    /// 注册父侧 started 通知；回调以独立 task 执行，不阻塞事件路由。
    pub fn on_started<F>(&self, callback: F)
    where
        F: Fn(RoutineHandle) -> BoxFuture<'static, ()> + Send + Sync + 'static,
    {
        *self
            .inner
            .on_started
            .lock()
            .expect("routine handle callback poisoned") = Some(Arc::new(callback));
    }

    /// 注册父侧 stopped 通知；回调以独立 task 执行，不阻塞事件路由。
    pub fn on_stopped<F>(&self, callback: F)
    where
        F: Fn(RoutineHandle) -> BoxFuture<'static, ()> + Send + Sync + 'static,
    {
        *self
            .inner
            .on_stopped
            .lock()
            .expect("routine handle callback poisoned") = Some(Arc::new(callback));
    }

    fn io(&self) -> Result<Arc<dyn RoutineIo>, String> {
        self.inner
            .io
            .upgrade()
            .ok_or_else(|| format!("{self:?}: routine IO is no longer available"))
    }

    async fn send_raw(&self, payload: RawWireEvent) -> Result<(), String> {
        self.io()?
            .send_raw_event(payload, Some(&self.inner.peer_id))
            .await
    }

    fn require_active(&self, op: &str) -> Result<(), String> {
        let state = self.state();
        if let Some(error) = &state.invalidated {
            return Err(format!(
                "RoutineHandle({} id={}): invalidated by {error}; cannot {op}",
                self.inner.name, self.inner.id
            ));
        }
        if state.done {
            return Err(format!(
                "RoutineHandle({} id={}): already done (reason={:?}), cannot {op}",
                self.inner.name, self.inner.id, state.reason
            ));
        }
        Ok(())
    }

    async fn send_and_wait(&self, payload: RawWireEvent) -> Result<(), String> {
        let id = self.inner.ack_sequence.fetch_add(1, Ordering::SeqCst) + 1;
        let (sender, receiver) = oneshot::channel();
        {
            let mut ack = self.inner.ack.lock().expect("routine handle ack poisoned");
            if ack.is_some() {
                return Err(format!("{self:?}: another start/stop is in flight"));
            }
            *ack = Some(AckSlot { id, sender });
        }

        let result = match self.send_raw(payload).await {
            Ok(()) => match tokio::time::timeout(ACK_TIMEOUT, receiver).await {
                Ok(Ok(result)) => result,
                Ok(Err(_)) => Err(format!("{self:?}: ack waiter closed")),
                Err(_) => Err(format!("{self:?}: ack timeout")),
            },
            Err(error) => Err(error),
        };

        let mut ack = self.inner.ack.lock().expect("routine handle ack poisoned");
        if ack.as_ref().is_some_and(|slot| slot.id == id) {
            *ack = None;
        }
        result
    }

    #[doc(hidden)]
    pub fn resolve_ack(&self, result: Result<(), String>) {
        let sender = self
            .inner
            .ack
            .lock()
            .expect("routine handle ack poisoned")
            .take()
            .map(|slot| slot.sender);
        if let Some(sender) = sender {
            let _ = sender.send(result);
        }
    }

    async fn start_common(&self, try_start: bool, priority: i64) -> Result<(), String> {
        self.require_active("start")?;
        let mut event = RawWireEvent::new(ROUTINE_START)
            .with_field("child_id", Value::String(self.id().to_string()));
        if try_start {
            event = event.with_field("try", Value::Bool(true));
        }
        if priority != 10 {
            event = event.with_field("priority", Value::Number(priority.into()));
        }
        let result = self.send_and_wait(event).await;
        match result {
            Ok(()) => {
                self.state().priority = Some(priority);
                Ok(())
            }
            Err(error) => {
                if !try_start {
                    self.invalidate(error.clone());
                    if let Ok(io) = self.io() {
                        io.pop_handle(self.id()).await;
                    }
                }
                Err(error)
            }
        }
    }

    /// 启动子 routine，并等待 kernel 的 lifecycle.started 或拒绝回执。
    pub async fn start(&self, priority: i64) -> Result<(), String> {
        self.start_common(false, priority).await
    }

    /// 礼貌启动（priority=0，不抢占）；失败保留 created 态，可稍后重试。
    pub async fn try_start(&self) -> Result<(), String> {
        self.start_common(true, 0).await
    }

    /// 停止子 routine，并等待 lifecycle.stopped 确认。
    pub async fn stop(&self) -> Result<(), String> {
        self.require_active("stop")?;
        self.send_and_wait(
            RawWireEvent::new(ROUTINE_STOP)
                .with_field("child_id", Value::String(self.id().to_string())),
        )
        .await
    }

    /// 只发送停止请求，不等待回执。
    pub async fn fire_stop(&self) -> Result<(), String> {
        self.require_active("stop")?;
        self.send_raw(
            RawWireEvent::new(ROUTINE_STOP)
                .with_field("child_id", Value::String(self.id().to_string())),
        )
        .await
    }

    /// 撤销 created 态子 routine，并等待 DESTROYED 终止回执。
    pub async fn destroy(&self) -> Result<(), String> {
        self.require_active("destroy")?;
        self.send_and_wait(
            RawWireEvent::new(ROUTINE_DESTROY)
                .with_field("child_id", Value::String(self.id().to_string())),
        )
        .await
    }

    /// 只发送 created 态撤销请求，不等待回执。
    pub async fn fire_destroy(&self) -> Result<(), String> {
        self.require_active("destroy")?;
        self.send_raw(
            RawWireEvent::new(ROUTINE_DESTROY)
                .with_field("child_id", Value::String(self.id().to_string())),
        )
        .await
    }

    /// 给子 routine 发送定向消息；created 态即可发送。
    pub async fn send(&self, data: Option<JsonObject>) -> Result<(), String> {
        self.send_raw(
            RawWireEvent::new(MESSAGE_SEND)
                .with_field(
                    "target_ids",
                    Value::Array(vec![Value::String(self.id().to_string())]),
                )
                .with_data(&Value::Object(data.unwrap_or_default()))
                .with_field("source_id", Value::String(self.inner.parent_id.clone())),
        )
        .await
    }

    pub async fn wait_started(&self) {
        loop {
            let notified = self.inner.started.notified();
            if self.is_started() {
                return;
            }
            notified.await;
        }
    }

    /// 等终止并保留 reason/by/detail；error 非空时仍按 Err 返回。
    pub async fn wait_detailed(&self) -> Result<RoutineTermination, String> {
        loop {
            let notified = self.inner.done.notified();
            {
                let state = self.state();
                if let Some(error) = &state.invalidated {
                    return Err(error.clone());
                }
                if state.done {
                    if let Some(error) = &state.error {
                        return Err(error.clone());
                    }
                    return Ok(RoutineTermination {
                        result: state.result.clone(),
                        reason: state.reason.clone().unwrap_or(TerminationReason::Unknown),
                        by: state.by.clone(),
                        detail: state.detail.clone(),
                    });
                }
            }
            notified.await;
        }
    }

    /// 等终止并只取 result；等待/抛错策略与其他 SDK 保持一致。
    pub async fn wait(&self) -> Result<JsonValue, String> {
        self.wait_detailed()
            .await
            .map(|termination| termination.result)
    }

    /// 消费子 routine 的 yield 项；Done 后重复调用仍返回 None。
    pub async fn next(&self) -> Option<Result<JsonValue, String>> {
        if self.state().body_closed {
            return None;
        }
        let event = self.inner.body_receiver.lock().await.recv().await;
        match event {
            Some(BodyEvent::Item(value)) => Some(Ok(value)),
            Some(BodyEvent::Error(error)) => Some(Err(error)),
            Some(BodyEvent::Done) | None => {
                self.state().body_closed = true;
                None
            }
        }
    }

    pub fn into_stream(self) -> impl Stream<Item = Result<JsonValue, String>> {
        futures::stream::unfold(self, |handle| async move {
            handle.next().await.map(|item| (item, handle))
        })
    }

    #[doc(hidden)]
    pub fn notify_started(&self) {
        let should_fire = {
            let mut state = self.state();
            if state.started || state.done {
                false
            } else {
                state.started = true;
                true
            }
        };
        if should_fire {
            self.inner.started.notify_waiters();
            let callback = self
                .inner
                .on_started
                .lock()
                .expect("routine handle callback poisoned")
                .clone();
            Self::fire_callback(callback, self.clone());
        }
    }

    #[doc(hidden)]
    #[allow(clippy::too_many_arguments)]
    pub fn notify_done(
        &self,
        result: JsonValue,
        error: Option<String>,
        reason: TerminationReason,
        by: Option<String>,
        detail: String,
    ) {
        {
            let mut state = self.state();
            if state.done {
                return;
            }
            state.result = result;
            state.error = error;
            state.reason = Some(reason);
            state.by = by;
            state.detail = detail;
            state.started = true;
            state.done = true;
        }
        self.inner.started.notify_waiters();
        self.inner.done.notify_waiters();
        self.finish_body();
        let callback = self
            .inner
            .on_stopped
            .lock()
            .expect("routine handle callback poisoned")
            .clone();
        Self::fire_callback(callback, self.clone());
    }

    #[doc(hidden)]
    pub fn invalidate(&self, error: String) {
        {
            let mut state = self.state();
            if state.done || state.invalidated.is_some() {
                return;
            }
            state.invalidated = Some(error);
        }
        self.inner.done.notify_waiters();
        self.finish_body();
    }

    #[doc(hidden)]
    pub fn on_yield_chunk(&self, data: JsonValue, is_final: bool, error: Option<String>) {
        let mut state = self.state();
        if state.body_done {
            return;
        }
        if let Some(error) = error {
            state.body_done = true;
            drop(state);
            let _ = self.inner.body_sender.send(BodyEvent::Error(error));
            let _ = self.inner.body_sender.send(BodyEvent::Done);
            return;
        }
        if is_final {
            state.body_done = true;
            drop(state);
            let _ = self.inner.body_sender.send(BodyEvent::Done);
            return;
        }
        drop(state);
        let _ = self.inner.body_sender.send(BodyEvent::Item(data));
    }

    fn finish_body(&self) {
        let mut state = self.state();
        if state.body_done {
            return;
        }
        state.body_done = true;
        drop(state);
        let _ = self.inner.body_sender.send(BodyEvent::Done);
    }

    fn fire_callback(callback: Option<HandleCallback>, handle: RoutineHandle) {
        if let Some(callback) = callback {
            tokio::spawn(async move {
                callback(handle).await;
            });
        }
    }
}
