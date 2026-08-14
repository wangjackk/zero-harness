use routine::{
    protocol::{events::LIFECYCLE_START, json_to_struct, struct_to_json, JsonObject},
    server::grpc::routine::routine_service_client::RoutineServiceClient,
};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut client = RoutineServiceClient::connect("http://127.0.0.1:50051").await?;
    let (tx, rx) = mpsc::channel(8);

    let response = client.stream(ReceiverStream::new(rx)).await?;
    let mut inbound = response.into_inner();

    let mut start = JsonObject::new();
    start.insert(
        "event".to_string(),
        Value::String(LIFECYCLE_START.to_string()),
    );
    start.insert("id".to_string(), Value::String("hello-1".to_string()));
    start.insert("name".to_string(), Value::String("hello".to_string()));
    start.insert("kwargs".to_string(), json!({ "name": "zero-rs" }));
    tx.send(json_to_struct(&start)).await?;

    while let Some(message) = inbound.message().await? {
        let event = struct_to_json(&message);
        let is_stopped = event.get("event").and_then(Value::as_str) == Some("lifecycle.stopped");
        println!("{}", serde_json::to_string_pretty(&Value::Object(event))?);
        if is_stopped {
            break;
        }
    }

    drop(tx);
    Ok(())
}
