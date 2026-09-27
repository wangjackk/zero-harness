//! list_music —— 列出本地音乐曲单(name + tags), 供 LLM 选曲参考。
//!
//! 语义对齐 Python `list_music.py`: 合并 assets/meta.json 元数据与目录下
//! 可直接播放的音频文件名(wav/mp3/flac/ogg/m4a), 返回 songs + listing 文本。
//! 不占模块(纯读盘)。曲库目录复用 `play_music` 的 `[play_music]` 配置。

use async_trait::async_trait;
use routine::core::{schema_for, Routine, RoutineError, RoutineInfo, RoutineOutput, RunContext};
use routine::protocol::JsonObject;
use schemars::JsonSchema;
use serde::Deserialize;
use serde_json::json;

/// 可直接播放的音频扩展名。
const AUDIO_EXTS: [&str; 5] = ["wav", "mp3", "flac", "ogg", "m4a"];

#[derive(Debug, Deserialize, JsonSchema)]
pub struct ListMusicParams {}

#[derive(Default)]
pub struct ListMusic;

impl RoutineInfo for ListMusic {
    const NAME: &'static str = "music/list";

    fn meta() -> JsonObject {
        let mut meta = JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("列出本地音乐曲单(name + tags), 供选曲参考.无参数."),
        );
        meta.insert("input_schema".to_string(), schema_for::<ListMusicParams>());
        meta
    }
}

#[async_trait]
impl Routine for ListMusic {
    async fn run(
        &self,
        _ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let _params: ListMusicParams = serde_json::from_value(serde_json::Value::Object(params))
            .map_err(|error| RoutineError::Message(format!("invalid params: {error}")))?;

        let dir = super::play_music::PlayMusicConfig::load()?.music_dir_path;

        // meta.json: {name: {tags: [...]}}. 缺失/损坏 → 空表(对齐 Python `_load_catalog`).
        let catalog: JsonObject = tokio::fs::read_to_string(dir.join("meta.json"))
            .await
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();

        // 目录下可直接播放的音频文件名(不含扩展名, 排序)。
        let mut entries = tokio::fs::read_dir(&dir)
            .await
            .map_err(|e| RoutineError::Message(format!("music dir {}: {e}", dir.display())))?;
        let mut audio: Vec<String> = Vec::new();
        while let Some(entry) = entries
            .next_entry()
            .await
            .map_err(|e| RoutineError::Message(format!("read music dir: {e}")))?
        {
            let path = entry.path();
            let Some(ext) = path
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.to_ascii_lowercase())
            else {
                continue;
            };
            if AUDIO_EXTS.contains(&ext.as_str()) {
                if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                    audio.push(stem.to_string());
                }
            }
        }
        audio.sort();

        // catalog 里的名在前, 目录里多的补上(无元数据 → 空标签)。
        let mut catalog = catalog;
        let mut names: Vec<String> = catalog.keys().cloned().collect();
        for stem in audio {
            if !catalog.contains_key(&stem) {
                names.push(stem.clone());
                catalog.insert(stem, json!({}));
            }
        }
        if names.is_empty() {
            return Ok(RoutineOutput::Value(json!({
                "songs": [],
                "listing": "(曲单为空)",
            })));
        }

        let lines: Vec<String> = names
            .iter()
            .map(|name| {
                let tags: Vec<&str> = catalog[name]
                    .get("tags")
                    .and_then(|t| t.as_array())
                    .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
                    .unwrap_or_default();
                let tag_str = if tags.is_empty() {
                    "(无标签)".to_string()
                } else {
                    tags.join(", ")
                };
                format!("- {name}: {tag_str}")
            })
            .collect();
        Ok(RoutineOutput::Value(json!({
            "songs": names,
            "listing": lines.join("\n"),
        })))
    }
}
