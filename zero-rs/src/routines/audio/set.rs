//! audio/output/set —— 设置音频输出设备（影响 speak 与 music 播放）。
//!
//! 查询走 `audio/output/list`。切换只影响下一次开流：在播会话挂旧流
//! 自然播完，不中断。不占模块（纯配置，与播放无串行约束）。

use async_trait::async_trait;
use routine::core::{schema_for, Routine, RoutineError, RoutineInfo, RoutineOutput, RunContext};
use routine::protocol::JsonObject;
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;

use crate::audio::service::AudioService;

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SetOutputParams {
    /// 目标输出设备 id（"default" = 恢复系统默认；可用 id 见 audio/output/list）。
    pub device: String,
}

#[derive(Default)]
pub struct SetOutput;

impl RoutineInfo for SetOutput {
    const NAME: &'static str = "audio/output/set";

    fn meta() -> JsonObject {
        let mut meta = JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("设置音频输出设备(影响 speak 与 music).device 指定设备 id(取自 audio/output/list), 在播声音不打断, 下一次播放生效."),
        );
        meta.insert("input_schema".to_string(), schema_for::<SetOutputParams>());
        meta
    }
}

#[async_trait]
impl Routine for SetOutput {
    async fn run(
        &self,
        _ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let params: SetOutputParams =
            serde_json::from_value(serde_json::Value::Object(params))
                .map_err(|e| RoutineError::Message(format!("invalid params: {e}")))?;

        let current = AudioService::open().set_output(&params.device)?;
        Ok(RoutineOutput::Value(json!({
            "current": current,
        })))
    }
}
