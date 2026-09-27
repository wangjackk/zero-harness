//! play_music —— 播放本地音乐文件，播完才 done。
//!
//! 与 speak 共用底层音频设备（`AudioService` 的常驻 mixer sink）：音乐 source
//! 由 rodio mixer 重采样到设备 48k/stereo 后与说话实时混音。语义对齐 Python
//! `play_music.py`：按 `name` 在 assets/ 下找任意扩展名的音频文件（排除
//! .json 元数据），`duration` 为 null/0 播整首，>0 播指定秒数后自动退出；
//! 打断走 `stop()`。
//!
//! 占 `audio` 模块 → 多个 play_music 串行；与 speak 的 `mouth` 模块互不互斥
//! （说话 + 背景音乐可同时混音）。sound_effect 不占模块，可叠在音乐上。
//!
//!
//! 资源走运行时磁盘读（音乐文件体积大，不 embed）。assets 目录由
//! `config.toml` 的 `[play_music].music_dir_path` 配置，默认复用 Python 树资源。

use std::io::Cursor;
use std::path::PathBuf;
use std::time::Duration;

use async_trait::async_trait;
use figment::providers::{Format, Toml};
use figment::Figment;
use rodio::{Decoder, Source};
use routine::{
    core::{schema_for, Routine, RoutineError, RoutineInfo, RoutineOutput, RunContext},
    protocol::JsonObject,
};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::json;
use tokio::sync::Mutex;

use crate::audio::service::{AudioOutcome, AudioService, PcmSink};

const DEFAULT_NAME: &str = "Found-My-Way";

/// play_music 配置。来源 `config.toml` 的 `[play_music]` 段（可选，缺省用默认）。
///
/// `music_dir_path` 缺省复用 Python 树的曲库（从 zero-rs 运行目录的相对路径）；
/// 仓库根的 `zero-rs/config.toml` 已显式配置了该值，此处默认仅作兜底。
#[derive(Debug, Clone, Deserialize)]
pub struct PlayMusicConfig {
    #[serde(default = "default_music_dir_path")]
    pub music_dir_path: PathBuf,
}

fn default_music_dir_path() -> PathBuf {
    PathBuf::from("../zero-py/app/routines/user/music/assets")
}

impl PlayMusicConfig {
    /// 从 `config.toml` 的 `[play_music]` 段加载；段缺失/文件不存在时用全默认值。
    pub fn load() -> Result<Self, RoutineError> {
        match Figment::new()
            .merge(Toml::file("config.toml"))
            .extract_inner::<Self>("play_music")
        {
            Ok(config) => Ok(config),
            // figment 对缺失 key 直接报 MissingField(轮不到 Option 给 None),
            // 段缺失 = 用默认曲库目录, 不是错误.
            Err(e) if matches!(e.kind, figment::error::Kind::MissingField(_)) => Ok(Self {
                music_dir_path: default_music_dir_path(),
            }),
            Err(e) => Err(RoutineError::Message(format!(
                "load play_music config: {e}"
            ))),
        }
    }
}

#[derive(Default)]
pub struct PlayMusic {
    /// 当前播放会话的打断句柄。`run` 投递 source 后存入，`stop` 取出调 `stop()`。
    session: Mutex<Option<PlaySession>>,
}

struct PlaySession {
    sink: PcmSink,
}

impl RoutineInfo for PlayMusic {
    const NAME: &'static str = "music/play";

    fn meta() -> routine::protocol::JsonObject {
        let mut meta = routine::protocol::JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("按 name 播放本地音乐(assets/ 曲单, 见 music/list)"),
        );
        meta.insert("input_schema".to_string(), schema_for::<PlayMusicParams>());
        meta
    }

    /// 占 `audio` 模块 → 多个 play_music 串行（对齐 Python play_music）。
    /// 与 speak 的 `mouth` 模块互不互斥：底层 mixer 实时混音。
    fn modules(_params: Option<&routine::protocol::JsonObject>) -> Vec<String> {
        vec!["audio".to_string()]
    }
}

#[derive(Debug, Deserialize, JsonSchema)]
pub struct PlayMusicParams {
    /// 音乐名（assets/ 下的文件名，不含扩展名）；缺省 Found-My-Way。
    #[serde(default)]
    pub name: Option<String>,
    /// 播放秒数；>0 播指定秒数后自动退出，0 或 null 播整首。
    /// 宽松接受数字字符串。
    #[serde(default, deserialize_with = "super::de_opt_f64_flexible")]
    pub duration: Option<f64>,
}

#[derive(Debug, Serialize, JsonSchema)]
pub struct PlayMusicResult {
    pub name: String,
    pub interrupted: bool,
    pub for_llm: Option<String>,
}

#[async_trait]
impl Routine for PlayMusic {
    async fn run(
        &self,
        _ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let params: PlayMusicParams = serde_json::from_value(serde_json::Value::Object(params))
            .map_err(|error| RoutineError::Message(format!("invalid params: {error}")))?;
        let name = params
            .name
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(DEFAULT_NAME)
            .to_string();

        let path = resolve_asset(&name)?;
        let bytes = tokio::fs::read(&path)
            .await
            .map_err(|e| RoutineError::Message(format!("read asset {}: {e}", path.display())))?;
        let decoder = Decoder::new(Cursor::new(bytes))
            .map_err(|e| RoutineError::Message(format!("decode {}: {e}", path.display())))?;

        let outcome = if let Some(secs) = params.duration.filter(|&d| d > 0.0) {
            self.run_source(decoder.take_duration(Duration::from_secs_f64(secs)))
                .await?
        } else {
            self.run_source(decoder).await?
        };

        tracing::info!(
            routine = Self::NAME,
            %name,
            interrupted = outcome.interrupted,
            "play_music finished"
        );

        serde_json::to_value(PlayMusicResult {
            name,
            interrupted: outcome.interrupted,
            for_llm: None,
        })
        .map(RoutineOutput::Value)
        .map_err(|e| RoutineError::Message(format!("serialize play_music result: {e}")))
    }

    async fn stop(&self) -> Result<Option<serde_json::Value>, RoutineError> {
        if let Some(session) = self.session.lock().await.take() {
            session.sink.stop();
        }
        Ok(Some(json!({
            "routine": Self::NAME,
            "stopped": true,
            "interrupted": true,
        })))
    }
}

impl PlayMusic {
    /// 把一个 source 喂进共享设备播放，存打断句柄，等播完（或被打断）。
    async fn run_source<S>(&self, source: S) -> Result<AudioOutcome, RoutineError>
    where
        S: Source + Send + 'static,
    {
        let svc = AudioService::open();
        let (sink, done_rx) = svc
            .play_source(source)
            .map_err(|e| RoutineError::Message(format!("audio service unavailable: {e}")))?;
        *self.session.lock().await = Some(PlaySession { sink });
        let outcome = done_rx
            .await
            .map_err(|_| RoutineError::Message("playback done signal closed".to_string()))?;
        // 自然播完：清掉 session（stop 若随后到达会看到 None，无副作用）。
        *self.session.lock().await = None;
        Ok(outcome)
    }
}

/// 按 name 在 music 目录下查找任意扩展名的音频文件（排除 .json 元数据），
/// 对齐 Python `_resolve(name)` 的 glob 语义。目录来自 `PlayMusicConfig`。
fn resolve_asset(name: &str) -> Result<PathBuf, RoutineError> {
    let dir = PlayMusicConfig::load()?.music_dir_path;
    let entries = std::fs::read_dir(&dir)
        .map_err(|e| RoutineError::Message(format!("music dir {}: {e}", dir.display())))?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().map(|e| e == "json").unwrap_or(false) {
            continue;
        }
        if path.file_stem().map(|s| s == name).unwrap_or(false) {
            return Ok(path);
        }
    }
    Err(RoutineError::Message(format!(
        "no asset matching {name}.* in {}",
        dir.display()
    )))
}
