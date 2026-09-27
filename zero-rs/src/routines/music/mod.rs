//! music —— 本地音乐/提示音 routine 组（与 speak 共用 audio 底层, mixer 实时混音）。

pub mod list_music;
pub mod play_music;
pub mod sound_effect;

use serde::Deserialize;

/// f64 宽松反序列化: 接受数字或数字字符串。
///
/// LLM 生成的 XML 属性全是字符串(`duration="3"`), Python 侧 pydantic lax 模式
/// 会自动强转; Rust serde 默认严格, 此处对齐 pydantic 语义。
/// (不能在 act 层统一强转: 文本字段恰为数字串时会被误杀, 宽松与否归字段 schema。)
pub(crate) fn de_f64_flexible<'de, D>(deserializer: D) -> Result<f64, D::Error>
where
    D: serde::Deserializer<'de>,
{
    use serde::de::Error;
    match serde_json::Value::deserialize(deserializer)? {
        serde_json::Value::Number(n) => n
            .as_f64()
            .ok_or_else(|| D::Error::custom("number out of f64 range")),
        serde_json::Value::String(s) => s
            .trim()
            .parse::<f64>()
            .map_err(|e| D::Error::custom(format!("invalid number {s:?}: {e}"))),
        other => Err(D::Error::custom(format!("expected number, got {other}"))),
    }
}

/// Option<f64> 版(含 null → None)。
pub(crate) fn de_opt_f64_flexible<'de, D>(deserializer: D) -> Result<Option<f64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    use serde::de::Error;
    match Option::<serde_json::Value>::deserialize(deserializer)? {
        None | Some(serde_json::Value::Null) => Ok(None),
        Some(serde_json::Value::Number(n)) => n
            .as_f64()
            .map(Some)
            .ok_or_else(|| D::Error::custom("number out of f64 range")),
        Some(serde_json::Value::String(s)) => s
            .trim()
            .parse::<f64>()
            .map(Some)
            .map_err(|e| D::Error::custom(format!("invalid number {s:?}: {e}"))),
        Some(other) => Err(D::Error::custom(format!("expected number, got {other}"))),
    }
}

/// f32 版。
pub(crate) fn de_f32_flexible<'de, D>(deserializer: D) -> Result<f32, D::Error>
where
    D: serde::Deserializer<'de>,
{
    Ok(de_f64_flexible(deserializer)? as f32)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Deserialize)]
    struct Params {
        #[serde(default, deserialize_with = "de_opt_f64_flexible")]
        duration: Option<f64>,
        #[serde(default = "volume", deserialize_with = "de_f32_flexible")]
        volume: f32,
    }

    fn volume() -> f32 {
        0.6
    }

    #[test]
    fn numeric_strings_are_coerced() {
        // LLM XML 属性: 数字以字符串形式到达
        let p: Params = serde_json::from_str(r#"{"duration": "3", "volume": "0.8"}"#).unwrap();
        assert_eq!(p.duration, Some(3.0));
        assert!((p.volume - 0.8).abs() < 1e-6);
    }

    #[test]
    fn plain_numbers_still_work() {
        let p: Params = serde_json::from_str(r#"{"duration": 1.5, "volume": 0.5}"#).unwrap();
        assert_eq!(p.duration, Some(1.5));
        assert!((p.volume - 0.5).abs() < 1e-6);
    }

    #[test]
    fn missing_and_null_are_none_or_default() {
        let p: Params = serde_json::from_str("{}").unwrap();
        assert_eq!(p.duration, None);
        assert!((p.volume - 0.6).abs() < 1e-6);
        let p: Params = serde_json::from_str(r#"{"duration": null}"#).unwrap();
        assert_eq!(p.duration, None);
    }

    #[test]
    fn garbage_still_errors() {
        assert!(serde_json::from_str::<Params>(r#"{"duration": "abc"}"#).is_err());
        assert!(serde_json::from_str::<Params>(r#"{"volume": true}"#).is_err());
    }
}
