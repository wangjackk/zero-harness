pub mod lifecycle;
pub mod queries;
pub mod router;
pub mod state;
pub mod transport;

use crate::{
    core::{BaseModule, RoutineRegistry},
    grpc::GrpcRoutineService,
    kernel::RemoteKernel,
};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tonic::transport::Server;

pub use lifecycle::LifecycleManager;
pub use queries::QueryService;
pub use router::BusinessEventRouter;
pub use state::{HandleWaiter, ServerRuntime, CreateWaiter};
pub use transport::OutboundTransport;

pub use crate::grpc::client::{GrpcClient, GrpcClientOptions};

pub struct RoutineHubOptions {
    pub routines: RoutineRegistry,
    pub modules: Vec<Arc<dyn BaseModule>>,
    /// hub 身份标识(catalog.push 带;对齐 Python RoutineHub(hub_id=...)).
    pub hub_id: String,
}

/// hub 侧组装体(对齐 Python RoutineHub):routines 注册表 + 生命周期/查询/路由.
pub struct RoutineHub {
    pub runtime: Arc<ServerRuntime>,
    pub transport: Arc<OutboundTransport>,
    pub lifecycle: Arc<LifecycleManager>,
    pub query: Arc<QueryService>,
    pub business: Arc<BusinessEventRouter>,
    pub grpc_service: GrpcRoutineService,
    pub hub_id: String,
}

impl RoutineHub {
    pub fn new(options: RoutineHubOptions) -> Self {
        let runtime = Arc::new(ServerRuntime::new(
            options.routines,
            options.modules,
        ));
        let transport = Arc::new(OutboundTransport::new(runtime.clone()));
        let lifecycle = Arc::new(LifecycleManager::new(runtime.clone(), transport.clone()));
        let query = Arc::new(QueryService::new(runtime.clone()));
        let business = Arc::new(BusinessEventRouter::new(
            runtime.clone(),
            transport.clone(),
            lifecycle.clone(),
        ));
        let grpc_service = GrpcRoutineService::new(
            runtime.clone(),
            business.clone(),
            lifecycle.clone(),
            query.clone(),
        );
        // 暂时关闭 heartbeat watchdog: py/TS kernel 未实现 lifecycle.heartbeat
        // 发送侧, 长跑 routine 会被 15s 误杀(heartbeat timeout)。
        // TODO: kernel 侧实现周期 heartbeat 后恢复。
        // lifecycle.ensure_watchdog();
        runtime.print_summary();
        let hub_id = options.hub_id;
        Self {
            runtime,
            transport,
            lifecycle,
            query,
            business,
            grpc_service,
            hub_id,
        }
    }

    /// 接入 kernel(RemoteKernel 引用),grpc 拨入 + catalog push + 后台重连,
    /// 返回 Connection(stop 级联关停 / join 挂住)。
    ///
    /// 对齐 Python `hub.connect(kernel.remote(addr))`:连接在后台任务驱动,
    /// 断线退避重连(200ms→5s),不阻塞本调用。
    pub fn connect(self, kernel: RemoteKernel) -> Connection {
        let client = Arc::new(GrpcClient::new(
            self.runtime.clone(),
            self.lifecycle.clone(),
            self.query.clone(),
            self.business.clone(),
            self.transport.clone(),
            GrpcClientOptions {
                address: kernel.address,
                hub_id: self.hub_id.clone(),
            },
        ));
        let handle = {
            let client = client.clone();
            tokio::spawn(async move {
                client.run().await;
            })
        };
        Connection { client, handle }
    }
}

/// hub.connect(kernel) 的产物:连接持有体(对齐 Python kernel.launch.Connection)。
pub struct Connection {
    client: Arc<GrpcClient>,
    handle: tokio::task::JoinHandle<()>,
}

impl Connection {
    /// 级联关停:停重连循环并等待后台任务退出(5s 上限)。
    pub async fn stop(self) {
        self.client.stop();
        let _ = tokio::time::timeout(Duration::from_secs(5), self.handle).await;
    }

    /// 挂住直到连接任务结束(常驻入口的 await 点)。
    pub async fn join(self) {
        let _ = self.handle.await;
    }
}

pub struct StartServerOptions {
    pub routine_options: RoutineHubOptions,
    pub address: SocketAddr,
}

pub async fn start_server(options: StartServerOptions) -> Result<(), Box<dyn std::error::Error>> {
    let hub = RoutineHub::new(options.routine_options);
    let service = hub.grpc_service.into_tonic_service();
    tracing::info!("routine hub listening: {}", options.address);
    Server::builder()
        .add_service(service)
        .serve(options.address)
        .await?;
    Ok(())
}
