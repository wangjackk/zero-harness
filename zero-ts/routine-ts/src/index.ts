/** routine-ts ---- routine SDK 第二代 TypeScript 版:内核嵌入,进程内内存通信(无 grpc).
 * 与 z2(py)同构移植.

 分层:
   protocol / errors / transport   wire 常量、错误、传输抽象
   inproc                          hub↔kernel 进程内直连(FIFO + 查询直达)
   grpcServer / grpcClient         kernel 侧 grpc 服务 / hub 侧拨入客户端(Frame wire)
   kernel/                         嵌入式内核(tree / command / manager / launch)
   routine / ctx / handle          SDK 三件套(业务 routine 写在这上面)
   runtime / lifecycle / hub       hub 侧运行时、生命周期、通信编排
 */
export {
  AcquireError, DeregisterError, LoadModuleError, RegisterError, ReloadError,
  ReleaseError, ReqError, ReqTimeout, StartError, StreamCancelled,
  StreamError, StreamTimeout, SubmitError, TimeoutErr, UnloadModuleError,
} from './errors'
export type { Msg, Payload } from './protocol'
export { ControlDoneReason } from './protocol'
export { RoutineHandle } from './handle'
export { RoutineHub, System, launch } from './hub'
export { Kernel, Node, Route, type CreatedResult } from './kernel'
export { Command, ConflictError, State, Tree } from './kernel'
export {
  Connection, InprocKernel, KernelRef, RemoteKernel,
  connect, create as createKernel, kernel, remote,
} from './kernel/launch'
export { ModuleTree } from './moduleTree'
export {
  type Modules, PassiveConfig, PRI_NORMAL, PRI_SAFETY, PRI_USER,
  type RoutineClass, Routine, type RoutineSource,
  Routines, passiveWire, request, routineNameOf, stream, subscribe,
} from './routine'
export { RunContext, Namespace, type ProviderStream } from './ctx'
export { ServerRuntime } from './runtime'
export { StreamReader, StreamCtx } from './stream'
export { Transport } from './transport'
export { InProcessLink, InProcessTransport, KERNEL_PEER, HUB_CONN } from './inproc'
export { GrpcKernelServer, frameDecode, frameEncode } from './grpcServer'
export { GrpcClientTransport } from './grpcClient'
export { LifecycleManager } from './lifecycle'
