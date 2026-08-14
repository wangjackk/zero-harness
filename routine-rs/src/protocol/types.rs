use super::envelope;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub type RoutineMeta = Map<String, Value>;

/// `lifecycle.stopped` 回报里的 reason 取值。
///
/// 对齐 Python `ControlDoneReason`:7 值。UNKNOWN 保留作默认/未知兜底。
/// kernel 对 reason 是 dumb-forward(不按值分流),故扩值无需 kernel 侧配合。
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ControlDoneReason {
    Unknown,
    Auto,
    Stop,
    Error,
    Cancel,
    Force,
    Disconnect,
}

impl Default for ControlDoneReason {
    fn default() -> Self {
        Self::Unknown
    }
}

impl ControlDoneReason {
    pub fn as_wire(&self) -> &'static str {
        match self {
            Self::Unknown => "UNKNOWN",
            Self::Auto => "AUTO",
            Self::Stop => "STOP",
            Self::Error => "ERROR",
            Self::Cancel => "CANCEL",
            Self::Force => "FORCE",
            Self::Disconnect => "DISCONNECT",
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParentRef {
    pub id: String,
    pub name: String,
}

/// `catalog.push` / `get_routines` 返回的单条 routine 描述。
///
/// 对齐 Python `query.py build_routines`:`{name, is_passive, meta}`。
/// modules 不在此上报 —— 实例级,由 created 回报带回(catalog 注册时无实例,
/// 无 kwargs,静态上报对 dynamic 不准)。meta 是类级自由扩展字典,Go 侧 dumb
/// forward 透传。
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RoutineCatalogEntry {
    pub name: String,
    #[serde(default)]
    pub is_passive: bool,
    #[serde(default)]
    pub meta: RoutineMeta,
}

/// `get_running_routines` 返回的单条 running 实例。
///
/// 对齐 Python:`{name, id}`。
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunningRoutineInfo {
    pub id: String,
    pub name: String,
}

/// 原始 wire 事件:平铺 `event` 字段 + 其余字段 flatten。
///
/// 对齐 Python 的 `msg: dict` —— kernel 侧不解析结构,消费方按 `event` 分发
/// 后从 `fields` 取 envelope / payload。
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct RawWireEvent {
    #[serde(default)]
    pub event: String,
    #[serde(flatten)]
    pub fields: Map<String, Value>,
}

impl RawWireEvent {
    pub fn new(event: impl Into<String>) -> Self {
        Self {
            event: event.into(),
            fields: Map::new(),
        }
    }

    pub fn with_field(mut self, key: impl Into<String>, value: Value) -> Self {
        self.fields.insert(key.into(), value);
        self
    }

    pub fn id(&self) -> Option<&str> {
        self.fields.get("id").and_then(Value::as_str)
    }

    // --- envelope 访问器(message.* 的 data 里对 kernel 透明的保留字段) ---

    pub fn req_id(&self) -> Option<&str> {
        self.fields
            .get(envelope::ENVELOPE_REQ_ID)
            .and_then(Value::as_str)
    }

    pub fn reply_to(&self) -> Option<&str> {
        self.fields
            .get(envelope::ENVELOPE_REPLY_TO)
            .and_then(Value::as_str)
    }

    pub fn stream_id(&self) -> Option<&str> {
        self.fields
            .get(envelope::ENVELOPE_STREAM_ID)
            .and_then(Value::as_str)
    }

    /// 业务事件名(@request/@stream 的 key)。区别于 wire 层的 `event` 字段。
    pub fn envelope_event(&self) -> Option<&str> {
        self.fields
            .get(envelope::ENVELOPE_EVENT)
            .and_then(Value::as_str)
    }

    pub fn data(&self) -> Option<&Value> {
        self.fields.get(envelope::ENVELOPE_DATA)
    }

    pub fn is_ok(&self) -> Option<bool> {
        self.fields
            .get(envelope::ENVELOPE_OK)
            .and_then(Value::as_bool)
    }

    pub fn error(&self) -> Option<&str> {
        self.fields
            .get(envelope::ENVELOPE_ERROR)
            .and_then(Value::as_str)
    }

    pub fn chunk(&self) -> Option<&Value> {
        self.fields.get(envelope::ENVELOPE_CHUNK)
    }

    /// stream 收口标志:done / error / cancelled。
    pub fn eof(&self) -> Option<&str> {
        self.fields
            .get(envelope::ENVELOPE_EOF)
            .and_then(Value::as_str)
    }

    pub fn into_json_object(self) -> Map<String, Value> {
        let mut out = self.fields;
        out.insert("event".to_string(), Value::String(self.event));
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use super::envelope;

    #[test]
    fn control_done_reason_as_wire_covers_all_variants() {
        assert_eq!(ControlDoneReason::Unknown.as_wire(), "UNKNOWN");
        assert_eq!(ControlDoneReason::Auto.as_wire(), "AUTO");
        assert_eq!(ControlDoneReason::Stop.as_wire(), "STOP");
        assert_eq!(ControlDoneReason::Error.as_wire(), "ERROR");
        assert_eq!(ControlDoneReason::Cancel.as_wire(), "CANCEL");
        assert_eq!(ControlDoneReason::Force.as_wire(), "FORCE");
        assert_eq!(ControlDoneReason::Disconnect.as_wire(), "DISCONNECT");
    }

    #[test]
    fn control_done_reason_serde_round_trip() {
        let json = serde_json::to_string(&ControlDoneReason::Force).unwrap();
        assert_eq!(json, "\"FORCE\"");
        let back: ControlDoneReason = serde_json::from_str(&json).unwrap();
        assert_eq!(back, ControlDoneReason::Force);
    }

    #[test]
    fn routine_catalog_entry_serializes_name_is_passive_meta() {
        let entry = RoutineCatalogEntry {
            name: "edit".to_string(),
            is_passive: false,
            meta: Map::new(),
        };
        let json = serde_json::to_value(&entry).unwrap();
        assert_eq!(json.get("name").and_then(Value::as_str), Some("edit"));
        assert_eq!(json.get("is_passive").and_then(Value::as_bool), Some(false));
        assert!(json.get("meta").unwrap().is_object());
    }

    #[test]
    fn running_routine_info_only_has_id_and_name() {
        let info = RunningRoutineInfo {
            id: "r-1".to_string(),
            name: "echo".to_string(),
        };
        let json = serde_json::to_value(&info).unwrap();
        let obj = json.as_object().unwrap();
        assert_eq!(obj.len(), 2);
        assert!(obj.contains_key("id"));
        assert!(obj.contains_key("name"));
    }

    #[test]
    fn raw_wire_event_envelope_accessors() {
        let ev = RawWireEvent::new("message.req_reply")
            .with_field(envelope::ENVELOPE_REQ_ID, Value::String("rq-1".into()))
            .with_field(envelope::ENVELOPE_OK, Value::Bool(true))
            .with_field(envelope::ENVELOPE_DATA, Value::String("hi".into()));
        assert_eq!(ev.req_id(), Some("rq-1"));
        assert_eq!(ev.is_ok(), Some(true));
        assert_eq!(ev.data().and_then(Value::as_str), Some("hi"));
    }
}
