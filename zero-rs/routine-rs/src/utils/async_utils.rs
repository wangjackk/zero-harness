use std::{future::Future, time::Duration};
use thiserror::Error;
use tokio::sync::mpsc;

#[derive(Debug, Error)]
#[error("{0}")]
pub struct TimeoutError(pub String);

#[derive(Debug)]
pub struct AsyncQueue<T> {
    sender: mpsc::UnboundedSender<T>,
    receiver: mpsc::UnboundedReceiver<T>,
}

impl<T> AsyncQueue<T> {
    pub fn new() -> Self {
        let (sender, receiver) = mpsc::unbounded_channel();
        Self { sender, receiver }
    }

    pub fn sender(&self) -> mpsc::UnboundedSender<T> {
        self.sender.clone()
    }

    pub fn push(&self, item: T) -> Result<(), mpsc::error::SendError<T>> {
        self.sender.send(item)
    }

    pub async fn recv(&mut self) -> Option<T> {
        self.receiver.recv().await
    }
}

impl<T> Default for AsyncQueue<T> {
    fn default() -> Self {
        Self::new()
    }
}

pub async fn timeout<T>(
    future: impl Future<Output = T>,
    duration: Duration,
    message: impl Into<String>,
) -> Result<T, TimeoutError> {
    tokio::time::timeout(duration, future)
        .await
        .map_err(|_| TimeoutError(message.into()))
}

pub fn spawn_logged(future: impl Future<Output = ()> + Send + 'static) {
    tokio::spawn(async move {
        future.await;
    });
}
