//! routine 间通信的 handler 类型与声明辅助。
//!
//! 对齐 Python `@request` / `@subscribe` 装饰器 + Go `ctx.HandleRequest` /
//! `ctx.HandleSubscribe`:Rust 无装饰器,routine 在 `request_specs()` /
//! `subscribe_specs()` 里声明(返回闭包,捕获自身 state 的 Arc),lifecycle
//! 在 created 时收集进 runtime 的分发表。

use crate::{
    core::{routine::RoutineError, RunContext},
    protocol::{events::MESSAGE_STREAM_OPEN, JsonObject, JsonValue},
};
use futures::future::BoxFuture;
use std::sync::Arc;

/// StreamRequest 用的 wire 事件名(message.stream_open)。
pub const STREAM_OPEN_EVENT: &str = MESSAGE_STREAM_OPEN;

/// 发送方 routine 引用(on_message / request / subscribe 的 source 参数)。
/// 对齐 Python `RoutineSource(id, name)` / Go `RoutineSource{ID, Name}`。
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RoutineSource {
    pub id: String,
    pub name: Option<String>,
}

impl RoutineSource {
    pub fn new(id: impl Into<String>) -> Self {
        Self {
            id: id.into(),
            name: None,
        }
    }
}

/// @request handler:收到 event 的 p2p req 时调用,返回值作为 req_reply 的
/// data 回给 source;返回 Err 则回 ok=false。
pub type RequestHandler = Arc<
    dyn Fn(RoutineSource, JsonObject) -> BoxFuture<'static, Result<JsonValue, RoutineError>>
        + Send
        + Sync,
>;

/// @subscribe handler:收到匹配 (namespace, topic) 的 pubsub.delivered 时调用。
pub type SubscribeHandler = Arc<
    dyn Fn(RoutineSource, JsonValue) -> BoxFuture<'static, Result<(), RoutineError>>
        + Send
        + Sync,
>;

/// @request 声明项:event 名 + handler。
#[derive(Clone)]
pub struct RequestSpec {
    pub event: String,
    pub handler: RequestHandler,
}

/// @subscribe 声明项:(namespace, topic) + handler。namespace 空串为默认空间。
#[derive(Clone)]
pub struct SubscribeSpec {
    pub namespace: String,
    pub topic: String,
    pub handler: SubscribeHandler,
}

/// 构造 @request 声明。闭包返回 `async move { ... }.boxed()`。
pub fn request_spec<F>(event: impl Into<String>, handler: F) -> RequestSpec
where
    F: Fn(
            RoutineSource,
            JsonObject,
        ) -> BoxFuture<'static, Result<JsonValue, RoutineError>>
        + Send
        + Sync
        + 'static,
{
    RequestSpec {
        event: event.into(),
        handler: Arc::new(handler),
    }
}

/// 构造 @subscribe 声明。闭包返回 `async move { ... }.boxed()`。
pub fn subscribe_spec<F>(
    topic: impl Into<String>,
    namespace: impl Into<String>,
    handler: F,
) -> SubscribeSpec
where
    F: Fn(RoutineSource, JsonValue) -> BoxFuture<'static, Result<(), RoutineError>>
        + Send
        + Sync
        + 'static,
{
    SubscribeSpec {
        namespace: namespace.into(),
        topic: topic.into(),
        handler: Arc::new(handler),
    }
}

/// 命名空间辅助:publish/unsubscribe 自动带 ns 前缀语义(对齐 Python
/// `routine.namespace(ns)`)。
#[derive(Clone)]
pub struct Namespace {
    ctx: RunContext,
    namespace: String,
}

impl Namespace {
    pub fn new(ctx: RunContext, namespace: impl Into<String>) -> Self {
        Self {
            ctx,
            namespace: namespace.into(),
        }
    }

    pub async fn publish(&self, event: &str, data: Option<JsonObject>) -> Result<(), String> {
        self.ctx.publish(event, data, Some(&self.namespace)).await
    }

    pub async fn unsubscribe(&self, event: &str) -> Result<(), String> {
        self.ctx.unsubscribe(&self.namespace, event).await
    }
}
