//! audio/output/list —— 列出音频输出设备（当前选定 + 全部可用）。
//!
//! 蓝牙音箱等虚拟 PCM 须在 /etc/asound.conf 有静态定义才会被枚举
//! （bluealsa 场景常见做法）。设置走 `audio/output/set`（id 取本列表）。

use async_trait::async_trait;
use routine::core::{schema_for, Routine, RoutineError, RoutineInfo, RoutineOutput, RunContext};
use routine::protocol::JsonObject;
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;

use crate::audio::service::AudioService;

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListParams {}

#[derive(Default)]
pub struct ListOutputs;

impl RoutineInfo for ListOutputs {
    const NAME: &'static str = "audio/output/list";

    fn meta() -> JsonObject {
        let mut meta = JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("列出音频输出设备: current 为当前选定设备 id(null = 系统默认), available 为可用设备列表 {id, name}(default 在前, 按显示名去重).切换用 audio/output/set(传 id)."),
        );
        meta.insert("input_schema".to_string(), schema_for::<ListParams>());
        meta
    }
}

#[async_trait]
impl Routine for ListOutputs {
    async fn run(
        &self,
        _ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let _params: ListParams =
            serde_json::from_value(serde_json::Value::Object(params))
                .map_err(|e| RoutineError::Message(format!("invalid params: {e}")))?;

        let (current, available) = AudioService::open().output_status();
        let available: Vec<_> = available
            .iter()
            .map(|o| json!({ "id": o.id, "name": o.name }))
            .collect();
        Ok(RoutineOutput::Value(json!({
            "current": current,
            "available": available,
        })))
    }
}
