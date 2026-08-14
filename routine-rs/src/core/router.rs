use crate::protocol::JsonObject;

pub trait RouterRoutine: Send + Sync {
    fn name(&self) -> &str;
    fn router(&self, params: JsonObject) -> (String, JsonObject);
}
