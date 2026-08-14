use async_trait::async_trait;
use routine::{
    core::{
        Routine, RoutineError, RoutineFactory, RoutineInfo, RoutineOutput, RoutineRegistry,
        RunContext,
    },
    server::{start_server, RoutineServerOptions, StartServerOptions},
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;
use std::{net::SocketAddr, sync::Arc};

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
}

#[async_trait]
impl Routine for HelloRoutine {
    type Params = HelloParams;

    async fn run(
        &self,
        _ctx: RunContext,
        params: Self::Params,
    ) -> Result<RoutineOutput, RoutineError> {
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
    routines.add_factory(Arc::new(RoutineFactory::<HelloRoutine>::new()));

    let address: SocketAddr = "0.0.0.0:50051".parse()?;
    start_server(StartServerOptions {
        routine_options: RoutineServerOptions {
            routines,
            modules: Vec::new(),
            routers: Vec::new(),
        },
        address,
    })
    .await
}
