use crate::{
    core::{
        messaging::{RequestSpec, RoutineSource, SubscribeSpec},
        RunContext,
    },
    protocol::{events::ROUTINE_YIELD, ChainedError, JsonObject, JsonValue, RawWireEvent},
};
use async_trait::async_trait;
use futures::{Stream, StreamExt};
use schemars::JsonSchema;
use serde_json::Value;
use std::{collections::HashMap, marker::PhantomData, pin::Pin, sync::Arc};
use thiserror::Error;

pub type RoutineStream =
    Pin<Box<dyn Stream<Item = Result<JsonValue, RoutineError>> + Send + 'static>>;

#[derive(Debug, Error)]
pub enum RoutineError {
    #[error("{0}")]
    Message(String),
    #[error(transparent)]
    Chained(#[from] ChainedErrorAsError),
}

#[derive(Debug, Error)]
#[error("{}", .0.msg)]
pub struct ChainedErrorAsError(pub ChainedError);

#[derive(Default)]
pub enum RoutineOutput {
    #[default]
    Empty,
    Value(JsonValue),
    Stream(RoutineStream),
}

/// routine 的静态元信息（**关联函数**，类方法语义，不依赖实例、不建实例）。
/// 对齐 Python ``cls.modules(kwargs)`` / ``cls.is_dynamic()`` / ``cls.meta()``。
///
/// 把原 ``Routine::meta()``（功能元数据 dict）+ modules + is_dynamic 合并到一处——
/// 都是静态类型数据，聚在一个 trait 声明，跟 routine 定义在一起。
///
/// 因含关联函数，本 trait 不能 ``dyn`` 派发；但泛型工厂知道具体 T，
/// ``T::meta()`` / ``T::modules(params)`` / ``T::is_dynamic()`` 静态调，无需 dyn。
/// ``RoutineFactory`` 的 ``&self`` 方法保留作 scheduler 的 dyn 入口，
/// 泛型工厂 override 时 delegate 到这里。
pub trait RoutineInfo {
    /// routine 名字（注册 key，scheduler 路由用）。关联常量，类方法语义，
    /// 无默认 —— 强制每个 routine 显式提供，避免遗漏时静默用空名。
    const NAME: &'static str;

    /// 功能元数据 dict（tool / readonly / concurrency_safe / input_schema /
    /// output_schema / description）。默认空。
    fn meta() -> JsonObject {
        JsonObject::new()
    }

    /// 占用的模块（module_id 字符串列表）。同模块兄弟由 scheduler 串行（模块互斥）。
    /// ``params`` 为本次入参（动态 routine 可按入参选不同模块）；``None`` 返回默认/静态。
    fn modules(_params: Option<&JsonObject>) -> Vec<String> {
        Vec::new()
    }

    /// 是否动态 routine（每次 push 动态解析模块/接口）。默认 false。
    fn is_dynamic() -> bool {
        false
    }
}

#[async_trait]
pub trait Routine: Send + Sync {
    async fn run(
        &self,
        ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError>;

    /// routine 被创建时调一次（早于 start）。对齐 Python `on_created(rid, kwargs)`。
    /// modules 声明走 `RoutineInfo::modules(params)` 关联函数（单一真理源），
    /// 本 hook 纯做轻量初始化，不返回 modules。
    async fn on_created(&self, _rid: &str, _kwargs: &JsonObject) -> Result<(), RoutineError> {
        Ok(())
    }

    /// started 回报后、run() 之前调。对齐 Python `on_started()`。
    async fn on_started(&self) -> Result<(), RoutineError> {
        Ok(())
    }

    /// run 完成/退出后调（stopped 回报前）。对齐 Python
    /// `on_stopped(reason, result, detail)`。reason: auto/stop/error/cancel/force/disconnect。
    async fn on_stopped(
        &self,
        _reason: &str,
        _result: Option<&JsonValue>,
        _detail: &str,
    ) -> Result<(), RoutineError> {
        Ok(())
    }

    async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
        Ok(None)
    }

    /// destroy hook: created 态被销毁时的资源撤销(与 stop 分野)。
    /// 仅在 create 之后、未 start 的实例被 destroy 时调用(started 实例的
    /// 终止走 stop)。start 前实例可能已收 body 持有资源 —— 在此撤销。
    /// 对齐 Python `Routine.destroy`。
    async fn destroy(&self) -> Result<Option<JsonValue>, RoutineError> {
        Ok(None)
    }

    /// 收到 message.delivered 的回调(实例 mailbox 串行投递,同实例严格保序)。
    /// 对齐 Python `on_message`。
    async fn on_message(
        &self,
        _source: RoutineSource,
        _data: JsonValue,
    ) -> Result<(), RoutineError> {
        Ok(())
    }

    /// @request 声明:lifecycle created 时收集进 runtime 分发表。
    fn request_specs(&self) -> Vec<RequestSpec> {
        Vec::new()
    }

    /// @subscribe 声明:created 时自动发 pubsub.subscribe + 注册本地 handler。
    fn subscribe_specs(&self) -> Vec<SubscribeSpec> {
        Vec::new()
    }
}

pub trait RoutineFactory: Send + Sync {
    fn routine_name(&self) -> &str;
    fn create(&self) -> Box<dyn Routine>;

    fn enabled(&self) -> bool {
        true
    }

    fn is_passive(&self) -> bool {
        false
    }

    /// passive 声明优先级(passive 没有 call-site, 声明处即调用点; 档位同
    /// start:10=常规默认档, 0 为最低档永无驱逐资格)。wire 上非 10 才写
    /// `is_passive.priority` 字段;现有 factory 实现零改动(默认方法)。
    fn passive_priority(&self) -> i64 {
        10
    }

    fn is_dynamic(&self) -> bool {
        false
    }

    /// 该 routine 占用的模块（dyn 入口，scheduler 经 ``Arc<dyn RoutineFactory>``
    /// 调）。默认空；泛型工厂 override 时 delegate 到
    /// ``T::modules(params)`` 关联函数（RoutineInfo trait，不建实例）。非泛型工厂
    /// 用默认空。
    fn modules(&self, _params: Option<&JsonObject>) -> Vec<String> {
        Vec::new()
    }

    fn meta(&self) -> JsonObject {
        JsonObject::new()
    }

    fn doc(&self) -> &str {
        ""
    }

    fn signature(&self) -> &str {
        "(params)"
    }
}

/// 泛型 routine 的 dyn 桥：``RoutineInfo`` 含关联函数不能 ``dyn``，经此桥接。
/// 私有 —— 外部一律走 ``RoutineRegistry::add::<T>()``，不感知工厂概念。
struct RoutineFactoryFor<T>
where
    T: Routine + Default + RoutineInfo + 'static,
{
    name: String,
    _marker: PhantomData<T>,
}

impl<T> RoutineFactoryFor<T>
where
    T: Routine + Default + RoutineInfo + 'static,
{
    fn new() -> Self {
        Self {
            name: T::NAME.to_string(),
            _marker: PhantomData,
        }
    }
}

impl<T> RoutineFactory for RoutineFactoryFor<T>
where
    T: Routine + Default + RoutineInfo + 'static,
{
    fn routine_name(&self) -> &str {
        &self.name
    }

    fn create(&self) -> Box<dyn Routine> {
        Box::new(T::default())
    }

    // meta/modules/is_dynamic 走关联函数 T::xxx()（RoutineInfo trait，类方法语义，不建实例）。
    fn meta(&self) -> JsonObject {
        T::meta()
    }

    fn modules(&self, params: Option<&JsonObject>) -> Vec<String> {
        T::modules(params)
    }

    fn is_dynamic(&self) -> bool {
        T::is_dynamic()
    }
}

pub fn schema_for<T>() -> JsonValue
where
    T: JsonSchema,
{
    serde_json::to_value(schemars::schema_for!(T)).unwrap_or(Value::Null)
}

pub fn signature_for<T>() -> String
where
    T: JsonSchema,
{
    signature_from_schema(&schema_for::<T>())
}

fn signature_from_schema(schema: &JsonValue) -> String {
    let properties = schema
        .get("properties")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if properties.is_empty() {
        return "()".to_string();
    }

    let required = schema
        .get("required")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .collect::<std::collections::HashSet<_>>()
        })
        .unwrap_or_default();

    let mut parts = properties
        .iter()
        .map(|(name, field_schema)| {
            let optional = if required.contains(name.as_str()) {
                ""
            } else {
                "?"
            };
            format!("{name}{optional}: {}", schema_type_name(field_schema))
        })
        .collect::<Vec<_>>();
    parts.sort();
    format!("({})", parts.join(", "))
}

fn schema_type_name(schema: &JsonValue) -> String {
    if let Some(values) = schema.get("enum").and_then(Value::as_array) {
        if !values.is_empty() {
            return values
                .iter()
                .map(|value| serde_json::to_string(value).unwrap_or_else(|_| "unknown".to_string()))
                .collect::<Vec<_>>()
                .join(" | ");
        }
    }

    if let Some(items) = schema.get("anyOf").and_then(Value::as_array) {
        return items
            .iter()
            .map(schema_type_name)
            .collect::<Vec<_>>()
            .join(" | ");
    }
    if let Some(items) = schema.get("oneOf").and_then(Value::as_array) {
        return items
            .iter()
            .map(schema_type_name)
            .collect::<Vec<_>>()
            .join(" | ");
    }

    match schema.get("type") {
        Some(Value::Array(types)) => {
            let mut names = types
                .iter()
                .filter_map(Value::as_str)
                .filter(|name| *name != "null")
                .map(schema_type_name_from_str)
                .collect::<Vec<_>>();
            names.sort();
            if names.is_empty() {
                "null".to_string()
            } else {
                names.join(" | ")
            }
        }
        Some(Value::String(value)) if value == "array" => {
            let item_type = schema
                .get("items")
                .map(schema_type_name)
                .unwrap_or_else(|| "unknown".to_string());
            if item_type.contains(" | ") {
                format!("({item_type})[]")
            } else {
                format!("{item_type}[]")
            }
        }
        Some(Value::String(value)) => schema_type_name_from_str(value),
        _ => "unknown".to_string(),
    }
}

fn schema_type_name_from_str(value: &str) -> String {
    match value {
        "integer" | "number" => "number".to_string(),
        "boolean" => "boolean".to_string(),
        "string" => "string".to_string(),
        "object" => "object".to_string(),
        "null" => "null".to_string(),
        other => other.to_string(),
    }
}

#[derive(Clone, Default)]
pub struct RoutineRegistry {
    routines: Arc<HashMap<String, Arc<dyn RoutineFactory>>>,
}

impl RoutineRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn from_factories(items: impl IntoIterator<Item = Arc<dyn RoutineFactory>>) -> Self {
        let mut routines = HashMap::new();
        for factory in items {
            if factory.enabled() {
                routines.insert(factory.routine_name().to_string(), factory);
            }
        }
        Self {
            routines: Arc::new(routines),
        }
    }

    /// 注册泛型 routine（``T: Routine + Default + RoutineInfo``）。
    /// 工厂在内部装箱，调用方不感知。
    pub fn add<T>(&mut self)
    where
        T: Routine + Default + RoutineInfo + 'static,
    {
        self.add_factory(Arc::new(RoutineFactoryFor::<T>::new()));
    }

    pub fn add_factory(&mut self, factory: Arc<dyn RoutineFactory>) {
        if !factory.enabled() {
            return;
        }
        Arc::make_mut(&mut self.routines).insert(factory.routine_name().to_string(), factory);
    }

    pub fn get_routines(&self) -> Vec<Arc<dyn RoutineFactory>> {
        self.routines.values().cloned().collect()
    }

    pub fn get_routine(&self, name: &str) -> Option<Arc<dyn RoutineFactory>> {
        self.routines.get(name).cloned()
    }
}

impl std::fmt::Display for RoutineRegistry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let mut names = self.routines.keys().cloned().collect::<Vec<_>>();
        names.sort();
        write!(f, "{}", names.join(", "))
    }
}

pub async fn drain_run_result(
    result: RoutineOutput,
    ctx: &RunContext,
    routine_name: &str,
) -> Result<Option<JsonValue>, RoutineError> {
    match result {
        RoutineOutput::Empty => Ok(None),
        RoutineOutput::Value(value) => Ok(Some(value)),
        RoutineOutput::Stream(mut stream) => {
            while let Some(item) = stream.next().await {
                match item {
                    Ok(data) => {
                        ctx.send_raw_event(
                            RawWireEvent::new(ROUTINE_YIELD)
                                .with_field("id", Value::String(ctx.id().to_string()))
                                .with_field("source_id", Value::String(ctx.id().to_string()))
                                .with_data(&data)
                                .with_field("is_final", Value::Bool(false)),
                        )
                        .await
                        .map_err(RoutineError::Message)?;
                    }
                    Err(error) => {
                        let wire = ChainedError::leaf(routine_name, error.to_string()).to_wire();
                        ctx.send_raw_event(
                            RawWireEvent::new(ROUTINE_YIELD)
                                .with_field("id", Value::String(ctx.id().to_string()))
                                .with_field("source_id", Value::String(ctx.id().to_string()))
                                .with_field("is_final", Value::Bool(true))
                                .with_field(
                                    "error",
                                    serde_json::to_value(wire).unwrap_or(Value::Null),
                                ),
                        )
                        .await
                        .map_err(RoutineError::Message)?;
                        return Err(error);
                    }
                }
            }
            ctx.send_raw_event(
                RawWireEvent::new(ROUTINE_YIELD)
                    .with_field("id", Value::String(ctx.id().to_string()))
                    .with_field("source_id", Value::String(ctx.id().to_string()))
                    .with_field("is_final", Value::Bool(true)),
            )
            .await
            .map_err(RoutineError::Message)?;
            Ok(None)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use schemars::JsonSchema;
    use serde::Deserialize;

    #[derive(Default)]
    struct DummyRoutine;

    #[async_trait]
    impl Routine for DummyRoutine {
        async fn run(
            &self,
            _ctx: RunContext,
            _params: JsonObject,
        ) -> Result<RoutineOutput, RoutineError> {
            Ok(RoutineOutput::Empty)
        }
    }

    impl RoutineInfo for DummyRoutine {
        const NAME: &'static str = "dummy";
        fn meta() -> JsonObject {
            let mut meta = JsonObject::new();
            meta.insert("input_schema".to_string(), schema_for::<DummyParams>());
            meta
        }
    }

    #[derive(Deserialize, JsonSchema)]
    struct DummyParams {
        #[allow(dead_code)]
        text: String,
    }

    #[test]
    fn factory_meta_includes_input_schema() {
        let mut registry = RoutineRegistry::new();
        registry.add::<DummyRoutine>();
        let meta = registry
            .get_routine("dummy")
            .expect("dummy routine should be registered")
            .meta();
        let schema = meta
            .get("input_schema")
            .and_then(Value::as_object)
            .expect("input_schema should be an object");

        assert_eq!(schema.get("type").and_then(Value::as_str), Some("object"));
        assert!(schema
            .get("properties")
            .and_then(Value::as_object)
            .is_some_and(|properties| properties.contains_key("text")));
    }

    #[derive(Default)]
    struct StopDummyRoutine;

    impl RoutineInfo for StopDummyRoutine {
        const NAME: &'static str = "stop_dummy";
    }

    /// 业务风格:impl Routine + run 内部 from_value 解析强类型 params。
    #[async_trait]
    impl Routine for StopDummyRoutine {
        async fn run(
            &self,
            _ctx: RunContext,
            params: JsonObject,
        ) -> Result<RoutineOutput, RoutineError> {
            let params: DummyParams = serde_json::from_value(Value::Object(params))
                .map_err(|error| RoutineError::Message(format!("invalid params: {error}")))?;
            Ok(RoutineOutput::Value(Value::String(params.text)))
        }

        async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
            Ok(Some(Value::String("stop_dummy_stopped".to_string())))
        }
    }

    #[test]
    fn signature_for_params_uses_json_schema() {
        assert_eq!(signature_for::<DummyParams>(), "(text: string)");
    }

    #[tokio::test]
    async fn routine_stop_is_called_on_created_instance() {
        let mut registry = RoutineRegistry::new();
        registry.add::<StopDummyRoutine>();
        let routine = registry
            .get_routine("stop_dummy")
            .expect("stop_dummy routine should be registered")
            .create();

        assert_eq!(
            routine.stop().await.unwrap(),
            Some(Value::String("stop_dummy_stopped".to_string()))
        );
    }
}
