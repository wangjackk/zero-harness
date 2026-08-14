pub trait BaseModule: Send + Sync {
    fn module_id(&self) -> &str;
}
