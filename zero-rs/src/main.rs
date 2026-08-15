//! zero-rs 启动程序:gRPC client 主动拨入 kernel(dial-in)。
//!
//! 用法: cargo run [address] [hub_id]
//!
//! routine 集合在 lib(src/routines/),这里只管启动。

use routine::server::{start_client, RoutineServerOptions, StartClientOptions};
use zero_rs::routines;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    let routines = routines::registry();

    let address = std::env::args().nth(1).unwrap_or_else(|| {
        std::env::var("ZERO_KERNEL_ADDR").unwrap_or_else(|_| "127.0.0.1:8888".to_string())
    });
    let hub_id = std::env::args()
        .nth(2)
        .unwrap_or_else(|| "zero-rs".to_string());

    println!("pid: {}", std::process::id());
    println!("🔗 dial-in zero-rs → kernel @ {address} (hub_id={hub_id})");

    start_client(StartClientOptions {
        routine_options: RoutineServerOptions {
            routines,
            modules: Vec::new(),
            routers: Vec::new(),
        },
        address,
        hub_id,
    })
    .await
}
