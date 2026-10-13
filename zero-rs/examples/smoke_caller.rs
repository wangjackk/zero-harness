//! smoke_caller ---- 在途调用兜底冒烟的 rs 侧 caller.
//!
//! passive 的 smoke_rs_caller 自启后: 等 ts 侧 smoke_target 注册 → create +
//! start → 并发两条在途调用(slow req + handle wait)。由测试方中途 kill ts
//! 进程, 验证 kernel 合成失败帧让 rs 侧立即抛错(与 py caller 同一种错误
//! 报告), 而不是挂到超时。
//!
//! 运行(在 zero-rs/ 目录下):
//! ``cargo run --example smoke_caller -- 127.0.0.1:7799``

use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use routine::{
    core::{
        Routine, RoutineError, RoutineFactory, RoutineInfo, RoutineOutput,
        RoutineRegistry, RunContext,
    },
    kernel,
    protocol::JsonObject,
    RoutineHub, RoutineHubOptions,
};
use serde_json::json;

struct SmokeCaller;

impl RoutineInfo for SmokeCaller {
    const NAME: &'static str = "smoke_rs_caller";

    fn meta() -> JsonObject {
        let mut meta = JsonObject::new();
        meta.insert(
            "description".to_string(),
            json!("smoke: 在途调用兜底 rs caller(slow req + handle wait)"),
        );
        meta
    }
}

#[async_trait]
impl Routine for SmokeCaller {
    async fn run(
        &self,
        ctx: RunContext,
        _params: JsonObject,
    ) -> Result<RoutineOutput, RoutineError> {
        // 等 ts 侧 smoke_target 注册进 kernel
        let mut registered = false;
        for i in 0..60 {
            let entries = match ctx.get_routines().await {
                Ok(v) => v,
                Err(e) => {
                    println!("RS CALLER: get_routines err: {e}");
                    vec![]
                }
            };
            if entries.iter().any(|e| e.name == "smoke_target") {
                registered = true;
                println!("RS CALLER: target visible after {i} polls");
                break;
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
        if !registered {
            return Err(RoutineError::Message(
                "smoke_target not registered in 60s".to_string(),
            ));
        }
        println!("RS CALLER: target registered, create + start");

        let (child_id, _) = ctx
            .create("smoke_target", None)
            .await
            .map_err(RoutineError::Message)?;
        ctx.start_child(&child_id, 10).await   // 10 = 常规默认档
            .map_err(RoutineError::Message)?;

        println!("RS CALLER: 2 in-flight calls open, kill ts target now");

        let t0 = Instant::now();
        let req_fut = async {
            match ctx.req(&child_id, "smoke.slow", None, 300).await {
                Ok(v) => format!("req done dt={:?} result={v}", t0.elapsed()),
                Err(e) => format!("REQ ERROR dt={:?}: {e}", t0.elapsed()),
            }
        };
        let wait_fut = async {
            match ctx.wait_child(&child_id, 600).await {
                Ok(v) => format!("handle done dt={:?} result={v}", t0.elapsed()),
                Err(e) => format!("HANDLE ERROR dt={:?}: {e}", t0.elapsed()),
            }
        };
        let (req_res, wait_res) = tokio::join!(req_fut, wait_fut);
        println!("RS RESULT req:    {req_res}");
        println!("RS RESULT handle: {wait_res}");

        Ok(RoutineOutput::Value(json!({
            "req": req_res,
            "handle": wait_res,
        })))
    }
}

/// 泛型工厂不桥接 is_passive, 冒烟 caller 需要 passive 自启 → 手写 factory。
struct SmokeCallerFactory;

impl RoutineFactory for SmokeCallerFactory {
    fn routine_name(&self) -> &str {
        "smoke_rs_caller"
    }

    fn create(&self) -> Box<dyn Routine> {
        Box::new(SmokeCaller)
    }

    fn is_passive(&self) -> bool {
        true
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_writer(std::io::stdout)
        .init();

    let address = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:7799".to_string());

    let mut routines = RoutineRegistry::new();
    routines.add_factory(Arc::new(SmokeCallerFactory));

    let hub = RoutineHub::new(RoutineHubOptions {
        routines,
        modules: Vec::new(),
        hub_id: "smoke-rs-caller".to_string(),
    });
    println!("pid: {} (dial-in kernel {address})", std::process::id());
    // py kernel 暂未实现 lifecycle.heartbeat, watchdog 15s 无心跳即杀实例,
    // 长等待冒烟必被误杀 ---- 关掉(不影响被测的 pendingCall 兜底路径).
    let runtime = hub.runtime.clone();
    let conn = hub.connect(kernel::remote(address));
    runtime.set_watchdog_started(false);
    conn.join().await;
    Ok(())
}
