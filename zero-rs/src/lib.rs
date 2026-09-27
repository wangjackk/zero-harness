//! zero-rs 业务 crate:routine 集合与启动支撑模块。
//!
//! bin(main.rs dial-in 模式)只管组装:调这里的 registry / logging / env,
//! 实现细节不进 bin,对齐 Python zero 的结构:routines 与启动程序分开。

pub mod audio;
pub mod banner;
pub mod env;
pub mod logging;
pub mod routines;
