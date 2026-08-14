use prost_types::{value::Kind, ListValue, Struct, Value as ProtoValue};
use serde_json::{Map, Number, Value};
use std::collections::BTreeMap;

pub type JsonObject = Map<String, Value>;
pub type JsonValue = Value;

pub fn json_to_struct(data: &JsonObject) -> Struct {
    Struct {
        fields: data
            .iter()
            .map(|(key, value)| (key.clone(), json_to_proto_value(value)))
            .collect(),
    }
}

pub fn struct_to_json(value: &Struct) -> JsonObject {
    value
        .fields
        .iter()
        .map(|(key, value)| (key.clone(), proto_value_to_json(value)))
        .collect()
}

fn json_to_proto_value(value: &Value) -> ProtoValue {
    let kind = match value {
        Value::Null => Kind::NullValue(0),
        Value::Bool(value) => Kind::BoolValue(*value),
        Value::Number(value) => Kind::NumberValue(value.as_f64().unwrap_or_default()),
        Value::String(value) => Kind::StringValue(value.clone()),
        Value::Array(values) => Kind::ListValue(ListValue {
            values: values.iter().map(json_to_proto_value).collect(),
        }),
        Value::Object(values) => Kind::StructValue(Struct {
            fields: map_to_fields(values),
        }),
    };
    ProtoValue { kind: Some(kind) }
}

fn map_to_fields(values: &Map<String, Value>) -> BTreeMap<String, ProtoValue> {
    values
        .iter()
        .map(|(key, value)| (key.clone(), json_to_proto_value(value)))
        .collect()
}

fn proto_value_to_json(value: &ProtoValue) -> Value {
    match value.kind.as_ref() {
        Some(Kind::NullValue(_)) | None => Value::Null,
        Some(Kind::NumberValue(value)) => {
            if value.is_finite() && value.fract() == 0.0 {
                Value::Number(Number::from(*value as i64))
            } else {
                Number::from_f64(*value)
                    .map(Value::Number)
                    .unwrap_or(Value::Null)
            }
        }
        Some(Kind::StringValue(value)) => Value::String(value.clone()),
        Some(Kind::BoolValue(value)) => Value::Bool(*value),
        Some(Kind::StructValue(value)) => Value::Object(struct_to_json(value)),
        Some(Kind::ListValue(value)) => {
            Value::Array(value.values.iter().map(proto_value_to_json).collect())
        }
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

        let proto = json_to_struct(&object);
        let restored = struct_to_json(&proto);

        assert_eq!(Value::Object(restored), raw);
    }
}
