//! kernel 句柄与组装入口(对齐 Python `routine/kernel/launch.py`)。
//!
//! Rust 版暂只有远程引用;进程内 kernel(inproc)是缺失的实现,
//! 补上时在此加 `create(modules, listen)`。用法:
//!
//! ```ignore
//! let hub = RoutineHub::new(options);       // hub 侧组装
//! let conn = hub.connect(kernel::remote("127.0.0.1:52051"));
//! conn.join().await;                       // 常驻挂住
//! ```

/// 远程 kernel 引用(`kernel::remote(addr)` 产物);hub.connect 时经 grpc 拨入。
pub struct RemoteKernel {
    pub address: String,
}

/// 远程 kernel 引用(不建本地实体);hub.connect 时经 grpc 拨入。
pub fn remote(address: impl Into<String>) -> RemoteKernel {
    RemoteKernel {
        address: address.into(),
    }
}
