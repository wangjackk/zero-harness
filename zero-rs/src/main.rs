//! zero-rs 启动程序:hub 接入远程 kernel(dial-in)。
//!
//! 用法: cargo run [address] [hub_id]
//!
//! 只负责组装:routine 集合(lib)、日志(env/logging)、env、hub、连接;
//! 实现细节都在 lib crate。

use routine::{kernel, RoutineHub, RoutineHubOptions};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    zero_rs::logging::init();
    zero_rs::env::load_dotenv();

    let routines = zero_rs::routines::registry();

    let address = std::env::args().nth(1).unwrap_or_else(|| {
        std::env::var("ZERO_KERNEL_ADDR").unwrap_or_else(|_| "127.0.0.1:7777".to_string())
    });
    let hub_id = std::env::args()
        .nth(2)
        .unwrap_or_else(|| "zero-rs".to_string());

    tracing::info!("pid: {}", std::process::id());
    println!("🔗 dial-in zero-rs → kernel @ {address} (hub_id={hub_id})");
    zero_rs::banner::print_banner(&routines, &[]);

    let hub = RoutineHub::new(RoutineHubOptions {
        routines,
        modules: Vec::new(),
        hub_id,
    });
    let conn = hub.connect(kernel::remote(address));
    conn.join().await;
    Ok(())
}
