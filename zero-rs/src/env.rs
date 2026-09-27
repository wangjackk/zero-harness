//! 部署环境变量加载:与 py 侧共用 `../zero-py/.env`。

/// 加载 `../zero-py/.env`(存在则解析进进程环境,已设变量不覆盖)。
///
/// 与 py 侧共用同一份部署密钥(speak 的 SEED_TTS_API_KEY 等),变量名逐个对齐。
pub fn load_dotenv() {
    let Ok(content) = std::fs::read_to_string("../zero-py/.env") else {
        return;
    };
    for line in content.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let (key, value) = (key.trim(), value.trim().trim_matches('"'));
        if !key.is_empty() && std::env::var_os(key).is_none() {
            std::env::set_var(key, value);
        }
    }
}
