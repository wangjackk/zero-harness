pub mod lifecycle;
pub mod queries;
pub mod router;
pub mod shell_requests;
pub mod state;
pub mod transport;

use crate::{
    core::{BaseModule, RouterRoutine, RoutineRegistry},
    grpc::GrpcRoutineService,
};
use std::{net::SocketAddr, sync::Arc};
use tonic::transport::Server;

pub use lifecycle::LifecycleManager;
pub use queries::QueryService;
pub use router::BusinessEventRouter;
pub use shell_requests::ShellReqManager;
pub use state::{HandleWaiter, ServerRuntime, SubmitWaiter};
pub use transport::OutboundTransport;

pub use crate::grpc::client::{GrpcClient, GrpcClientOptions};

pub struct RoutineServerOptions {
    pub routines: RoutineRegistry,
    pub modules: Vec<Arc<dyn BaseModule>>,
    pub routers: Vec<Arc<dyn RouterRoutine>>,
}

pub struct RoutineServer {
    pub runtime: Arc<ServerRuntime>,
    pub transport: Arc<OutboundTransport>,
    pub lifecycle: Arc<LifecycleManager>,
    pub shell_req: Arc<ShellReqManager>,
    pub query: Arc<QueryService>,
    pub business: Arc<BusinessEventRouter>,
    pub grpc_service: GrpcRoutineService,
}

impl RoutineServer {
    pub fn new(options: RoutineServerOptions) -> Self {
        let runtime = Arc::new(ServerRuntime::new(
            options.routines,
            options.modules,
            options.routers,
        ));
        let transport = Arc::new(OutboundTransport::new(runtime.clone()));
        let shell_req = Arc::new(ShellReqManager::new(runtime.clone(), transport.clone()));
        transport.attach_shell_req(shell_req.clone());
        let lifecycle = Arc::new(LifecycleManager::new(runtime.clone(), transport.clone()));
        let query = Arc::new(QueryService::new(runtime.clone()));
        let business = Arc::new(BusinessEventRouter::new(
            runtime.clone(),
            lifecycle.clone(),
            shell_req.clone(),
        ));
        let grpc_service = GrpcRoutineService::new(
            runtime.clone(),
            business.clone(),
            lifecycle.clone(),
            query.clone(),
        );
        lifecycle.ensure_watchdog();
        runtime.print_summary();
        Self {
            runtime,
            transport,
            lifecycle,
            shell_req,
            query,
            business,
            grpc_service,
        }
    }
}

pub struct StartServerOptions {
    pub routine_options: RoutineServerOptions,
    pub address: SocketAddr,
}

pub async fn start_server(options: StartServerOptions) -> Result<(), Box<dyn std::error::Error>> {
    let routine_server = RoutineServer::new(options.routine_options);
    let service = routine_server.grpc_service.into_tonic_service();
    tracing::info!("routine server started: {}", options.address);
    Server::builder()
        .add_service(service)
        .serve(options.address)
        .await?;
    Ok(())
}

/// dial-in:routine 作为 gRPC client 主动连接 kernel。
///
/// 连接后 push catalog → 收 lifecycle.created/start/stop/destroy 事件驱动 routine 运行。
/// 断线自动退避重连(200ms→5s)。
pub async fn start_client(options: StartClientOptions) -> Result<(), Box<dyn std::error::Error>> {
    let routine_server = RoutineServer::new(options.routine_options);
    let client = GrpcClient::new(
        routine_server.runtime.clone(),
        routine_server.lifecycle.clone(),
        routine_server.query.clone(),
        routine_server.transport.clone(),
        GrpcClientOptions {
            address: options.address,
            hub_id: options.hub_id,
        },
    );
    client.run().await;
    Ok(())
}

pub struct StartClientOptions {
    pub routine_options: RoutineServerOptions,
    pub address: String,
    pub hub_id: String,
}
