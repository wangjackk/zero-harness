//! 精简 routine SDK 的 wire 协议常量(对齐 Python `routine/protocol.py`)。
//!
//! 平铺事件名(`msg["event"]` 分发)。只保留 create/start/stop 生命周期 +
//! Req 查询 + routine↔routine 通信(message.*) + pubsub + routine.yield +
//! 运行时模块占领/释放(routine.*) + catalog 推送/增量 + module.tree。

// ---------------------------------------------------------------------------
// lifecycle 事件(走 Stream 双向流)
// ---------------------------------------------------------------------------

/// 双向:调度器→server 实例化+注册;server→调度器 created 回报(带 modules)
pub const LIFECYCLE_CREATED: &str = "lifecycle.created";
/// 调度器 → server:启动 routine
pub const LIFECYCLE_START: &str = "lifecycle.start";
/// 调度器 → server:打断 routine(started 态)
pub const LIFECYCLE_STOP: &str = "lifecycle.stop";
/// 调度器 → server:销毁 created 态 routine(未 start,无 body)
pub const LIFECYCLE_DESTROY: &str = "lifecycle.destroy";
/// server → 调度器:已启动
pub const LIFECYCLE_STARTED: &str = "lifecycle.started";
/// server → 调度器:已停止(带 reason)
pub const LIFECYCLE_STOPPED: &str = "lifecycle.stopped";

// ---------------------------------------------------------------------------
// Req 查询事件(走 Req unary)
// ---------------------------------------------------------------------------

pub const REQ_EVENT_GET_MODULES: &str = "get_modules";
pub const REQ_EVENT_GET_ROUTINES: &str = "get_routines";
/// dial-in routine→kernel 拉 module.tree
pub const REQ_EVENT_GET_MODULE_TREE: &str = "get_module_tree";
/// dial-in routine→kernel 查 running 实例 [{name,id}]
pub const REQ_EVENT_GET_RUNNING_ROUTINES: &str = "get_running_routines";

// ---------------------------------------------------------------------------
// routine↔routine 通信事件(py→kernel→py,kernel 做 broker 转发)
//
// message.* 是 dumb forwarder:kernel 按 target_ids 逐个转发成对应的 delivered。
// envelope(__req_id__/__stream_id__/event 等)全在 data 里,kernel 不解析,
// 消费方 demux。
// ---------------------------------------------------------------------------

pub const MESSAGE_SEND: &str = "message.send";
pub const MESSAGE_DELIVERED: &str = "message.delivered";
pub const MESSAGE_REQ: &str = "message.req";
pub const MESSAGE_REQ_DELIVERED: &str = "message.req_delivered";
pub const MESSAGE_REQ_REPLY: &str = "message.req_reply";
pub const MESSAGE_REQ_REPLY_DELIVERED: &str = "message.req_reply_delivered";
pub const MESSAGE_STREAM_OPEN: &str = "message.stream_open";
pub const MESSAGE_STREAM_OPEN_DELIVERED: &str = "message.stream_open_delivered";
pub const MESSAGE_STREAM_DATA: &str = "message.stream_data";
pub const MESSAGE_STREAM_DATA_DELIVERED: &str = "message.stream_data_delivered";
pub const MESSAGE_STREAM_CANCEL: &str = "message.stream_cancel";
pub const MESSAGE_STREAM_CANCEL_DELIVERED: &str = "message.stream_cancel_delivered";

// ---------------------------------------------------------------------------
// pubsub 事件(py→kernel→py,kernel 维护订阅表做 fanout)
// ---------------------------------------------------------------------------

pub const PUBSUB_SUBSCRIBE: &str = "pubsub.subscribe";
pub const PUBSUB_UNSUBSCRIBE: &str = "pubsub.unsubscribe";
pub const PUBSUB_PUBLISH: &str = "pubsub.publish";
pub const PUBSUB_DELIVERED: &str = "pubsub.delivered";

// ---------------------------------------------------------------------------
// yield 事件(child→parent routine yield,kernel dumb forward)
// ---------------------------------------------------------------------------

/// child→kernel: yield 一项(is_final=true 收尾)
pub const ROUTINE_YIELD: &str = "routine.yield";
/// kernel→parent: 转发(按 id=child_id 路由到 handle)
pub const ROUTINE_YIELDED: &str = "routine.yielded";

// ---------------------------------------------------------------------------
// 运行时模块占领/释放(py→kernel,走同一条 Stream)
//
// 跟类静态声明同一底层 TryAcquire/Release,只是触发在 run() 体里(用户主动调)。
// acquire 冲突要拿结果,必须等 acquired ack;release 等 released ack 保持对称。
// ---------------------------------------------------------------------------

pub const ROUTINE_ACQUIRE: &str = "routine.acquire";
pub const ROUTINE_ACQUIRED: &str = "routine.acquired";
pub const ROUTINE_RELEASE: &str = "routine.release";
pub const ROUTINE_RELEASED: &str = "routine.released";
/// 强制释放模块(驱逐 cone 内第三方 holder 后空出,不自己占)
pub const ROUTINE_FORCE_RELEASE: &str = "routine.force_release";
/// 强制占领模块(驱逐 cone 内第三方 holder 后自己占住,带驱逐的 acquire;ack 复用 routine.acquired)
pub const ROUTINE_FORCE_ACQUIRE: &str = "routine.force_acquire";
/// 抢占式 start 子(驱逐占住者后 start)
pub const ROUTINE_FORCE_START: &str = "routine.force_start";
/// py(dial-out)→kernel: 查 running 实例(带 req_id,kernel 回 get_running_reply)
pub const ROUTINE_GET_RUNNING: &str = "routine.get_running";
pub const ROUTINE_GET_RUNNING_REPLY: &str = "routine.get_running_reply";
/// py(dial-out)→kernel: 拉 module.tree(带 req_id,kernel 回 get_module_tree_reply)
pub const ROUTINE_GET_MODULE_TREE: &str = "routine.get_module_tree";
pub const ROUTINE_GET_MODULE_TREE_REPLY: &str = "routine.get_module_tree_reply";
/// py→kernel: 往父模块加载子模块(带 req_id+parent_id+child_id,kernel 回 module_loaded)
pub const ROUTINE_LOAD_MODULE: &str = "routine.load_module";
pub const ROUTINE_MODULE_LOADED: &str = "routine.module_loaded";
/// py→kernel: 卸载子模块(带 req_id+child_id,kernel 回 module_unloaded)
pub const ROUTINE_UNLOAD_MODULE: &str = "routine.unload_module";
pub const ROUTINE_MODULE_UNLOADED: &str = "routine.module_unloaded";

// ---------------------------------------------------------------------------
// routine 调 routine(submit/start/stop,py→kernel→py)
//
// 父 routine 调子 routine 的 wire 协议:submit 创建子命令 → submitted 回执带
// child_id+modules → start 启动子 → lifecycle.stopped 中转回父拿 result.
// kernel 是中央调度器,跨 hub 路由靠 kernel 完成.
// ---------------------------------------------------------------------------

/// py→kernel: 提交子 routine(带 req_id+parent_id+name+kwargs,kernel 回 submitted)
pub const ROUTINE_SUBMIT: &str = "routine.submit";
/// kernel→py: submit 回执(带 req_id+child_id+modules 或 error)
pub const ROUTINE_SUBMITTED: &str = "routine.submitted";
/// py→kernel: 启动已 submit 的子 routine(带 child_id)
pub const ROUTINE_START: &str = "routine.start";
/// py→kernel: 停止子 routine(带 child_id)
pub const ROUTINE_STOP: &str = "routine.stop";
/// py→kernel: 撤销 submit(清 created 态子命令,带 child_id)
pub const ROUTINE_UNSUBMIT: &str = "routine.unsubmit";

// ---------------------------------------------------------------------------
// kernel→server 推模块树拓扑(静态 config,运行期不变)
// ---------------------------------------------------------------------------

pub const MODULE_TREE: &str = "module.tree";

// ---------------------------------------------------------------------------
// catalog 推送与增量变更(走 Stream)
//
// dial-in 下 routine 连上后主动 push 一次 catalog(routines + modules),
// kernel 收到注册路由表 + 回推 module.tree。
// 运行期变更走单条 register/reload/deregister,带 req_id + 回执。
// deregister 走两跳:kernel→持有者(deregister.cmd)→持有者回 ack→kernel 删路由+回执请求者。
// ---------------------------------------------------------------------------

/// dial-in 专用:routine→kernel 全量推送 {routines, modules}
pub const CATALOG_PUSH: &str = "catalog.push";
/// py→kernel: {req_id, name, is_passive, meta} 同名 fail
pub const CATALOG_REGISTER: &str = "catalog.register";
/// py→kernel: {req_id, name, is_passive, meta} 不区分 conn 覆盖
pub const CATALOG_RELOAD: &str = "catalog.reload";
/// py→kernel: {req_id, name} 请求移除
pub const CATALOG_DEREGISTER: &str = "catalog.deregister";
/// kernel→py: {req_id, name} 通知持有者本地 dereg
pub const CATALOG_DEREGISTER_CMD: &str = "catalog.deregister.cmd";
/// py→kernel: {req_id, ok, error?} 持有者回执
pub const CATALOG_DEREGISTER_CMD_ACK: &str = "catalog.deregister.cmd.ack";
/// kernel→py: {req_id, ok, error?}
pub const CATALOG_REGISTERED: &str = "catalog.registered";
pub const CATALOG_RELOADED: &str = "catalog.reloaded";
pub const CATALOG_DEREGISTERED: &str = "catalog.deregistered";
/// kernel→py: {req_id, registered[], skipped[]} 全量 push 回执
pub const CATALOG_PUSHED: &str = "catalog.pushed";

// ---------------------------------------------------------------------------
// ⚠ DEPRECATED:老版本遗留事件(p2p)
//
// Python 新版已移除 p2p(→message.*)。shell 模块已从框架层移除(下沉到业务层),
// shell.* 事件常量已删除。此处保留 p2p 事件作过渡桩,**待 messaging 模块
// 对齐后连同本区块一并删除**。新代码严禁使用。
// ---------------------------------------------------------------------------

pub const LIFECYCLE_HEARTBEAT: &str = "lifecycle.heartbeat";
pub const LIFECYCLE_HEARTBEAT_ACK: &str = "lifecycle.heartbeat_ack";

pub const P2P_SEND: &str = "p2p.send";
pub const P2P_DELIVERED: &str = "p2p.delivered";

pub const SHELL_REQ: &str = "shell.req";
pub const SHELL_REQ_REPLY: &str = "shell.req_reply";

pub const REQ_EVENT_GET_ROUTINE_MODULES: &str = "get_routine_modules";
pub const REQ_EVENT_GET_ROUTERS: &str = "get_routers";
pub const REQ_EVENT_GET_ROUTINE_FROM_ROUTER: &str = "get_routine_from_router";
