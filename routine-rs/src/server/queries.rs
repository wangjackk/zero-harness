use crate::{
    grpc::{frame_to_json, json_to_frame, routine::Frame},
    protocol::{
        events::{
            REQ_EVENT_GET_MODULES, REQ_EVENT_GET_ROUTERS, REQ_EVENT_GET_ROUTINES,
            REQ_EVENT_GET_ROUTINE_FROM_ROUTER, REQ_EVENT_GET_ROUTINE_MODULES,
        },
        JsonObject, RoutineCatalogEntry,
    },
    server::ServerRuntime,
};
use serde_json::Value;
use std::sync::Arc;

pub struct QueryService {
    runtime: Arc<ServerRuntime>,
}

impl QueryService {
    pub fn new(runtime: Arc<ServerRuntime>) -> Self {
        Self { runtime }
    }

    pub async fn handle_req(&self, request: Frame) -> Frame {
        match self.handle_req_inner(request).await {
            Ok(response) => json_to_frame(&response),
            Err(error) => {
                let mut out = JsonObject::new();
                out.insert("error".to_string(), Value::String(error));
                json_to_frame(&out)
            }
        }
    }

    /// 构建 routine 列表(catalog.push 与 get_routines 共用)。
    pub fn build_routines(&self) -> Vec<Value> {
        self.runtime
            .routines
            .get_routines()
            .into_iter()
            .map(|factory| {
                serde_json::to_value(RoutineCatalogEntry {
                    name: factory.routine_name().to_string(),
                    is_passive: factory.is_passive(),
                    meta: factory.meta(),
                })
                .unwrap_or(Value::Null)
            })
            .collect()
    }

    async fn handle_req_inner(&self, request: Frame) -> Result<JsonObject, String> {
        let req = frame_to_json(&request);
        let event = req.get("event").and_then(Value::as_str).unwrap_or_default();
        match event {
            REQ_EVENT_GET_ROUTINES => {
                object_with("routines", Value::Array(self.build_routines()))
            }
            REQ_EVENT_GET_MODULES => object_with(
                "modules",
                Value::Array(
                    self.runtime
                        .modules
                        .iter()
                        .map(|module| Value::String(module.module_id().to_string()))
                        .collect(),
                ),
            ),
            REQ_EVENT_GET_ROUTINE_MODULES => {
                let routine_name = req.get("name").and_then(Value::as_str).unwrap_or_default();
                let params = req
                    .get("kwargs")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                let Some(factory) = self.runtime.routines.get_routine(routine_name) else {
                    return object_with("error", Value::String("routine not found".to_string()));
                };
                object_with(
                    "modules",
                    Value::Array(
                        factory
                            .modules(Some(&params))
                            .into_iter()
                            .map(Value::String)
                            .collect(),
                    ),
                )
            }
            REQ_EVENT_GET_ROUTERS => {
                let routers = self.runtime.routers.read().await;
                object_with(
                    "routers",
                    Value::Array(routers.keys().cloned().map(Value::String).collect()),
                )
            }
            REQ_EVENT_GET_ROUTINE_FROM_ROUTER => {
                let router_name = req.get("name").and_then(Value::as_str).unwrap_or_default();
                let params = req
                    .get("kwargs")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default();
                let routers = self.runtime.routers.read().await;
                let Some(router) = routers.get(router_name) else {
                    return object_with(
                        "error",
                        Value::String(format!("router {router_name} not found")),
                    );
                };
                let (name, kwargs) = router.router(params);
                let mut out = JsonObject::new();
                out.insert("name".to_string(), Value::String(name));
                out.insert("kwargs".to_string(), Value::Object(kwargs));
                Ok(out)
            }
            _ => object_with("error", Value::String("unknown event".to_string())),
        }
    }
}

fn object_with(key: &str, value: Value) -> Result<JsonObject, String> {
    let mut out = JsonObject::new();
    out.insert(key.to_string(), value);
    Ok(out)
}
