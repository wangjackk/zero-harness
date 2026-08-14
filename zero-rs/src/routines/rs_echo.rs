//! 跨语言 echo:原样返回 message,附 runtime 标识。
//! 证明 kernel 跨语言路由:Python call → Go kernel → Rust routine。

use async_trait::async_trait;
use routine::{
    core::{Routine, RoutineError, RoutineFactory, RoutineInfo, RoutineOutput, RunContext},
    protocol::JsonObject,
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;

#[derive(Default)]
pub struct RsEcho;

#[derive(Debug, Deserialize, JsonSchema)]
pub struct RsEchoParams {
    /// 回显的消息文本
    #[serde(default = "default_message")]
    message: String,
}

fn default_message() -> String {
    "hello from python zero".to_string()
}

impl RoutineInfo for RsEcho {
    const NAME: &'static str = "rs_echo";

    fn meta() -> JsonObject {
        let schema = serde_json::to_value(schemars::schema_for!(RsEchoParams))
            .expect("RsEchoParams schema is always serializable");
        let mut meta = JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("Rust 版 echo: 原样返回 message 并附 runtime=rs。证明 kernel 跨语言路由 (Python call → Go kernel → Rust routine)。"),
        );
        meta.insert("input_schema".to_string(), schema);
        meta
    }
}

#[async_trait]
impl Routine for RsEcho {
    type Params = RsEchoParams;

    async fn run(
        &self,
        _ctx: RunContext,
        params: Self::Params,
    ) -> Result<RoutineOutput, RoutineError> {
        Ok(RoutineOutput::Value(json!({
            "message": params.message,
            "runtime": "rs",
        })))
    }
}

use std::sync::Arc;

/// 聚合本 crate 全部 routine(启动程序共用)。
pub fn registry() -> routine::core::RoutineRegistry {
    let mut routines = routine::core::RoutineRegistry::new();
    routines.add_factory(Arc::new(RoutineFactory::<RsEcho>::new()));
    routines
}
