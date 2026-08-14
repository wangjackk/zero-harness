use crate::{
    core::RunContext,
    protocol::{events::ROUTINE_YIELD, ChainedError, JsonObject, JsonValue, RawWireEvent},
};
use async_trait::async_trait;
use futures::{Stream, StreamExt};
use schemars::JsonSchema;
use serde::de::DeserializeOwned;
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
/// 因含关联函数，本 trait 不能 ``dyn`` 派发；但泛型 factory
/// （``SimpleRoutineFactory<T>`` / ``RoutineFactory<T>``）知道具体 T，
/// ``T::meta()`` / ``T::modules(params)`` / ``T::is_dynamic()`` 静态调，无需 dyn。
/// ``WireRoutineFactory`` 的 ``&self`` 方法保留作 scheduler 的 dyn 入口，
/// 泛型 factory override 时 delegate 到这里。
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
pub trait WireRoutine: Send + Sync {
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
}

#[async_trait]
pub trait Routine: Default + Send + Sync {
    type Params: DeserializeOwned + JsonSchema + Send + Sync + 'static;

    fn input_schema_meta() -> JsonObject {
        let mut meta = JsonObject::new();
        meta.insert("input_schema".to_string(), schema_for::<Self::Params>());
        meta
    }

    async fn run(
        &self,
        ctx: RunContext,
        params: Self::Params,
    ) -> Result<RoutineOutput, RoutineError>;

    async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
        Ok(None)
    }
}

struct RoutineAdapter<T>
where
    T: Routine,
{
    inner: T,
}

#[async_trait]
impl<T> WireRoutine for RoutineAdapter<T>
where
    T: Routine + 'static,
{
    async fn run(
        &self,
        ctx: RunContext,
        params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        let params = serde_json::from_value(Value::Object(params))
            .map_err(|error| RoutineError::Message(format!("invalid params: {error}")))?;
        self.inner.run(ctx, params).await
    }

    async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
        self.inner.stop().await
    }
}

pub trait WireRoutineFactory: Send + Sync {
    fn routine_name(&self) -> &str;
    fn create(&self) -> Box<dyn WireRoutine>;

    fn enabled(&self) -> bool {
        true
    }

    fn is_passive(&self) -> bool {
        false
    }

    fn is_dynamic(&self) -> bool {
        false
    }

    /// 该 routine 占用的模块（dyn 入口，scheduler 经 ``Arc<dyn WireRoutineFactory>``
    /// 调）。默认空；泛型 factory（Simple/RoutineFactory）override 时 delegate 到
    /// ``T::modules(params)`` 关联函数（RoutineInfo trait，不建实例）。非泛型 factory
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

pub struct SimpleRoutineFactory<T>
where
    T: WireRoutine + Default + RoutineInfo + 'static,
{
    name: String,
    _marker: PhantomData<T>,
}

impl<T> SimpleRoutineFactory<T>
where
    T: WireRoutine + Default + RoutineInfo + 'static,
{
    pub fn new() -> Self {
        Self {
            name: T::NAME.to_string(),
            _marker: PhantomData,
        }
    }
}

impl<T> WireRoutineFactory for SimpleRoutineFactory<T>
where
    T: WireRoutine + Default + RoutineInfo + 'static,
{
    fn routine_name(&self) -> &str {
        &self.name
    }

    fn create(&self) -> Box<dyn WireRoutine> {
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

pub struct RoutineFactory<T>
where
    T: Routine + 'static,
{
    name: String,
    meta: JsonObject,
    signature: String,
    _marker: PhantomData<T>,
}

impl<T> RoutineFactory<T>
where
    T: Routine + RoutineInfo + 'static,
{
    pub fn new() -> Self {
        Self {
            name: T::NAME.to_string(),
            meta: T::meta(),
            signature: signature_for::<T::Params>(),
            _marker: PhantomData,
        }
    }

    pub fn with_name(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            ..Self::new()
        }
    }

    pub fn with_meta(mut self, meta: JsonObject) -> Self {
        self.meta = meta;
        self
    }

    pub fn with_signature(mut self, signature: impl Into<String>) -> Self {
        self.signature = signature.into();
        self
    }
}

impl<T> WireRoutineFactory for RoutineFactory<T>
where
    T: Routine + RoutineInfo + 'static,
{
    fn routine_name(&self) -> &str {
        &self.name
    }

    fn create(&self) -> Box<dyn WireRoutine> {
        Box::new(RoutineAdapter {
            inner: T::default(),
        })
    }

    fn meta(&self) -> JsonObject {
        self.meta.clone()
    }

    fn signature(&self) -> &str {
        &self.signature
    }

    // modules/is_dynamic 走关联函数 T::xxx()（RoutineInfo trait，类方法语义，不建实例）。
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
    routines: Arc<HashMap<String, Arc<dyn WireRoutineFactory>>>,
}

impl RoutineRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn from_factories(items: impl IntoIterator<Item = Arc<dyn WireRoutineFactory>>) -> Self {
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

    pub fn add_factory(&mut self, factory: Arc<dyn WireRoutineFactory>) {
        if !factory.enabled() {
            return;
        }
        Arc::make_mut(&mut self.routines).insert(factory.routine_name().to_string(), factory);
    }

    pub fn add<T>(&mut self) -> &mut Self
    where
        T: Routine + RoutineInfo + 'static,
    {
        self.add_factory(Arc::new(RoutineFactory::<T>::new()));
        self
    }

    pub fn get_routines(&self) -> Vec<Arc<dyn WireRoutineFactory>> {
        self.routines.values().cloned().collect()
    }

    pub fn get_routine(&self, name: &str) -> Option<Arc<dyn WireRoutineFactory>> {
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
                                .with_field("data", data)
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
    impl WireRoutine for DummyRoutine {
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
        let factory = SimpleRoutineFactory::<DummyRoutine>::new();
        let meta = factory.meta();
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
    struct TypedDummyRoutine;

    impl RoutineInfo for TypedDummyRoutine {
        const NAME: &'static str = "typed_dummy";
        fn meta() -> JsonObject {
            Self::input_schema_meta()
        }
    }

    #[async_trait]
    impl Routine for TypedDummyRoutine {
        type Params = DummyParams;

        async fn run(
            &self,
            _ctx: RunContext,
            params: Self::Params,
        ) -> Result<RoutineOutput, RoutineError> {
            Ok(RoutineOutput::Value(Value::String(params.text)))
        }

        async fn stop(&self) -> Result<Option<JsonValue>, RoutineError> {
            Ok(Some(Value::String("typed_dummy_stopped".to_string())))
        }
    }

    #[test]
    fn typed_factory_meta_includes_input_schema() {
        let factory = RoutineFactory::<TypedDummyRoutine>::new();
        let meta = factory.meta();
        assert_eq!(factory.routine_name(), "typed_dummy");
        assert_eq!(factory.signature(), "(text: string)");
        assert!(meta
            .get("input_schema")
            .and_then(Value::as_object)
            .and_then(|schema| schema.get("properties"))
            .and_then(Value::as_object)
            .is_some_and(|properties| properties.contains_key("text")));
    }

    #[test]
    fn typed_factory_meta_defaults_to_business_meta() {
        #[derive(Default)]
        struct NoMetaRoutine;

        impl RoutineInfo for NoMetaRoutine {
            const NAME: &'static str = "no_meta";
        }

        #[async_trait]
        impl Routine for NoMetaRoutine {
            type Params = DummyParams;

            async fn run(
                &self,
                _ctx: RunContext,
                _params: Self::Params,
            ) -> Result<RoutineOutput, RoutineError> {
                Ok(RoutineOutput::Empty)
            }
        }

        let factory = RoutineFactory::<NoMetaRoutine>::new();
        assert!(factory.meta().is_empty());
    }

    #[test]
    fn registry_add_registers_routine_type() {
        let mut registry = RoutineRegistry::new();
        registry.add::<TypedDummyRoutine>();

        assert!(registry.get_routine("typed_dummy").is_some());
    }

    #[test]
    fn signature_for_params_uses_json_schema() {
        assert_eq!(signature_for::<DummyParams>(), "(text: string)");
    }

    #[tokio::test]
    async fn typed_routine_stop_is_forwarded_to_wire_adapter() {
        let routine = RoutineFactory::<TypedDummyRoutine>::new().create();

        assert_eq!(
            routine.stop().await.unwrap(),
            Some(Value::String("typed_dummy_stopped".to_string()))
        );
    }
}
