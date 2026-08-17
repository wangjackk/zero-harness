//! Frame wire 编解码:payload = 完整事件消息的 JSON 文本。
//!
//! 对齐 Python `protocol.py` 的 `dict_to_frame/frame_to_dict` 与 Go `mapToFrame/
//! frameToMap`----wire 上唯一消息类型是 `Frame{payload}`,整条平铺消息一次
//! 序列化/反序列化,无嵌套包装。Frame proto 类型在 grpc 层(`grpc::routine::
//! Frame`),本模块只做 payload 文本与 JsonObject 的转换,供 grpc 边界包装。

use serde_json::{Map, Value};

pub type JsonObject = Map<String, Value>;
pub type JsonValue = Value;

/// JsonObject → 紧凑 JSON 文本(Frame.payload)。
pub fn json_to_payload(data: &JsonObject) -> String {
    Value::Object(data.clone()).to_string()
}

/// JSON 文本 → JsonObject。格式不对直接 panic 暴露协议破坏,不做兼容读
/// (对齐 py `frame_to_dict` 直接 json.loads 抛错)。
pub fn payload_to_json(payload: &str) -> JsonObject {
    match serde_json::from_str::<Value>(payload) {
        Ok(Value::Object(map)) => map,
        _ => panic!("bad frame payload: {payload}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn round_trips_nested_json_object() {
        let raw = json!({
            "event": "lifecycle.start",
            "id": "abc",
            "kwargs": {
                "text": "hello",
                "count": 3,
                "enabled": true,
                "items": [null, "x", 2]
            }
        });
        let object = raw.as_object().unwrap().clone();

        let payload = json_to_payload(&object);
        let restored = payload_to_json(&payload);

        assert_eq!(Value::Object(restored), raw);
    }

    #[test]
    fn payload_is_compact() {
        let mut object = JsonObject::new();
        object.insert("k".to_string(), Value::String("v".to_string()));
        assert_eq!(json_to_payload(&object), r#"{"k":"v"}"#);
    }
}
