use crate::{
    protocol::{
        events::{
            ROUTINE_SUBMITTED, ROUTINE_YIELDED, LIFECYCLE_HEARTBEAT,
            LIFECYCLE_START, LIFECYCLE_STOP, LIFECYCLE_STOPPED, P2P_DELIVERED,
            PUBSUB_DELIVERED, SHELL_REQ_REPLY,
        },
        JsonObject,
    },
    server::{LifecycleManager, ServerRuntime, ShellReqManager},
};
use serde_json::Value;
use std::sync::Arc;

pub struct BusinessEventRouter {
    runtime: Arc<ServerRuntime>,
    lifecycle: Arc<LifecycleManager>,
    shell_req: Arc<ShellReqManager>,
}

impl BusinessEventRouter {
    pub fn new(
        runtime: Arc<ServerRuntime>,
        lifecycle: Arc<LifecycleManager>,
        shell_req: Arc<ShellReqManager>,
    ) -> Self {
        Self {
            runtime,
            lifecycle,
            shell_req,
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
            SHELL_REQ_REPLY => self.shell_req.handle_reply(msg).await,
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

    pub async fn dispatch_business_event(
        &self,
        peer_id: &str,
        msg: JsonObject,
    ) -> Result<(), String> {
        let event = msg
            .get("event")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();

        let rid = match event.as_str() {
            P2P_DELIVERED => msg
                .get("target_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            PUBSUB_DELIVERED => msg
                .get("subscriber_id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            _ => String::new(),
        };

        if rid.is_empty() {
            tracing::warn!("business event missing routing id: event={event}");
            return Ok(());
        }
        if self.runtime.resolve_running(peer_id, &rid).await.is_some() {
            tracing::debug!("business event routed: event={event} rid={rid}");
        } else if event == ROUTINE_YIELDED {
            tracing::debug!("drop {event} rid={rid}: parent routine already exited");
        } else {
            tracing::warn!("no running routine for event={event} rid={rid}");
        }
        Ok(())
    }
}
