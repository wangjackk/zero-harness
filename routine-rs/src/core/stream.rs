use crate::{
    core::{messaging::STREAM_OPEN_EVENT, RunContext},
    protocol::{JsonObject, JsonValue},
};
use futures::Stream;
use serde_json::Value;
use std::{
    pin::Pin,
    task::{Context, Poll},
};
use tokio::sync::mpsc;
use uuid::Uuid;

pub struct StreamReader {
    receiver: mpsc::UnboundedReceiver<Result<JsonValue, String>>,
    sender: mpsc::UnboundedSender<Result<JsonValue, String>>,
    finalized: bool,
}

impl StreamReader {
    pub fn new() -> Self {
        let (sender, receiver) = mpsc::unbounded_channel();
        Self {
            receiver,
            sender,
            finalized: false,
        }
    }

    pub fn push(&self, value: JsonValue) {
        let _ = self.sender.send(Ok(value));
    }

    pub fn finalize(&mut self, state: &str, error: Option<String>) {
        if self.finalized {
            return;
        }
        self.finalized = true;
        if state == "error" || state == "cancelled" {
            let _ = self
                .sender
                .send(Err(error.unwrap_or_else(|| state.to_string())));
        }
    }
}

impl Default for StreamReader {
    fn default() -> Self {
        Self::new()
    }
}

impl Stream for StreamReader {
    type Item = Result<JsonValue, String>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.receiver.poll_recv(cx)
    }
}

pub struct StreamRequest {
    ctx: RunContext,
    event: String,
    data: Option<JsonObject>,
    to: String,
    stream_id: String,
}

impl StreamRequest {
    pub fn new(
        ctx: RunContext,
        event: impl Into<String>,
        data: Option<JsonObject>,
        to: impl Into<String>,
    ) -> Self {
        Self {
            ctx,
            event: event.into(),
            data,
            to: to.into(),
            stream_id: Uuid::new_v4().simple().to_string(),
        }
    }

    pub fn stream_id(&self) -> &str {
        &self.stream_id
    }

    pub async fn open(self) -> Result<StreamReader, String> {
        let reader = StreamReader::new();
        let mut payload = JsonObject::new();
        payload.insert("event".to_string(), Value::String(self.event));
        payload.insert("__stream_id__".to_string(), Value::String(self.stream_id));
        payload.insert(
            "__reply_to__".to_string(),
            Value::String(self.ctx.id().to_string()),
        );
        payload.insert(
            "data".to_string(),
            Value::Object(self.data.unwrap_or_default()),
        );
        self.ctx
            .send(STREAM_OPEN_EVENT, Some(payload), [self.to])
            .await?;
        Ok(reader)
    }
}
