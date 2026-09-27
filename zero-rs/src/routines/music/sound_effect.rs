//! sound_effect —— 短提示音（纯合成 chirp，无音频文件依赖）。
//!
//! start: 660→990Hz 上行（动作开始），stop: 990→660Hz 下行（动作结束）。
//! 正弦扫频 + 5ms 淡入淡出防爆音。语义对齐 Python `sound_effect.py`。
//!
//! 不占 `audio` 模块 —— 独立 Player 与正在播放的音乐/说话自动混音
//! （提示音必须随时可响，不能被占着 audio 的音乐阻塞）。

use async_trait::async_trait;
use rodio::buffer::SamplesBuffer;
use routine::{
    core::{schema_for, Routine, RoutineError, RoutineInfo, RoutineOutput, RunContext},
    protocol::JsonObject,
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::audio::service::AudioService;

const SAMPLE_RATE: u32 = 44_100;
/// 单音时长。
const DUR: f32 = 0.16;
/// 淡入淡出秒数（防爆音）。
const FADE_SECS: f32 = 0.005;

/// effect → (f0, f1) Hz。
fn effect_freqs(effect: &str) -> Option<(f32, f32)> {
    match effect {
        "start" => Some((660.0, 990.0)),
        "stop" => Some((990.0, 660.0)),
        _ => None,
    }
}

/// 合成线性扫频正弦波（对齐 Python `_chirp`：逐点线性频率 + 累积相位）。
fn chirp(f0: f32, f1: f32, dur: f32, volume: f32) -> Vec<f32> {
    let n = (SAMPLE_RATE as f32 * dur) as usize;
    let fade = (SAMPLE_RATE as f32 * FADE_SECS) as usize;
    let mut out = Vec::with_capacity(n);
    let mut phase = 0.0f32;
    let step = std::f32::consts::TAU / SAMPLE_RATE as f32;
    for i in 0..n {
        let f = f0 + (f1 - f0) * (i as f32 / n as f32);
        phase += step * f;
        let mut s = phase.sin() * volume;
        if i < fade {
            s *= i as f32 / fade as f32;
        }
        if i + fade >= n {
            s *= (n - i) as f32 / fade as f32;
        }
        out.push(s);
    }
    out
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct SoundEffectParams {
    /// 提示音: start 上行(动作开始), stop 下行(动作结束)。
    pub effect: String,
    /// 音量 0~1, 默认 0.6。宽松接受数字字符串(LLM XML 属性)。
    #[serde(
        default = "default_volume",
        deserialize_with = "super::de_f32_flexible"
    )]
    pub volume: f32,
}

fn default_volume() -> f32 {
    0.6
}

#[derive(Debug, Serialize, JsonSchema)]
pub struct SoundEffectResult {
    /// 实际播放的提示音。
    pub effect: String,
    /// 播放时长（秒）。
    pub duration: f32,
}

#[derive(Default)]
pub struct SoundEffect;

impl RoutineInfo for SoundEffect {
    const NAME: &'static str = "sound_effect";

    fn meta() -> routine::protocol::JsonObject {
        let mut meta = routine::protocol::JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!(
                "播放短合成提示音(start 上行 / stop 下行, ~0.16s), \
                 用于动作开始/结束的听觉提示; 与音乐混音不互斥"
            ),
        );
        meta.insert(
            "input_schema".to_string(),
            schema_for::<SoundEffectParams>(),
        );
        meta.insert(
            "output_schema".to_string(),
            schema_for::<SoundEffectResult>(),
        );
        meta
    }

    /// 不占模块: 提示音随时可响, 叠在音乐/说话上混音。
    fn modules(_params: Option<&routine::protocol::JsonObject>) -> Vec<String> {
        Vec::new()
    }
}

#[async_trait]
impl Routine for SoundEffect {
    async fn run(
        &self,
        _ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let params: SoundEffectParams =
            serde_json::from_value(serde_json::Value::Object(params))
                .map_err(|error| RoutineError::Message(format!("invalid params: {error}")))?;
        let Some((f0, f1)) = effect_freqs(&params.effect) else {
            return Err(RoutineError::Message(format!(
                "unknown effect {:?} (expected start|stop)",
                params.effect
            )));
        };
        let volume = params.volume.clamp(0.0, 1.0);

        // 单声道 44.1k f32 → mixer 重采样到设备 48k/stereo 混音。
        let source = SamplesBuffer::new(
            std::num::NonZero::new(1).expect("non-zero channels"),
            std::num::NonZero::new(SAMPLE_RATE).expect("non-zero sample rate"),
            chirp(f0, f1, DUR, volume),
        );
        let svc = AudioService::open();
        let (_sink, done_rx) = svc
            .play_source(source)
            .map_err(|e| RoutineError::Message(format!("audio service unavailable: {e}")))?;
        // ~0.16s 的短音, 等播完即 done; run 自然结束, 无需打断句柄。
        let outcome = done_rx
            .await
            .map_err(|_| RoutineError::Message("playback done signal closed".to_string()))?;
        tracing::info!(
            routine = Self::NAME,
            effect = %params.effect,
            interrupted = outcome.interrupted,
            "sound_effect finished"
        );

        serde_json::to_value(SoundEffectResult {
            effect: params.effect,
            duration: DUR,
        })
        .map(RoutineOutput::Value)
        .map_err(|e| RoutineError::Message(format!("serialize sound_effect result: {e}")))
    }
}
