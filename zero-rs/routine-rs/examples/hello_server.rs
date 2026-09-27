use async_trait::async_trait;
use routine::{
    core::{
        schema_for, Routine, RoutineError, RoutineInfo, RoutineOutput, RoutineRegistry,
        RunContext,
    },
    start_server, RoutineHubOptions, StartServerOptions,
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;
use std::net::SocketAddr;

#[derive(Default)]
struct HelloRoutine;

#[derive(Debug, Deserialize, JsonSchema)]
struct HelloParams {
    #[serde(default = "default_name")]
    name: String,
}

fn default_name() -> String {
    "routine".to_string()
}

impl RoutineInfo for HelloRoutine {
    const NAME: &'static str = "hello";

    fn meta() -> routine::protocol::JsonObject {
        let mut meta = routine::protocol::JsonObject::new();
        meta.insert("input_schema".to_string(), schema_for::<HelloParams>());
        meta
    }
}

#[async_trait]
impl Routine for HelloRoutine {
    async fn run(
        &self,
        _ctx: RunContext,
        params: routine::protocol::JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let params: HelloParams =
            serde_json::from_value(serde_json::Value::Object(params))
                .map_err(|error| RoutineError::Message(format!("invalid params: {error}")))?;
        Ok(RoutineOutput::Value(json!({
            "message": format!("hello, {}", params.name),
            "runtime": "rust",
        })))
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    let mut routines = RoutineRegistry::new();
    routines.add::<HelloRoutine>();

    let address: SocketAddr = "0.0.0.0:50051".parse()?;
    start_server(StartServerOptions {
        routine_options: RoutineHubOptions {
            routines,
            modules: Vec::new(),
            hub_id: "hello-rs".to_string(),
        },
        address,
    })
    .await
}
