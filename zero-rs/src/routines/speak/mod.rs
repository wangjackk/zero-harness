//! speak —— TTS 文本转语音叶子 routine（Rust 版，协议对齐 Python `speak.py`）。
//!
//! 给 Act 当 on_body_text 派生的子: 收 text body(流式), 喂 Seed TTS 合成音频,
//! audio 服务独占设备播放.
//!
//! 流式设计(对齐 Python):
//!   on_message 收 {text} 直接 feed 给实例私有 handle(mailbox 保证按发送序
//!   串行到达, 无需业务 reorder);{_eof} 调 finish 让音频流自然结束.
//!   run 等待合成+播放结束(wait_done), stop 打断立即停.
//!
//! 情绪指令是 agent 的活状态: submit 只透传 `from_agent_id`, run 起点经
//! `agent/state` bridge 现取 `tts_instruction` 写入活单元; TTS session 惰性
//! 建立于首个 Feed 的消化(受 started 门, 晚于 run), 建立时读活单元现值 ——
//! 排队段(mouth 被占)与立即段两种时序读到的都是"临出声前"的最新情绪.
//!
//! TTS 资源(WS 连接)进程级单例复用(TtsService actor 懒启动, on_created 触发);
//! 每个 speak instance 持独立 TtsHandle(token 身份), actor 按 token 隔离
//! 串行处理(对齐 py 每实例独立 SeedTTS); speak instance 间另靠 mouth 模块
//! 互斥串行.

mod protocol;
mod seed;
mod tts;

use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use routine::{
    core::{
        Routine, RoutineError, RoutineInfo, RoutineOutput, RoutineSource,
        RunContext,
    },
    protocol::{JsonObject, JsonValue},
};
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;

pub use seed::SeedConfig;
pub use tts::{TtsHandle, TtsService};

/// 模块树常量 (kernel module tree 的 core/mouth): 占用后嘴巴互斥, 多 speak 串行.
pub const SPEAK_MODULE: &str = "mouth";

/// TTS PCM 的采样率/声道 (Doubao Seed TTS 输出 24k/mono).
pub(crate) const SAMPLE_RATE: u32 = 24_000;
pub(crate) const CHANNELS: u16 = 1;

#[derive(Debug, Default, Deserialize, JsonSchema)]
pub struct SpeakParams {
    /// 调用方 agent 身份(框架注入): run 起点经 agent/state bridge 反查该
    /// agent 的 tts_instruction. 空 = 无 agent 上下文(前端直调), 中性播放.
    #[serde(default)]
    from_agent_id: Option<String>,
}

/// 每个 speak 实例一个（factory per-rid create），持实例私有 TtsHandle:
/// token 是 actor 侧的实例身份，多实例命令互不串扰。
/// `from_agent_id` 在 on_created 从 submit kwargs 灌入; run 起点据此取
/// 情绪指令写入 `instruction` 活单元, actor 建 session 时读现值.
#[derive(Default)]
pub struct SpeakRoutine {
    tts: TtsHandle,
    from_agent_id: Mutex<Option<String>>,
    instruction: Arc<Mutex<Option<String>>>,
}

impl RoutineInfo for SpeakRoutine {
    const NAME: &'static str = "speak";

    fn meta() -> JsonObject {
        let schema = serde_json::to_value(schemars::schema_for!(SpeakParams))
            .expect("SpeakParams schema is always serializable");
        let mut meta = JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("TTS 文本转语音(Seed WS 双向流)"),
        );
        meta.insert("hidden".to_string(), json!(true));
        meta.insert("input_schema".to_string(), schema);
        meta
    }

    /// 占用 mouth 模块: 嘴巴同时只能做一件事, 多个 speak 串行
    /// (一个播完释放, 下一个才 start).
    fn modules(_params: Option<&JsonObject>) -> Vec<String> {
        vec![SPEAK_MODULE.to_string()]
    }
}

#[async_trait]
impl Routine for SpeakRoutine {
    async fn run(
        &self,
        ctx: RunContext,
        _params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        // 情绪指令: 起 run 时经 agent/state bridge 现取(与 react/LLM 并行评估,
        // 此刻通常已落地). 取失败/超时 = 中性, 不阻塞不出声降级 ---- 情绪是
        // 背景染色, speak 绝不为此卡住.
        let agent_id = self.from_agent_id.lock().unwrap().clone();
        if let Some(agent_id) = agent_id {
            match fetch_instruction(&ctx, &agent_id).await {
                Ok(instr) => *self.instruction.lock().unwrap() = instr,
                Err(e) => tracing::warn!("speak: fetch emotion failed: {e}"),
            }
        }
        // wait_done 等合成+播放结束(流收口由 on_message 的 {_eof} 驱动,
        // 先于文本到达也正确挂起).
        let instruction = self.instruction.lock().unwrap().clone();
        match instruction.as_deref() {
            Some(instr) => tracing::info!("speak: tts start (emotion: {instr})"),
            None => tracing::info!("speak: tts start"),
        }
        let outcome = self.tts.wait_done().await?;
        tracing::info!(
            frames = outcome.frames,
            interrupted = outcome.interrupted,
            "speak done"
        );
        Ok(RoutineOutput::Value(json!({
            "frames": outcome.frames,
            "interrupted": outcome.interrupted,
        })))
    }

    async fn on_created(&self, _rid: &str, kwargs: &JsonObject) -> Result<(), RoutineError> {
        // TTS actor 懒启动(进程单例, 重复调用忽略): config 缺失时 created 即失败,
        // 不影响其它 routine.
        TtsService::start(SeedConfig::load()?)?;
        // 调用方 agent 身份: act submit speak 时透传(created 投递, 早于任何
        // body 消息); run 起点据此反查情绪. 指令活单元初始为空(中性).
        let params: SpeakParams =
            serde_json::from_value(JsonValue::Object(kwargs.clone())).unwrap_or_default();
        *self.from_agent_id.lock().unwrap() =
            params.from_agent_id.filter(|s| !s.is_empty());
        Ok(())
    }

    async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
        // 打断时立即停: cancel session + 停播放.
        tracing::info!("speak stopping");
        self.tts.cancel();
        Ok(Some(json!({
            "routine": "speak",
            "stopped": true,
            "interrupted": true,
        })))
    }

    /// 收 body chunk 直接喂 tts(mailbox 串行, 按发送序到达).
    /// 协议对齐 Python speak.py: {text} → feed; {_eof: true} → finish.
    async fn on_message(
        &self,
        _source: RoutineSource,
        data: JsonValue,
    ) -> Result<(), RoutineError> {
        let Some(obj) = data.as_object() else {
            return Ok(());
        };
        if obj
            .get("_eof")
            .and_then(JsonValue::as_bool)
            .unwrap_or(false)
        {
            self.tts.finish()?;
            return Ok(());
        }
        let text = obj.get("text").and_then(JsonValue::as_str).unwrap_or("");
        if !text.is_empty() {
            // 活单元引用(Arc clone): actor 建 session 时才读现值, 此处不拷贝.
            let additions = Arc::clone(&self.instruction);
            self.tts.feed(text, additions).await?;
        }
        Ok(())
    }
}

/// Run 起点经 agent/state bridge 反查该 agent 当前 mood 出的 TTS 指令.
/// 采样式查询: 与订阅推送相对, 任何时刻问都是最新值, 无错过窗口.
async fn fetch_instruction(
    ctx: &RunContext,
    agent_id: &str,
) -> Result<Option<String>, String> {
    let mut kwargs = JsonObject::new();
    kwargs.insert("agent_id".to_string(), json!(agent_id));
    let (child, _) = ctx.submit("agent/state", Some(kwargs)).await?;
    ctx.start_child(&child, 10).await?;   // 10 = 常规默认档
    let state = ctx.wait_child(&child, 2).await?;
    Ok(state
        .get("tts_instruction")
        .and_then(JsonValue::as_str)
        .map(str::to_string)
        .filter(|s| !s.is_empty()))
}
