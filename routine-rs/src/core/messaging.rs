use crate::{
    core::RunContext,
    protocol::{JsonObject, JsonValue},
};
use async_trait::async_trait;
use std::sync::Arc;
use thiserror::Error;

pub const REQ_REPLY_EVENT: &str = "__req_reply__";
pub const STREAM_OPEN_EVENT: &str = "__stream_open__";
pub const STREAM_DATA_EVENT: &str = "__stream_data__";

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PeerRef {
    pub id: String,
    pub name: String,
}

#[async_trait]
pub trait MessageHandler: Send + Sync {
    async fn handle(
        &self,
        source: PeerRef,
        payload: JsonObject,
    ) -> Result<Option<JsonValue>, String>;
}

#[async_trait]
impl<F, Fut> MessageHandler for F
where
    F: Fn(PeerRef, JsonObject) -> Fut + Send + Sync,
    Fut: std::future::Future<Output = Result<Option<JsonValue>, String>> + Send,
{
    async fn handle(
        &self,
        source: PeerRef,
        payload: JsonObject,
    ) -> Result<Option<JsonValue>, String> {
        self(source, payload).await
    }
}

#[derive(Clone)]
pub struct Subscription {
    pub event: String,
    pub raw_event: String,
    pub namespace: String,
    pub subscribe_wire: bool,
    handler: Arc<dyn MessageHandler>,
}

impl Subscription {
    pub fn new(
        event: impl Into<String>,
        handler: Arc<dyn MessageHandler>,
        subscribe_wire: bool,
        namespace: impl Into<String>,
    ) -> Self {
        let event = event.into();
        Self {
            raw_event: event.clone(),
            event,
            namespace: namespace.into(),
            subscribe_wire,
            handler,
        }
    }

    pub async fn dispatch(
        &self,
        source: PeerRef,
        payload: JsonObject,
    ) -> Result<Option<JsonValue>, String> {
        self.handler.handle(source, payload).await
    }
}

#[derive(Clone, Debug)]
pub struct HandlerMeta {
    pub event: String,
    pub namespace: Option<String>,
    pub handler_type: HandlerType,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum HandlerType {
    Event,
    Request,
    Stream,
    Subscribe,
}

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

#[derive(Debug, Error)]
#[error("{0}")]
pub struct ReqError(pub String);

#[derive(Debug, Error)]
#[error("{0}")]
pub struct ReqTimeout(pub String);
